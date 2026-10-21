"""OpenAPI 3.0 description of the API, served at /api/v1/openapi.json (generate a client with
openapi-generator, e.g. for Kotlin/Retrofit). A test keeps it in sync with the URL configuration."""


def ref(name):
    return {"$ref": "#/components/schemas/%s" % name}


def obj(props, required=None, **extra):
    out = {"type": "object", "properties": props}
    if required:
        out["required"] = required
    out.update(extra)
    return out


INT, STR, BOOL, NUM = {"type": "integer"}, {"type": "string"}, {"type": "boolean"}, {"type": "number"}
NULLABLE_STR = {"type": "string", "nullable": True}
NULLABLE_INT = {"type": "integer", "nullable": True}


def arr(items):
    return {"type": "array", "items": items}


SCHEMAS = {
    "Error": obj({"error": STR}, ["error"]),
    "Ok": obj({"ok": INT}),
    "User": obj({"id": INT, "username": STR, "is_staff": BOOL}),
    "TokenInfo": obj({"id": INT, "name": STR, "prefix": STR, "read_only": BOOL, "created_at": STR, "last_used_at": NULLABLE_STR, "expires_at": NULLABLE_STR}),
    "TokenCreated": obj({"token": {"type": "string", "description": "Shown once. Send as `Authorization: Bearer <token>`."}, "token_type": STR, "user": ref("User"), "token_info": ref("TokenInfo")}),
    "Server": obj({"id": INT, "name": STR, "is_default": BOOL, "scheduler": obj({"enabled": BOOL, "max_downloads": INT})}),
    "Global": obj({"downloadSpeed": INT, "uploadSpeed": INT, "numActive": INT, "numWaiting": INT, "numStopped": INT}),
    "SchedulerInfo": obj({"enabled": BOOL, "max": INT, "running": INT, "queued": INT}),
    "Download": obj({
        "gid": {"type": "string", "description": "aria2 gid, or `q:<id>` for a queued item that aria2 does not know yet"},
        "status": {"type": "string", "enum": ["active", "waiting", "paused", "queued", "complete", "error", "removed"]},
        "name": STR, "total": INT, "completed": INT, "progress": NUM, "downloadSpeed": INT, "uploadSpeed": INT,
        "connections": INT, "seeders": INT, "seeding": BOOL, "eta": NULLABLE_INT, "errorCode": NULLABLE_STR, "errorMessage": STR,
        "dir": STR, "torrent": BOOL, "numFiles": INT, "queued": BOOL,
    }),
    "Snapshot": obj({"server": obj({"id": INT, "name": STR}), "global": ref("Global"), "downloads": arr(ref("Download")), "scheduler": ref("SchedulerInfo")}),
    "ProgressItem": obj({"gid": STR, "name": STR, "status": STR, "total": INT, "completed": INT, "progress": NUM, "downloadSpeed": INT, "uploadSpeed": INT, "eta": NULLABLE_INT, "connections": INT, "seeding": BOOL}),
    "Progress": obj({"global": ref("Global"), "active": arr(ref("ProgressItem")), "scheduler": ref("SchedulerInfo")}),
    "Event": obj({"id": INT, "kind": {"type": "string", "enum": ["added", "completed", "error", "removed"]}, "gid": STR, "name": STR, "total": INT, "errorCode": STR, "errorMessage": STR, "ts": {"type": "integer", "description": "epoch milliseconds"}}),
    "EventHistory": obj({"events": arr(ref("Event")), "last_id": INT}),
    "AddResult": obj({"dispatched": INT, "queued": INT, "failed": INT, "errors": arr(STR)}),
    "ActionResult": obj({"ok": INT, "errors": arr(STR)}),
    "History": obj({"now": INT, "interval": INT, "samples": {"type": "array", "items": arr(INT), "description": "[epochMs, downloadBytesPerSec, uploadBytesPerSec]"}}),
    "SchedulerState": obj({"config": obj({"enabled": BOOL, "max_downloads": INT, "enforce_in_aria2": BOOL, "default_download_limit": STR, "default_upload_limit": STR}), "running": NULLABLE_INT, "aria2_error": NULLABLE_STR, "queue": arr(obj({})), "recent": arr(obj({})), "rules": arr(obj({})), "now": STR, "time_zone": STR, "can_edit": BOOL}),
    "GlobalOptions": obj({"max-overall-download-limit": STR, "max-overall-upload-limit": STR, "max-concurrent-downloads": STR}),
}

JSON = "application/json"
SERVER_ID = {"name": "server_id", "in": "path", "required": True, "schema": INT}
GID = {"name": "gid", "in": "path", "required": True, "schema": STR}
TOKEN_ID = {"name": "token_id", "in": "path", "required": True, "schema": INT}


def resp(schema=None, description="OK"):
    out = {"description": description}
    if schema is not None:
        out["content"] = {JSON: {"schema": schema if "$ref" in schema or "type" in schema else ref(schema)}}
    return out


def body(props, required=None):
    return {"required": True, "content": {JSON: {"schema": obj(props, required)}}}


def op(method, summary, tag, ok=None, request=None, params=None, auth=True, admin=False, extra_responses=None):
    operation = {"summary": summary, "tags": [tag], "responses": {"200": resp(ok) if ok else resp(description="OK")}}
    if request:
        operation["requestBody"] = request
    if params:
        operation["parameters"] = params
    if auth:
        operation["security"] = [{"bearerAuth": []}]
    else:
        operation["security"] = []
    if admin:
        operation["description"] = "Requires an administrator (is_staff) account."
    operation["responses"].update({"401": resp("Error", "Missing, invalid or expired token")} if auth else {})
    operation["responses"].update(extra_responses or {})
    return method, operation


def build_spec():
    s = [params for params in [[SERVER_ID]]][0]
    paths = {
        "/api/v1/": dict([op("get", "API version, features and server time", "meta", ok=obj({"name": STR, "api_version": INT, "server_time": INT, "features": arr(STR)}), auth=False)]),
        "/api/v1/openapi.json": dict([op("get", "This document", "meta", ok=obj({}), auth=False)]),
        "/api/v1/auth/login/": dict([op("post", "Exchange username and password for a token", "auth", ok=ref("TokenCreated"), auth=False,
                                      request=body({"username": STR, "password": STR, "device_name": STR, "expires_days": INT}, ["username", "password"]),
                                      extra_responses={"201": resp("TokenCreated", "Token created"), "401": resp("Error", "Wrong credentials"), "429": resp("Error", "Too many attempts")})]),
        "/api/v1/auth/logout/": dict([op("post", "Revoke the token used for this request", "auth", ok=ref("Ok"))]),
        "/api/v1/auth/me/": dict([op("get", "Current user and token", "auth", ok=obj({"user": ref("User"), "token": ref("TokenInfo")}))]),
        "/api/v1/tokens/": {
            **dict([op("get", "List your active tokens", "auth", ok=obj({"tokens": arr(ref("TokenInfo"))}))]),
            **dict([op("post", "Create a token", "auth", ok=ref("TokenCreated"), request=body({"name": STR, "read_only": BOOL, "expires_days": INT}), extra_responses={"201": resp("TokenCreated", "Token created")})]),
        },
        "/api/v1/tokens/{token_id}/revoke/": dict([op("post", "Revoke one of your tokens", "auth", ok=ref("Ok"), params=[TOKEN_ID])]),
        "/api/v1/servers/": dict([op("get", "aria2 servers you can use", "servers", ok=obj({"servers": arr(ref("Server"))}))]),
        "/api/v1/servers/test/": dict([op("post", "Test a connection to an aria2 RPC endpoint", "servers", admin=True, ok=obj({"version": STR, "features": arr(STR)}),
                                         request=body({"server_id": INT, "rpc_url": STR, "secret": STR, "verify_tls": BOOL}))]),
        "/api/v1/servers/{server_id}/snapshot/": dict([op("get", "Everything: all downloads, queued items and scheduler state", "monitor", ok=ref("Snapshot"), params=s)]),
        "/api/v1/servers/{server_id}/progress/": dict([op("get", "Compact state of the running downloads (cheap, good for polling and widgets)", "monitor", ok=ref("Progress"), params=s)]),
        "/api/v1/servers/{server_id}/history/": dict([op("get", "Recorded global speed samples", "monitor", ok=ref("History"), params=s + [{"name": "seconds", "in": "query", "schema": INT}])]),
        "/api/v1/servers/{server_id}/diagnostics/": dict([op("get", "Why downloads may be slow: effective speed limits, aria2 defaults and plain language hints", "settings", ok=obj({"limits": obj({}), "aria2": obj({}), "effective": obj({}), "defaults": obj({}), "recommended": obj({}), "per_download_limits": arr(obj({})), "hints": arr(obj({"level": {"type": "string", "enum": ["ok", "info", "warning"]}, "code": STR, "message": STR}))}), params=s)]),
        "/api/v1/servers/{server_id}/defaults/": {
            **dict([op("get", "aria2 options added to every new download", "settings", ok=obj({"defaults": obj({}), "recommended": obj({})}), params=s)]),
            **dict([op("post", "Set the default download options (split, connections per server, min-split-size, file-allocation, continue, max-tries, user-agent)", "settings", admin=True, ok=obj({"defaults": obj({}), "recommended": obj({})}), params=s, request=body({"defaults": obj({})}, ["defaults"]))]),
        },
        "/api/v1/servers/{server_id}/events/": dict([op("get", "Server-Sent Events stream (progress + download events)", "events", params=s + [
            {"name": "Last-Event-ID", "in": "header", "schema": INT, "description": "Resume: replay persisted events with a greater id. Without it only new events are sent. `?after=<id>` works too."}],
            ok=None, extra_responses={"200": {"description": "text/event-stream. Events: snapshot, progress, download.added, download.completed, download.error, download.removed, connection, bye.", "content": {"text/event-stream": {"schema": STR}}}})]),
        "/api/v1/servers/{server_id}/events/history/": dict([op("get", "Persisted download events (completed/failed/...) for catching up after being offline", "events", ok=ref("EventHistory"),
                                                              params=s + [{"name": "after", "in": "query", "schema": INT}, {"name": "limit", "in": "query", "schema": INT}])]),
        "/api/v1/servers/{server_id}/add/": dict([op("post", "Add links, torrents or metalinks (queued when the scheduler limit is reached)", "downloads", ok=ref("AddResult"), params=s,
                                                    request={"required": True, "content": {"multipart/form-data": {"schema": obj({"uris": {"type": "string", "description": "one download per line, mirrors separated by spaces"}, "files": arr({"type": "string", "format": "binary"}), "options": {"type": "string", "description": "JSON object of aria2 options"}, "not_before": {"type": "string", "description": "ISO 8601 start time"}})}}})]),
        "/api/v1/servers/{server_id}/action/": dict([op("post", "Pause, resume, remove or retry downloads", "downloads", ok=ref("ActionResult"), params=s,
                                                       request=body({"action": {"type": "string", "enum": ["pause", "forcePause", "unpause", "remove", "forceRemove", "removeResult", "retry", "pauseAll", "unpauseAll", "purge"]}, "gid": STR, "gids": arr(STR)}, ["action"]))]),
        "/api/v1/servers/{server_id}/download/{gid}/": dict([op("get", "Details of one download: files and options", "downloads", params=s + [GID], ok=obj({"summary": ref("Download"), "files": arr(obj({})), "options": obj({}), "info": obj({}), "dir": STR}))]),
        "/api/v1/servers/{server_id}/global/": {
            **dict([op("get", "aria2 global limits", "settings", ok=ref("GlobalOptions"), params=s)]),
            **dict([op("post", "Change aria2 global limits", "settings", admin=True, ok=ref("GlobalOptions"), params=s, request=body({"max-overall-download-limit": STR, "max-overall-upload-limit": STR, "max-concurrent-downloads": STR}))]),
        },
        "/api/v1/servers/{server_id}/scheduler/": {
            **dict([op("get", "Scheduler configuration, queue, speed rules and recent activity", "scheduler", ok=ref("SchedulerState"), params=s)]),
            **dict([op("post", "Change the scheduler configuration", "scheduler", admin=True, ok=ref("SchedulerState"), params=s,
                       request=body({"enabled": BOOL, "max_downloads": INT, "enforce_in_aria2": BOOL, "default_download_limit": STR, "default_upload_limit": STR}))]),
        },
        "/api/v1/servers/{server_id}/queue/": dict([op("post", "Reorder, cancel, start or schedule queued downloads", "scheduler", ok=ref("Ok"), params=s,
                                                      request=body({"op": {"type": "string", "enum": ["up", "down", "top", "bottom", "remove", "start", "schedule", "clear"]}, "id": INT, "not_before": NULLABLE_STR}, ["op"]))]),
        "/api/v1/servers/{server_id}/rules/": dict([op("post", "Create, update or delete a speed rule", "scheduler", admin=True, ok=obj({"ok": INT, "id": INT}), params=s,
                                                      request=body({"op": {"type": "string", "enum": ["save", "delete"]}, "id": INT, "name": STR, "days": arr(INT), "start_time": STR, "end_time": STR, "download_limit": STR, "upload_limit": STR, "enabled": BOOL}))]),
    }
    return {
        "openapi": "3.0.3",
        "info": {"title": "Aria2 Panel API", "version": "1.0.0",
                 "description": "Authenticate with `Authorization: Bearer <token>` (create one on the API tokens page or via /api/v1/auth/login/). "
                                "Errors are `{\"error\": \"...\"}` with a 4xx/5xx status. Live updates: /api/v1/servers/{id}/events/ (SSE)."},
        "servers": [{"url": "/"}],
        "components": {"securitySchemes": {"bearerAuth": {"type": "http", "scheme": "bearer"}}, "schemas": SCHEMAS},
        "paths": paths,
    }
