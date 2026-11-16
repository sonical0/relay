"""Shared base of the supervision tests: isolated write roots, safety checks, simulated wmux.

Tests once erased real resume sheets. Since then every test class (IsolatedTestCase):
  - creates a root `tempfile.mkdtemp(prefix="sup-test-")` and points ALL write roots at it
    (SUP_STATE_ROOT, SUP_RELAY_STATE, SUP_BACKUP_DIR, SUP_CHECK_LOCK, SUP_VAULT_DIR), sets SUP_TEST=1;
  - replaces wmux with a fake client (call log in the root) and removes any access to the real one
    (WMUX_PIPE, WMUX_PIPE_TOKEN, WMUX_SURFACE_ID); SUP_URL points at a closed port, never 8765;
  - REFUSES TO START (IsolationError) if a variable is missing or points at a real directory;
  - deletes, at the end, only that root (safe_rmtree checks prefix and location).
These checks do NOT use config.py (the code under test): a safety check does not depend on what it protects.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path

SUP = Path(__file__).resolve().parent.parent
RELAY_ROOT = Path(os.environ["RELAY_ROOT"]).resolve() if os.environ.get("RELAY_ROOT") else SUP.parent.parent
if str(SUP) not in sys.path:
    sys.path.insert(0, str(SUP))

PREFIX = "sup-test-"
RESERVED_PORT = 8765
PROJECT = "zz-test"  # fictitious project: not in the brain
# Real directories: no test variable may point at them (nor at one of their parents).
REAL = {
    "run/": SUP / "run",
    ".relay-supervision": RELAY_ROOT / ".relay-supervision",
    ".relay-state": RELAY_ROOT / ".relay-state",
    "BACKUPS": RELAY_ROOT / "BACKUPS",
    "LOG": RELAY_ROOT / "LOG",
    "PROJECTS": RELAY_ROOT / "PROJECTS",
    "vault": RELAY_ROOT / "PROJECTS",
    "real lock": RELAY_ROOT / "PROJECTS" / "example-app" / ".claude" / "check.lock",
}
VARS = ("SUP_STATE_ROOT", "SUP_RELAY_STATE", "SUP_BACKUP_DIR", "SUP_CHECK_LOCK", "SUP_VAULT_DIR")
ALIASES = ("SUP_RUN_DIR", "SUP_REPRISE_DIR")  # older names: removed in tests (they would win otherwise)
WMUX_REAL = ("WMUX_PIPE", "WMUX_PIPE_TOKEN", "WMUX_SURFACE_ID", "WMUX_BIN", "WMUX_NODE_ELECTRON")
TOUCHED = (*VARS, *ALIASES, *WMUX_REAL, "SUP_TEST", "SUP_URL", "SUP_NOTIFY", "SUP_CONFIG_FILE", "SUP_PROJECT", "SUP_LOG_DIR", "SUP_BACKEND", "SUP_AGENT", "SUP_NOTIFY_CMD",
           "SUP_SURFACE", "SUP_DASHBOARD_SURFACE", "WMUX_CLI", "WMUX_NODE")

FAKE_WMUX = r"""// Fake wmux client (tests): logs its arguments, contacts nothing, answers an empty JSON.
const fs = require('fs');
fs.appendFileSync(__dirname + '/wmux-calls.log', JSON.stringify(process.argv.slice(2)) + '\n');
process.stdout.write('{}');
"""


class IsolationError(RuntimeError):
    """Tests refuse to start: the real state would be exposed."""


def _canon(p):
    return os.path.normcase(os.path.realpath(str(p)))


def under(p, root):
    p, root = _canon(p), _canon(root)
    return p == root or p.startswith(root.rstrip(os.sep) + os.sep)


def check_root(root):
    """A test root: under the system temp directory, prefixed, outside the Relay tree."""
    out = []
    if not root:
        return ["no test root"]
    if not under(root, tempfile.gettempdir()):
        out.append(f"root {root} outside the temp directory")
    if not Path(root).name.startswith(PREFIX):
        out.append(f"root {root} without the prefix {PREFIX}")
    if under(root, RELAY_ROOT):
        out.append(f"root {root} inside the Relay tree")
    return out


def isolation_problems(env, root):
    """Everything that makes the environment dangerous for the real state (empty list = isolated)."""
    out = check_root(root)
    for v in VARS:
        p = env.get(v)
        if not p:
            out.append(f"{v} not set")
            continue
        if root and not under(p, root):
            out.append(f"{v} = {p}: outside the test root")
        for name, real in REAL.items():
            if under(p, real) or under(real, p):
                out.append(f"{v} = {p}: points at the real directory {name}")
    for v in ALIASES:
        if env.get(v) and (not root or not under(env[v], root)):
            out.append(f"{v} = {env[v]}: older name set outside the test root")
    if env.get("SUP_TEST") != "1":
        out.append("SUP_TEST is not 1")
    url = env.get("SUP_URL", "")
    try:
        port = urllib.parse.urlsplit(url).port
    except ValueError:
        port = None
    if not url or port in (None, RESERVED_PORT):
        out.append(f"SUP_URL = {url or '(unset)'}: the client would reach the live server")
    for v in ("WMUX_PIPE", "WMUX_PIPE_TOKEN", "WMUX_SURFACE_ID"):
        if env.get(v):
            out.append(f"{v} set: access to the real wmux")
    if not env.get("WMUX_CLI") or not root or not under(env["WMUX_CLI"], root):
        out.append("WMUX_CLI does not point at the fake wmux of the test root")
    return out


def assert_isolated(env, root):
    problems = isolation_problems(env, root)
    if problems:
        raise IsolationError("tests refused, incomplete isolation: " + "; ".join(problems))


def safe_rmtree(root):
    """Delete a test root, and nothing else: refusal (IsolationError) before any deletion otherwise."""
    problems = check_root(root)
    if problems:
        raise IsolationError("deletion refused: " + "; ".join(problems))
    shutil.rmtree(root)


def make_env(root):
    """Variables of an isolated test environment under `root` (a dict, no side effect)."""
    root = Path(root)
    return {
        "SUP_TEST": "1",
        "SUP_STATE_ROOT": str(root / "run"),
        "SUP_RELAY_STATE": str(root / "relay-supervision"),
        "SUP_BACKUP_DIR": str(root / "backups"),
        "SUP_CHECK_LOCK": str(root / "depot" / ".claude" / "check.lock"),
        "SUP_VAULT_DIR": str(root / "vault"),
        "SUP_LOG_DIR": str(root / "log"),
        "SUP_URL": "http://127.0.0.1:9",  # closed port: a forgotten call fails instead of reaching 8765
        "SUP_NOTIFY": "0",
        "SUP_BACKEND": "wmux",  # the root's fake client plays wmux; the no-wmux tests set none themselves
        "SUP_PROJECT": PROJECT,
        "SUP_CONFIG_FILE": str(root / "config.json"),
        "WMUX_CLI": str(root / "fake-wmux.js"),
        "WMUX_NODE": "node",
    }


class IsolatedTestCase(unittest.TestCase):
    """Base class: one temp root per test class, safety checks verified BEFORE any test."""

    root = None
    _saved = None

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        root = Path(tempfile.mkdtemp(prefix=PREFIX))
        cls._saved = {k: os.environ.get(k) for k in TOUCHED}
        try:
            for k in TOUCHED:
                os.environ.pop(k, None)
            os.environ.update(make_env(root))
            (root / "fake-wmux.js").write_text(FAKE_WMUX, encoding="utf-8")
            (root / "depot" / ".claude").mkdir(parents=True)
            (root / "config.json").write_text(json.dumps({"path": str(root / "depot")}), encoding="utf-8")
            assert_isolated(os.environ, root)
        except BaseException:
            cls._restore()
            safe_rmtree(root)
            raise
        cls.root = root
        import backend
        import config
        config._cache.clear()  # a config loaded by another class would point at its old root
        backend.reset()

    @classmethod
    def tearDownClass(cls):
        cls._restore()
        if "backend" in sys.modules:
            sys.modules["backend"].reset()
        srv = sys.modules.get("server")  # SQLite database of the test server: closed before deletion (Windows)
        if srv is not None and getattr(srv, "STORE", None) is not None:
            srv.STORE.close()
            srv.STORE = None
        if cls.root:
            safe_rmtree(cls.root)
            cls.root = None
        super().tearDownClass()

    @classmethod
    def _restore(cls):
        for k, v in (cls._saved or {}).items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def setUp(self):
        assert_isolated(os.environ, self.root)  # before every test too: a previous test may have touched the environment

    def wmux_calls(self):
        f = self.root / "wmux-calls.log"
        return [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines()] if f.exists() else []
