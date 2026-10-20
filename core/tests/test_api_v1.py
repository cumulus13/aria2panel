import json
import threading
import time
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import caches
from django.test import Client, TestCase
from django.urls import get_resolver, reverse, resolve
from django.utils import timezone

from core import conf, events, notify, openapi, scheduler, services, sse
from core.models import ApiToken, DownloadEvent

from .helpers import LiveAria2Case, free_port
from .test_scheduler import wait_until


def post(client, name, payload, args=None, **extra):
    return client.post(reverse(name, args=args or []), data=json.dumps(payload), content_type="application/json", **extra)


def parse_frames(chunks):
    frames = []
    for raw in "".join(chunks).split("\n\n"):
        if not raw.strip():
            continue
        frame = {"comment": raw.startswith(":")}
        for line in raw.split("\n"):
            if line.startswith("event: "):
                frame["event"] = line[7:]
            elif line.startswith("id: "):
                frame["id"] = int(line[4:])
            elif line.startswith("data: "):
                frame["data"] = json.loads(line[6:])
            elif line.startswith("retry: "):
                frame["retry"] = int(line[7:])
        frames.append(frame)
    return frames


class AuthTests(TestCase):
    def setUp(self):
        caches["shared"].clear()
        User = get_user_model()
        self.admin = User.objects.create_user("admin", password="pw", is_staff=True)
        self.bob = User.objects.create_user("bob", password="pw")

    def login(self, username="admin", password="pw", **extra):
        return post(Client(), "api_login", dict({"username": username, "password": password, "device_name": "pixel"}, **extra))

    def test_public_endpoints(self):
        c = Client()
        data = c.get(reverse("api_root")).json()
        self.assertEqual(data["api_version"], 1)
        self.assertIn("sse", data["features"])
        self.assertEqual(c.get(reverse("api_openapi")).json()["openapi"], "3.0.3")

    def test_login_token_lifecycle(self):
        self.assertEqual(self.login(password="wrong").status_code, 401)
        r = self.login()
        self.assertEqual(r.status_code, 201)
        plain = r.json()["token"]
        self.assertTrue(plain.startswith("a2p_"))
        stored = ApiToken.objects.get()
        self.assertNotIn(plain, (stored.token_hash, stored.prefix))  # only a hash is stored
        auth = {"HTTP_AUTHORIZATION": "Bearer " + plain}

        strict = Client(enforce_csrf_checks=True)
        me = strict.get(reverse("api_me"), **auth).json()
        self.assertEqual((me["user"]["username"], me["token"]["name"]), ("admin", "pixel"))
        # token requests never need CSRF
        self.assertEqual(post(strict, "api_tokens", {"name": "second"}, **auth).status_code, 201)

        self.assertEqual(Client().get(reverse("api_me")).status_code, 401)
        bad = Client().get(reverse("api_me"), HTTP_AUTHORIZATION="Bearer a2p_nope")
        self.assertEqual(bad.status_code, 401)
        self.assertIn("Bearer", bad["WWW-Authenticate"])

        self.assertEqual(post(Client(), "api_logout", {}, **auth).status_code, 200)  # revokes this token
        self.assertEqual(Client().get(reverse("api_me"), **auth).status_code, 401)

    def test_login_is_throttled(self):
        for _ in range(5):
            self.assertEqual(self.login(password="x").status_code, 401)
        r = self.login()  # even the right password is refused now
        self.assertEqual(r.status_code, 429)
        self.assertIn("Retry-After", r)
        self.assertEqual(self.login(username="bob").status_code, 201)  # per user name

    def test_expiry_read_only_and_revocation(self):
        expired, plain_expired = ApiToken.issue(self.admin, "old", expires_at=timezone.now() - timedelta(minutes=1))
        self.assertEqual(Client().get(reverse("api_me"), HTTP_AUTHORIZATION="Bearer " + plain_expired).status_code, 401)
        _, plain = ApiToken.issue(self.admin, "viewer", read_only=True)
        auth = {"HTTP_AUTHORIZATION": "Bearer " + plain}
        self.assertEqual(Client().get(reverse("api_servers"), **auth).status_code, 200)
        r = post(Client(), "api_tokens", {"name": "x"}, **auth)
        self.assertEqual((r.status_code, r.json()["error"]), (403, "this token is read-only"))
        self.admin.is_active = False
        self.admin.save()
        self.assertEqual(Client().get(reverse("api_me"), **auth).status_code, 401)  # disabled user

    def test_token_management(self):
        _, mine = ApiToken.issue(self.admin, "mine")
        theirs, _ = ApiToken.issue(self.bob, "bobs")
        auth = {"HTTP_AUTHORIZATION": "Bearer " + mine}
        created = post(Client(), "api_tokens", {"name": "tablet", "expires_days": 30}, **auth).json()
        self.assertTrue(created["token"].startswith("a2p_") and created["token_info"]["expires_at"])
        listing = Client().get(reverse("api_tokens"), **auth).json()["tokens"]
        self.assertEqual({t["name"] for t in listing}, {"mine", "tablet"})
        self.assertTrue(all("token" not in t and "token_hash" not in t for t in listing))
        self.assertEqual(post(Client(), "api_token_revoke", {}, args=[theirs.pk], **auth).status_code, 404)  # not yours
        self.assertEqual(post(Client(), "api_token_revoke", {}, args=[listing[0]["id"]], **auth).status_code, 200)

    def test_token_page(self):
        c = Client()
        c.login(username="bob", password="pw")
        r = c.post(reverse("tokens"), {"name": "from the page", "expires_days": "7"}, follow=True)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "a2p_")  # shown once
        self.assertNotContains(c.get(reverse("tokens")), "id=\"new-token\"")
        token = ApiToken.objects.get(user=self.bob)
        self.assertTrue(token.expires_at)
        c.post(reverse("token_revoke", args=[token.pk]))
        token.refresh_from_db()
        self.assertIsNotNone(token.revoked_at)


class OpenApiTests(TestCase):
    def test_spec_matches_the_url_configuration(self):
        def norm(path):
            return path.replace("<int:server_id>", "{server_id}").replace("<str:gid>", "{gid}").replace("<int:token_id>", "{token_id}")

        routes = {"/" + norm(str(p.pattern)) for p in get_resolver().url_patterns[-1].url_patterns if str(p.pattern).startswith("api/v1/")}
        self.assertEqual(routes, set(openapi.build_spec()["paths"]))
        for path in openapi.build_spec()["paths"]:
            resolve(path.replace("{server_id}", "1").replace("{gid}", "0123456789abcdef").replace("{token_id}", "1"))


class Capture(BaseHTTPRequestHandler):
    seen = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        Capture.seen.append((self.path, json.loads(self.rfile.read(length))))
        self.send_response(200)
        self.end_headers()

    def log_message(self, *a):
        pass


class Inline:
    def submit(self, fn, *args, **kwargs):
        fn(*args, **kwargs)


class V1LiveTests(LiveAria2Case):
    def setUp(self):
        self.clean_aria2()
        caches["shared"].clear()
        scheduler._applied_limits.clear(); scheduler._baseline.clear(); scheduler._concurrency.clear(); scheduler._concurrency_baseline.clear()
        events._state.clear(); services._names.clear(); services._progress_cache.clear()
        User = get_user_model()
        self.admin = User.objects.create_user("admin", password="pw", is_staff=True)
        _, self.plain = ApiToken.issue(self.admin, "test")
        self.auth = {"HTTP_AUTHORIZATION": "Bearer " + self.plain}
        self.server = self.make_server(max_downloads=3, is_default=True)

    def step(self):
        result = scheduler.tick(self.server)
        events.observe(self.server, result)
        return result

    def test_progress_endpoint(self):
        services.add_downloads(self.server, uri_lines=[[self.url(1)]], options={"max-download-limit": "20K"})
        client = Client()
        self.assertTrue(wait_until(lambda: client.get(reverse("api_progress", args=[self.server.pk]), **self.auth).json()["active"], timeout=10))
        data = client.get(reverse("api_progress", args=[self.server.pk]), **self.auth).json()
        item = data["active"][0]
        self.assertEqual((item["name"], item["total"], item["status"]), ("file1.bin", 204800, "active"))
        self.assertEqual(data["scheduler"]["running"], 1)
        self.assertGreater(data["global"]["downloadSpeed"], 0)
        self.assertEqual(Client().get(reverse("api_progress", args=[self.server.pk])).status_code, 401)

    def test_event_tracking_history_and_replay(self):
        self.step()  # first tick only records the baseline
        services.add_downloads(self.server, uri_lines=[[self.url(1)]], options={"max-download-limit": "100K"})
        self.step()
        self.assertEqual(list(DownloadEvent.objects.values_list("kind", "name")), [("added", "file1.bin")])
        self.assertTrue(wait_until(lambda: (self.step(), DownloadEvent.objects.filter(kind="completed").exists())[1], timeout=20))
        done = DownloadEvent.objects.get(kind="completed")
        self.assertEqual((done.name, done.total), ("file1.bin", 204800))

        services.add_downloads(self.server, uri_lines=[["http://127.0.0.1:1/missing.bin"]], options={"max-tries": "1"})
        self.assertTrue(wait_until(lambda: (self.step(), DownloadEvent.objects.filter(kind="error").exists())[1], timeout=20))
        failed = DownloadEvent.objects.get(kind="error")
        self.assertTrue(failed.error_code and failed.error_message)
        self.step()
        self.assertEqual(DownloadEvent.objects.filter(kind="completed").count(), 1)  # nothing is reported twice

        c = Client()
        url = reverse("api_events_history", args=[self.server.pk])
        data = c.get(url, **self.auth).json()
        kinds = [e["kind"] for e in data["events"]]
        self.assertEqual(kinds[0], "added")
        self.assertIn("completed", kinds)
        self.assertEqual(data["last_id"], data["events"][-1]["id"])
        first_id = data["events"][0]["id"]
        after = c.get(url + "?after=%d&limit=1" % first_id, **self.auth).json()
        self.assertEqual(len(after["events"]), 1)
        self.assertGreater(after["events"][0]["id"], first_id)
        self.assertEqual(c.get(url + "?limit=abc", **self.auth).status_code, 400)

    def test_sse_stream_protocol(self):
        a = DownloadEvent.objects.create(server=self.server, kind="completed", gid="g1", name="one.iso", total=5)
        b = DownloadEvent.objects.create(server=self.server, kind="error", gid="g2", name="two.iso", error_code="1", error_message="boom")
        frames = parse_frames(sse.stream(self.server, 0, interval=0.1, max_seconds=1.0, heartbeat=0.25))
        self.assertEqual(frames[0]["retry"], 3000)
        replay = [f for f in frames if f.get("event", "").startswith("download.")]
        self.assertEqual([(f["event"], f["id"]) for f in replay], [("download.completed", a.pk), ("download.error", b.pk)])
        self.assertEqual(replay[1]["data"]["errorMessage"], "boom")
        names = [f.get("event") for f in frames]
        self.assertLess(names.index("download.error"), names.index("snapshot"))  # missed events come first
        self.assertEqual(names[-1], "bye")
        self.assertTrue(any(f["comment"] for f in frames), "idle streams send keep-alive pings")
        # idle: progress is only sent when something changed (about 10 ticks happen in this window;
        # leftovers of earlier tests may still settle in aria2, so allow a couple)
        self.assertLessEqual(names.count("progress"), 3)

        resumed = parse_frames(sse.stream(self.server, a.pk, interval=0.1, max_seconds=0.3))
        self.assertEqual([f["id"] for f in resumed if "id" in f], [b.pk])

    def test_sse_streams_progress_and_new_events(self):
        services.add_downloads(self.server, uri_lines=[[self.url(1)]], options={"max-download-limit": "20K"})
        created = []

        def sleep(seconds):
            if not created:
                created.append(DownloadEvent.objects.create(server=self.server, kind="added", gid="live", name="live.bin"))
            time.sleep(seconds)

        frames = parse_frames(sse.stream(self.server, 0, interval=0.2, max_seconds=1.5, sleep=sleep))
        progress = [f for f in frames if f.get("event") == "progress" and f["data"]["active"]]
        self.assertTrue(progress)
        self.assertEqual(progress[-1]["data"]["active"][0]["name"], "file1.bin")
        live = [f for f in frames if f.get("event") == "download.added"]
        self.assertEqual([f["id"] for f in live], [created[0].pk])

    def test_sse_reports_aria2_going_away(self):
        self.server.rpc_url = "http://127.0.0.1:1/jsonrpc"
        self.server.save()
        frames = parse_frames(sse.stream(self.server, 0, interval=0.1, max_seconds=0.5))
        down = [f for f in frames if f.get("event") == "connection"]
        self.assertEqual([f["data"]["aria2"] for f in down], ["down"])  # reported once, not on every tick
        self.assertEqual(frames[-1]["event"], "bye")

    def test_sse_endpoint(self):
        # The view is called directly: closing a Django test client streaming response fires
        # request_finished, whose close_old_connections would drop the test transaction's connection.
        from django.contrib.auth.models import AnonymousUser
        from django.test import RequestFactory

        from core import views

        a = DownloadEvent.objects.create(server=self.server, kind="completed", gid="g1", name="one.iso")
        b = DownloadEvent.objects.create(server=self.server, kind="error", gid="g2", name="two.iso")
        url = reverse("api_events", args=[self.server.pk])

        def call(**headers):
            request = RequestFactory().get(url, **headers)
            request.user = AnonymousUser()
            return views.api_events(request, self.server.pk)

        self.assertEqual(call().status_code, 401)
        self.assertEqual(call(HTTP_AUTHORIZATION="Bearer a2p_wrong").status_code, 401)

        r = call(**self.auth)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r["Content-Type"].startswith("text/event-stream"))
        self.assertEqual((r["Cache-Control"], r["X-Accel-Buffering"]), ("no-cache", "no"))
        chunks = iter(r.streaming_content)
        self.assertEqual(next(chunks), b"retry: 3000\n\n")
        self.assertIn(b"event: snapshot", next(chunks))  # no Last-Event-ID: nothing is replayed
        r._iterator.close()

        r = call(HTTP_LAST_EVENT_ID=str(a.pk), **self.auth)
        chunks = iter(r.streaming_content)
        next(chunks)
        self.assertIn(("id: %d\nevent: download.error" % b.pk).encode(), next(chunks))
        r._iterator.close()

    def test_push_notifications(self):
        Capture.seen = []
        port = free_port()
        httpd = ThreadingHTTPServer(("127.0.0.1", port), Capture)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            conf.set("notify.ntfy_url", "http://127.0.0.1:%d/mytopic" % port)
            conf.set("notify.webhook_url", "http://127.0.0.1:%d/hook" % port)
            with mock.patch.object(notify, "_pool", Inline()):
                events._record(self.server, "completed", "gid1", "Ubuntu.iso", 123)
                events._record(self.server, "added", "gid2", "ignored.bin")  # not in notify.events
                events._record(self.server, "error", "gid3", "bad.bin", error_code="3", error_message="Resource not found")
            posts = dict()
            for path, payload in Capture.seen:
                posts.setdefault(path, []).append(payload)
            self.assertEqual(len(posts["/"]), 2)
            ntfy = posts["/"][0]
            self.assertEqual((ntfy["topic"], ntfy["message"]), ("mytopic", "Ubuntu.iso"))
            self.assertIn("Download completed", ntfy["title"])
            self.assertIn("Resource not found", posts["/"][1]["message"])
            self.assertEqual([h["kind"] for h in posts["/hook"]], ["completed", "error"])
            self.assertEqual(posts["/hook"][0]["server"], "test")
        finally:
            httpd.shutdown()
            conf.reset("notify.ntfy_url")
            conf.reset("notify.webhook_url")
        with self.assertRaises(conf.ConfigError):
            conf.set("notify.ntfy_url", "ftp://nope")
