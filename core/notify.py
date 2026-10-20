"""Optional push notifications for download events: ntfy (works with the ntfy Android app,
self-hosted ntfy needs no Firebase) and/or a generic JSON webhook."""
import logging
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

import requests

from . import conf

log = logging.getLogger("core.notify")
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="notify")
TIMEOUT = 5


def _title_and_tags(event):
    if event.kind == "completed":
        return "Download completed", ["white_check_mark"]
    if event.kind == "error":
        return "Download failed", ["x"]
    if event.kind == "added":
        return "Download added", ["inbox_tray"]
    return "Download removed", ["wastebasket"]


def _send(ntfy_url, webhook_url, server_name, event):
    payload = dict(event.as_dict(), server=server_name)
    if ntfy_url:
        try:
            parts = urlsplit(ntfy_url)
            title, tags = _title_and_tags(event)
            message = event.name or event.gid
            if event.kind == "error" and event.error_message:
                message += "\n" + event.error_message
            requests.post(
                "%s://%s/" % (parts.scheme, parts.netloc),
                json={"topic": parts.path.strip("/"), "title": "%s (%s)" % (title, server_name), "message": message, "tags": tags,
                      "priority": 4 if event.kind == "error" else 3},
                timeout=TIMEOUT,
            ).raise_for_status()
        except Exception as exc:
            log.warning("ntfy notification failed: %s", exc)
    if webhook_url:
        try:
            requests.post(webhook_url, json=payload, timeout=TIMEOUT).raise_for_status()
        except Exception as exc:
            log.warning("webhook notification failed: %s", exc)


def dispatch(server_name, event):
    """Non blocking, never raises."""
    try:
        ntfy_url, webhook_url = conf.get("notify.ntfy_url"), conf.get("notify.webhook_url")
        if (ntfy_url or webhook_url) and event.kind in conf.get("notify.events"):
            _pool.submit(_send, ntfy_url, webhook_url, server_name, event)
    except Exception:
        log.exception("could not queue notification")
