# Aria2 Panel

Django control panel for one or more [aria2](https://aria2.github.io/) servers: live dashboard, download
queue with a **max simultaneous downloads** scheduler, time based **speed rules**, dark mode, and all
configuration stored in **PostgreSQL**.

![Django](https://img.shields.io/badge/django-%23092E20.svg?style=flat&logo=django&logoColor=white) ![PostgreSQL](https://img.shields.io/badge/postgresql-%23316192.svg?style=flat&logo=postgresql&logoColor=white) ![CI Status](https://github.com/cumulus13/aria2panel/actions/workflows/ci.yml/badge.svg)

<p align="center">
  <img src="https://files.catbox.moe/wtuttw.png" width="20%" alt="Powered by Django">
</p>

## Architecture

```
browser ──HTTP/JSON──► web  (Django + gunicorn) ──JSON-RPC──► aria2
                         │                                       ▲
                         ▼                                       │
                    PostgreSQL ◄──── scheduler (manage.py run_scheduler)
```

```mermaid
graph TD
    Browser -->|HTTP/JSON| Web["Web<br>(Django + Gunicorn)"]
    Web -->|JSON-RPC| Aria2[aria2]
    Web --> PostgreSQL[(PostgreSQL)]
    Scheduler[Scheduler<br>manage.py run_scheduler] --> PostgreSQL
    Scheduler --> Aria2
```    

[![Architecture](https://files.catbox.moe/5fdngl.jpg)](https://files.catbox.moe/5fdngl.jpg)

<p align="center">
  <a href="https://files.catbox.moe/e4da8v.png" target="_blank">
    <img src="https://files.catbox.moe/e4da8v.png" width="500" alt="Screenshot 1">
  </a>
  <a href="https://files.catbox.moe/26f5kk.png" target="_blank">
    <img src="https://files.catbox.moe/26f5kk.png" width="500" alt="Screenshot 2">
  </a>
  <a href="https://files.catbox.moe/139p3t.png" target="_blank">
    <img src="https://files.catbox.moe/139p3t.png" width="500" alt="Screenshot 3">
  </a>
  <a href="https://files.catbox.moe/1u27a2.png" target="_blank">
    <img src="https://files.catbox.moe/1u27a2.png" width="500" alt="Screenshot 4">
  </a>
  <a href="https://files.catbox.moe/g73bf9.png" target="_blank">
    <img src="https://files.catbox.moe/g73bf9.png" width="500" alt="Screenshot 5">
  </a>
</p>


* **web** serves the UI and a small JSON API. The dashboard polls one `snapshot` endpoint (a single
  aria2 `system.multicall`), no WebSocket and no broker needed.
* **scheduler** is a plain long-running process, one loop, one tick per second. It counts what runs in
  aria2 and starts queued downloads until `max_downloads` is reached. The queue lives in PostgreSQL, so
  it keeps working when no browser is open and survives restarts (the old browser-side scheduler did not).
  A row lock (`SELECT … FOR UPDATE SKIP LOCKED`) makes several scheduler processes safe.
* No Celery, Redis or RabbitMQ: there are no background jobs besides that loop, aria2 does the transfers.
  Redis + Django channels will be added for server push for multiple simultaneous viewers in the next version.

## Quick start (Docker)

```bash
cp .env.example .env          # edit: DJANGO_SECRET_KEY, DATABASE_URL, admin + aria2 settings
docker compose up -d --build  # web + scheduler
# with a bundled aria2:  docker compose --profile local-aria2 up -d --build
```

Open `http://localhost:8000`, sign in with `DJANGO_SUPERUSER_*`. The first aria2 server is created from
`ARIA2_RPC_URL` / `ARIA2_RPC_SECRET`; more can be added on the **Servers** page.

Without Docker:

```bash
cp .env.example .env          # edit: DJANGO_SECRET_KEY, DATABASE_URL, admin + aria2 settings
python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
python manage.py collectstatic
export DATABASE_URL=postgres://user:pass@host:5432/db DJANGO_SECRET_KEY=... DJANGO_DEBUG=1
python manage.py migrate && python manage.py bootstrap
python manage.py runserver        # terminal 1
python manage.py run_scheduler    # terminal 2
```

## Configuration

Only what is needed to reach the database lives in the environment (`DATABASE_URL`, `DJANGO_SECRET_KEY`,
hosts, TLS flags, see `.env.example`). Everything else is stored in the database:

| Where | What |
|---|---|
| `Settings` page (`core.Setting`) | site name, default theme, refresh interval, scheduler tick, retention, RPC timeout, allowed download directories, event stream and push notification (ntfy / webhook) options |
| `Servers` page (`core.Aria2Server`) | RPC URL, RPC secret (Fernet encrypted), TLS verify, scheduler on/off, max downloads, default speed limits |
| `Scheduler` page | queue, speed rules (`core.QueueItem`, `core.SpeedRule`) |

In code: `from core import conf; conf.get("ui.poll_interval_ms")`, `conf.set(...)`. A new setting is one
entry in `core/conf.py`, the Settings form is generated from it. Values are cached for 5 s per process.

## Scheduler semantics

* Slots in use = aria2 *active* downloads (torrents that only seed do not count) + *waiting* ones that are
  not paused. Paused downloads do not use a slot.
* Anything added above the limit stays queued (FIFO, reorderable) and starts the moment a slot frees up:
  a download finishes, is paused, is removed or fails. A rejected item (bad URI) is marked failed and does
  not block the ones behind it.
* **Enforce inside aria2** (default on) keeps aria2's own `max-concurrent-downloads` at
  `max_downloads + seeders`, so resuming a paused download above the limit waits instead of starting.
  The original value is restored when the scheduler is switched off.
* Items can have a *start at* time, they wait for it and for a free slot.
* **Speed rules**: time windows (days, from–to, may span midnight) with download/upload limits. Outside
  all windows the server's default limits apply. The limits aria2 had before are restored when the last
  rule is removed. If the scheduler daemon restarts while rules are active the "before" value is lost.
* Scheduler off: everything is sent to aria2 immediately, as before.

## Security

* Login required everywhere, `Servers`/`Settings`/scheduler configuration need `is_staff`.
* CSRF on every state changing request (token via `<meta>`, cookie is `HttpOnly`), `SameSite=Lax`.
* RPC secrets are stored encrypted (`FIELD_ENCRYPTION_KEY`, or derived from `DJANGO_SECRET_KEY`).
* User supplied aria2 options are validated: `on-download-*` hooks (command execution on the aria2 host),
  `rpc-*`, `conf-path`, … are rejected. `security.allowed_download_dirs` restricts `dir`.
* Set `DJANGO_BEHIND_PROXY`, `DJANGO_SECURE_COOKIES`, `DJANGO_SSL_REDIRECT`, `DJANGO_HSTS_SECONDS` behind TLS.
* `GET /healthz` (no auth) checks the database, used by the container healthcheck.

## API for apps (v1)

Token (Bearer) authenticated JSON API plus an SSE event stream with replay, OpenAPI at `/api/v1/openapi.json`,
full guide incl. an Android design without Firebase in **[docs/API.md](docs/API.md)**. Create tokens under user menu → *API tokens*.
Completion/error push notifications: set `notify.ntfy_url` (works with self-hosted ntfy, no Firebase) and/or `notify.webhook_url` on the Settings page.
Each open event stream uses one gunicorn thread (`GUNICORN_WORKERS` x `GUNICORN_THREADS`). RPC secrets are encrypted with a key derived from
`DJANGO_SECRET_KEY`; set `FIELD_ENCRYPTION_KEY` so rotating the secret key does not lock you out of them.

## Browser API (same endpoints, session auth + `X-CSRFToken`)

`GET /api/v1/servers/<id>/snapshot/` · `POST …/add/` (multipart: `uris`, `files`, `options`, `not_before`) ·
`POST …/action/` (`pause|unpause|forcePause|remove|forceRemove|retry|pauseAll|unpauseAll|purge`) ·
`GET …/download/<gid>/` · `GET|POST …/global/` · `GET|POST …/scheduler/` · `POST …/queue/`
(`up|down|top|bottom|remove|start|schedule|clear`) · `POST …/rules/` · `POST /api/v1/servers/test/`

## Tests

```bash
export DATABASE_URL=postgres://…   # the role needs CREATEDB, Django creates test_<db>
python manage.py test
```

The suite needs `aria2c` on `PATH`: it starts a real aria2 (with an RPC secret) and a local file server,
and checks the scheduler end to end (limit enforcement, ordering, delayed start, failures, speed rules,
manual resume, restoration of aria2's own settings) plus the API and permissions.

## Operations

* Backup = PostgreSQL dump (`pg_dump`), that contains the queue, servers and settings.
* Several aria2 servers: select one in the navigation bar, each has its own queue, limits and rules.
* Logs go to stdout. `DJANGO_LOG_LEVEL=DEBUG` for more.

## Ideas not built yet

Per-user permissions/quotas, audit log of actions, notifications (webhook/Telegram/e-mail) on
completion/error, download categories → target directories, RSS auto-add, disk space guard that pauses the
queue, Prometheus `/metrics`, WebSocket push through aria2 notifications.

---

## License

[MIT](LICENSE)

## 👤 Author
        
[Hadi Cahyadi](mailto:cumulus13@gmail.com)
    

[![Buy Me a Coffee](https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png)](https://www.buymeacoffee.com/cumulus13)

[![Donate via Ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/cumulus13)
 
[Support me on Patreon](https://www.patreon.com/cumulus13)
