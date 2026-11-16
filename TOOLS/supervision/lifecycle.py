"""Life cycle of the supervision server: log, clean stop, launch checks, restart.

  - setup_log(run_dir): rotating log `run/<project>/server.log` (RotatingFileHandler); logprint() replaces
    the server's `print` (started detached, its prints were lost);
  - STOP + stopper: clean stop (signal, or POST /api/shutdown protected by the token) → the thread loops
    stop (STOP.wait), the HTTP server stops, last write then the store is closed;
  - preflight(cfg, port): wmux reachable by the Node client (wmux backend), brain readable, project repository
    present, port free;
  - port_free(port), wait_port_free(port), request_shutdown(url, token): for `launch.py --restart`.
"""
import logging
import logging.handlers
import os
import signal
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import config

LOG_NAME = "server.log"
LOG_BYTES = 1_000_000
LOG_BACKUPS = 5
STOP = threading.Event()  # set on stop: the server loops exit (STOP.wait(n) instead of time.sleep(n))
LOGGER = logging.getLogger("supervision")
# Words that make a log line a WARNING (the French ones match older messages and user text).
WARN_WORDS = ("error", "failed", "exception", "erreur", "échec")


def setup_log(run_dir, console=True):
    """Rotating log in <run_dir>/server.log (1 MB × 5). Returns the logger. Idempotent."""
    path = config.guard_write(Path(run_dir) / LOG_NAME)
    for h in list(LOGGER.handlers):
        LOGGER.removeHandler(h)
        h.close()
    h = logging.handlers.RotatingFileHandler(path, maxBytes=LOG_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8",
                                             delay=True)
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S"))
    LOGGER.addHandler(h)
    if console:
        LOGGER.addHandler(logging.StreamHandler())
    LOGGER.setLevel(logging.INFO)
    LOGGER.propagate = False
    return LOGGER


def logprint(*args, sep=" ", end="\n", file=None, flush=False):  # noqa: ARG001 — print's signature
    """print replacement for the server: one log line (WARNING level if it reports an error)."""
    msg = sep.join(str(a) for a in args)
    if not LOGGER.handlers:
        return print(msg, end=end, flush=flush)
    low = msg.lower()
    LOGGER.log(logging.WARNING if any(w in low for w in WARN_WORDS) else logging.INFO, msg)


def stopper(httpd, finish, timeout=10):
    """Clean stop function, idempotent: STOP set, HTTP server stopped, then finish() (last write).

    Called from a signal or from an HTTP handler: httpd.shutdown() blocks until serve_forever ends,
    so it runs in a separate thread.
    """
    done = threading.Event()
    once = threading.Lock()

    def run():
        if not once.acquire(blocking=False):
            return
        STOP.set()
        LOGGER.info("stop requested: ending threads")
        try:
            if httpd is not None:
                httpd.shutdown()
        finally:
            try:
                finish()
            except Exception as e:  # noqa: BLE001
                LOGGER.warning("stop: last write failed: %s", e)
            LOGGER.info("server stopped")
            done.set()

    def stop(*_):
        threading.Thread(target=run, daemon=False, name="stop").start()
        return done
    stop.done = done
    return stop


def install_signals(stop):
    """SIGINT, SIGTERM (and SIGBREAK on Windows: Ctrl+Pause, or CTRL_BREAK_EVENT sent to the group) → stop()."""
    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        sig = getattr(signal, name, None)
        if sig is not None:
            try:
                signal.signal(sig, lambda *_: stop())
            except (ValueError, OSError):  # outside the main thread
                pass


def port_free(port, host="127.0.0.1"):
    """True if nothing listens on host:port (connection attempt, nothing sent)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) != 0


def wait_port_free(port, timeout=20, step=0.25):
    end = time.time() + timeout
    while time.time() < end:
        if port_free(port):
            return True
        time.sleep(step)
    return port_free(port)


def request_shutdown(url, token, timeout=5):
    """POST /api/shutdown with the token. Returns (ok, message)."""
    req = urllib.request.Request(url.rstrip("/") + "/api/shutdown", data=b"{}", method="POST",
                                 headers={"Content-Type": "application/json", "X-Sup-Token": token})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return True, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        with e:
            return False, f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}"
    except (urllib.error.URLError, OSError) as e:
        return False, str(e)


def read_token(run_dir):
    try:
        return (Path(run_dir) / ".token").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def wmux_reachable():
    """(ok, detail): the wmux Node client answers `ping` (the same one as server.py and wbrowser.mjs)."""
    from wmuxcli import wmux_cmd
    base, env = wmux_cmd()
    try:
        r = subprocess.run([*base, "ping"], capture_output=True, text=True, timeout=10, encoding="utf-8",
                           errors="replace", env=env, creationflags=config.NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)
    out = (r.stdout or "").strip()
    return r.returncode == 0 and "pong" in out.lower(), out[:120] or (r.stderr or "").strip()[:120]


def preflight(cfg, port, need_wmux=True, wmux_check=wmux_reachable):
    """Launch checks. Returns the list of problems (empty = all good), in plain words."""
    out = []
    src = cfg.get("source", "")
    if "unreadable" in src:
        out.append(f"brain unreadable: {src} (TOOLS/supervision/brain.mjs, relay-brain.yml, js-yaml installed?)")
    repo = Path(cfg.get("path", ""))
    if not repo.is_dir():
        out.append(f"project repository missing: {repo} (project path in the brain)")
    elif not (repo / ".git").exists():
        out.append(f"project repository without .git: {repo}")
    if not port_free(port):
        out.append(f"port {port} already taken (another server? `--restart` restarts the supervision's one)")
    if need_wmux:
        ok, detail = wmux_check()
        if not ok:
            out.append(f"wmux unreachable by the Node client ({detail or 'no answer'}): launch from a wmux terminal, "
                       "or SUP_BACKEND=none")
    return out


def describe(problems):
    return "launch checks:\n" + "\n".join(f"  - {p}" for p in problems)
