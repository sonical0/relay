"""Claude Code statusline: shows model, context and quota, and forwards them to the board.

settings.json:  "statusLine": {"type": "command", "command": "python \"<path>/statusline.py\""}
Sending to the servers is best-effort (0.3 s max each): server stopped = statusline unchanged. The project's server is
found through SUP_URL, else SUP_PROJECT / SUP_AGENT (file run/<project>/server.port), else every running server.
Without it, the board gets no quota figures (the quota brake then never tightens).
Also shows the bar of a long task (TOOLS/progress.mjs, with "~m:ss left") and, past 150 k tokens of context,
a hint to hand over (write the resume card, then /clear).
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
cw = d.get("context_window") or {}
ctx = cw.get("used_percentage")
if ctx is not None:
    parts.append(f"ctx {ctx:.0f} %")


def context_tokens(cw):
    """Tokens in the context: usage of the last call, else percentage × window size."""
    try:
        cu = cw.get("current_usage") or {}
        n = sum(int(cu.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
        if n:
            return n
        if cw.get("used_percentage") is not None and cw.get("context_window_size"):
            return int(float(cw["used_percentage"]) * float(cw["context_window_size"]) / 100)
    except Exception:  # noqa: BLE001
        pass
    return None


# Short sessions: past 150 k, every call re-reads this whole context; writing the resume card then
# /clear costs less than going on.
HAND_OVER = 150_000
_n = context_tokens(cw)
if _n and _n >= HAND_OVER:
    parts.append(f"⚠ {_n // 1000} k: hand over: write resume card, /clear")
rl = d.get("rate_limits") or {}
for k, label in (("five_hour", "5 h"), ("seven_day", "7 d")):
    if (rl.get(k) or {}).get("used_percentage") is not None:
        parts.append(f"{label} {rl[k]['used_percentage']:.0f} %")


def progress():
    """Bar of the long task in progress, written by TOOLS/progress.mjs to <tmp>/relay-progress/<session>.json.
    Ignored after 15 min without an update (task interrupted without `done`)."""
    import tempfile
    import time
    sid = d.get("session_id") or ""
    if not sid.replace("-", "").replace("_", "").isalnum():
        return ""
    try:
        f = os.path.join(tempfile.gettempdir(), "relay-progress", sid + ".json")
        with open(f, encoding="utf-8") as fh:
            p = json.load(fh)
        if time.time() * 1000 - p["ts"] > 15 * 60 * 1000:
            return ""
        k, n = int(p["k"]), int(p["n"])
        full = max(0, min(10, round(10 * k / n)))
        bar = "█" * full + "░" * (10 - full)
        # Remaining time: the caller's (remaining_s, usual duration), else proportional to the time
        # elapsed since `start`, only after 1 step and 5 s.
        left = p.get("remaining_s")
        if left is None and p.get("start") and 1 <= k < n:
            elapsed = (time.time() * 1000 - p["start"]) / 1000
            if elapsed >= 5:
                left = elapsed * (n - k) / k
        eta = ""
        if left is not None and k < n:
            s = max(0, round(left))
            eta = f" · ~{s // 3600} h {(s % 3600) // 60:02d} left" if s >= 3600 else f" · ~{s // 60}:{s % 60:02d} left"
        return f"[{bar}] {k}/{n}{eta}" + (f" · {p['label']}" if p.get("label") else "")
    except Exception:  # noqa: BLE001 — no bar, statusline unchanged
        return ""


parts.append(progress())
print(" · ".join(p for p in parts if p))
