"""statusline.py: the quota reaches the right project's server (port read from run/<project>/server.port), not the
default port 8765 (a project on another port would otherwise show no quota).
Fake HTTP servers on free ports (never 8765), state under the temp root of tests/base.py.
"""
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from tests.base import PROJECT, SUP, IsolatedTestCase

PAYLOAD = {"model": {"display_name": "Opus"}, "context_window": {"used_percentage": 12.0},
           "rate_limits": {"five_hour": {"used_percentage": 42.0, "resets_at": 4102444800},
                           "seven_day": {"used_percentage": 7.0, "resets_at": 4102444800}}}


def fake_server(received):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            received.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class Statusline(IsolatedTestCase):
    def run_statusline(self, **env):
        base = {k: v for k, v in os.environ.items() if k not in ("SUP_URL", "SUP_PROJECT", "SUP_AGENT")}
        base.update(env)
        return subprocess.run([sys.executable, str(SUP / "statusline.py")], input=json.dumps(PAYLOAD), env=base,
                              capture_output=True, text=True, encoding="utf-8", timeout=30)

    def port_file(self, project, srv):
        d = self.root / "run" / project
        d.mkdir(parents=True, exist_ok=True)
        (d / "server.port").write_text(str(srv.server_address[1]), encoding="utf-8")

    def test_le_serveur_du_projet_recoit_le_quota(self):
        got = []
        srv = fake_server(got)
        self.addCleanup(srv.shutdown)
        self.port_file(PROJECT, srv)
        r = self.run_statusline(SUP_PROJECT=PROJECT)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("5 h 42 %", r.stdout)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0][0], "/api/statusline")
        self.assertEqual(got[0][1]["rate_limits"]["five_hour"]["used_percentage"], 42.0)

    def test_projet_deduit_de_sup_agent(self):
        got = []
        srv = fake_server(got)
        self.addCleanup(srv.shutdown)
        self.port_file(PROJECT, srv)
        self.run_statusline(SUP_AGENT=f"{PROJECT}/B")
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0][1]["_agent"], f"{PROJECT}/B")

    def test_sans_projet_tous_les_serveurs_en_service(self):
        a, b = [], []
        sa, sb = fake_server(a), fake_server(b)
        self.addCleanup(sa.shutdown)
        self.addCleanup(sb.shutdown)
        self.port_file("zz-un", sa)
        self.port_file("zz-deux", sb)
        self.run_statusline()
        self.assertEqual((len(a), len(b)), (1, 1))

    def test_sup_url_prime(self):
        got, other = [], []
        srv, ignored = fake_server(got), fake_server(other)
        self.addCleanup(srv.shutdown)
        self.addCleanup(ignored.shutdown)
        self.port_file(PROJECT, ignored)
        self.run_statusline(SUP_PROJECT=PROJECT, SUP_URL=f"http://127.0.0.1:{srv.server_address[1]}")
        self.assertEqual((len(got), len(other)), (1, 0))

    def test_serveur_arrete_la_statusline_reste_intacte(self):
        got = []
        srv = fake_server(got)
        self.port_file(PROJECT, srv)
        srv.shutdown()
        srv.server_close()   # stale port file: connection refused
        r = self.run_statusline(SUP_PROJECT=PROJECT)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("5 h 42 %", r.stdout)
