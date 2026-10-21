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


# options an administrator can set as defaults for every new download (speed related only)
RECOMMENDED_DEFAULTS = {
    "max-connection-per-server": "16", "split": "16", "min-split-size": "1M", "file-allocation": "none", "continue": "true",
}
_SIZE = re.compile(r"^(\d+)([KkMm]?)$")


def clean_defaults(data):
    """Validate the per-server default options, empty values are dropped."""
    out = {}
    for key, value in (data or {}).items():
        value = str(value).strip() if value is not None else ""
        if value == "":
            continue
        if key == "max-connection-per-server":
            if not value.isdigit() or not 1 <= int(value) <= 16:
                raise OptionError("connections per server must be between 1 and 16")
        elif key == "split":
            if not value.isdigit() or not 1 <= int(value) <= 64:
                raise OptionError("split must be between 1 and 64")
        elif key == "min-split-size":
            m = _SIZE.match(value)
            if not m or int(m.group(1)) * {"": 1, "k": 1024, "m": 1048576}[m.group(2).lower()] < 1048576:
                raise OptionError("min-split-size must be at least 1M")
        elif key == "file-allocation":
            if value not in ("none", "prealloc", "trunc", "falloc"):
                raise OptionError("file-allocation must be none, prealloc, trunc or falloc")
        elif key == "continue":
            value = "true" if value.lower() in ("1", "true", "yes", "on") else "false"
        elif key == "max-tries":
            if not value.isdigit() or int(value) > 100:
                raise OptionError("max-tries must be between 0 and 100")
        elif key == "user-agent":
            if len(value) > 300 or "\n" in value:
                raise OptionError("invalid user-agent")
        else:
            raise OptionError("option %s cannot be a default" % key)
        out[key] = value
    return out


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
    options = {**(server.default_options or {}), **clean_options(options)}  # what was chosen when adding wins
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


# ---------------------------------------------------------------- "why is it slow?"
def human_speed(n):
    n = float(n)
    for unit in ("B/s", "KiB/s", "MiB/s", "GiB/s"):
        if n < 1024 or unit == "GiB/s":
            return ("%d %s" % (n, unit)) if unit == "B/s" else ("%.1f %s" % (n, unit))
        n /= 1024


def _num(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def analyze(opts, effective, panel, per_download):
    """Pure function: turn aria2's options into plain language hints.
    opts: aria2 global options, effective: options a new download really gets (aria2 + panel defaults),
    panel: {managed, rule, scheduler...}, per_download: [{gid, name, limit}]."""
    hints = []

    def add(level, code, message):
        hints.append({"level": level, "code": code, "message": message})

    for direction, key in (("download", "max-overall-download-limit"), ("upload", "max-overall-upload-limit")):
        limit = _num(opts.get(key))
        if limit <= 0 or (direction == "upload"):
            continue
        if panel["managed"]:
            where = "the speed rule “%s”" % panel["rule"] if panel["rule"] else "the default download limit on the Scheduler page"
            add("warning", "panel_limit", "All downloads together are capped at %s by %s. Change or remove it there (0 = unlimited)." % (human_speed(limit), where))
        else:
            add("warning", "aria2_limit", "aria2 is capped at %s for all downloads together (max-overall-download-limit). It comes from aria2.conf or an earlier RPC call. Set it to 0 below to remove it." % human_speed(limit))
    for item in per_download:
        add("warning", "download_limit", "“%s” has its own speed cap of %s (max-download-limit). Remove it in the details of that download." % (item["name"], human_speed(item["limit"])))
    if _num(opts.get("lowest-speed-limit")) > 0:
        add("info", "lowest_speed", "aria2 drops connections slower than %s (lowest-speed-limit)." % human_speed(_num(opts.get("lowest-speed-limit"))))
    if _num(effective.get("max-connection-per-server")) <= 1:
        add("info", "one_connection", "Each download uses 1 connection per server, exactly like wget. Servers that allow it are much faster with up to 16: use “Use recommended” below.")
    if effective.get("file-allocation") == "prealloc":
        add("info", "prealloc", "File preallocation (aria2's default) reserves the whole file before the transfer starts. That delays big downloads and is slow on network or Windows-mounted disks. “none” starts at once.")
    directory = str(opts.get("dir", ""))
    if directory.startswith("/mnt/"):
        add("warning", "slow_disk", "The download directory %s is a Windows drive mounted into WSL. Writes through it are often far slower than the network, compare with wget writing to the same folder." % directory)
    if not any(h["level"] == "warning" for h in hints):
        add("ok", "no_limit", "No speed limit was found in aria2 or in the panel.")
    return hints


def diagnostics(server):
    client = server.client()
    results = client.multicall([("getGlobalOption", []), ("tellActive", [["gid", "status", "seeder"]])])
    for r in results:
        if isinstance(r, Aria2Error):
            raise r
    opts, active = results
    gids = [d["gid"] for d in active if str(d.get("seeder", "false")).lower() != "true"][:10]
    limited = []
    if gids:
        names = resolve_names(client, server.pk, gids)
        for gid, r in zip(gids, client.multicall([("getOption", [g]) for g in gids])):
            if not isinstance(r, Aria2Error) and _num(r.get("max-download-limit")) > 0:
                limited.append({"gid": gid, "name": names[gid], "limit": _num(r["max-download-limit"])})
    from django.utils import timezone

    rules = list(server.speed_rules.filter(enabled=True))
    local = timezone.localtime()
    rule = next((r for r in rules if r.matches(local)), None)
    managed = bool(rules) or server.default_download_limit != "0" or server.default_upload_limit != "0"
    keys = ("max-connection-per-server", "split", "min-split-size", "file-allocation", "continue")
    effective = {k: opts.get(k) for k in keys}
    effective.update(server.default_options or {})
    panel = {"managed": managed, "rule": rule.name if rule else None}
    return {
        "limits": {
            "download": {"bytes": _num(opts.get("max-overall-download-limit")), "human": human_speed(_num(opts.get("max-overall-download-limit"))) if _num(opts.get("max-overall-download-limit")) else "unlimited"},
            "upload": {"bytes": _num(opts.get("max-overall-upload-limit")), "human": human_speed(_num(opts.get("max-overall-upload-limit"))) if _num(opts.get("max-overall-upload-limit")) else "unlimited"},
            "managed_by_panel": managed, "active_rule": panel["rule"],
        },
        "aria2": {k: opts.get(k) for k in keys + ("max-concurrent-downloads", "dir", "disk-cache")},
        "effective": effective,
        "defaults": server.default_options or {},
        "recommended": RECOMMENDED_DEFAULTS,
        "per_download_limits": limited,
        "hints": analyze(opts, effective, panel, limited),
    }
