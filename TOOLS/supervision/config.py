"""Configuration of a supervised project: the project's `supervision:` block in the Relay brain.

    import config
    cfg = config.load("example-app")   # {project, path, repo, base_branch, check, lock, managers, integrator,
                                       #  max_workers, quota_brake, common, port, vault_dir, run_dir, source}

The brain (`relay-brain.yml` at RELAY_ROOT) is read by Node (`brain.mjs`, js-yaml: Python has no YAML in its
stdlib). Missing block or unreadable brain: DEFAULTS apply, and the `source` field says so.
`SUP_CONFIG_FILE` (JSON) overrides the result: reserved for tests.

RELAY_ROOT: the `RELAY_ROOT` environment variable if set, else two levels up from this file.

Runtime state of a project: `TOOLS/supervision/run/<project>/` (state.json, .token, check-baseline.json,
reports/, server.log), generated, never committed.

Write roots ("test isolation"): EVERYTHING the supervision writes goes through one of them. With no variable set,
the real values apply:
  SUP_STATE_ROOT    root of the states, `<root>/<project>/` (default TOOLS/supervision/run/)
  SUP_RUN_DIR       older name, kept: state directory OF THE PROJECT itself (wins over SUP_STATE_ROOT)
  SUP_RELAY_STATE   resume sheets and index.json (default .relay-supervision/); alias SUP_REPRISE_DIR;
                    TOOLS/supervision/session-sheet.js reads the same variable
  SUP_BACKUP_DIR    backups (default BACKUPS/)
  SUP_CHECK_LOCK    check lock (default: the config's `lock`, under the project's repository)
  SUP_VAULT_DIR     output of `sup.py report --vault` (default: PROJECTS/<project>/supervision/)
  SUP_LOG_DIR       per-host session log written by the end-of-day close (default LOG/)
  SUP_INBOX_DIR     proposals written by `sup.py improve` (default INBOX/)
Test mode (SUP_TEST=1, or a state root other than the real one): all these roots must be set, under the system
temp directory and outside the Relay tree; any write outside them is refused (guard_write), and so is the port
reserved for the live server (8765).
"""
import json
import os
import subprocess
import tempfile
from pathlib import Path

# No console window for the server's subprocesses (git, wmux, node) when it runs without a console.
NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

HERE = Path(__file__).resolve().parent
RELAY_ROOT = Path(os.environ["RELAY_ROOT"]).resolve() if os.environ.get("RELAY_ROOT") else HERE.parent.parent
RUN_ROOT = HERE / "run"
DEFAULT_PROJECT = "example-app"
RELAY_STATE_DIR = RELAY_ROOT / ".relay-supervision"  # real resume sheets (reprise.py, session-sheet.js)
BACKUP_ROOT = RELAY_ROOT / "BACKUPS"
LOG_ROOT = RELAY_ROOT / "LOG"
RESERVED_PORT = 8765  # live server: never taken by a test server

# Defaults when the brain says nothing (the brain wins). See brain-example.yml.
DEFAULTS = {
    "base_branch": "dev",
    "check": {"quick": ["typecheck", "lint", "test"], "full": "check"},
    "lock": ".claude/check.lock",
    "managers": ["A", "B", "C", "D"],
    "integrator": "D",
    "max_workers": 4,
    "quota_brake": 85,
    # true: when an exhausted quota window (100 %) resets, the board tells the managers to resume.
    "quota_resume": False,
    # Host load (hostload.py): CPU/memory brakes, limits derived from cores and RAM.
    "cpu_brake": 90, "cpu_release": 75, "cpu_window": 300,   # %; seconds above the threshold before tightening
    "mem_brake": 90, "mem_release": 80, "mem_window": 60,
    "host_limits": True,    # false: no cap on total workers and no npm ci semaphore
    "workers_total": None,  # cap on active workers, all managers together (default: threads // 2)
    "npm_ci_max": None,     # concurrent npm ci (default: 1 under 8 threads, 2 above)
    "setup": "",   # how to set up a worktree (one sentence), repeated in the resume sheets
    # Git-ignored directories that registered worktrees share with the main checkout (junction), e.g.
    # ["node_modules"]: no more `npm ci` per worker; `npm install` is then refused there (shared.py).
    "shared_dirs": [],
    "port": 8765,
    "jira_url": "",  # e.g. https://example.atlassian.net: ticket keys in task names become links on the board
    "common": ["package.json", "package-lock.json", ".env*"],
}

# Overrides set at launch (launch.py --base/--managers/--integrator/--lite): `run/<project>/overrides.json`,
# read by ALL processes (server, sup.py, sheets) so they see the same base. The brain keeps the defaults.
OVERRIDABLE = ("base_branch", "managers", "integrator", "max_workers", "lite")
LITE = {"managers": ["A"], "integrator": "A", "max_workers": 3, "lite": True}  # S acts as manager A, without panes

_cache = {}


# --- Write roots (overridable) and test-mode guards ---------------------------------------------------------

def _env(*names):
    for n in names:
        if os.environ.get(n):
            return Path(os.environ[n])
    return None


def state_dir(project):
    """Project state directory: SUP_RUN_DIR (older name, the directory itself), else SUP_STATE_ROOT/<project>."""
    return _env("SUP_RUN_DIR") or (_env("SUP_STATE_ROOT") or RUN_ROOT) / project


def relay_state_dir():
    """Resume sheets and index.json (read by session-sheet.js, which follows the same variables)."""
    return _env("SUP_RELAY_STATE", "SUP_REPRISE_DIR") or RELAY_STATE_DIR


def overrides_path(project):
    return state_dir(project) / "overrides.json"


def read_overrides(project):
    try:
        ov = json.loads(overrides_path(project).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in ov.items() if k in OVERRIDABLE} if isinstance(ov, dict) else {}


def write_overrides(project, ov):
    """Write (or, if empty, delete) the project's overrides atomically; clears the config cache."""
    path = guard_write(overrides_path(project))
    if not ov:
        path.unlink(missing_ok=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = guard_write(path.with_suffix(".tmp"))
        tmp.write_text(json.dumps(ov, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
        os.replace(tmp, path)
    _cache.pop(project, None)


def inbox_dir():
    """Agent proposals (`INBOX/<host>/`), written by `sup.py improve`."""
    return _env("SUP_INBOX_DIR") or RELAY_ROOT / "INBOX"


def backup_dir():
    return _env("SUP_BACKUP_DIR") or BACKUP_ROOT


def log_dir():
    """Per-host session log (`LOG/<host>.md`), written by the end-of-day close (cloture.py)."""
    return _env("SUP_LOG_DIR") or LOG_ROOT


def _canon(p):
    return os.path.normcase(os.path.realpath(str(p)))


def under(p, root):
    p, root = _canon(p), _canon(root)
    return p == root or p.startswith(root.rstrip(os.sep) + os.sep)


def test_mode():
    """True for a test server or client: SUP_TEST=1, or a state root other than the real one.

    The second condition catches a manual trial started without SUP_TEST: the live server is always started without
    any variable (launch.py sets none), hence on the real root, so no false positive is possible.
    """
    if os.environ.get("SUP_TEST") == "1":
        return True
    d = _env("SUP_RUN_DIR") or _env("SUP_STATE_ROOT")
    return d is not None and not under(d, RUN_ROOT)


# Variables required in test mode (the first name is canonical, the next ones are aliases).
ISOLATION_VARS = (("SUP_STATE_ROOT", "SUP_RUN_DIR"), ("SUP_RELAY_STATE", "SUP_REPRISE_DIR"),
                  ("SUP_BACKUP_DIR",), ("SUP_CHECK_LOCK",), ("SUP_VAULT_DIR",))


def write_roots():
    """Effective write roots, from the environment (real values otherwise)."""
    roots = {"SUP_STATE_ROOT": _env("SUP_RUN_DIR", "SUP_STATE_ROOT") or RUN_ROOT,
             "SUP_RELAY_STATE": relay_state_dir(), "SUP_BACKUP_DIR": backup_dir()}
    for n in ("SUP_CHECK_LOCK", "SUP_VAULT_DIR", "SUP_LOG_DIR", "SUP_INBOX_DIR"):
        if _env(n):
            roots[n] = _env(n)
    return roots


def _bad_root(p):
    if under(p, RELAY_ROOT):
        return "inside the Relay tree (real state)"
    if not under(p, tempfile.gettempdir()):
        return "outside the system temp directory"
    return None


def isolation_errors():
    """What prevents a test from starting without risk for the real state (empty list = isolated)."""
    errs = [f"{names[0]} not set" for names in ISOLATION_VARS if not _env(*names)]
    errs += [f"{n} = {p}: {why}" for n, p in write_roots().items() if (why := _bad_root(p))]
    return errs


def check_isolation():
    """In test mode, refuse to go on if a root is missing or points at the real state."""
    errs = isolation_errors() if test_mode() else []
    if errs:
        raise RuntimeError("test mode without full isolation: " + "; ".join(errs))


def guard_write(path):
    """Return `path`; in test mode, refuse any write outside the test roots (or into the real state)."""
    if not test_mode():
        return Path(path)
    why = _bad_root(path)
    if not why and not any(under(path, r) for r in write_roots().values() if not _bad_root(r)):
        why = "outside the test roots"
    if why:
        raise PermissionError(f"write refused in test mode: {path} ({why})")
    return Path(path)


def check_port(port):
    """In test mode, the live server's port is forbidden (neither listened on nor called)."""
    if test_mode() and int(port) == RESERVED_PORT:
        raise RuntimeError(f"test mode: port {RESERVED_PORT} is reserved for the live server")
    return int(port)


def step_label(step):
    """Label of a check step: the npm script name, or the explicit command (argument list) joined."""
    return step if isinstance(step, str) else " ".join(str(a) for a in step)


def _host():
    import socket
    return os.environ.get("RELAY_HOST") or socket.gethostname()


def _project_path(info):
    """Path of the clone on this host: `paths.<host>`, else `path`; an absolute `path` missing here is looked for
    in ~/Documents/GitHub/ then ~/Documents/Git/, same name."""
    info = info or {}
    own = (info.get("paths") or {}).get(_host())
    if own:
        return own
    p = info.get("path")
    if not p or "{{" in p or not Path(p).is_absolute() or Path(p).exists():
        return p
    for d in ("GitHub", "Git"):
        guess = Path.home() / "Documents" / d / Path(p).name
        if guess.exists():
            return str(guess)
    return p


def _brain_project(project):
    try:
        r = subprocess.run(["node", str(HERE / "brain.mjs"), str(RELAY_ROOT), project, _host()],
                           capture_output=True, text=True, encoding="utf-8", timeout=20, creationflags=NO_WINDOW)
        return json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else None, r.stderr.strip()[:300]
    except (OSError, ValueError, subprocess.TimeoutExpired) as e:
        return None, str(e)


def load(project=None):
    project = (project or os.environ.get("SUP_PROJECT") or DEFAULT_PROJECT).strip()
    if project in _cache:
        return _cache[project]
    if not project.replace("-", "").replace("_", "").isalnum():
        raise ValueError(f"invalid project name: {project}")
    info, err = _brain_project(project)
    sup = (info or {}).get("supervision") or {}
    cfg = {k: (json.loads(json.dumps(v)) if isinstance(v, (list, dict)) else v) for k, v in DEFAULTS.items()}
    for k in DEFAULTS:
        if sup.get(k) is not None:
            cfg[k] = sup[k]
    ov = read_overrides(project)
    for k, v in ov.items():
        cfg[k] = v
    check = dict(DEFAULTS["check"])
    check.update(sup.get("check") or {})
    cfg["check"] = check
    path = _project_path(info) or str(RELAY_ROOT / "PROJECTS" / project)
    vault_docs = (info or {}).get("vault_docs")
    cfg.update({
        "project": project,
        "overrides": ov,
        "lite": bool(ov.get("lite")),
        "path": str(Path(path)),
        "repo": (info or {}).get("repo"),
        "vault_dir": str(_env("SUP_VAULT_DIR") or (RELAY_ROOT / Path(vault_docs).parent if vault_docs
                         else RELAY_ROOT / "PROJECTS" / project / "supervision")),
        "run_dir": str(state_dir(project)),
        "source": "brain" if sup else ("defaults (no supervision block)" if info
                                       else f"defaults (brain unreadable: {err or 'project missing'})"),
    })
    if isinstance(cfg["managers"], str):  # "A,D" given by hand
        cfg["managers"] = cfg["managers"].replace(",", " ").split()
    cfg["managers"] = [str(m).upper() for m in cfg["managers"]]
    cfg["integrator"] = str(cfg["integrator"] or "").upper()
    cfg["port"] = int(cfg["port"])
    cfg["max_workers"] = int(cfg["max_workers"])
    q = float(cfg["quota_brake"])
    cfg["quota_brake"] = int(q) if q.is_integer() else q
    for k in ("cpu_brake", "cpu_release", "cpu_window", "mem_brake", "mem_release", "mem_window"):
        cfg[k] = int(cfg[k])
    for k in ("workers_total", "npm_ci_max"):
        cfg[k] = int(cfg[k]) if cfg[k] else None
    cfg["host_limits"] = cfg["host_limits"] not in (False, "false", "False", 0)
    cfg["quota_resume"] = cfg["quota_resume"] in (True, "true", "True", 1)
    lock = Path(cfg["lock"])
    cfg["lock"] = str(lock if lock.is_absolute() else Path(cfg["path"]) / lock)
    if os.environ.get("SUP_CHECK_LOCK"):
        cfg["lock"] = os.environ["SUP_CHECK_LOCK"]
    if os.environ.get("SUP_CONFIG_FILE"):  # tests
        cfg.update(json.loads(Path(os.environ["SUP_CONFIG_FILE"]).read_text(encoding="utf-8")))
    _cache[project] = cfg
    return cfg


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(load(sys.argv[1] if len(sys.argv) > 1 else None), ensure_ascii=False, indent=2))
