"""lifecycle.py: rotating log, clean stop (signal / POST /api/shutdown), launch.py restart, launch checks;
logic of wbrowser.mjs and wmux-tab.mjs with a simulated wmux (node, never the real one).

No real process is contacted or stopped: test servers on a free port (never 8765), fake wmux under the
test root, test named pipe (a sup-test-… pipe) for wmux-tab.mjs.
"""
import json
import os
import secrets
import socket
import subprocess
import threading
import urllib.error
import urllib.request

from tests.base import PROJECT, SUP, IsolatedTestCase

import config
import launch
import lifecycle
import server
import store


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Journal(IsolatedTestCase):
    def tearDown(self):
        for h in list(lifecycle.LOGGER.handlers):  # Windows: file closed before the root is deleted
            lifecycle.LOGGER.removeHandler(h)
            h.close()
        super().tearDown()

    def test_rotation_et_print(self):
        run = self.root / "run" / PROJECT
        run.mkdir(parents=True, exist_ok=True)
        old = lifecycle.LOG_BYTES
        lifecycle.LOG_BYTES = 300
        try:
            lifecycle.setup_log(run, console=False)
        finally:
            lifecycle.LOG_BYTES = old
        for i in range(40):
            lifecycle.logprint("scan:", f"line {i}", "→ é")
        lifecycle.logprint("wmux serious error")
        files = sorted(p.name for p in run.iterdir() if p.name.startswith("server.log"))
        self.assertIn("server.log", files)
        self.assertIn("server.log.1", files)
        self.assertLessEqual(len(files), lifecycle.LOG_BACKUPS + 1)
        text = (run / "server.log").read_text(encoding="utf-8")
        self.assertIn("WARNING wmux serious error", text)

    def test_journal_hors_racine_refuse(self):
        with self.assertRaises(Exception):
            lifecycle.setup_log(SUP / "run" / "zz-interdit", console=False)


class ArretPropre(IsolatedTestCase):
    def tearDown(self):
        lifecycle.STOP.clear()
        server.STOPPER = None
        super().tearDown()

    def test_stopper_idempotent(self):
        httpd = server.make_server(0)
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        calls = []
        stop = lifecycle.stopper(httpd, lambda: calls.append(1))
        stop()
        stop()  # second call (repeated signal): no effect
        self.assertTrue(stop.done.wait(10))
        t.join(5)
        self.assertFalse(t.is_alive())
        self.assertTrue(lifecycle.STOP.is_set())
        self.assertEqual(calls, [1])
        httpd.server_close()

    def test_shutdown_http_et_relance(self):
        server.configure(PROJECT)
        server.save(server.empty_state())
        httpd = server.make_server(0)
        port = httpd.server_address[1]
        url = f"http://127.0.0.1:{port}"
        server.STOPPER = lifecycle.stopper(httpd, server.finish)
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        ok, msg = lifecycle.request_shutdown(url, "wrong-token")
        self.assertFalse(ok)
        self.assertIn("403", msg)
        self.assertFalse(lifecycle.port_free(port))
        launch.restart(url, port, server.RUN_DIR, timeout=10)  # clean stop with the token of run/<p>/.token
        self.assertTrue(server.STOPPER.done.wait(10))
        t.join(5)
        httpd.server_close()
        self.assertTrue(lifecycle.port_free(port))
        self.assertIsNone(server.STORE)  # store closed after the last write
        st = store.Store.open(server.RUN_DIR, log=lambda m: None)
        try:
            msgs = [e["msg"] for e in st.load()["events"]]
        finally:
            st.close()
        self.assertIn("Server stopped cleanly", msgs)
        self.assertIn("Server stop requested (clean stop)", msgs)
        server.configure(PROJECT)  # for the next classes and tearDownClass

    def test_relance_sans_serveur(self):
        launch.restart("http://127.0.0.1:9", free_port(), self.root, timeout=1)  # nothing to stop: no exit

    def test_relance_sans_jeton(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            s.listen()
            port = s.getsockname()[1]
            with self.assertRaises(SystemExit) as e:
                launch.restart(f"http://127.0.0.1:{port}", port, self.root / "no-token", timeout=1)
        self.assertIn("token missing", str(e.exception))


class Controles(IsolatedTestCase):
    def cfg(self, **kw):
        repo = self.root / "repo-ok"
        if not repo.exists():
            subprocess.run(["git", "init", "-q", str(repo)], check=True, creationflags=config.NO_WINDOW)
        return {"source": "brain", "path": str(repo), **kw}

    def test_tout_va_bien(self):
        self.assertEqual(lifecycle.preflight(self.cfg(), free_port(), wmux_check=lambda: (True, "pong")), [])

    def test_problemes_en_clair(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            s.listen()
            port = s.getsockname()[1]
            out = lifecycle.preflight({"source": "defaults (brain unreadable: node missing)", "path": str(self.root / "absent")},
                                      port, wmux_check=lambda: (False, "timeout"))
        text = lifecycle.describe(out)
        for needle in ("brain unreadable", "project repository missing", f"port {port} already taken", "wmux unreachable by the Node client (timeout)"):
            self.assertIn(needle, text)

    def test_depot_sans_git(self):
        out = lifecycle.preflight({"source": "brain", "path": str(self.root / "depot")}, free_port(), need_wmux=False)
        self.assertEqual(out, [f"project repository without .git: {self.root / 'depot'}"])

    def test_wmux_simule_sans_pong(self):
        ok, _ = lifecycle.wmux_reachable()  # fake wmux of the root: answers {} and not pong
        self.assertFalse(ok)
        self.assertIn(["ping"], self.wmux_calls())


# --- wbrowser.mjs: fake wmux whose surface list is read from a file ----------------------------------------------

FAKE_WB = r"""
const fs = require('fs');
const dir = __dirname, a = process.argv.slice(2);
fs.appendFileSync(dir + '/wb-calls.log', JSON.stringify(a) + '\n');
if (fs.existsSync(dir + '/wb-down')) process.exit(1);
const surf = JSON.parse(fs.readFileSync(dir + '/wb-surfaces.json', 'utf8'));
if (a[0] === 'list-surfaces') {
  const extra = fs.existsSync(dir + '/wb-grow') && fs.existsSync(dir + '/wb-sent') ? [{id: 'surf-new', type: 'browser', paneId: 'p9'}] : [];
  process.stdout.write(JSON.stringify({surfaces: [...surf, ...extra]}));
} else {
  if (a[0] === 'browser') fs.writeFileSync(dir + '/wb-sent', '1');
  process.stdout.write('ok');
}
"""

SURFACES = [
    {"id": "surf-term-A", "type": "terminal", "paneId": "p1", "isActive": False},
    {"id": "surf-brow-A", "type": "browser", "paneId": "p1", "isActive": True},
    {"id": "surf-brow-B", "type": "browser", "paneId": "p2", "isActive": False},
    {"id": "surf-term-B", "type": "terminal", "paneId": "p2", "isActive": True},
]


class WBrowser(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        self.dir = self.root / f"wb-{secrets.token_hex(3)}"
        self.dir.mkdir()
        (self.dir / "fake.js").write_text(FAKE_WB, encoding="utf-8")
        (self.dir / "wb-surfaces.json").write_text(json.dumps(SURFACES), encoding="utf-8")

    def wb(self, *args):
        env = dict(os.environ, WMUX_CLI=str(self.dir / "fake.js"), WMUX_NODE="node")
        r = subprocess.run(["node", str(SUP / "wbrowser.mjs"), *args], env=env, capture_output=True, text=True,
                           encoding="utf-8", timeout=30, creationflags=config.NO_WINDOW)
        return r

    def calls(self):
        f = self.dir / "wb-calls.log"
        return [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines()] if f.exists() else []

    def sent(self):
        return [c for c in self.calls() if c[0] != "list-surfaces"]

    def test_onglet_inexistant_refuse(self):
        r = self.wb("surf-ferme", "eval", "1")
        self.assertEqual(r.returncode, 3)
        self.assertIn("does not exist", r.stderr)
        self.assertEqual(self.sent(), [])

    def test_terminal_refuse(self):
        r = self.wb("surf-term-A", "eval", "1")
        self.assertEqual(r.returncode, 3)
        self.assertIn("is not a browser", r.stderr)

    def test_open_refuse(self):
        r = self.wb("surf-brow-A", "open", "http://x")
        self.assertEqual(r.returncode, 3)
        self.assertIn("goto", r.stderr)
        self.assertEqual(self.sent(), [])

    def test_onglet_cache_refuse(self):
        r = self.wb("surf-brow-B", "eval", "1")
        self.assertEqual(r.returncode, 3)
        self.assertIn("is not shown", r.stderr)
        self.assertEqual(self.sent(), [])

    def test_goto_par_eval_et_prefixe(self):
        r = self.wb("surf-brow-A"[:9], "goto", "http://localhost:3102/admin")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.sent(), [["browser", "eval", 'location.href="http://localhost:3102/admin"; "ok"',
                                        "--surface", "surf-brow-A"]])

    def test_show_et_hide(self):
        self.assertEqual(self.wb("surf-brow-B", "hide").returncode, 0)
        self.assertEqual(self.sent(), [["focus-surface", "surf-term-B"]])

    def test_surface_creee_signalee(self):
        (self.dir / "wb-grow").write_text("1", encoding="utf-8")
        r = self.wb("surf-brow-A", "snapshot")
        self.assertIn("WARNING: 1 surface(s) created", r.stderr)
        self.assertIn("surf-new", r.stderr)

    def test_wmux_injoignable(self):
        (self.dir / "wb-down").write_text("1", encoding="utf-8")
        r = self.wb("surf-brow-A", "eval", "1")
        self.assertEqual(r.returncode, 2)
        self.assertIn("wmux unreachable", r.stderr)


# --- wmux-tab.mjs: fake wmux server on a test named pipe -------------------------------------------------------

HARNESS = r"""
// Fake wmux (test named pipe): answers pane.list / surface.create / surface.rename / surface.focus,
// runs wmux-tab.mjs against it, then prints {calls, code, out, err}.
const net = require('net'), { spawn } = require('child_process');
const [pipe, script, ...args] = process.argv.slice(2);
const calls = [];
const panes = [{paneId: 'p1', activeSurfaceIndex: 0, surfaces: [{id: 'surf-term-A'}]},
               {paneId: 'p2', activeSurfaceIndex: 1, surfaces: [{id: 'surf-x'}, {id: 'surf-term-B'}]}];
const srv = net.createServer((c) => {
  let buf = '';
  c.on('data', (d) => {
    buf += d;
    if (!buf.includes('\n')) return;
    const req = JSON.parse(buf.trim()); buf = '';
    calls.push(req);
    const result = req.method === 'pane.list' ? {panes}
      : req.method === 'surface.create' ? {surfaceId: 'surf-new', paneId: req.params.paneId} : {};
    c.end(JSON.stringify({id: req.id, result}) + '\n');
  });
});
srv.listen(pipe, () => {
  const ch = spawn(process.execPath, [script, ...args], {env: process.env});
  let out = '', err = '';
  ch.stdout.on('data', (d) => out += d); ch.stderr.on('data', (d) => err += d);
  ch.on('close', (code) => { srv.close(); process.stdout.write(JSON.stringify({calls, code, out, err})); });
});
"""


class WmuxTab(IsolatedTestCase):
    def tab(self, *args, surface="surf-term-B"):
        h = self.root / "harness.js"
        h.write_text(HARNESS, encoding="utf-8")
        pipe = rf"\\.\pipe\sup-test-{secrets.token_hex(6)}" if os.name == "nt" else str(self.root / "pipe.sock")
        # Fake wmux variables for the subprocess ONLY: os.environ is not touched (base.py guards).
        env = dict(os.environ, WMUX_PIPE=pipe, WMUX_PIPE_TOKEN="test-token", WMUX_SURFACE_ID=surface)
        r = subprocess.run(["node", str(h), pipe, str(SUP / "wmux-tab.mjs"), *args], env=env, capture_output=True,
                           text=True, encoding="utf-8", timeout=30, creationflags=config.NO_WINDOW)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_onglet_dans_le_panneau_de_l_appelant(self):
        r = self.tab("--title", "Browser B")
        self.assertEqual(r["code"], 0, r["err"])
        m = [(c["method"], c["params"]) for c in r["calls"]]
        self.assertEqual(m[0][0], "pane.list")
        self.assertEqual(m[1], ("surface.create", {"type": "browser", "paneId": "p2", "caller": "surf-term-B"}))
        self.assertEqual(m[2], ("surface.rename", {"id": "surf-new", "title": "Browser B", "caller": "surf-term-B"}))
        self.assertEqual(m[3], ("surface.focus", {"id": "surf-term-B", "caller": "surf-term-B"}))  # terminal brought back to front
        self.assertTrue(all(c["token"] == "test-token" for c in r["calls"]))
        self.assertEqual(json.loads(r["out"])["surfaceId"], "surf-new")

    def test_show_garde_l_onglet_devant(self):
        r = self.tab("--show")
        self.assertEqual([c["method"] for c in r["calls"]], ["pane.list", "surface.create"])

    def test_panneau_introuvable(self):
        r = self.tab(surface="surf-inconnu")
        self.assertEqual(r["code"], 2)
        self.assertIn("not found", r["err"])
        self.assertNotIn("surface.create", [c["method"] for c in r["calls"]])
