"""Download event tracker, run by the scheduler daemon after every tick.

It compares what aria2 reports now with the previous tick: a gid that appeared is
`added`, a gid that newly shows up in the stopped list is `completed` / `error` /
`removed`. Events go to the database (replayable by API clients) and to the optional
ntfy / webhook notifier. State is in memory, so nothing is emitted for the first tick
after the daemon starts and events during daemon downtime are not reconstructed."""
import logging

from . import notify, services
from .aria2 import Aria2Error
from .models import DownloadEvent

log = logging.getLogger("core.events")
_state = {}

_KIND = {"complete": DownloadEvent.Kind.COMPLETED, "error": DownloadEvent.Kind.ERROR, "removed": DownloadEvent.Kind.REMOVED}


def _record(server, kind, gid, name="", total=0, error_code="", error_message=""):
    event = DownloadEvent.objects.create(
        server=server, kind=kind, gid=gid, name=name[:500], total=total,
        error_code=str(error_code or ""), error_message=error_message or "",
    )
    log.info("%s: %s %s (%s)", server.name, kind, name or gid, gid)
    notify.dispatch(server.name, event)
    return event


def observe(server, result):
    if result is None or result.skipped or not result.info:
        return
    live, stopped = result.info["live_gids"], result.info["stopped"]
    state = _state.get(server.pk)
    _state[server.pk] = {"live": set(live), "stopped": set(stopped)}
    if state is None:
        return  # first tick after start: nothing to compare with
    new_live = live - state["live"]
    new_stopped = {g: st for g, st in stopped.items() if g not in state["stopped"]}
    if not new_live and not new_stopped:
        return
    client = server.client()
    try:
        if new_live:
            names = services.resolve_names(client, server.pk, sorted(new_live))
            for gid in sorted(new_live):
                if not names[gid].startswith("[METADATA]"):
                    _record(server, DownloadEvent.Kind.ADDED, gid, names[gid])
        for gid in sorted(new_stopped):
            kind = _KIND.get(new_stopped[gid])
            if kind is None:
                continue
            status = client.tell_status(gid, ["gid", "status", "errorCode", "errorMessage", "totalLength", "files", "bittorrent", "followedBy"])
            if status.get("followedBy"):
                continue  # magnet metadata finished, the real download follows
            if services.display_name(status).startswith("[METADATA]"):
                continue
            _record(server, kind, gid, services.display_name(status), services._int(status.get("totalLength")),
                    status.get("errorCode", "") if kind == DownloadEvent.Kind.ERROR else "",
                    status.get("errorMessage", "") if kind == DownloadEvent.Kind.ERROR else "")
    except Aria2Error as exc:
        log.warning("%s: could not read download details: %s", server.name, exc)
