"""Application configuration stored in the database (core.Setting).

get()/set() are the only API. Values are cached for a few seconds in-process, so a
change made in the UI reaches every worker process within CACHE_TTL seconds."""
from django.core.cache import cache

CACHE_TTL = 5
_MISSING = object()


def _bool(v):
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return bool(v)


def _list(v):
    if isinstance(v, str):
        v = v.splitlines()
    return [str(x).strip() for x in v if str(x).strip()]


# key -> (default, type, help text, options)
DEFINITIONS = {
    "app.site_name": ("Aria2 Panel", str, "Name shown in the navigation bar and the browser title.", {}),
    "ui.default_theme": ("auto", str, "Theme for users that did not pick one themselves.", {"choices": ["auto", "light", "dark"]}),
    "ui.poll_interval_ms": (1500, int, "How often the dashboard refreshes (milliseconds).", {"min": 500, "max": 60000}),
    "ui.stopped_limit": (100, int, "Number of finished/failed downloads shown in the dashboard.", {"min": 0, "max": 1000}),
    "scheduler.tick_seconds": (1.0, float, "Interval of the scheduler daemon (seconds).", {"min": 0.2, "max": 60}),
    "scheduler.keep_days": (7, int, "Days to keep dispatched/failed/cancelled queue records.", {"min": 1, "max": 3650}),
    "history.interval_seconds": (2, int, "How often the scheduler daemon records the transfer speed for the dashboard graph (seconds).", {"min": 1, "max": 60}),
    "history.keep_minutes": (60, int, "How long speed samples are kept (minutes).", {"min": 5, "max": 1440}),
    "events.keep_days": (30, int, "Days to keep the download event log (completed/failed), used to replay missed notifications.", {"min": 1, "max": 3650}),
    "sse.interval_seconds": (1.0, float, "How often live progress is pushed to API clients (seconds).", {"min": 0.5, "max": 10}),
    "sse.max_seconds": (1800, int, "Maximum lifetime of one event stream, clients reconnect and replay (seconds).", {"min": 60, "max": 86400}),
    "notify.ntfy_url": ("", str, "ntfy topic URL (https://ntfy.example.com/my-topic) for completion/error push notifications. Empty = off.", {"url": True}),
    "notify.webhook_url": ("", str, "Webhook that receives a JSON POST for each notified event. Empty = off.", {"url": True}),
    "notify.events": (["completed", "error"], list, "Which events are sent to ntfy / the webhook (added, completed, error, removed), one per line.", {}),
    "rpc.timeout_seconds": (10.0, float, "Timeout for aria2 RPC calls (seconds).", {"min": 1, "max": 120}),
    "security.allowed_download_dirs": ([], list, "If not empty, downloads may only be saved below these directories (one per line).", {}),
}

_COERCE = {str: str, int: int, float: float, bool: _bool, list: _list}


class ConfigError(ValueError):
    pass


def _validate(key, value):
    if key not in DEFINITIONS:
        raise ConfigError("unknown setting: %s" % key)
    default, typ, _help, opts = DEFINITIONS[key]
    try:
        value = _COERCE[typ](value)
    except (TypeError, ValueError):
        raise ConfigError("%s: invalid value" % key)
    if opts.get("url") and value and not (value.startswith("http://") or value.startswith("https://")):
        raise ConfigError("%s: must start with http:// or https://" % key)
    if "choices" in opts and value not in opts["choices"]:
        raise ConfigError("%s: must be one of %s" % (key, ", ".join(opts["choices"])))
    if "min" in opts and value < opts["min"]:
        raise ConfigError("%s: must be at least %s" % (key, opts["min"]))
    if "max" in opts and value > opts["max"]:
        raise ConfigError("%s: must be at most %s" % (key, opts["max"]))
    return value


def get(key):
    if key not in DEFINITIONS:
        raise KeyError(key)
    cache_key = "conf:" + key
    cached = cache.get(cache_key, _MISSING)
    if cached is not _MISSING:
        return cached
    from .models import Setting

    row = Setting.objects.filter(key=key).first()
    value = DEFINITIONS[key][0]
    if row is not None and row.value is not None:
        try:
            value = _validate(key, row.value)
        except ConfigError:
            value = DEFINITIONS[key][0]
    cache.set(cache_key, value, CACHE_TTL)
    return value


def set(key, value):  # noqa: A001 - mirrors get()
    from .models import Setting

    value = _validate(key, value)
    Setting.objects.update_or_create(key=key, defaults={"value": value})
    cache.delete("conf:" + key)
    return value


def reset(key):
    from .models import Setting

    Setting.objects.filter(key=key).delete()
    cache.delete("conf:" + key)


def describe():
    """All settings with their current value, for the settings page."""
    out = []
    for key, (default, typ, help_text, opts) in DEFINITIONS.items():
        out.append(
            {
                "key": key,
                "value": get(key),
                "default": default,
                "type": typ.__name__,
                "help": help_text,
                "options": opts,
            }
        )
    return out
