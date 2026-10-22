import os
import time
from datetime import timedelta

from django.utils import timezone

from core import scheduler, services
from core.models import QueueItem, SpeedRule

from .helpers import Aria2Client_for, LiveAria2Case


def wait_until(predicate, timeout=60, step=0.3):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(step)
    return False


class SchedulerLiveTests(LiveAria2Case):
    def setUp(self):
        self.clean_aria2()
        scheduler._applied_limits.clear()
        scheduler._baseline.clear()
        scheduler._concurrency.clear()
        scheduler._concurrency_baseline.clear()
        Aria2Client_for(self).change_global_option({"max-concurrent-downloads": "50"})

    def test_max_downloads_is_enforced_and_queue_drains(self):
        server = self.make_server(max_downloads=2)
        result = services.add_downloads(server, uri_lines=[[self.url(i)] for i in range(1, 6)])
        self.assertEqual((result.dispatched, result.queued), (2, 3))
        client = Aria2Client_for(self)
        peak = 0

        def drained():
            nonlocal peak
            peak = max(peak, scheduler.running_count(client))
            scheduler.tick(server)
            return not server.queue.filter(status=QueueItem.Status.QUEUED).exists() and scheduler.running_count(client) == 0

        self.assertTrue(wait_until(drained), "queue did not drain")
        self.assertLessEqual(peak, 2)
        self.assertEqual(server.queue.filter(status=QueueItem.Status.DISPATCHED).count(), 5)
        for i in range(1, 6):
            self.assertTrue(os.path.exists(os.path.join(self.dl, "file%d.bin" % i)), "file%d missing" % i)

    def test_order_follows_position_and_top(self):
        server = self.make_server(max_downloads=1)
        services.add_downloads(server, uri_lines=[[self.url(1)], [self.url(2)], [self.url(3)]])
        queued = list(server.queue.filter(status=QueueItem.Status.QUEUED))
        self.assertEqual([q.name for q in queued], ["file2.bin", "file3.bin"])  # file1 started immediately
        services.queue_op(server, "top", queued[1].pk)
        self.assertEqual([q.name for q in server.queue.filter(status=QueueItem.Status.QUEUED)], ["file3.bin", "file2.bin"])

    def test_not_before_in_the_future_stays_queued(self):
        server = self.make_server(max_downloads=5)
        result = services.add_downloads(server, uri_lines=[[self.url(1)]], not_before=timezone.now() + timedelta(hours=1))
        self.assertEqual((result.dispatched, result.queued), (0, 1))
        item = server.queue.get()
        item.not_before = timezone.now() - timedelta(seconds=1)
        item.save()
        tick = scheduler.tick(server)
        self.assertEqual(tick.dispatched, 1)

    def test_disabled_scheduler_flushes_everything(self):
        server = self.make_server(max_downloads=1, scheduler_enabled=False)
        result = services.add_downloads(server, uri_lines=[[self.url(i)] for i in range(1, 4)])
        self.assertEqual((result.dispatched, result.queued), (3, 0))

    def test_paused_and_seeding_do_not_use_slots(self):
        server = self.make_server(max_downloads=1)
        services.add_downloads(server, uri_lines=[[self.url(1)]], options={"max-download-limit": "5K"})
        client = Aria2Client_for(self)
        gid = server.queue.get().gid  # aria2 may still list a new download as waiting
        ok = wait_until(lambda: client.tell_status(gid, ["status"])["status"] == "active", timeout=10)
        self.assertTrue(ok, client.tell_status(gid, ["status", "errorCode", "errorMessage", "files"]))
        client.force_pause(gid)
        self.assertTrue(wait_until(lambda: scheduler.running_count(client) == 0, timeout=10))
        result = services.add_downloads(server, uri_lines=[[self.url(2)]])
        self.assertEqual(result.dispatched, 1)

    def test_failed_dispatch_does_not_block_the_queue(self):
        server = self.make_server(max_downloads=1)
        QueueItem.objects.create(server=server, kind="uri", uris=["not a valid uri"], name="bad", options={})
        QueueItem.objects.create(server=server, kind="uri", uris=[self.url(1)], name="good", options={})
        tick = scheduler.tick(server)
        self.assertEqual((tick.failed, tick.dispatched), (1, 1))
        self.assertEqual(server.queue.get(name="bad").status, QueueItem.Status.FAILED)

    def test_unreachable_aria2_keeps_items_queued(self):
        server = self.make_server(max_downloads=2)
        server.rpc_url = "http://127.0.0.1:1/jsonrpc"
        server.save()
        QueueItem.objects.create(server=server, kind="uri", uris=[self.url(1)], name="x", options={})
        tick = scheduler.tick(server)
        self.assertTrue(tick.skipped)
        self.assertEqual(server.queue.get().status, QueueItem.Status.QUEUED)

    def test_speed_rule_is_applied_and_cleared(self):
        server = self.make_server()
        client = Aria2Client_for(self)
        SpeedRule.objects.create(server=server, name="always", days=list(range(7)), start_time="00:00", end_time="00:00", download_limit="123K", upload_limit="45K")
        scheduler.tick(server)
        opts = client.get_global_option()
        self.assertEqual((opts["max-overall-download-limit"], opts["max-overall-upload-limit"]), ("125952", "46080"))
        server.speed_rules.all().delete()
        scheduler.tick(server)
        # back to what aria2 had before the rule took over (250K)
        self.assertEqual(client.get_global_option()["max-overall-download-limit"], "256000")

    def test_manual_resume_cannot_exceed_the_limit_and_baseline_is_restored(self):
        server = self.make_server(max_downloads=1)
        client = Aria2Client_for(self)
        slow = {"max-download-limit": "5K"}
        services.add_downloads(server, uri_lines=[[self.url(1)]], options=slow)
        first = server.queue.get().gid
        self.assertEqual(client.get_global_option()["max-concurrent-downloads"], "1")
        self.assertTrue(wait_until(lambda: client.tell_status(first, ["status"])["status"] == "active", timeout=10))
        client.force_pause(first)
        services.add_downloads(server, uri_lines=[[self.url(2)]], options=slow)  # takes the freed slot
        second = server.queue.get(name="file2.bin").gid
        self.assertTrue(wait_until(lambda: client.tell_status(second, ["status"])["status"] == "active", timeout=10))
        client.unpause(first)  # bypasses the queue
        self.assertTrue(wait_until(lambda: client.tell_status(first, ["status"])["status"] == "waiting", timeout=10))
        self.assertEqual(client.tell_status(second, ["status"])["status"], "active")
        # switching the scheduler off gives aria2 its own limit back
        server.scheduler_enabled = False
        server.save()
        scheduler.tick(server)
        self.assertEqual(client.get_global_option()["max-concurrent-downloads"], "50")


class SpeedHistoryTests(LiveAria2Case):
    def setUp(self):
        self.clean_aria2()

    def test_daemon_records_samples_and_respects_the_interval(self):
        from django.core.management import call_command

        from core.models import SpeedSample

        server = self.make_server()
        services.add_downloads(server, uri_lines=[[self.url(1)]], options={"max-download-limit": "30K"})
        tick = scheduler.tick(server)
        self.assertIsNotNone(tick.download_speed)
        call_command("run_scheduler", "--once")
        self.assertEqual(SpeedSample.objects.filter(server=server).count(), 1)
        sample = SpeedSample.objects.get(server=server)
        self.assertGreaterEqual(sample.download, 0)
        # unreachable aria2: no sample, no crash
        server.rpc_url = "http://127.0.0.1:1/jsonrpc"
        server.save()
        call_command("run_scheduler", "--once")
        self.assertEqual(SpeedSample.objects.filter(server=server).count(), 1)

    def test_old_samples_are_pruned(self):
        from datetime import timedelta

        from django.core.management import call_command
        from django.utils import timezone

        from core.models import SpeedSample

        server = self.make_server()
        old = SpeedSample.objects.create(server=server, download=1)
        SpeedSample.objects.filter(pk=old.pk).update(ts=timezone.now() - timedelta(hours=3))
        call_command("run_scheduler", "--once")
        self.assertFalse(SpeedSample.objects.filter(pk=old.pk).exists())


class UnencodedLinkTests(LiveAria2Case):
    """Links copied from a browser bar or a site often contain spaces, commas, brackets."""

    NAMES = [
        "(Acoustic, Guitar, Instrumental) Tugores - Imatges De Knopfler (A Mark Knopfler Tribute) - 2008, MP3, 320 kbps.zip",
        "[FTUApps com] - Text to Speech Studio v2 0 0 Pre-Activated.zip",
    ]

    def setUp(self):
        self.clean_aria2()
        folder = os.path.join(self.www, "get_zip_ngen_free", "30004742")
        os.makedirs(folder, exist_ok=True)
        for name in self.NAMES:
            with open(os.path.join(folder, name), "wb") as f:
                f.write(b"x" * 50000)

    def test_links_with_spaces_download_with_the_right_file_name(self):
        base = "http://127.0.0.1:%d/get_zip_ngen_free/30004742/" % self.http_port
        text = "\n".join(base + n + "?st=abc_DEF-1&e=1792648254" for n in self.NAMES)
        server = self.make_server(max_downloads=5)
        result = services.add_downloads(server, uri_lines=services.parse_uri_lines(text))
        self.assertEqual((result.dispatched, result.failed), (2, 0))
        client = Aria2Client_for(self)

        def finished():
            scheduler.tick(server)
            return all(client.tell_status(q.gid.split(",")[0], ["status"])["status"] == "complete" for q in server.queue.all())

        self.assertTrue(wait_until(finished, timeout=20), [client.tell_status(q.gid, ["status", "errorMessage"]) for q in server.queue.all()])
        for name in self.NAMES:
            self.assertTrue(os.path.exists(os.path.join(self.dl, name)), name + " missing")
        self.assertEqual(sorted(q.name for q in server.queue.all()), sorted(self.NAMES))
