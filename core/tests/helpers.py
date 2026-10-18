import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from django.test import TestCase

from core.models import Aria2Server

SECRET = "s3cr3t"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


class LiveAria2Case(TestCase):
    """Starts a real aria2c and a local HTTP file server for the whole test class."""

    FILES = 6
    FILE_SIZE = 200 * 1024
    SPEED_LIMIT = "250K"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not shutil.which("aria2c"):
            raise unittest.SkipTest("aria2c is not installed")
        cls.tmp = tempfile.mkdtemp(prefix="aria2panel-test-")
        cls.www = os.path.join(cls.tmp, "www")
        cls.dl = os.path.join(cls.tmp, "dl")
        os.makedirs(cls.www)
        os.makedirs(cls.dl)
        for i in range(1, cls.FILES + 1):
            with open(os.path.join(cls.www, "file%d.bin" % i), "wb") as f:
                f.write(os.urandom(cls.FILE_SIZE))
        cls.http_port = free_port()
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", cls.http_port), partial(QuietHandler, directory=cls.www))
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.rpc_port = free_port()
        cls.proc = subprocess.Popen(
            [
                "aria2c", "--enable-rpc", "--rpc-listen-port=%d" % cls.rpc_port, "--rpc-secret=" + SECRET,
                "--dir=" + cls.dl, "--max-overall-download-limit=" + cls.SPEED_LIMIT,
                "--max-concurrent-downloads=50", "--no-conf=true", "--quiet=true",
            ],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                socket.create_connection(("127.0.0.1", cls.rpc_port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        try:
            cls.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            cls.proc.kill()
        cls.httpd.shutdown()
        shutil.rmtree(cls.tmp, ignore_errors=True)
        super().tearDownClass()

    def make_server(self, **kwargs):
        server = Aria2Server(name=kwargs.pop("name", "test"), rpc_url="http://127.0.0.1:%d/jsonrpc" % self.rpc_port, **kwargs)
        server.secret = SECRET
        server.save()
        return server

    def url(self, n):
        return "http://127.0.0.1:%d/file%d.bin" % (self.http_port, n)

    def clean_aria2(self):
        client = Aria2Client_for(self)
        for d in client.tell_active() + client.tell_waiting(0, 100):
            try:
                client.force_remove(d["gid"])
            except Exception:
                pass
        client.purge()
        time.sleep(0.2)
        # leftovers (partial files + .aria2 control files) would make aria2 try to resume,
        # which the plain test HTTP server does not support
        for name in os.listdir(self.dl):
            os.remove(os.path.join(self.dl, name))


def Aria2Client_for(case):
    from core.aria2 import Aria2Client

    return Aria2Client("http://127.0.0.1:%d/jsonrpc" % case.rpc_port, SECRET)
