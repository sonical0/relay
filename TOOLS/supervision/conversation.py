"""Supervisor conversation, read from the Claude Code transcript (JSONL), for the board's supervisor tab.

Incremental reading: the position in the file is kept and only appended lines are read.
Only what reads well is kept: user messages, supervisor answers, manager messages, questions asked (with their
options) and their answers, and one short line per action (tool).
Thinking blocks, tool results and subagents (isSidechain) are dropped.
"""
import json
import re
import threading
from pathlib import Path

PROJECTS = Path.home() / ".claude" / "projects"
REMINDER = re.compile(r"<system-reminder[^>]*>.*?</system-reminder[^>]*>", re.S)
PEER = re.compile(r'<cross-session-message[^>]*from-name="([^"]*)"[^>]*>(.*?)</cross-session-message>', re.S)
SUBAGENT = re.compile(r'<agent-message[^>]*from="([^"]*)"[^>]*>(.*?)</agent-message>', re.S)
# Framing added by the harness around cross-session messages: it is not the user speaking.
FRAME = re.compile(
    r"(Another Claude session sent a message:?"
    r"|(?:This came from another Claude session|That \"other Claude session\" is an agent).*?permission laundering\.?"
    r"(?:\s*After completing your current task[^\n]*)?"
    r"|\[Subagent hand-back\][^\n]*(?:\n[^\n]*?(?:report follows|carry no user authority)[^\n]*)*)", re.S)
NOISE = re.compile(r"<(task-notification|local-command-caveat|command-name|command-message|command-args|"
                   r"local-command-stdout|bash-input|bash-stdout|bash-stderr)[^>]*>.*?</\1>", re.S)
MAX_ITEMS = 3000


def _short(text, n=160):
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _tool_line(name, inp):
    """One readable line per action, without the detail (command, file content…)."""
    inp = inp or {}
    if name in ("Bash", "PowerShell"):
        return inp.get("description") or _short(inp.get("command", ""), 100)
    if name in ("Read", "Write", "Edit"):
        return Path(str(inp.get("file_path", ""))).name
    if name == "SendMessage":
        return f"→ {inp.get('to', '?')}: {inp.get('summary') or _short(inp.get('message', ''), 90)}"
    if name in ("Grep", "Glob"):
        return _short(inp.get("pattern", ""), 90)
    if name == "Agent":
        return inp.get("description", "")
    return _short(json.dumps(inp, ensure_ascii=False), 100)


class Conversation:
    def __init__(self):
        self.path = None
        self.offset = 0
        self.items = []
        self.tools = {}        # tool_use_id -> index of the "question" item
        # Item number, never reset: neither by the MAX_ITEMS cut nor by a new transcript (fresh restart = new
        # session). The client asks for what follows its last number; numbering that restarted from 0 left it
        # waiting for numbers already gone.
        self.seq = 0
        self.lock = threading.Lock()

    def attach(self, session_id):
        """Find the transcript of the supervisor's session (id given by wmux)."""
        if not session_id:
            return
        if self.path and self.path.stem == session_id:
            return
        found = next(PROJECTS.glob(f"*/{session_id}.jsonl"), None)
        if found:
            with self.lock:
                self.path, self.offset, self.items, self.tools = found, 0, [], {}

    def _add(self, item):
        item["i"] = self.seq
        self.seq += 1
        self.items.append(item)

    def _user(self, o, content):
        ts = o.get("timestamp", "")
        if isinstance(content, list):
            for b in content:
                if b.get("type") == "tool_result":
                    q = self.tools.get(b.get("tool_use_id"))
                    if q is not None:  # answer to a question asked to the user
                        res = b.get("content")
                        txt = res if isinstance(res, str) else " ".join(x.get("text", "") for x in res or [] if isinstance(x, dict))
                        # '…"Question?"="Answer", "Question 2?"="Answer 2"…' -> one answer per line
                        pairs = re.findall(r'"([^"]+)"="([^"]*)"', txt)
                        self.items[q]["answer"] = "\n".join(a for _, a in pairs) if pairs else _short(txt, 1200)
                elif b.get("type") == "text":
                    self._user_text(ts, b.get("text", ""))
            return
        self._user_text(ts, content or "")

    def _user_text(self, ts, text):
        for name, body in PEER.findall(text):
            self._add({"role": "peer", "who": name, "text": FRAME.sub("", body).strip(), "t": ts})
        for name, body in SUBAGENT.findall(text):
            self._add({"role": "peer", "who": "subagent", "text": FRAME.sub("", body).strip(), "t": ts})
        text = SUBAGENT.sub("", PEER.sub("", text))
        text = FRAME.sub("", NOISE.sub("", REMINDER.sub("", text))).strip()
        if text and not text.startswith(("This is how Claude Code surfaces", "[SYSTEM NOTIFICATION")):
            self._add({"role": "user", "text": text, "t": ts})

    def _assistant(self, o, content):
        ts = o.get("timestamp", "")
        for b in content if isinstance(content, list) else []:
            k = b.get("type")
            if k == "text" and b.get("text", "").strip():
                self._add({"role": "assistant", "text": b["text"], "t": ts})
            elif k == "tool_use":
                name, inp = b.get("name", ""), b.get("input") or {}
                if name == "AskUserQuestion":
                    self._add({"role": "question", "questions": inp.get("questions", []), "t": ts, "answer": None})
                    self.tools[b.get("id")] = len(self.items) - 1
                else:
                    self._add({"role": "tool", "name": name, "text": _tool_line(name, inp), "t": ts})

    def refresh(self):
        if not self.path:
            return
        with self.lock:
            try:
                with open(self.path, "rb") as f:
                    f.seek(self.offset)
                    chunk = f.read()
            except OSError:
                return
            end = chunk.rfind(b"\n")
            if end < 0:
                return
            self.offset += end + 1
            for raw in chunk[: end + 1].splitlines():
                try:
                    o = json.loads(raw)
                except ValueError:
                    continue
                if o.get("isSidechain") or o.get("type") not in ("user", "assistant"):
                    continue
                msg = o.get("message") or {}
                (self._user if o["type"] == "user" else self._assistant)(o, msg.get("content"))
            if len(self.items) > MAX_ITEMS:  # keep the end; indexes stay stable for the client
                drop = len(self.items) - MAX_ITEMS
                self.items = self.items[drop:]
                self.tools = {k: v - drop for k, v in self.tools.items() if v >= drop}

    def since(self, i, limit=MAX_ITEMS):
        self.refresh()
        with self.lock:
            if not self.items:
                return {"items": [], "next": 0, "path": str(self.path or "")}
            first = self.items[0]["i"]
            last = self.items[-1]["i"] + 1
            if i > last:  # client ahead: the server restarted since, send the end
                i = -1
            start = max(i, first) if i >= 0 else max(first, last - limit)
            items = [x for x in self.items if x["i"] >= start]
            # A question already listed may get its answer later: recent questions are sent again.
            pending = [x for x in self.items[-60:] if x["role"] == "question" and x["i"] < start]
            return {"items": pending + items, "next": last, "path": self.path.name}
