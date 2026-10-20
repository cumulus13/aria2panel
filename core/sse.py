"""Server-Sent Events stream of one aria2 server.

Event types (all `data:` payloads are JSON):
  snapshot            full state, sent once after connecting (and after aria2 comes back)
  progress            running downloads + global speed, every `sse.interval_seconds` while something runs
  download.added | download.completed | download.error | download.removed
                      persisted events, carry `id:` so a client can resume with Last-Event-ID
  connection          {"aria2": "up"|"down"} when panel <-> aria2 connectivity changes
  bye                 the stream reached its maximum lifetime, reconnect
Comment lines (`: ping`) keep idle connections alive."""
import json
import time

from django.db import close_old_connections, connection

from . import conf, services
from .aria2 import Aria2Error
from .models import DownloadEvent

HEARTBEAT = 15.0


def frame(event, data, event_id=None):
    head = "id: %s\n" % event_id if event_id is not None else ""
    return "%sevent: %s\ndata: %s\n\n" % (head, event, json.dumps(data, separators=(",", ":")))


def latest_event_id(server):
    last = DownloadEvent.objects.filter(server=server).order_by("-id").values_list("id", flat=True).first()
    return last or 0


def stream(server, last_id, *, interval=None, max_seconds=None, heartbeat=HEARTBEAT, sleep=time.sleep, clock=time.monotonic):
    interval = interval or conf.get("sse.interval_seconds")
    max_seconds = max_seconds or conf.get("sse.max_seconds")
    started = last_sent = clock()
    yield "retry: 3000\n\n"

    def pending():
        return list(DownloadEvent.objects.filter(server=server, id__gt=last_id).order_by("id")[:200])

    for event in pending():  # replay what the client missed
        yield frame("download." + event.kind, event.as_dict(), event.pk)
        last_id = event.pk

    aria2_up = None
    try:
        yield frame("snapshot", services.snapshot(server))
        aria2_up = True
    except Aria2Error as exc:
        yield frame("connection", {"aria2": "down", "error": exc.message})
        aria2_up = False
    last_payload = None

    while clock() - started < max_seconds:
        tick = clock()
        sent = False
        try:
            data = services.progress(server)
            if aria2_up is not True:
                yield frame("connection", {"aria2": "up"})
                yield frame("snapshot", services.snapshot(server))
                aria2_up, sent = True, True
            payload = json.dumps(data, separators=(",", ":"))
            if data["active"] or payload != last_payload:
                yield "event: progress\ndata: %s\n\n" % payload
                last_payload, sent = payload, True
        except Aria2Error as exc:
            if aria2_up is not False:
                yield frame("connection", {"aria2": "down", "error": exc.message})
                aria2_up, sent = False, True
        for event in pending():
            yield frame("download." + event.kind, event.as_dict(), event.pk)
            last_id, sent = event.pk, True
        if sent:
            last_sent = clock()
        elif clock() - last_sent >= heartbeat:
            yield ": ping\n\n"
            last_sent = clock()
        if not connection.in_atomic_block:  # long-lived stream: drop stale connections between ticks
            close_old_connections()
        sleep(max(0.0, interval - (clock() - tick)))
    yield frame("bye", {"reconnect": True})
