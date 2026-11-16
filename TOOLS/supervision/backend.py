"""Supervision backend: without wmux (default) or with wmux (panes, typing into terminals, notifications).

The supervision does not need wmux. Two modes, chosen once when the process starts:

  none : no wmux (the documented default: Linux, macOS, or wmux not wanted). The server and the board work in any
         browser; agents identify themselves with SUP_AGENT=<project>/<X> (resume sheet injected by the
         SessionStart hook, push guard, statusline); what wmux did by typing into terminals goes through a
         MAILBOX (server.mailbox_*) that every `sup.py` call empties and prints.
  wmux : wmux answers `ping` ("pong"). Enhancements: registered panes, messages typed into the terminals, answers
         to prompts from the board, notifications, safe browser tabs.

SUP_BACKEND = auto (default: detection) | wmux | none (forces it: tests and special machines).
Detection: the wmux Node client (wmuxcli.wmux_cmd) answers "pong" within 4 s; otherwise none.
"""
import os
import subprocess
import threading

import config
import wmuxcli

_mode = None
_lock = threading.Lock()


def detect(timeout=4):
    """'wmux' or 'none', from SUP_BACKEND, otherwise a `ping` of the wmux client."""
    forced = (os.environ.get("SUP_BACKEND") or "auto").strip().lower()
    if forced in ("wmux", "none"):
        return forced
    if forced != "auto":
        raise ValueError(f"unknown SUP_BACKEND: {forced} (expected: auto, wmux or none)")
    try:
        base, env = wmuxcli.wmux_cmd()
        r = subprocess.run([*base, "ping"], capture_output=True, text=True, timeout=timeout, env=env,
                           encoding="utf-8", errors="replace", creationflags=config.NO_WINDOW)
        return "wmux" if r.returncode == 0 and "pong" in r.stdout else "none"
    except (OSError, subprocess.TimeoutExpired):
        return "none"


def mode():
    """The process backend, detected once."""
    global _mode
    with _lock:
        if _mode is None:
            _mode = detect()
        return _mode


def reset():
    """Forget the detected backend (tests)."""
    global _mode
    with _lock:
        _mode = None


def enabled():
    return mode() == "wmux"


def notify(text):
    """Notify the user: wmux, otherwise the command in SUP_NOTIFY_CMD (text in the SUP_MSG variable,
    e.g. `notify-send Supervision "$SUP_MSG"`), otherwise nothing (the board's banner stays)."""
    if enabled():
        return wmuxcli.wmux("notify", text)
    cmd = os.environ.get("SUP_NOTIFY_CMD")
    if not cmd:
        return False
    try:
        subprocess.Popen(cmd, shell=True, env=dict(os.environ, SUP_MSG=text), stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, creationflags=config.NO_WINDOW)
        return True
    except OSError:
        return False


def wmux(*args, out=False):
    """Call the wmux client; without wmux, does nothing (False / empty string), silently."""
    if not enabled():
        return "" if out else False
    return wmuxcli.wmux(*args, out=out)
