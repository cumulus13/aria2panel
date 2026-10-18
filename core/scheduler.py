"""Download scheduler.

tick(server) is the whole algorithm: count what is running in aria2, then dispatch
queued items (FIFO by position) until `max_downloads` is reached. It is called by the
daemon (manage.py run_scheduler) every second and right after downloads are added, so
a free slot is used immediately. A row lock on the server serialises concurrent ticks
from several processes."""
import logging
from dataclasses import dataclass

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .aria2 import Aria2ConnectionError, Aria2Error
from .models import Aria2Server, QueueItem, SpeedRule

log = logging.getLogger("core.scheduler")

# last speed limits pushed to each aria2 server, to only talk to aria2 on changes
_applied_limits = {}
# limits aria2 had before this process started managing them, restored when management ends
_baseline = {}
# aria2 max-concurrent-downloads value pushed by sync_concurrency, and the original one
_concurrency = {}
_concurrency_baseline = {}


@dataclass
class TickResult:
    running: int = 0
    free: int = 0
    dispatched: int = 0
    failed: int = 0
    queued: int = 0
    skipped: bool = False


def running_info(client):
    """(running, seeding): downloads that occupy a slot = active ones (torrents that only
    seed do not) plus waiting ones that are not paused; seeding = active seeders."""
    result = client.multicall(
        [
            ("tellActive", [["gid", "status", "seeder"]]),
            ("tellWaiting", [0, 1000, ["gid", "status"]]),
        ]
    )
    for r in result:
        if isinstance(r, Aria2Error):
            raise r
    active, waiting = result
    seeding = sum(1 for d in active if str(d.get("seeder", "false")).lower() == "true")
    running = len(active) - seeding + sum(1 for d in waiting if d.get("status") != "paused")
    return running, seeding


def running_count(client):
    return running_info(client)[0]


def sync_concurrency(server, client, seeding):
    """Keep aria2's own limit at max_downloads (+ seeders, which the scheduler ignores),
    so downloads that bypass the queue, e.g. resuming a paused one, cannot exceed it."""
    pk = server.pk
    if server.scheduler_enabled and server.enforce_in_aria2:
        desired = str(server.max_downloads + seeding)
        if _concurrency.get(pk) != desired:
            if pk not in _concurrency_baseline:
                _concurrency_baseline[pk] = client.get_global_option().get("max-concurrent-downloads", "5")
            client.change_global_option({"max-concurrent-downloads": desired})
            _concurrency[pk] = desired
    elif pk in _concurrency:
        _concurrency.pop(pk)
        baseline = _concurrency_baseline.pop(pk, None)
        if baseline:
            client.change_global_option({"max-concurrent-downloads": baseline})
            log.info("%s: aria2 max-concurrent-downloads restored to %s", server.name, baseline)


def dispatch(client, item):
    """Send one queued item to aria2, returns the gid(s) as a string."""
    if item.kind == QueueItem.Kind.TORRENT:
        result = client.add_torrent(item.payload, [], item.options)
    elif item.kind == QueueItem.Kind.METALINK:
        result = client.add_metalink(item.payload, item.options)
    else:
        result = client.add_uri(item.uris, item.options)
    return ",".join(result) if isinstance(result, list) else str(result)


def _current_limits(client):
    options = client.get_global_option()
    return (options.get("max-overall-download-limit", "0"), options.get("max-overall-upload-limit", "0"))


def apply_speed_rules(server, client, now):
    rules = list(SpeedRule.objects.filter(server=server, enabled=True))
    defaults = (server.default_download_limit, server.default_upload_limit)
    if not rules and defaults == ("0", "0"):
        # nothing to manage: leave aria2's own limits alone, and give back the ones it
        # had before a rule (or default) took over
        was_managed = _applied_limits.pop(server.pk, None) is not None
        baseline = _baseline.pop(server.pk, None)
        if was_managed and baseline is not None:
            client.change_global_option({"max-overall-download-limit": baseline[0], "max-overall-upload-limit": baseline[1]})
            log.info("%s: speed limits restored to down=%s up=%s", server.name, baseline[0], baseline[1])
        return
    local = timezone.localtime(now)
    rule = next((r for r in rules if r.matches(local)), None)
    desired = (rule.download_limit, rule.upload_limit) if rule else defaults
    if _applied_limits.get(server.pk) != desired:
        if server.pk not in _baseline:
            _baseline[server.pk] = _current_limits(client)
        client.change_global_option({"max-overall-download-limit": desired[0], "max-overall-upload-limit": desired[1]})
        _applied_limits[server.pk] = desired
        log.info("%s: speed limits set to down=%s up=%s%s", server.name, desired[0], desired[1], " (rule %s)" % rule.name if rule else "")


def tick(server_or_pk):
    """Returns a TickResult, or None if the server is disabled or another process is
    already running a tick for it."""
    pk = getattr(server_or_pk, "pk", server_or_pk)
    now = timezone.now()
    with transaction.atomic():
        server = Aria2Server.objects.select_for_update(skip_locked=True).filter(pk=pk, enabled=True).first()
        if server is None:
            return None
        client = server.client()
        try:
            apply_speed_rules(server, client, now)
            running, seeding = running_info(client)
            sync_concurrency(server, client, seeding)
        except Aria2Error as exc:
            log.warning("%s: aria2 unavailable: %s", server.name, exc)
            return TickResult(skipped=True, queued=QueueItem.objects.filter(server=server, status=QueueItem.Status.QUEUED).count())

        due = (
            QueueItem.objects.filter(server=server, status=QueueItem.Status.QUEUED)
            .filter(Q(not_before__isnull=True) | Q(not_before__lte=now))
            .order_by("position", "id")
        )
        # scheduler disabled: no limit, the queue is flushed to aria2
        free = (server.max_downloads - running) if server.scheduler_enabled else None
        result = TickResult(running=running, free=max(free, 0) if free is not None else -1)
        budget = free  # None = unlimited
        if budget is None or budget > 0:
            for item in due[:1000]:
                if budget is not None and budget <= 0:
                    break
                try:
                    gid = dispatch(client, item)
                except Aria2ConnectionError as exc:
                    log.warning("%s: lost connection while dispatching: %s", server.name, exc)
                    break
                except Aria2Error as exc:
                    # a rejected item must not use up a slot or block the ones behind it
                    item.status = QueueItem.Status.FAILED
                    item.error = exc.message
                    item.save(update_fields=["status", "error"])
                    result.failed += 1
                    log.warning("%s: could not add %r: %s", server.name, item.name, exc.message)
                    continue
                item.status = QueueItem.Status.DISPATCHED
                item.gid = gid
                item.dispatched_at = timezone.now()
                item.save(update_fields=["status", "gid", "dispatched_at"])
                result.dispatched += 1
                if budget is not None:
                    budget -= 1
                log.info("%s: started %r (%s)", server.name, item.name, gid)
        result.queued = QueueItem.objects.filter(server=server, status=QueueItem.Status.QUEUED).count()
        return result
