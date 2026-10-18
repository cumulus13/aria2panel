import logging
import signal
import threading
import time
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.utils import timezone

from core import conf, scheduler
from core.models import Aria2Server, QueueItem

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
        last_cleanup = 0.0
        while not stop.is_set():
            started = time.monotonic()
            try:
                close_old_connections()
                for pk in list(Aria2Server.objects.filter(enabled=True).values_list("pk", flat=True)):
                    try:
                        scheduler.tick(pk)
                    except Exception:  # keep the daemon alive, one bad server must not stop the others
                        log.exception("tick failed for server %s", pk)
                if time.monotonic() - last_cleanup > 3600:
                    last_cleanup = time.monotonic()
                    cutoff = timezone.now() - timedelta(days=conf.get("scheduler.keep_days"))
                    deleted, _ = QueueItem.objects.exclude(status=QueueItem.Status.QUEUED).filter(created_at__lt=cutoff).delete()
                    if deleted:
                        log.info("removed %d old queue records", deleted)
                interval = conf.get("scheduler.tick_seconds")
            except Exception:
                log.exception("scheduler pass failed")
                interval = 5.0
            if options["once"]:
                break
            stop.wait(max(0.0, interval - (time.monotonic() - started)))
        log.info("scheduler stopped")
