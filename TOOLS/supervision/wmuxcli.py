"""Call the wmux client, shared by server.py and launch.py.

The wmux client is a Node script: it is called without going through wmux.cmd, hence without cmd.exe
(which would interpret & | < > ^ % in the text typed into a pane).
Variables: WMUX_BIN (path of the installed wmux), WMUX_CLI (wmux.js), WMUX_NODE.
"""
import os
import subprocess
from pathlib import Path

NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0  # no console window per call

# Default: per-user wmux installation, specific to each host (the account name differs from one host to another).
_LOCAL = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
def _default_wmux():
    """WMUX_BIN, else the per-user installation (Windows), else a `wmux` on the PATH (Linux, macOS)."""
    if os.environ.get("WMUX_BIN"):
        return os.environ["WMUX_BIN"]
    local = _LOCAL / "Programs" / "wmux" / "resources" / "cli-bin" / "wmux"
    if local.exists():
        return str(local)
    import shutil
    found = shutil.which("wmux")
    return os.path.realpath(found) if found else str(local)


WMUX = _default_wmux()


def wmux_cmd():
    env = dict(os.environ)
    if env.get("WMUX_NODE_ELECTRON"):
        env["ELECTRON_RUN_AS_NODE"] = "1"
    cli = env.get("WMUX_CLI") or str(Path(WMUX).parent.parent / "cli" / "wmux.js")
    return [env.get("WMUX_NODE") or "node", cli], env


def wmux(*args, out=False):
    """True/False return code, or the standard output with out=True ("" on failure)."""
    try:
        base, env = wmux_cmd()
        r = subprocess.run([*base, *args], capture_output=True, text=True, timeout=10, encoding="utf-8",
                           errors="replace", env=env, creationflags=NO_WINDOW)
        if r.returncode != 0:
            print("wmux", args[0], "→", r.returncode, (r.stderr or r.stdout)[:200], flush=True)
        if out:
            return r.stdout if r.returncode == 0 else ""
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return "" if out else False
