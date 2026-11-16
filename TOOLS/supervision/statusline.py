"""Claude Code statusline: shows model, context and quota, and forwards them to the board.

settings.json:  "statusLine": {"type": "command", "command": "python \"<path>/statusline.py\""}
Sending to the servers is best-effort (0.3 s max each): server stopped = statusline unchanged. The project's server is
found through SUP_URL, else SUP_PROJECT / SUP_AGENT (file run/<project>/server.port), else every running server.
Without it, the board gets no quota figures (the quota brake then never tightens).
"""
import json
import os
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")
try:
    d = json.loads(sys.stdin.buffer.read().decode("utf-8") or "{}")
except ValueError:
    d = {}

d["_surface"] = os.environ.get("WMUX_SURFACE_ID", "")
d["_agent"] = os.environ.get("SUP_AGENT", "")  # without wmux: <project>/<X>


def targets():
    """Supervision servers to notify: SUP_URL, else the agent's project server (run/<project>/server.port, written when
    the server starts), else every running one (the quota belongs to the account: it holds for every board),
    else the default port 8765. Up to 3 servers, 0.3 s each at most."""
    if os.environ.get("SUP_URL"):
        return [os.environ["SUP_URL"]]
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        import config
        project = os.environ.get("SUP_PROJECT") or d["_agent"].split("/")[0]
        files = [config.state_dir(project) / "server.port"] if project else sorted(
            config.state_dir("x").parent.glob("*/server.port"), key=lambda f: f.stat().st_mtime, reverse=True)
        urls = []
        for f in files[:3]:
            if f.is_file():
                urls.append(f"http://127.0.0.1:{int(f.read_text(encoding='utf-8').strip())}")
        if urls:
            return urls
    except Exception:  # noqa: BLE001 — a statusline must never fail
        pass
    return ["http://127.0.0.1:8765"]


for base in targets():
    try:
        req = urllib.request.Request(base + "/api/statusline", json.dumps(d).encode("utf-8"),
                                     {"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=0.3).close()
    except Exception:  # noqa: BLE001
        pass

parts = [(d.get("model") or {}).get("display_name", "")]
ctx = (d.get("context_window") or {}).get("used_percentage")
if ctx is not None:
    parts.append(f"ctx {ctx:.0f} %")
rl = d.get("rate_limits") or {}
for k, label in (("five_hour", "5 h"), ("seven_day", "7 d")):
    if (rl.get(k) or {}).get("used_percentage") is not None:
        parts.append(f"{label} {rl[k]['used_percentage']:.0f} %")
print(" · ".join(p for p in parts if p))
