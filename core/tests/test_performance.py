import json

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from core import services
from core.models import ApiToken, SpeedRule

from .helpers import Aria2Client_for, LiveAria2Case
from .test_scheduler import wait_until

PANEL = {"managed": False, "rule": None}


def codes(hints):
    return [h["code"] for h in hints]


class AnalyzeTests(TestCase):
    def test_nothing_wrong(self):
        eff = {"max-connection-per-server": "16", "file-allocation": "none"}
        hints = services.analyze({"max-overall-download-limit": "0", "dir": "/downloads"}, eff, PANEL, [])
        self.assertEqual(codes(hints), ["no_limit"])

    def test_limit_that_comes_from_aria2(self):
        hints = services.analyze({"max-overall-download-limit": "51200"}, {"max-connection-per-server": "16"}, PANEL, [])
        self.assertEqual(codes(hints)[0], "aria2_limit")
        self.assertIn("50.0 KiB/s", hints[0]["message"])
        self.assertIn("aria2.conf", hints[0]["message"])

    def test_limit_that_comes_from_the_panel(self):
        hints = services.analyze({"max-overall-download-limit": "102400"}, {"max-connection-per-server": "16"}, {"managed": True, "rule": "Office hours"}, [])
        self.assertEqual(codes(hints)[0], "panel_limit")
        self.assertIn("Office hours", hints[0]["message"])
        hints = services.analyze({"max-overall-download-limit": "102400"}, {"max-connection-per-server": "16"}, {"managed": True, "rule": None}, [])
        self.assertIn("default download limit", hints[0]["message"])

    def test_single_connection_and_prealloc_hints(self):
        hints = services.analyze({}, {"max-connection-per-server": "1", "file-allocation": "prealloc"}, PANEL, [])
        self.assertEqual(sorted(codes(hints)), ["no_limit", "one_connection", "prealloc"])

    def test_windows_drive_and_single_download_cap(self):
        hints = services.analyze({"dir": "/mnt/c/DOWNLOADS"}, {"max-connection-per-server": "16"}, PANEL, [{"gid": "g", "name": "big.iso", "limit": 20480}])
        self.assertEqual(codes(hints), ["download_limit", "slow_disk"])
        self.assertIn("big.iso", hints[0]["message"])

    def test_clean_defaults(self):
        self.assertEqual(
            services.clean_defaults({"split": "8", "max-connection-per-server": 16, "min-split-size": "1m", "file-allocation": "none", "continue": "yes", "split ": ""} if False else
                                    {"split": "8", "max-connection-per-server": 16, "min-split-size": "1m", "file-allocation": "none", "continue": "yes"}),
            {"split": "8", "max-connection-per-server": "16", "min-split-size": "1m", "file-allocation": "none", "continue": "true"},
        )
        self.assertEqual(services.clean_defaults({"split": "", "file-allocation": None}), {})
        for bad in ({"max-connection-per-server": "17"}, {"max-connection-per-server": "0"}, {"split": "x"}, {"min-split-size": "100K"},
                    {"file-allocation": "magic"}, {"dir": "/etc"}, {"on-download-complete": "x"}):
            with self.assertRaises(services.OptionError):
                services.clean_defaults(bad)


class PerformanceLiveTests(LiveAria2Case):
    def setUp(self):
        self.clean_aria2()
        User = get_user_model()
        self.admin = User.objects.create_user("admin", password="pw", is_staff=True)
        self.bob = User.objects.create_user("bob", password="pw")
        self.admin_auth = {"HTTP_AUTHORIZATION": "Bearer " + ApiToken.issue(self.admin, "t")[1]}
        self.bob_auth = {"HTTP_AUTHORIZATION": "Bearer " + ApiToken.issue(self.bob, "t")[1]}
        self.server = self.make_server(max_downloads=5)
        self.addCleanup(lambda: Aria2Client_for(self).change_global_option({"max-overall-download-limit": self.SPEED_LIMIT}))

    def diag(self):
        return Client().get(reverse("api_diagnostics", args=[self.server.pk]), **self.admin_auth).json()

    def test_defaults_are_added_to_new_downloads_and_can_be_overridden(self):
        self.server.default_options = {"split": "3", "max-connection-per-server": "4", "max-download-limit": "9K"}  # last one is not allowed as default, but is a plain stored option
        self.server.save()
        client = Aria2Client_for(self)
        services.add_downloads(self.server, uri_lines=[[self.url(1)]], options={"max-download-limit": "7K"})
        services.add_downloads(self.server, uri_lines=[[self.url(2)]], options={"split": "7"})
        gids = {q.name: q.gid for q in self.server.queue.all()}
        a, b = client.get_option(gids["file1.bin"]), client.get_option(gids["file2.bin"])
        self.assertEqual((a["split"], a["max-connection-per-server"]), ("3", "4"))  # panel defaults
        self.assertEqual(b["split"], "7")  # chosen when adding wins
        self.assertEqual(b["max-connection-per-server"], "4")

    def test_diagnostics_names_the_source_of_a_limit(self):
        Aria2Client_for(self).change_global_option({"max-overall-download-limit": "50K"})
        data = self.diag()
        self.assertEqual(data["limits"]["download"]["human"], "50.0 KiB/s")
        self.assertIn("aria2_limit", codes(data["hints"]))
        self.assertFalse(data["limits"]["managed_by_panel"])

        SpeedRule.objects.create(server=self.server, name="Always", days=list(range(7)), start_time="00:00", end_time="00:00", download_limit="50K")
        data = self.diag()
        self.assertTrue(data["limits"]["managed_by_panel"])
        self.assertEqual(data["limits"]["active_rule"], "Always")
        self.assertIn("panel_limit", codes(data["hints"]))
        self.assertNotIn("aria2_limit", codes(data["hints"]))

    def test_diagnostics_finds_a_per_download_cap_and_shows_effective_defaults(self):
        services.add_downloads(self.server, uri_lines=[[self.url(1)]], options={"max-download-limit": "20K"})
        self.assertTrue(wait_until(lambda: any(d["name"] == "file1.bin" for d in self.diag()["per_download_limits"]), timeout=10))
        data = self.diag()
        self.assertEqual(data["per_download_limits"][0]["limit"], 20480)
        self.assertIn("download_limit", codes(data["hints"]))
        self.assertEqual(data["effective"]["max-connection-per-server"], "1")  # aria2's own default
        self.assertIn("one_connection", codes(data["hints"]))
        self.server.default_options = {"max-connection-per-server": "16", "file-allocation": "none"}
        self.server.save()
        data = self.diag()
        self.assertEqual((data["effective"]["max-connection-per-server"], data["effective"]["file-allocation"]), ("16", "none"))
        self.assertNotIn("one_connection", codes(data["hints"]))
        self.assertNotIn("prealloc", codes(data["hints"]))

    def test_defaults_api_permissions_and_validation(self):
        url = reverse("api_defaults", args=[self.server.pk])

        def post(payload, auth):
            return Client().post(url, data=json.dumps(payload), content_type="application/json", **auth)

        self.assertEqual(post({"defaults": {"split": "8"}}, self.bob_auth).status_code, 403)
        r = post({"defaults": services.RECOMMENDED_DEFAULTS}, self.admin_auth)
        self.assertEqual(r.status_code, 200, r.content)
        self.server.refresh_from_db()
        self.assertEqual(self.server.default_options["max-connection-per-server"], "16")
        self.assertEqual(Client().get(url, **self.bob_auth).json()["defaults"]["split"], "16")  # anyone may read
        self.assertEqual(post({"defaults": {"split": "999"}}, self.admin_auth).status_code, 400)
        self.assertEqual(post({"defaults": {}}, self.admin_auth).json()["defaults"], {})  # clearing works

    def test_performance_page(self):
        staff, user = Client(), Client()
        staff.login(username="admin", password="pw")
        user.login(username="bob", password="pw")
        self.assertEqual(staff.get(reverse("performance")).status_code, 200)
        self.assertContains(staff.get(reverse("performance")), "Why might downloads be slow?")
        self.assertEqual(user.get(reverse("performance")).status_code, 302)
