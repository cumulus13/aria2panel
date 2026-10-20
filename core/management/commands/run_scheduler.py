import logging
import signal
import threading
import time
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.utils import timezone

from core import conf, events, scheduler
from core.models import Aria2Server, DownloadEvent, QueueItem, SpeedSample

log = logging.getLogger("core.scheduler")


class Command(BaseCommand):
    help = "Run the download scheduler daemon (dispatches queued downloads, applies speed rules)."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="run a single pass and exit")

    def handle(self, *args, **options):
        stop = threading.Event()
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: stop.set())
        log.info("scheduler started")
        last_cleanup = float("-inf")
        last_sample = {}
        while not stop.is_set():
            started = time.monotonic()
            try:
                if not options["once"]:
                    close_old_connections()  # long-running daemon: drop dead connections
                for server in list(Aria2Server.objects.filter(enabled=True)):
                    try:
                        result = scheduler.tick(server.pk)
                        self.record_sample(server.pk, result, last_sample)
                        events.observe(server, result)
                    except Exception:  # keep the daemon alive, one bad server must not stop the others
                        log.exception("tick failed for server %s", server.pk)
                if time.monotonic() - last_cleanup > 3600:
                    last_cleanup = time.monotonic()
                    cutoff = timezone.now() - timedelta(days=conf.get("scheduler.keep_days"))
                    deleted, _ = QueueItem.objects.exclude(status=QueueItem.Status.QUEUED).filter(created_at__lt=cutoff).delete()
                    if deleted:
                        log.info("removed %d old queue records", deleted)
                    keep = timezone.now() - timedelta(minutes=conf.get("history.keep_minutes"))
                    SpeedSample.objects.filter(ts__lt=keep).delete()
                    DownloadEvent.objects.filter(ts__lt=timezone.now() - timedelta(days=conf.get("events.keep_days"))).delete()
                interval = conf.get("scheduler.tick_seconds")
            except Exception:
                log.exception("scheduler pass failed")
                interval = 5.0
            if options["once"]:
                break
            stop.wait(max(0.0, interval - (time.monotonic() - started)))
        log.info("scheduler stopped")

    @staticmethod
    def record_sample(pk, result, last_sample):
        if result is None or result.skipped or result.download_speed is None:
            return
        now = time.monotonic()
        if now - last_sample.get(pk, float("-inf")) < conf.get("history.interval_seconds"):
            return
        last_sample[pk] = now
        SpeedSample.objects.create(server_id=pk, download=result.download_speed, upload=result.upload_speed)
