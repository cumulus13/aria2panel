# Aria2 Panel API v1

JSON over HTTP, live updates over Server-Sent Events. Machine readable description:
`GET /api/v1/openapi.json` (OpenAPI 3.0, feed it to openapi-generator for a Kotlin/Retrofit client).

## Authentication

1. **Token from the app**: `POST /api/v1/auth/login/` `{"username","password","device_name","expires_days"}` → `201 {"token": "a2p_…"}`.
   Wrong passwords are throttled (5 per 5 minutes per IP + user, then `429`).
2. **Token from the web UI**: user menu → *API tokens* (optionally read-only and/or expiring).

Send `Authorization: Bearer a2p_…` on every request. No CSRF token, no cookies. Tokens are stored hashed,
can be revoked (`POST /api/v1/auth/logout/`, `POST /api/v1/tokens/<id>/revoke/`) and have the rights of the
account (`is_staff` is needed for servers/settings/scheduler configuration/speed rules). A read-only token
can only `GET` (good for widgets). Errors: `{"error": "…"}` with 400/401/403/404/429/502 (502 = aria2 unreachable).

## Endpoints

| | |
|---|---|
| `GET /api/v1/` | version, features, server time (no auth) |
| `POST /api/v1/auth/login/` · `POST …/logout/` · `GET …/me/` | session-less sign in/out |
| `GET/POST /api/v1/tokens/` · `POST …/tokens/<id>/revoke/` | manage your tokens |
| `GET /api/v1/servers/` | aria2 servers |
| `GET /api/v1/servers/<id>/snapshot/` | everything: downloads, queued items, scheduler state |
| `GET /api/v1/servers/<id>/progress/` | only the running downloads + global speed (cheap, for polling/widgets) |
| `GET /api/v1/servers/<id>/events/` | **SSE** stream, see below |
| `GET /api/v1/servers/<id>/events/history/?after=<id>&limit=` | persisted events, for catching up without SSE |
| `GET /api/v1/servers/<id>/history/?seconds=` | recorded global speed samples |
| `POST /api/v1/servers/<id>/add/` | multipart: `uris` (one per line), `files`, `options` (JSON), `not_before` |
| `POST /api/v1/servers/<id>/action/` | `{"action": pause\|unpause\|forcePause\|remove\|forceRemove\|retry\|pauseAll\|unpauseAll\|purge, "gid"/"gids"}` |
| `GET /api/v1/servers/<id>/download/<gid>/` | details: files, options |
| `GET/POST …/global/` · `GET/POST …/scheduler/` · `POST …/queue/` · `POST …/rules/` | limits, scheduler, queue, speed rules |

```bash
TOKEN=$(curl -s -X POST $URL/api/v1/auth/login/ -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"…","device_name":"my phone"}' | jq -r .token)
curl -H "Authorization: Bearer $TOKEN" $URL/api/v1/servers/1/progress/
curl -H "Authorization: Bearer $TOKEN" -F 'uris=https://example.com/big.iso' $URL/api/v1/servers/1/add/
curl -N -H "Authorization: Bearer $TOKEN" $URL/api/v1/servers/1/events/        # live stream
```

## Event stream (SSE)

`GET /api/v1/servers/<id>/events/` (`text/event-stream`). Frames:

| event | when | `id:` |
|---|---|---|
| `snapshot` | right after connecting, and after aria2 comes back | – |
| `progress` | every `sse.interval_seconds` (default 1 s) while something runs; when idle only on change | – |
| `download.added` `download.completed` `download.error` `download.removed` | something happened, payload `{id, kind, gid, name, total, errorCode, errorMessage, ts}` | **event id** |
| `connection` | `{"aria2": "down"\|"up", "error"}` when the panel loses/regains aria2 | – |
| `bye` | the stream reached `sse.max_seconds` (30 min), reconnect | – |

`: ping` comment lines arrive every 15 s on an idle stream (detect dead connections with a ~40 s read timeout).

**Not losing notifications**: the `download.*` events are written to the database by the scheduler daemon and
carry an increasing `id`. Remember the last id you processed and reconnect with `Last-Event-ID: <id>` (or
`?after=<id>`): everything missed while offline is replayed before the snapshot. Without the header only new
events are sent. Events are kept `events.keep_days` (30). Reconnect with exponential backoff (1 s → 60 s);
the server suggests `retry: 3000`. Behind nginx use `proxy_buffering off; proxy_read_timeout 1h;`.

`progress` payload (one item per running download): `gid, name, status, total, completed, progress (%), downloadSpeed,
uploadSpeed, eta (s|null), connections, seeding`, plus `global` speeds/counters and `scheduler {enabled, max, running, queued}`.

## Android app without Firebase

**Foreground service + SSE** is the design that gives an IDM+ like experience with no push provider:

* One foreground service owns the SSE connection (OkHttp `EventSource`, `readTimeout` ≈ 40 s because of the 15 s pings).
  Its ongoing notification is the summary ("3 downloading · ↓ 4.2 MB/s").
* For each item of a `progress` frame post/update a notification with `setProgress(total, completed, false)`
  (the same notification id per `gid`, `setOnlyAlertOnce(true)`, low-importance channel) and actions
  *Pause / Resume / Cancel* that call `POST …/action/`. Update at most ~1/s.
* `download.completed` / `download.error` → a separate, high-importance notification. Persist the last
  event `id` and send it as `Last-Event-ID` after every reconnect: completions that happened while the phone was
  offline or the service was dead are replayed.
* Stop the service when nothing is running and the user left the app if you want zero battery use, and rely on
  the push option below to wake up for completions.

Platform rules to plan for (check the current Android docs, they keep changing):

* Android 13+: runtime permission `POST_NOTIFICATIONS`. Android 14+: declare a foreground service type and the
  matching permission. `dataSync` services are time limited on Android 15+ (about 6 hours in 24 hours, and not
  startable from a boot receiver); `specialUse` has no such limit but needs a justification when publishing to Google Play.
* Doze and OEM battery savers (Xiaomi, Oppo, Samsung …) can still freeze or kill the app: ask for the battery
  optimization exemption (`ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS`) and show a short explanation. This is the
  part you can only verify on real devices.

**Do you need Firebase?** Not for live progress. FCM is only needed to wake an app that Android has stopped.
Without Google services use a push channel: set `notify.ntfy_url` (Settings page) to a topic on your own
[ntfy](https://ntfy.sh) server and install the ntfy app (UnifiedPush, no Firebase when self-hosted) or have your app
register as a UnifiedPush distributor/receiver. The panel posts `completed`/`error` (configurable in `notify.events`)
to ntfy and/or `notify.webhook_url` (JSON `{id, kind, gid, name, total, errorCode, errorMessage, ts, server}`).
Use that webhook if you ever want to forward events to FCM from your own small relay.

Kotlin sketch (not compiled here):

```kotlin
val client = OkHttpClient.Builder().readTimeout(40, TimeUnit.SECONDS).build()
val req = Request.Builder().url("$base/api/v1/servers/$id/events/")
    .header("Authorization", "Bearer $token")
    .apply { lastEventId?.let { header("Last-Event-ID", it) } }.build()
val source = EventSources.createFactory(client).newEventSource(req, object : EventSourceListener() {
    override fun onEvent(es: EventSource, id: String?, type: String?, data: String) {
        when {
            type == "progress" -> notifier.updateProgress(Json.decodeFromString<Progress>(data))
            type?.startsWith("download.") == true -> { notifier.show(type, data); id?.let(store::saveLastEventId) }
            type == "bye" -> reconnect()
        }
    }
    override fun onFailure(es: EventSource, t: Throwable?, r: Response?) = scheduleReconnectWithBackoff()
})
```

## 👤 Author
        
[Hadi Cahyadi](mailto:cumulus13@gmail.com)
    

[![Buy Me a Coffee](https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png)](https://www.buymeacoffee.com/cumulus13)

[![Donate via Ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/cumulus13)
 
[Support me on Patreon](https://www.patreon.com/cumulus13)