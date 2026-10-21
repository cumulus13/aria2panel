import json
import logging
import re
from datetime import datetime, time, timedelta
from math import ceil
from functools import wraps

from django.contrib import messages
from django.contrib.auth import authenticate
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.exceptions import ValidationError
from django.db import connection
from django.core.cache import caches
from django.http import Http404, JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.middleware.csrf import CsrfViewMiddleware
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from . import conf, openapi, scheduler, services, sse
from .aria2 import Aria2Client, Aria2ConnectionError, Aria2Error
from .forms import ServerForm, SettingsForm
from .models import SPEED_VALIDATOR, WEEKDAYS, Aria2Server, ApiToken, DownloadEvent, QueueItem, SpeedRule, SpeedSample

log = logging.getLogger("core.views")
staff_required = user_passes_test(lambda u: u.is_active and u.is_staff, login_url="login")
MAX_UPLOAD = 10 * 1024 * 1024


# ---------------------------------------------------------------- helpers
def pick_server(request):
    servers = list(Aria2Server.objects.filter(enabled=True))
    if not servers:
        return None, servers
    wanted = request.GET.get("server") or request.session.get("server_id")
    chosen = next((s for s in servers if str(s.pk) == str(wanted)), None) or servers[0]
    request.session["server_id"] = chosen.pk
    return chosen, servers


_csrf_check = CsrfViewMiddleware(lambda request: None)
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")
API_VERSION = 1


def _unauthorized(message):
    return JsonResponse({"error": message}, status=401, headers={"WWW-Authenticate": 'Bearer realm="aria2panel"'})


def api(methods=("GET",), staff=False, auth=True):
    """JSON endpoint. Authenticated by `Authorization: Bearer <token>` (no CSRF needed) or by
    the browser session (CSRF token required for unsafe methods)."""

    def decorator(view):
        @csrf_exempt
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if request.method not in methods:
                return JsonResponse({"error": "method not allowed"}, status=405)
            request.api_token = None
            if auth:
                header = request.META.get("HTTP_AUTHORIZATION", "")
                if header[:7].lower() == "bearer ":
                    token = ApiToken.authenticate(header[7:].strip())
                    if token is None:
                        return _unauthorized("invalid or expired token")
                    request.user, request.api_token = token.user, token
                    if token.read_only and request.method not in SAFE_METHODS:
                        return JsonResponse({"error": "this token is read-only"}, status=403)
                elif not request.user.is_authenticated:
                    return _unauthorized("authentication required")
                elif request.method not in SAFE_METHODS and _csrf_check.process_view(request, None, (), {}) is not None:
                    return JsonResponse({"error": "CSRF verification failed"}, status=403)
                if staff and not request.user.is_staff:
                    return JsonResponse({"error": "administrator rights required"}, status=403)
            try:
                return view(request, *args, **kwargs)
            except Http404:
                return JsonResponse({"error": "not found"}, status=404)
            except Aria2ConnectionError as exc:
                return JsonResponse({"error": "Cannot reach aria2: %s" % exc.message}, status=502)
            except Aria2Error as exc:
                return JsonResponse({"error": exc.message}, status=400)
            except (ValidationError, ValueError, conf.ConfigError) as exc:
                message = "; ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc)
                return JsonResponse({"error": message}, status=400)
            except Exception:
                log.exception("API error in %s", view.__name__)
                return JsonResponse({"error": "internal error"}, status=500)

        return wrapper

    return decorator


def body(request):
    try:
        data = json.loads(request.body or b"{}")
    except ValueError:
        raise ValueError("invalid JSON body")
    if not isinstance(data, dict):
        raise ValueError("JSON object expected")
    return data


def parse_dt(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("invalid date/time")
    return timezone.make_aware(dt) if timezone.is_naive(dt) else dt


def server_or_404(server_id):
    return get_object_or_404(Aria2Server, pk=server_id, enabled=True)


# ---------------------------------------------------------------- pages
@require_GET
def healthz(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        return JsonResponse({"status": "ok"})
    except Exception:
        return JsonResponse({"status": "database unavailable"}, status=503)


@login_required
def dashboard(request):
    server, servers = pick_server(request)
    if server is None:
        if request.user.is_staff:
            messages.info(request, "Add your first aria2 server to get started.")
            return redirect("server_new")
        return render(request, "core/empty.html", status=200)
    return render(request, "core/dashboard.html", {
        "server": server, "servers": servers, "poll": conf.get("ui.poll_interval_ms"), "nav": "dashboard",
    })


@staff_required
def scheduler_page(request):
    server, servers = pick_server(request)
    if server is None:
        return redirect("server_new")
    return render(request, "core/scheduler.html", {
        "server": server, "servers": servers, "weekdays": list(enumerate(WEEKDAYS)), "nav": "scheduler",
        "time_zone": timezone.get_current_timezone_name(),
    })


@staff_required
def server_list(request):
    return render(request, "core/server_list.html", {"servers": Aria2Server.objects.all(), "nav": "servers"})


@staff_required
@require_http_methods(["GET", "POST"])
def server_edit(request, pk=None):
    instance = get_object_or_404(Aria2Server, pk=pk) if pk else None
    form = ServerForm(request.POST or None, instance=instance)
    if request.method == "POST" and form.is_valid():
        server = form.save()
        messages.success(request, "Server “%s” saved." % server.name)
        return redirect("server_list")
    return render(request, "core/server_form.html", {"form": form, "instance": instance, "nav": "servers"})


@staff_required
@require_POST
def server_delete(request, pk):
    server = get_object_or_404(Aria2Server, pk=pk)
    name = server.name
    server.delete()
    messages.success(request, "Server “%s” deleted." % name)
    return redirect("server_list")


@staff_required
@require_http_methods(["GET", "POST"])
def settings_page(request):
    form = SettingsForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Settings saved.")
        return redirect("settings")
    return render(request, "core/settings.html", {"form": form, "nav": "settings"})


# ---------------------------------------------------------------- api: monitoring & actions
@api()
def api_snapshot(request, server_id):
    return JsonResponse(services.snapshot(server_or_404(server_id)))


@api()
def api_history(request, server_id):
    server = server_or_404(server_id)
    seconds = max(10, min(int(request.GET.get("seconds", 300)), 24 * 3600))
    now = timezone.now()
    rows = list(
        SpeedSample.objects.filter(server=server, ts__gte=now - timedelta(seconds=seconds))
        .order_by("ts").values_list("ts", "download", "upload")
    )
    if len(rows) > 600:
        rows = rows[:: ceil(len(rows) / 600)]
    return JsonResponse({
        "now": int(now.timestamp() * 1000),  # lets the browser correct for clock differences
        "interval": conf.get("history.interval_seconds"),
        "samples": [[int(ts.timestamp() * 1000), d, u] for ts, d, u in rows],
    })


@api(methods=("POST",))
def api_add(request, server_id):
    server = server_or_404(server_id)
    options = json.loads(request.POST.get("options") or "{}")
    torrents, metalinks = [], []
    for f in request.FILES.getlist("files"):
        if f.size > MAX_UPLOAD:
            raise ValueError("%s is larger than %d MB" % (f.name, MAX_UPLOAD // 1048576))
        lower = f.name.lower()
        if lower.endswith(".torrent"):
            torrents.append((f.name, f.read()))
        elif lower.endswith((".meta4", ".metalink")):
            metalinks.append((f.name, f.read()))
        else:
            raise ValueError("%s: only .torrent, .meta4 and .metalink files are supported" % f.name)
    result = services.add_downloads(
        server,
        uri_lines=services.parse_uri_lines(request.POST.get("uris")),
        torrents=torrents, metalinks=metalinks, options=options,
        not_before=parse_dt(request.POST.get("not_before")),
    )
    return JsonResponse({"dispatched": result.dispatched, "queued": result.queued, "failed": result.failed, "errors": result.errors})


def _smart_remove(client, gid, force):
    try:
        status = client.tell_status(gid, ["status"])["status"]
    except Aria2Error:
        status = None
    if status in ("active", "waiting", "paused"):
        (client.force_remove if force else client.remove)(gid)
    try:
        client.remove_result(gid)
    except Aria2Error:
        pass


@api(methods=("POST",))
def api_action(request, server_id):
    server = server_or_404(server_id)
    data = body(request)
    action = data.get("action")
    client = server.client()
    if action in ("pauseAll", "unpauseAll", "purge"):
        {"pauseAll": client.pause_all, "unpauseAll": client.unpause_all, "purge": client.purge}[action]()
        return JsonResponse({"ok": 1, "errors": []})
    gids = data.get("gids") or ([data["gid"]] if data.get("gid") else [])
    if not gids:
        raise ValueError("no download selected")
    done, errors = 0, []
    for gid in gids[:500]:
        gid = str(gid)
        try:
            if gid.startswith("q:"):  # queued, not yet known to aria2
                op = {"remove": "remove", "forceRemove": "remove", "start": "start"}.get(action)
                if op is None:
                    raise ValueError("%s is not available for queued downloads" % action)
                services.queue_op(server, op, int(gid[2:]))
            elif action in ("pause", "forcePause", "unpause"):
                {"pause": client.pause, "forcePause": client.force_pause, "unpause": client.unpause}[action](gid)
            elif action in ("remove", "forceRemove"):
                _smart_remove(client, gid, action == "forceRemove")
            elif action == "removeResult":
                client.remove_result(gid)
            elif action == "retry":
                services.retry(server, gid)
            else:
                raise ValueError("unknown action")
            done += 1
        except (Aria2Error, ValueError) as exc:
            errors.append("%s: %s" % (gid, getattr(exc, "message", None) or str(exc)))
    return JsonResponse({"ok": done, "errors": errors})


@api()
def api_detail(request, server_id, gid):
    if not re.match(r"^[0-9a-fA-F]{16}$", gid):
        raise ValueError("invalid gid")
    return JsonResponse(services.detail(server_or_404(server_id), gid))


@api(methods=("GET", "POST"))
def api_global_options(request, server_id):
    server = server_or_404(server_id)
    client = server.client()
    names = ("max-overall-download-limit", "max-overall-upload-limit", "max-concurrent-downloads")
    if request.method == "POST":
        if not request.user.is_staff:
            return JsonResponse({"error": "administrator rights required"}, status=403)
        data = body(request)
        options = {}
        for name in names:
            if str(data.get(name, "")) != "":
                value = str(data[name]).strip()
                if name == "max-concurrent-downloads":
                    if not value.isdigit() or int(value) < 1:
                        raise ValueError("max-concurrent-downloads must be a positive number")
                    if server.scheduler_enabled and server.enforce_in_aria2:
                        raise ValueError("max-concurrent-downloads is managed by the scheduler, change it on the Scheduler page")
                else:
                    SPEED_VALIDATOR(value)
                options[name] = value
        if options:
            client.change_global_option(services.clean_options(options))
    current = client.get_global_option()
    return JsonResponse({n: current.get(n, "") for n in names})


@api(methods=("POST",), staff=True)
def api_test_connection(request):
    data = body(request)
    server = None
    if data.get("server_id"):
        server = get_object_or_404(Aria2Server, pk=data["server_id"])
    secret = data.get("secret") or (server.secret if server else "")
    url = data.get("rpc_url") or (server.rpc_url if server else "")
    if not re.match(r"^https?://", url):
        raise ValueError("invalid RPC URL")
    client = Aria2Client(url, secret, timeout=conf.get("rpc.timeout_seconds"), verify=bool(data.get("verify_tls", True)))
    version = client.version()
    return JsonResponse({"version": version.get("version"), "features": version.get("enabledFeatures", [])})


# ---------------------------------------------------------------- api: scheduler
def _item_dict(i):
    return {
        "id": i.pk, "name": i.name, "kind": i.kind, "status": i.status, "position": i.position,
        "not_before": i.not_before.isoformat() if i.not_before else None, "error": i.error,
        "dir": (i.options or {}).get("dir", ""), "created_at": i.created_at.isoformat(),
        "dispatched_at": i.dispatched_at.isoformat() if i.dispatched_at else None,
    }


def _rule_dict(r):
    return {
        "id": r.pk, "name": r.name, "days": r.days, "start_time": r.start_time.strftime("%H:%M"),
        "end_time": r.end_time.strftime("%H:%M"), "download_limit": r.download_limit,
        "upload_limit": r.upload_limit, "enabled": r.enabled,
    }


@api(methods=("GET", "POST"))
def api_scheduler(request, server_id):
    server = server_or_404(server_id)
    if request.method == "POST":
        if not request.user.is_staff:
            return JsonResponse({"error": "administrator rights required"}, status=403)
        data = body(request)
        if "enabled" in data:
            server.scheduler_enabled = bool(data["enabled"])
        if "max_downloads" in data:
            server.max_downloads = int(data["max_downloads"])
        if "enforce_in_aria2" in data:
            server.enforce_in_aria2 = bool(data["enforce_in_aria2"])
        for key in ("default_download_limit", "default_upload_limit"):
            if key in data:
                setattr(server, key, str(data[key]).strip() or "0")
        server.full_clean(exclude=["name", "rpc_url"])
        server.save()
        scheduler.tick(server)
        server.refresh_from_db()
    queue = server.queue.filter(status=QueueItem.Status.QUEUED).order_by("position", "id")[:1000]
    recent = server.queue.exclude(status=QueueItem.Status.QUEUED).order_by("-id")[:15]
    running, error = None, None
    try:
        running = scheduler.running_count(server.client())
    except Aria2Error as exc:
        error = exc.message
    now = timezone.localtime()
    return JsonResponse({
        "config": {
            "enabled": server.scheduler_enabled, "max_downloads": server.max_downloads, "enforce_in_aria2": server.enforce_in_aria2,
            "default_download_limit": server.default_download_limit, "default_upload_limit": server.default_upload_limit,
        },
        "running": running, "aria2_error": error,
        "queue": [_item_dict(i) for i in queue], "recent": [_item_dict(i) for i in recent],
        "rules": [_rule_dict(r) for r in server.speed_rules.all()],
        "now": now.strftime("%a %H:%M"), "time_zone": timezone.get_current_timezone_name(),
        "can_edit": request.user.is_staff,
    })


@api(methods=("POST",))
def api_queue(request, server_id):
    server = server_or_404(server_id)
    data = body(request)
    op = data.get("op")
    if op not in ("up", "down", "top", "bottom", "remove", "start", "clear", "schedule"):
        raise ValueError("unknown operation")
    if op == "clear" and not request.user.is_staff:
        return JsonResponse({"error": "administrator rights required"}, status=403)
    count = services.queue_op(server, op, data.get("id"), parse_dt(data.get("not_before")))
    return JsonResponse({"ok": count})


def _parse_time(value):
    try:
        h, m = str(value).split(":")[:2]
        return time(int(h), int(m))
    except (ValueError, TypeError):
        raise ValueError("invalid time, use HH:MM")


@api(methods=("POST",), staff=True)
def api_rules(request, server_id):
    server = server_or_404(server_id)
    data = body(request)
    if data.get("op") == "delete":
        SpeedRule.objects.filter(server=server, pk=data.get("id")).delete()
        return JsonResponse({"ok": 1})
    days = sorted({int(d) for d in data.get("days", [])})
    if not days or any(d < 0 or d > 6 for d in days):
        raise ValueError("pick at least one day")
    rule = SpeedRule.objects.filter(server=server, pk=data.get("id")).first() if data.get("id") else SpeedRule(server=server)
    if rule is None:
        raise Http404
    rule.name = str(data.get("name", "")).strip() or "Rule"
    rule.days = days
    rule.start_time = _parse_time(data.get("start_time"))
    rule.end_time = _parse_time(data.get("end_time"))
    rule.download_limit = str(data.get("download_limit", "0")).strip() or "0"
    rule.upload_limit = str(data.get("upload_limit", "0")).strip() or "0"
    rule.enabled = bool(data.get("enabled", True))
    rule.full_clean(exclude=["server"])
    rule.save()
    return JsonResponse({"ok": 1, "id": rule.pk})


# ---------------------------------------------------------------- api v1: meta, auth, tokens
@api(auth=False)
def api_openapi(request):
    return JsonResponse(openapi.build_spec())


@api(auth=False)
def api_root(request):
    return JsonResponse({
        "name": conf.get("app.site_name"), "api_version": API_VERSION,
        "server_time": int(timezone.now().timestamp() * 1000),
        "features": ["token-auth", "sse", "progress", "event-history", "scheduler", "speed-history", "push-ntfy", "webhook"],
    })


def _user_dict(user):
    return {"id": user.pk, "username": user.get_username(), "is_staff": user.is_staff}


def _token_dict(t):
    return {
        "id": t.pk, "name": t.name, "prefix": t.prefix, "read_only": t.read_only,
        "created_at": t.created_at.isoformat(), "last_used_at": t.last_used_at.isoformat() if t.last_used_at else None,
        "expires_at": t.expires_at.isoformat() if t.expires_at else None,
    }


LOGIN_ATTEMPTS, LOGIN_WINDOW = 5, 300


@api(methods=("POST",), auth=False)
def api_login(request):
    data = body(request)
    username, password = str(data.get("username", "")).strip(), str(data.get("password", ""))
    key = "login:%s:%s" % (request.META.get("REMOTE_ADDR", ""), username.lower())
    cache = caches["shared"]
    failures = cache.get(key, 0)
    if failures >= LOGIN_ATTEMPTS:
        return JsonResponse({"error": "too many attempts, try again later"}, status=429, headers={"Retry-After": str(LOGIN_WINDOW)})
    user = authenticate(request, username=username, password=password)
    if user is None or not user.is_active:
        cache.set(key, failures + 1, LOGIN_WINDOW)
        return JsonResponse({"error": "invalid username or password"}, status=401)
    cache.delete(key)
    days = int(data.get("expires_days") or 0)
    token, plain = ApiToken.issue(
        user, str(data.get("device_name") or "mobile app"),
        expires_at=timezone.now() + timedelta(days=days) if days > 0 else None,
    )
    return JsonResponse({"token": plain, "token_type": "Bearer", "user": _user_dict(user), "token_info": _token_dict(token)}, status=201)


@api()
def api_me(request):
    return JsonResponse({"user": _user_dict(request.user), "token": _token_dict(request.api_token) if request.api_token else None})


@api(methods=("POST",))
def api_logout(request):
    if request.api_token is None:
        raise ValueError("not signed in with a token")
    request.api_token.revoked_at = timezone.now()
    request.api_token.save(update_fields=["revoked_at"])
    return JsonResponse({"ok": 1})


@api(methods=("GET", "POST"))
def api_tokens(request):
    if request.method == "POST":
        data = body(request)
        days = int(data.get("expires_days") or 0)
        token, plain = ApiToken.issue(
            request.user, str(data.get("name") or "token"), bool(data.get("read_only")),
            timezone.now() + timedelta(days=days) if days > 0 else None,
        )
        return JsonResponse({"token": plain, "token_info": _token_dict(token)}, status=201)
    tokens = request.user.api_tokens.filter(revoked_at__isnull=True)
    return JsonResponse({"tokens": [_token_dict(t) for t in tokens]})


@api(methods=("POST", "DELETE"))
def api_token_revoke(request, token_id):
    token = get_object_or_404(ApiToken, pk=token_id, user=request.user, revoked_at__isnull=True)
    token.revoked_at = timezone.now()
    token.save(update_fields=["revoked_at"])
    return JsonResponse({"ok": 1})


@api()
def api_servers(request):
    return JsonResponse({"servers": [
        {"id": s.pk, "name": s.name, "is_default": s.is_default,
         "scheduler": {"enabled": s.scheduler_enabled, "max_downloads": s.max_downloads}}
        for s in Aria2Server.objects.filter(enabled=True)
    ]})


# ---------------------------------------------------------------- api v1: live data
@api()
def api_progress(request, server_id):
    return JsonResponse(services.progress(server_or_404(server_id)))


@api()
def api_events_history(request, server_id):
    server = server_or_404(server_id)
    limit = max(1, min(int(request.GET.get("limit", 100)), 500))
    qs = DownloadEvent.objects.filter(server=server)
    if request.GET.get("after") not in (None, ""):
        rows = list(qs.filter(id__gt=int(request.GET["after"])).order_by("id")[:limit])
    else:
        rows = list(qs.order_by("-id")[:limit])[::-1]
    return JsonResponse({"events": [e.as_dict() for e in rows], "last_id": rows[-1].pk if rows else sse.latest_event_id(server)})


@api()
def api_events(request, server_id):
    """text/event-stream, see core/sse.py for the protocol."""
    server = server_or_404(server_id)
    raw = request.headers.get("Last-Event-ID") or request.GET.get("after")
    last_id = int(raw) if raw not in (None, "") else sse.latest_event_id(server)  # no id: only new events
    response = StreamingHttpResponse(sse.stream(server, last_id), content_type="text/event-stream; charset=utf-8")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"  # nginx: do not buffer
    return response


# ---------------------------------------------------------------- pages: API tokens
@login_required
@require_http_methods(["GET", "POST"])
def token_page(request):
    if request.method == "POST":
        days = request.POST.get("expires_days") or "0"
        token, plain = ApiToken.issue(
            request.user, request.POST.get("name") or "token", bool(request.POST.get("read_only")),
            timezone.now() + timedelta(days=int(days)) if days.isdigit() and int(days) > 0 else None,
        )
        request.session["new_token"] = plain
        return redirect("tokens")
    return render(request, "core/tokens.html", {
        "tokens": request.user.api_tokens.filter(revoked_at__isnull=True), "new_token": request.session.pop("new_token", None), "nav": "tokens",
    })


@login_required
@require_POST
def token_revoke(request, token_id):
    token = get_object_or_404(ApiToken, pk=token_id, user=request.user, revoked_at__isnull=True)
    token.revoked_at = timezone.now()
    token.save(update_fields=["revoked_at"])
    messages.success(request, "Token “%s” revoked." % token.name)
    return redirect("tokens")


@api()
def api_diagnostics(request, server_id):
    return JsonResponse(services.diagnostics(server_or_404(server_id)))


@api(methods=("GET", "POST"))
def api_defaults(request, server_id):
    server = server_or_404(server_id)
    if request.method == "POST":
        if not request.user.is_staff:
            return JsonResponse({"error": "administrator rights required"}, status=403)
        server.default_options = services.clean_defaults(body(request).get("defaults"))
        server.save(update_fields=["default_options", "updated_at"])
    return JsonResponse({"defaults": server.default_options or {}, "recommended": services.RECOMMENDED_DEFAULTS})


@staff_required
def performance_page(request):
    server, servers = pick_server(request)
    if server is None:
        return redirect("server_new")
    return render(request, "core/performance.html", {"server": server, "servers": servers, "nav": "performance"})
