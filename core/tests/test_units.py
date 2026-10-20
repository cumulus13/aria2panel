from datetime import datetime, time

from django.test import TestCase
from django.utils import timezone

from core import conf, crypto, services
from core.models import Aria2Server, SpeedRule


class ConfTests(TestCase):
    def test_defaults_and_roundtrip(self):
        self.assertEqual(conf.get("ui.poll_interval_ms"), 1500)
        conf.set("ui.poll_interval_ms", "3000")
        self.assertEqual(conf.get("ui.poll_interval_ms"), 3000)
        conf.reset("ui.poll_interval_ms")
        self.assertEqual(conf.get("ui.poll_interval_ms"), 1500)

    def test_validation(self):
        with self.assertRaises(conf.ConfigError):
            conf.set("ui.poll_interval_ms", 10)
        with self.assertRaises(conf.ConfigError):
            conf.set("ui.default_theme", "pink")
        with self.assertRaises(conf.ConfigError):
            conf.set("nope", 1)

    def test_list_setting(self):
        conf.set("security.allowed_download_dirs", "/downloads\n\n/data/media\n")
        self.assertEqual(conf.get("security.allowed_download_dirs"), ["/downloads", "/data/media"])

    def test_stored_in_database(self):
        from core.models import Setting

        conf.set("app.site_name", "My Panel")
        self.assertEqual(Setting.objects.get(key="app.site_name").value, "My Panel")


class SecretTests(TestCase):
    def test_secret_encrypted_at_rest(self):
        server = Aria2Server(name="a", rpc_url="http://x:6800/jsonrpc")
        server.secret = "topsecret"
        server.save()
        raw = Aria2Server.objects.values_list("secret_enc", flat=True).get(pk=server.pk)
        self.assertNotIn("topsecret", raw)
        self.assertEqual(Aria2Server.objects.get(pk=server.pk).secret, "topsecret")
        with self.assertLogs("core.crypto", level="ERROR"):
            self.assertEqual(crypto.decrypt("garbage"), "")

    def test_single_default(self):
        a = Aria2Server.objects.create(name="a", is_default=True)
        b = Aria2Server.objects.create(name="b", is_default=True)
        a.refresh_from_db()
        self.assertFalse(a.is_default)
        self.assertTrue(b.is_default)


class SpeedRuleTests(TestCase):
    def setUp(self):
        self.server = Aria2Server.objects.create(name="s")
        self.tz = timezone.get_current_timezone()

    def at(self, weekday, hh, mm):
        # 2026-10-19 is a Monday
        return datetime(2026, 10, 19 + weekday, hh, mm, tzinfo=self.tz)

    def test_daytime_window(self):
        r = SpeedRule(server=self.server, days=[0, 1], start_time=time(9), end_time=time(17))
        self.assertTrue(r.matches(self.at(0, 9, 0)))
        self.assertFalse(r.matches(self.at(0, 17, 0)))
        self.assertFalse(r.matches(self.at(2, 10, 0)))

    def test_overnight_window_belongs_to_start_day(self):
        r = SpeedRule(server=self.server, days=[4], start_time=time(22), end_time=time(6))  # Friday night
        self.assertTrue(r.matches(self.at(4, 23, 0)))
        self.assertTrue(r.matches(self.at(5, 3, 0)))  # Saturday early morning
        self.assertFalse(r.matches(self.at(4, 3, 0)))
        self.assertFalse(r.matches(self.at(5, 23, 0)))


class OptionTests(TestCase):
    def test_blocks_command_hooks(self):
        for key in ("on-download-complete", "rpc-secret", "conf-path"):
            with self.assertRaises(services.OptionError):
                services.clean_options({key: "x"})

    def test_cleaning(self):
        self.assertEqual(services.clean_options({"split": 4, "out": "", "check-integrity": True, "dir": None}), {"split": "4", "check-integrity": "true"})
        with self.assertRaises(services.OptionError):
            services.clean_options({"Bad Name": "1"})

    def test_allowed_dirs(self):
        conf.set("security.allowed_download_dirs", "/downloads")
        self.assertEqual(services.clean_options({"dir": "/downloads/movies"}), {"dir": "/downloads/movies"})
        for bad in ("/etc", "/downloads/../etc", "/downloadsX"):
            with self.assertRaises(services.OptionError):
                services.clean_options({"dir": bad})


class SummaryTests(TestCase):
    def test_names(self):
        self.assertEqual(services.display_name({"files": [{"path": "/d/a/b.iso"}]}), "b.iso")
        self.assertEqual(services.display_name({"files": [{"path": "", "uris": [{"uri": "http://h/x/y.zip?t=1"}]}]}), "y.zip")
        self.assertEqual(services.display_name({"bittorrent": {"info": {"name": "Ubuntu"}}, "files": [{"path": "/d/Ubuntu/a"}]}), "Ubuntu")
        self.assertEqual(services.display_name({"files": [{"path": "/d/a.bin"}, {"path": "/d/b.bin"}]}), "a.bin (+1)")

    def test_summary_numbers(self):
        s = services.summarize({"gid": "g", "status": "active", "totalLength": "1000", "completedLength": "250", "downloadSpeed": "50", "files": []})
        self.assertEqual((s["progress"], s["eta"]), (25.0, 15))


class DotenvTests(TestCase):
    def test_loader(self):
        import os
        import tempfile

        from aria2panel.env import load_dotenv

        with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as f:
            f.write("# comment\nPANEL_T_A=1\nexport PANEL_T_B = \"two words\"\nPANEL_T_C=pa#ss=word\nPANEL_T_D=from-file\n\nbroken line\n")
        os.environ["PANEL_T_D"] = "from-env"
        try:
            load_dotenv(f.name)
            self.assertEqual(os.environ["PANEL_T_A"], "1")
            self.assertEqual(os.environ["PANEL_T_B"], "two words")
            self.assertEqual(os.environ["PANEL_T_C"], "pa#ss=word")
            self.assertEqual(os.environ["PANEL_T_D"], "from-env")  # real env wins
            load_dotenv(f.name + ".missing")  # no error
        finally:
            for k in ("PANEL_T_A", "PANEL_T_B", "PANEL_T_C", "PANEL_T_D"):
                os.environ.pop(k, None)
            os.unlink(f.name)
