import json
import os

from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from core import conf
from core.models import QueueItem, SpeedRule

from .helpers import Aria2Client_for, LiveAria2Case
from .test_scheduler import wait_until


class ApiTests(LiveAria2Case):
    def setUp(self):
        self.clean_aria2()
        User = get_user_model()
        self.admin = User.objects.create_user("admin", password="pw", is_staff=True, is_superuser=True)
        self.user = User.objects.create_user("bob", password="pw")
        self.server = self.make_server(max_downloads=2, is_default=True)
        self.c = Client(enforce_csrf_checks=True)
        self.c.login(username="admin", password="pw")

    def csrf(self, client=None):
        client = client or self.c
        client.get(reverse("dashboard"))  # sets the csrftoken cookie
        return {"HTTP_X_CSRFTOKEN": client.cookies["csrftoken"].value}

    def post(self, name, payload, client=None, **kw):
        client = client or self.c
        return client.post(reverse(name, args=[self.server.pk]), data=json.dumps(payload), content_type="application/json", **self.csrf(client), **kw)

    def test_auth_and_permissions(self):
        anon = Client()
        r = anon.get(reverse("api_snapshot", args=[self.server.pk]))
        self.assertEqual(r.status_code, 401)
        self.assertEqual(anon.get(reverse("dashboard")).status_code, 302)
        bob = Client(enforce_csrf_checks=True)
        bob.login(username="bob", password="pw")
        self.assertEqual(bob.get(reverse("dashboard")).status_code, 200)
        self.assertEqual(bob.get(reverse("settings")).status_code, 302)  # staff only
        self.assertEqual(bob.get(reverse("server_list")).status_code, 302)
        self.assertEqual(self.post("api_rules", {"op": "delete", "id": 1}, client=bob).status_code, 403)
        self.assertEqual(self.post("api_scheduler", {"max_downloads": 9}, client=bob).status_code, 403)
        self.assertEqual(self.post("api_queue", {"op": "clear"}, client=bob).status_code, 403)
        self.assertEqual(bob.get(reverse("api_scheduler", args=[self.server.pk])).status_code, 200)

    def test_csrf_is_enforced(self):
        r = self.c.post(reverse("api_action", args=[self.server.pk]), data="{}", content_type="application/json")
        self.assertEqual(r.status_code, 403)

    def test_pages_render(self):
        for name in ("dashboard", "scheduler", "server_list", "server_new", "settings"):
            r = self.c.get(reverse(name))
            self.assertEqual(r.status_code, 200, name)
        html = self.c.get(reverse("dashboard")).content.decode()
        self.assertIn('id="dashboard"', html)
        self.assertIn("csrf-token", html)
        self.assertEqual(self.c.get(reverse("login")).status_code, 302)  # already signed in
        self.assertEqual(Client().get(reverse("login")).status_code, 200)
        self.assertEqual(Client().get(reverse("healthz")).json(), {"status": "ok"})

    def test_add_snapshot_queue_flow(self):
        uris = "\n".join(self.url(i) for i in range(1, 5))
        r = self.c.post(
            reverse("api_add", args=[self.server.pk]),
            data={"uris": uris, "options": json.dumps({"max-download-limit": "20K"})},
            **self.csrf(),
        )
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual((r.json()["dispatched"], r.json()["queued"]), (2, 2))

        snap = self.c.get(reverse("api_snapshot", args=[self.server.pk])).json()
        self.assertEqual(snap["scheduler"]["running"], 2)
        self.assertEqual(snap["scheduler"]["queued"], 2)
        names = {d["name"] for d in snap["downloads"]}
        self.assertTrue({"file1.bin", "file2.bin", "file3.bin", "file4.bin"} <= names)
        queued = [d for d in snap["downloads"] if d["queued"]]
        self.assertEqual([d["name"] for d in queued], ["file3.bin", "file4.bin"])

        # reorder, cancel through the generic action endpoint (queued pseudo gids)
        self.assertEqual(self.post("api_queue", {"op": "top", "id": queued[1]["id"]}).status_code, 200)
        order = [q.name for q in self.server.queue.filter(status="queued")]
        self.assertEqual(order, ["file4.bin", "file3.bin"])
        r = self.post("api_action", {"action": "remove", "gids": [queued[0]["gid"]]}).json()
        self.assertEqual((r["ok"], r["errors"]), (1, []))
        self.assertEqual(self.server.queue.filter(status="queued").count(), 1)

        # pause an active download, details
        gid = [d["gid"] for d in snap["downloads"] if d["status"] == "active" or d["status"] == "waiting"][0]
        self.assertEqual(self.post("api_action", {"action": "pause", "gid": gid}).json()["errors"], [])
        detail = self.c.get(reverse("api_detail", args=[self.server.pk, gid])).json()
        self.assertEqual(detail["summary"]["gid"], gid)
        self.assertTrue(detail["files"])
        self.assertEqual(self.c.get(reverse("api_detail", args=[self.server.pk, "not-a-gid"])).status_code, 400)

    def test_history_endpoint(self):
        from datetime import timedelta

        from django.utils import timezone

        from core.models import SpeedSample

        now = timezone.now()
        for i in range(5):
            SpeedSample.objects.create(server=self.server, ts=now - timedelta(seconds=10 - i), download=1000 * i, upload=i)
        SpeedSample.objects.create(server=self.server, ts=now - timedelta(hours=2), download=999)
        url = reverse("api_history", args=[self.server.pk])
        data = self.c.get(url + "?seconds=60").json()
        self.assertEqual([s[1] for s in data["samples"]], [0, 1000, 2000, 3000, 4000])
        self.assertTrue(data["samples"][0][0] < data["samples"][-1][0] <= data["now"])
        self.assertEqual(self.c.get(url + "?seconds=abc").status_code, 400)
        self.assertEqual(Client().get(url).status_code, 401)

    def test_add_rejects_bad_input(self):
        def add(**data):
            return self.c.post(reverse("api_add", args=[self.server.pk]), data=data, **self.csrf())

        self.assertEqual(add(uris="").status_code, 400)
        self.assertEqual(add(uris=self.url(1), options=json.dumps({"on-download-complete": "/bin/sh"})).status_code, 400)
        from django.core.files.uploadedfile import SimpleUploadedFile

        bad = SimpleUploadedFile("x.exe", b"MZ")
        self.assertEqual(self.c.post(reverse("api_add", args=[self.server.pk]), data={"files": bad}, **self.csrf()).status_code, 400)

    def test_scheduler_config_and_rules(self):
        r = self.post("api_scheduler", {"enabled": True, "max_downloads": 4, "default_download_limit": "0", "default_upload_limit": "0"})
        self.assertEqual(r.status_code, 200)
        self.server.refresh_from_db()
        self.assertEqual(self.server.max_downloads, 4)
        self.assertEqual(self.post("api_scheduler", {"max_downloads": 0}).status_code, 400)
        self.assertEqual(self.post("api_scheduler", {"default_download_limit": "fast"}).status_code, 400)

        r = self.post("api_rules", {"op": "save", "name": "Night", "days": [0, 1, 2], "start_time": "22:00", "end_time": "06:00", "download_limit": "2M", "upload_limit": "0", "enabled": True})
        self.assertEqual(r.status_code, 200, r.content)
        rule = SpeedRule.objects.get()
        self.assertEqual((rule.name, rule.days, rule.download_limit), ("Night", [0, 1, 2], "2M"))
        self.assertEqual(self.post("api_rules", {"op": "save", "name": "x", "days": [], "start_time": "1:00", "end_time": "2:00"}).status_code, 400)
        data = self.c.get(reverse("api_scheduler", args=[self.server.pk])).json()
        self.assertEqual(data["rules"][0]["start_time"], "22:00")
        self.assertEqual(data["config"]["max_downloads"], 4)
        self.post("api_rules", {"op": "delete", "id": rule.pk})
        self.assertFalse(SpeedRule.objects.exists())

    def test_global_options_and_test_connection(self):
        # managed by the scheduler while it is enabled
        self.assertEqual(self.post("api_global", {"max-concurrent-downloads": "7"}).status_code, 400)
        self.server.scheduler_enabled = False
        self.server.save()
        r = self.post("api_global", {"max-overall-download-limit": "300K", "max-concurrent-downloads": "7"})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["max-concurrent-downloads"], "7")
        self.assertEqual(self.post("api_global", {"max-concurrent-downloads": "0"}).status_code, 400)
        Aria2Client_for(self).change_global_option({"max-overall-download-limit": self.SPEED_LIMIT, "max-concurrent-downloads": "50"})
        r = self.c.post(reverse("api_test"), data=json.dumps({"server_id": self.server.pk}), content_type="application/json", **self.csrf())
        self.assertEqual(r.status_code, 200, r.content)
        self.assertIn("version", r.json())
        r = self.c.post(reverse("api_test"), data=json.dumps({"rpc_url": "http://127.0.0.1:1/jsonrpc"}), content_type="application/json", **self.csrf())
        self.assertEqual(r.status_code, 502)

    def test_server_and_settings_forms(self):
        r = self.c.post(reverse("server_new"), {"name": "second", "rpc_url": "http://aria2:6800/jsonrpc", "secret": "abc", "verify_tls": "on", "enabled": "on", "scheduler_enabled": "on", "max_downloads": 5, "default_download_limit": "0", "default_upload_limit": "0"}, **self.csrf())
        self.assertEqual(r.status_code, 302, r.content[:500])
        from core.models import Aria2Server

        second = Aria2Server.objects.get(name="second")
        self.assertEqual(second.secret, "abc")
        self.c.post(reverse("server_edit", args=[second.pk]), {"name": "second", "rpc_url": "http://aria2:6800/jsonrpc", "enabled": "on", "max_downloads": 6, "default_download_limit": "0", "default_upload_limit": "0"}, **self.csrf())
        second.refresh_from_db()
        self.assertEqual((second.secret, second.max_downloads), ("abc", 6))  # blank secret keeps the stored one
        self.assertEqual(self.c.post(reverse("server_delete", args=[second.pk]), **self.csrf()).status_code, 302)

        form = {"app__site_name": "Panel X", "ui__default_theme": "dark", "ui__poll_interval_ms": 2000, "ui__stopped_limit": 50, "scheduler__tick_seconds": 1, "scheduler__keep_days": 7, "history__interval_seconds": 2, "history__keep_minutes": 60, "events__keep_days": 30, "sse__interval_seconds": 1, "sse__max_seconds": 1800, "notify__ntfy_url": "", "notify__webhook_url": "", "notify__events": "completed\nerror", "rpc__timeout_seconds": 10, "security__allowed_download_dirs": "/downloads"}
        r = self.c.post(reverse("settings"), form, **self.csrf())
        self.assertEqual(r.status_code, 302, r.content[:800])
        conf_values = (conf.get("app.site_name"), conf.get("ui.default_theme"), conf.get("security.allowed_download_dirs"))
        self.assertEqual(conf_values, ("Panel X", "dark", ["/downloads"]))
        self.assertIn("Panel X", self.c.get(reverse("dashboard")).content.decode())
        self.assertIn('data-default-theme="dark"', self.c.get(reverse("dashboard")).content.decode())
