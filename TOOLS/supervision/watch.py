"""Supervisor monitor (`sup.py watch`): one line per useful change, continuously.

Designed for Claude Code's Monitor tool: stdout line by line, flushed on every line, nothing repeated while nothing
changes. Motivation: a manager once asked its question only in its own terminal and waited 8 min with no reader.

What is followed (read from /api/state):
  - each manager: status, progress, last line it wrote to the log;
  - question asked through `sup.py ask` (card in blocked);
  - open requests to the integrator;
  - NEW alerts (key code + agent: "silent for 12 min" then 13 min is not a new alert);
  - messages waiting in the supervisor's mailbox (without wmux);
  - with wmux: a manager whose pane is idle and whose last screen line is a question
    (`read-screen` on the registered surface, client resolved by wmuxcli, never the `wmux` on PATH).
Network errors tolerated: one line when the board stops answering, the monitor keeps going.
"""
import json
import sys
import time
import urllib.request

import backend

SCREEN_LINES = 14
# Claude Code interface lines, not answers: frame, prompt, status bar, indicators.
CHROME = ("─", ">", "❯", "⏵", "Sonnet", "Opus", "Haiku", "✻", "※", "?", "│", "╭", "╰")


class Watch:
    """Memory of the lines already printed: `lines(state, screens)` only returns what changed."""

    def __init__(self):
        self.seen = {}

    def _diff(self, current, sticky=()):
        """current: {key: text}. Returns new or changed texts; forgets keys that disappeared (an alert that comes
        back is printed again). Keys in `sticky` are only printed when they appear."""
        out = []
        for key, text in current.items():
            old = self.seen.get(key)
            if old is None or (old != text and key not in sticky):
                out.append(text)
            self.seen[key] = text
        for key in [k for k in self.seen if k not in current and not k.startswith("err")]:
            del self.seen[key]
        return out

    def lines(self, state, screens=None):
        cur, sticky = {}, set()
        last = {}  # last log line of each manager (events: newest first)
        for e in state.get("events") or []:
            who = str(e.get("who") or "")
            if who.startswith("Manager ") and who[8:] not in last:
                last[who[8:]] = e.get("msg") or ""
        managers = state.get("managers") or {}
        for mid, m in sorted(managers.items()):
            msg = last.get(mid, "")
            cur[f"m{mid}"] = (f"[board] {mid} {m.get('status')} {m.get('progress', 0)}%"
                              + (f" · {m['task']}" if m.get("task") else "") + (f" · {msg}" if msg else ""))[:300]
            q = m.get("question") or {}
            if q.get("text"):
                cur[f"q{mid}"] = (f"[question] {mid} is waiting for an answer: {q['text']}"
                                  + (f" [{' / '.join(q.get('choices') or [])}]" if q.get("choices") else ""))[:300]
        for r in (state.get("requests") or {}).values():
            if r.get("status") == "open":
                cur[f"r{r['id']}"] = f"[request] {r['id']} from {r.get('from')} open: {str(r.get('what', ''))[:200]}"
        for a in state.get("alerts") or []:
            key = f"a{a.get('code')}:{a.get('who')}"
            cur[key] = f"[alert {a.get('level', '?')}] {str(a.get('msg', ''))[:250]}"
            sticky.add(key)
        n = (state.get("mailbox_pending") or {}).get("S")
        if n:
            cur["boxS"] = f"[mailbox] {n} message(s) waiting for the supervisor: sup.py inbox S"
        for mid, last_line in (screens or {}).items():
            if last_line and not (managers.get(mid) or {}).get("question"):
                cur[f"s{mid}"] = f"[screen] {mid} idle, waiting for an answer: {last_line[:200]}"
        return self._diff(cur, sticky)

    def error(self, e):
        """One line per board outage, not one per pass; nothing when it comes back."""
        text = f"[board unreachable] {e}"
        if self.seen.get("err") == text:
            return []
        self.seen["err"] = text
        return [text]

    def ok(self):
        self.seen.pop("err", None)


def screen_question(text, busy=None):
    """Last useful line of a screen if it is a question from an idle agent, else ''.

    busy: pane state known to the server (wmux poller); None = infer it from the screen ("… (12s · esc…")."""
    lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
    if busy is None:
        busy = any("…" in l and "(" in l for l in lines)
    if busy:
        return ""
    body = [l for l in lines if not l.startswith(CHROME)]
    last = body[-1] if body else ""
    return last if last.endswith("?") else ""


def read_screens(state):
    """{manager: on-screen question} for managers registered with a surface; {} without wmux."""
    if not backend.enabled():
        return {}
    out = {}
    agents = state.get("agents") or {}
    for mid, m in (state.get("managers") or {}).items():
        sid = m.get("surface")
        if not sid or m.get("stopped"):
            continue
        raw = backend.wmux("read-screen", "--lines", str(SCREEN_LINES), "--surface", sid, out=True)
        try:
            text = json.loads(raw).get("text", "") if raw else ""
        except (ValueError, AttributeError):
            text = raw or ""
        pane = (agents.get(mid) or {}).get("state")
        out[mid] = screen_question(text, None if pane is None else pane == "working")
    return out


def fetch(base, timeout=10):
    with urllib.request.urlopen(base + "/api/state", timeout=timeout) as r:
        return json.load(r)


def run(base, once=False, interval=30, screens=True, out=None):
    """Monitor loop. once: a single pass (tests, quick look)."""
    out = out or sys.stdout
    w = Watch()
    try:
        while True:
            try:
                state = fetch(base)
                w.ok()
                lines = w.lines(state, read_screens(state) if screens else None)
            except (OSError, ValueError) as e:
                lines = w.error(getattr(e, "reason", None) or e)
            for line in lines:
                print(line, file=out, flush=True)
            if once:
                return
            time.sleep(max(5, interval))
    except KeyboardInterrupt:
        return
