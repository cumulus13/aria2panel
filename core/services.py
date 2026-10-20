"""Use cases shared by the views: download summaries, adding downloads, queue control."""
import base64
import os
import posixpath
import re
import threading
import time
from dataclasses import dataclass, field

from django.db import transaction
from django.db.models import Max

from . import conf, scheduler
from .aria2 import Aria2Error
from .models import QueueItem

LIST_KEYS = [
    "gid", "status", "totalLength", "completedLength", "downloadSpeed", "uploadSpeed",
    "connections", "numSeeders", "seeder", "errorCode", "errorMessage", "dir",
    "bittorrent", "files", "followedBy", "verifyIntegrityPending",
]

# aria2 options that run commands on the aria2 host or reconfigure its RPC endpoint
BLOCKED_OPTIONS = {
    "on-download-start", "on-download-pause", "on-download-stop", "on-download-complete",
    "on-download-error", "on-bt-download-complete", "conf-path", "rpc-listen-port",
    "rpc-listen-all", "rpc-secret", "rpc-user", "rpc-passwd", "enable-rpc", "rpc-allow-origin-all",
    "rpc-certificate", "rpc-private-key", "rpc-secure", "save-session", "input-file", "log",
    "daemon", "stop", "stop-with-process",
}
_OPTION_NAME = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class OptionError(ValueError):
    pass


def clean_options(options):
    """Validate user supplied aria2 options (add dialog, global options)."""
    cleaned = {}
    for key, value in (options or {}).items():
        key = str(key).strip()
        if not key:
            continue
        if not _OPTION_NAME.match(key):
            raise OptionError("invalid option name: %r" % key)
        if key in BLOCKED_OPTIONS:
            raise OptionError("option %s is not allowed" % key)
        if value is None or str(value) == "":
            continue
        if isinstance(value, bool):
            value = "true" if value else "false"
        cleaned[key] = str(value)
    allowed = conf.get("security.allowed_download_dirs")
    if allowed and "dir" in cleaned:
        target = posixpath.normpath(cleaned["dir"])
        ok = ".." not in target.split("/") and any(
            target == posixpath.normpath(a) or target.startswith(posixpath.normpath(a).rstrip("/") + "/") for a in allowed
        )
        if not ok:
            raise OptionError("download directory is not allowed")
    return cleaned


def _basename(path):
    return posixpath.basename(str(path).replace("\\", "/").rstrip("/"))


def display_name(d):
    info = (d.get("bittorrent") or {}).get("info") or {}
    if info.get("name"):
        return info["name"]
    files = d.get("files") or []
    if files:
        path = files[0].get("path") or ""
        uris = files[0].get("uris") or []
        if path and not path.startswith("[METADATA]"):
            name = _basename(path)
            return name if len(files) == 1 else "%s (+%d)" % (name, len(files) - 1)
        if path:
            return path
        if uris:
            uri = uris[0].get("uri", "")
            return _basename(uri.split("?")[0]) or uri
    return d.get("gid", "")


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def summarize(d):
    total, done = _int(d.get("totalLength")), _int(d.get("completedLength"))
    speed = _int(d.get("downloadSpeed"))
    files = d.get("files") or []
    return {
        "gid": d.get("gid"),
        "status": d.get("status"),
        "name": display_name(d),
        "total": total,
        "completed": done,
        "progress": round(done * 100.0 / total, 2) if total else 0.0,
        "downloadSpeed": speed,
        "uploadSpeed": _int(d.get("uploadSpeed")),
        "connections": _int(d.get("connections")),
        "seeders": _int(d.get("numSeeders")),
        "seeding": str(d.get("seeder", "false")).lower() == "true",
        "eta": int((total - done) / speed) if speed > 0 and total > done else None,
        "errorCode": d.get("errorCode"),
        "errorMessage": d.get("errorMessage", ""),
        "dir": d.get("dir", ""),
        "torrent": "bittorrent" in d,
        "numFiles": len(files),
        "followedBy": d.get("followedBy") or [],
        "queued": False,
    }


def queue_row(item, position):
    return {
        "gid": "q:%d" % item.pk,
        "id": item.pk,
        "status": "queued",
        "name": item.name or item.kind,
        "kind": item.kind,
        "position": position,
        "notBefore": item.not_before.isoformat() if item.not_before else None,
        "queued": True,
        "total": 0, "completed": 0, "progress": 0.0, "downloadSpeed": 0, "uploadSpeed": 0,
        "connections": 0, "seeders": 0, "seeding": False, "eta": None, "errorCode": None,
        "errorMessage": "", "dir": (item.options or {}).get("dir", ""), "torrent": item.kind == "torrent",
        "numFiles": 0, "followedBy": [],
    }


def snapshot(server):
    client = server.client()
    limit = conf.get("ui.stopped_limit")
    calls = [
        ("getGlobalStat", []),
        ("tellActive", [LIST_KEYS]),
        ("tellWaiting", [0, 1000, LIST_KEYS]),
    ]
    if limit:
        calls.append(("tellStopped", [0, limit, LIST_KEYS]))
    results = client.multicall(calls)
    for r in results:
        if isinstance(r, Aria2Error):
            raise r
    stat, active, waiting = results[0], results[1], results[2]
    stopped = results[3] if limit else []
    downloads = [summarize(d) for d in list(active) + list(waiting) + list(stopped)]
    queue = list(
        QueueItem.objects.filter(server=server, status=QueueItem.Status.QUEUED).order_by("position", "id")[:500]
    )
    downloads += [queue_row(q, i + 1) for i, q in enumerate(queue)]
    running = sum(1 for d in active if str(d.get("seeder", "false")).lower() != "true") + sum(
        1 for d in waiting if d.get("status") != "paused"
    )
    return {
        "server": {"id": server.pk, "name": server.name},
        "global": {k: _int(stat.get(k)) for k in ("downloadSpeed", "uploadSpeed", "numActive", "numWaiting", "numStopped")},
        "downloads": downloads,
        "scheduler": {"enabled": server.scheduler_enabled, "max": server.max_downloads, "running": running, "queued": len(queue)},
    }


@dataclass
class AddResult:
    dispatched: int = 0
    queued: int = 0
    failed: int = 0
    errors: list = field(default_factory=list)


def parse_uri_lines(text):
    """One download per line, several mirrors for the same file separated by whitespace."""
    out = []
    for line in (text or "").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line.split())
    return out


@transaction.atomic
def _create_items(server, specs):
    top = QueueItem.objects.filter(server=server).aggregate(m=Max("position"))["m"] or 0
    items = []
    for i, spec in enumerate(specs, start=1):
        items.append(QueueItem.objects.create(server=server, position=top + i, **spec))
    return items


def add_downloads(server, *, uri_lines=(), torrents=(), metalinks=(), options=None, not_before=None):
    """torrents / metalinks: [(filename, bytes)]. Returns AddResult."""
    options = clean_options(options)
    specs = []
    for mirrors in uri_lines:
        specs.append({"kind": QueueItem.Kind.URI, "uris": mirrors, "name": _basename(mirrors[0].split("?")[0]) or mirrors[0], "options": options, "not_before": not_before})
    for kind, files in ((QueueItem.Kind.TORRENT, torrents), (QueueItem.Kind.METALINK, metalinks)):
        for filename, data in files:
            specs.append({"kind": kind, "payload": base64.b64encode(data).decode(), "name": filename, "options": options, "not_before": not_before})
    if not specs:
        raise ValueError("nothing to add")
    items = _create_items(server, specs)
    scheduler.tick(server)  # uses a free slot right away, the daemon handles the rest
    result = AddResult()
    for item in QueueItem.objects.filter(pk__in=[i.pk for i in items]):
        if item.status == QueueItem.Status.DISPATCHED:
            result.dispatched += 1
        elif item.status == QueueItem.Status.FAILED:
            result.failed += 1
            result.errors.append("%s: %s" % (item.name, item.error))
        else:
            result.queued += 1
    return result


def queue_op(server, op, item_id=None, not_before=None):
    qs = QueueItem.objects.filter(server=server, status=QueueItem.Status.QUEUED)
    if op == "clear":
        return qs.update(status=QueueItem.Status.CANCELLED)
    item = qs.filter(pk=item_id).first()
    if item is None:
        raise ValueError("queue item not found (already started?)")
    if op == "remove":
        item.status = QueueItem.Status.CANCELLED
        item.save(update_fields=["status"])
    elif op in ("up", "down", "top", "bottom"):
        with transaction.atomic():
            ordered = list(qs.select_for_update().order_by("position", "id"))
            idx = next(i for i, q in enumerate(ordered) if q.pk == item.pk)
            ordered.pop(idx)
            new = {"up": max(idx - 1, 0), "down": idx + 1, "top": 0, "bottom": len(ordered)}[op]
            ordered.insert(new, item)
            for pos, q in enumerate(ordered, start=1):
                if q.position != pos:
                    q.position = pos
                    q.save(update_fields=["position"])
    elif op == "start":
        client = server.client()
        try:
            gid = scheduler.dispatch(client, item)
        except Aria2Error as exc:
            item.status = QueueItem.Status.FAILED
            item.error = exc.message
            item.save(update_fields=["status", "error"])
            raise
        from django.utils import timezone

        item.status, item.gid, item.dispatched_at = QueueItem.Status.DISPATCHED, gid, timezone.now()
        item.save(update_fields=["status", "gid", "dispatched_at"])
    elif op == "schedule":
        item.not_before = not_before
        item.save(update_fields=["not_before"])
        scheduler.tick(server)
    else:
        raise ValueError("unknown queue operation")
    return 1


def retry(server, gid):
    """Re-add a failed download from its original URIs and drop the old result."""
    client = server.client()
    status = client.tell_status(gid, ["gid", "status", "files", "dir", "bittorrent"])
    if "bittorrent" in status:
        raise ValueError("torrent downloads cannot be retried, add the torrent again")
    uris = []
    for f in status.get("files", []):
        for u in f.get("uris", []):
            if u.get("uri") not in uris:
                uris.append(u["uri"])
    if not uris:
        raise ValueError("no source URI is known for this download")
    options = client.get_option(gid)
    keep = {k: v for k, v in options.items() if k in ("dir", "out", "split", "max-connection-per-server", "user-agent", "referer", "header", "max-download-limit")}
    result = add_downloads(server, uri_lines=[uris], options=keep)
    try:
        client.remove_result(gid)
    except Aria2Error:
        pass
    return result


def detail(server, gid):
    client = server.client()
    status = client.tell_status(gid)
    options = {}
    try:
        options = client.get_option(gid)
    except Aria2Error:
        pass
    summary = summarize(status)
    files = []
    for f in status.get("files", []):
        length, done = _int(f.get("length")), _int(f.get("completedLength"))
        files.append({
            "index": f.get("index"),
            "path": f.get("path"),
            "length": length,
            "completed": done,
            "progress": round(done * 100.0 / length, 2) if length else 0.0,
            "selected": f.get("selected") == "true",
        })
    info = {k: status.get(k) for k in ("infoHash", "numPieces", "pieceLength", "connections", "numSeeders", "creationDate", "comment") if status.get(k) is not None}
    return {"summary": summary, "files": files, "options": dict(sorted(options.items())), "info": info, "dir": os.path.normpath(status.get("dir", "")) if status.get("dir") else ""}


# ---------------------------------------------------------------- live progress (API / SSE)
PROGRESS_KEYS = [
    "gid", "status", "totalLength", "completedLength", "downloadSpeed", "uploadSpeed",
    "connections", "numSeeders", "seeder",
]
_names = {}
_names_lock = threading.Lock()
_progress_cache = {}
_progress_lock = threading.Lock()


def resolve_names(client, server_pk, gids):
    """gid -> display name. Names are looked up once per gid (the file list can be large)."""
    with _names_lock:
        missing = [g for g in gids if (server_pk, g) not in _names]
    if missing:
        results = client.multicall([("tellStatus", [g, ["files", "bittorrent"]]) for g in missing])
        with _names_lock:
            if len(_names) > 5000:
                for key in list(_names)[:2500]:
                    del _names[key]
            for gid, r in zip(missing, results):
                if isinstance(r, Aria2Error):
                    _names[(server_pk, gid)] = gid
                else:
                    r["gid"] = gid
                    _names[(server_pk, gid)] = display_name(r)
    with _names_lock:
        return {g: _names.get((server_pk, g), g) for g in gids}


def progress(server, ttl=0.8):
    """Compact live state of the running downloads, cached for `ttl` seconds so many
    connected clients share one aria2 round trip."""
    now = time.monotonic()
    with _progress_lock:
        hit = _progress_cache.get(server.pk)
        if hit and now - hit[0] < ttl:
            return hit[1]
    client = server.client()
    results = client.multicall([("getGlobalStat", []), ("tellActive", [PROGRESS_KEYS])])
    for r in results:
        if isinstance(r, Aria2Error):
            raise r
    stat, active = results
    names = resolve_names(client, server.pk, [d["gid"] for d in active])
    items = []
    for d in active:
        total, done, speed = _int(d.get("totalLength")), _int(d.get("completedLength")), _int(d.get("downloadSpeed"))
        items.append({
            "gid": d["gid"], "name": names[d["gid"]], "status": d.get("status"),
            "total": total, "completed": done, "progress": round(done * 100.0 / total, 2) if total else 0.0,
            "downloadSpeed": speed, "uploadSpeed": _int(d.get("uploadSpeed")),
            "eta": int((total - done) / speed) if speed > 0 and total > done else None,
            "connections": _int(d.get("connections")), "seeding": str(d.get("seeder", "false")).lower() == "true",
        })
    data = {
        "global": {k: _int(stat.get(k)) for k in ("downloadSpeed", "uploadSpeed", "numActive", "numWaiting", "numStopped")},
        "active": items,
        "scheduler": {
            "enabled": server.scheduler_enabled, "max": server.max_downloads,
            "running": sum(1 for i in items if not i["seeding"]),
            "queued": QueueItem.objects.filter(server=server, status=QueueItem.Status.QUEUED).count(),
        },
    }
    with _progress_lock:
        _progress_cache[server.pk] = (time.monotonic(), data)
    return data
