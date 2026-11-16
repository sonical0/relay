"""Supervision board for the managers (stdlib only).

Launch:  python TOOLS/supervision/server.py [--project example-app] [--port N]   (or: npm run supervision -- <project>)
Configuration: the project's `supervision:` block in the brain (config.py); port from the config (8765 by default).
Persistent state: SQLite database TOOLS/supervision/run/<project>/supervision.db, through store.py ONLY (WAL mode,
a single writer: this server; `events` is append-only). On first start, an existing state.json is imported
then renamed state.json.bak (store.Store.open). `/api/state` keeps the shape of the old state.json.
Overridable write roots (SUP_STATE_ROOT, SUP_RELAY_STATE, SUP_BACKUP_DIR, SUP_CHECK_LOCK…): config.py.
In test mode (SUP_TEST=1 or a non-real state root), the server refuses to start without full isolation,
refuses port 8765 and any write outside its test roots.

API:
  GET  /                 board page (carries the command token)
  GET  /api/state        full state + computed alerts
  GET  /api/island       short, versioned summary (schema 1) for an external app: island.py
  POST /api/island/answer  one choice for the open question; requires X-Island-Token (run/<project>/island.token)
  POST /api/report       declarations from the managers and the supervisor (see sup.py)
  POST /api/statusline   Claude Code statusline JSON (quota, context) — statusline.py
  POST /api/command      arbitration buttons; requires the X-Sup-Token header (action `review`: "Review" button)
  POST /api/shutdown     clean stop (launch.py --restart); requires X-Sup-Token. Log: run/<project>/server.log
Pre-review of branches in review (prereview.py): `prereview` field added to the manager and to the merge queue entry.

Controls:
  - scope: a manager may only declare files within its `scope`;
    the common files (COMMON) are reserved to the integrator manager;
  - reality: every SCAN_SECONDS, `git diff` + `git status` of each declared
    worktree, compared with the declarations, the scopes and across managers.
"""
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows console in cp1252: never crash on a "→"
sys.stderr.reconfigure(encoding="utf-8", errors="replace")
import argparse
import fnmatch
import re
import json
import os
import secrets
import subprocess
import threading

import sysstats
import time
import urllib.parse

import cloture  # end-of-day close, evening reminder
import config
import conversation
import island  # GET /api/island: versioned summary for an external app
import hostload  # host load: CPU/memory brakes, worker cap, npm ci semaphore
import lifecycle  # rotating log, clean stop
import objectif  # current goal: progress and estimated remaining time (board, S's sheet)
import metrics  # history and metrics (read-only)
import prereview  # deterministic pre-review of branches in review
import reprise
import signals  # structured error signals (events kind=signal)
import store
import backend  # wmux detected, or none: mailbox instead of typing into panes
from backend import notify, wmux  # without wmux: wmux does nothing; notify -> SUP_NOTIFY_CMD or nothing
from wmuxcli import WMUX, wmux_cmd  # noqa: F401 — re-exported for older callers
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
PAGE = HERE / "dashboard.html"
# Supervised project: set by configure() at startup (brain config). Values below are defaults.
CFG = {}
PROJECT = config.DEFAULT_PROJECT
RUN_DIR = config.state_dir(PROJECT)
STATE_FILE = RUN_DIR / "state.json"  # old format: only read by the migration
STORE = None  # the project's store.Store, opened by configure()
BASE_BRANCH = "dev"
INTEGRATOR = "D"
PORT = 8765
MAX_WORKERS = 4
STALE_SECONDS = 15 * 60
CHECK_FRESH_SECONDS = 2 * 3600  # an older recorded green check no longer proves the delivery
CHECK_RED_MAX = 3       # consecutive red checks of a manager before the "check_red" signal
SCAN_SECONDS = 20
HISTORY_MAX = 500
# Supervisor pane: registered on the board (sup.py assign S --surface …), else SUP_SURFACE.
SUPERVISOR_SURFACE_ENV = os.environ.get("SUP_SURFACE", "")
# Shared lock of `npm run check`: a directory (mkdir/rmdir), with an `owner` file naming the holder.
CHECK_LOCK = Path(os.environ.get("SUP_CHECK_LOCK") or config.RELAY_ROOT / "PROJECTS" / config.DEFAULT_PROJECT / ".claude" / "check.lock")
LOCK_STALE_SECONDS = 15 * 60
QUOTA_BRAKE = 85        # % of the 5-hour window above which the brake engages (config: quota_brake)
QUOTA_RESUME = False    # tell the managers to resume when an exhausted window resets (config: quota_resume)
QUOTA_LABELS = {"five_hour": "5 h", "seven_day": "7 d"}
BRAKED_WORKERS = 2      # active workers per manager when a brake is tight (quota, CPU or memory)
HOST = {}               # hostload.profile() of the host, set by configure()
BRAKES = {}             # hostload.brakes(): {"cpu": Brake, "mem": Brake}, maintained by watcher()
WORKERS_TOTAL = None    # cap on active workers, all managers together (None: no cap)
WATCH_SECONDS = 5
PUSH_OK_SECONDS = 30 * 60  # lifetime of an unused push go-ahead
NOTIFY = os.environ.get("SUP_NOTIFY", "1") != "0"
# Common files: only the integrator writes them, the others send it a request (config: common).
COMMON = list(config.DEFAULTS["common"])
STATUSES = {"idle", "working", "review", "blocked", "done", "waiting"}
MODELS = ("opus", "sonnet", "haiku")  # a manager may lower a worker's model; the work is still re-checked
def _token(name=".token"):
    """Button token, kept across restarts: an already open page keeps valid buttons.
    `island.token`: token for POST /api/island/answer only (the external app reads it next to server.port)."""
    f = RUN_DIR / name
    try:
        t = f.read_text(encoding="utf-8").strip()
        if len(t) >= 24:
            return t
    except OSError:
        pass
    t = secrets.token_urlsafe(24)
    config.guard_write(f).write_text(t, encoding="utf-8")
    return t


TOKEN = ""  # set by configure()
ISLAND_TOKEN = ""  # same; only grants answering an open question (island.answer)
LOCK = threading.Lock()
# Pane state read from wmux (in memory only): who is waiting for an answer from the user.
AGENTS = {}
AGENTS_LOCK = threading.Lock()
STICKY_ASKS = {}  # question declared to wmux, kept after the end of turn that clears it (agent_poller)
ANSWERED_ASKS = {}  # mid → blockedSince (s) of the question answered from the board (answer)
CONV = conversation.Conversation()  # "Supervisor" tab
DASHBOARD_SURFACE_ENV = os.environ.get("SUP_DASHBOARD_SURFACE", "")
STRAY_BROWSERS = []  # browsers open outside the allowed panes (found by agent_poller)
_STRAY_PREV = set()  # previous reading: a browser is "outside its pane" only on two readings in a row


def confirm_stray(previous, current):
    """Confirmed stray browsers: present in BOTH last readings. At launch, the board tab exists for a few seconds
    before being registered: without this confirmation, it raised a false red alert."""
    return [sf for sf in current if sf in previous]
AGENT_SECONDS = 3
# Screen of a Claude Code prompt: numbered menu ("❯ 1. Yes"), "Do you want…", "Esc to cancel".
PROMPT_RE = re.compile(r"(❯\s*\d+\.|Do you want to|Esc to cancel|\(y/n\)|Voulez-vous|Enter to select)", re.I)
KEYS = {"1", "2", "3", "4", "5", "enter", "escape", "up", "down", "tab", "y", "n"}


def now():
    return int(time.time())


def configure(project=None, run_dir=None):
    """Load the project config (brain) and set the module values. run_dir: override (tests).

    Test mode: raises RuntimeError if isolation is incomplete (config.check_isolation), BEFORE any write.
    """
    global CFG, PROJECT, RUN_DIR, STATE_FILE, CHECK_BASELINE_FILE, CHECK_LOCK, COMMON, MAX_WORKERS
    global QUOTA_BRAKE, QUOTA_RESUME, BASE_BRANCH, INTEGRATOR, PORT, CHECK_STAGES, TOKEN, STORE, HOST, BRAKES
    global WORKERS_TOTAL, ISLAND_TOKEN
    config.check_isolation()
    CFG = config.load(project)
    PROJECT = CFG["project"]
    RUN_DIR = Path(run_dir or CFG["run_dir"])  # CFG["run_dir"] follows SUP_STATE_ROOT / SUP_RUN_DIR
    config.guard_write(RUN_DIR)
    if config.test_mode():  # the config's lock too (SUP_CONFIG_FILE may override it)
        config.guard_write(CFG["lock"])
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE = RUN_DIR / "state.json"
    CHECK_BASELINE_FILE = RUN_DIR / "check-baseline.json"
    CHECK_LOCK = Path(CFG["lock"])
    COMMON = list(CFG["common"])
    MAX_WORKERS = CFG["max_workers"]
    QUOTA_BRAKE = CFG["quota_brake"]
    QUOTA_RESUME = CFG.get("quota_resume", False)
    HOST = hostload.profile(cfg=CFG)
    BRAKES = hostload.brakes(CFG)
    # Tests run on any host: no cap derived from its cores, unless set in the config.
    WORKERS_TOTAL = CFG.get("workers_total") if config.test_mode() else HOST["workers_total"]
    BASE_BRANCH = CFG["base_branch"]
    INTEGRATOR = CFG["integrator"]
    PORT = CFG["port"]
    CHECK_STAGES = [*map(config.step_label, CFG["check"]["quick"]), "build"]
    TOKEN = _token()
    ISLAND_TOKEN = _token("island.token")
    if STORE is not None:  # reconfiguration (tests): a single open connection
        STORE.close()
    STORE = store.Store.open(RUN_DIR, log=lambda m: print(m, flush=True))
    return CFG


def objective_status(state):
    """Computed progress of the current goal (objectif.py), None without a goal; never fails the board."""
    try:
        return objectif.status(state, STORE)
    except Exception as e:  # noqa: BLE001
        print("goal:", e, flush=True)
        return None


def supervisor(state):
    """Supervisor's pane, session and board tab: registered on the board, else environment variables."""
    sv = state.get("supervisor") or {}
    return {"surface": sv.get("surface") or SUPERVISOR_SURFACE_ENV, "session": sv.get("session", ""),
            "dashboard": sv.get("dashboard") or DASHBOARD_SURFACE_ENV}


empty_state = store.empty_state


def load():
    """Copy of the state (shape of the old state.json), read from the database by store.py."""
    s = STORE.load()
    for k, v in empty_state().items():
        s.setdefault(k, v)
    s.setdefault("mailbox", {})  # pending messages per agent (without wmux: replaces typing into panes)
    return s


def save(state, who=""):
    """Write the changes to the database (one transaction; one events row per change). who: author."""
    state["updated"] = now()
    STORE.save(state, actor=who)


def log(state, who, msg, level="info"):
    ev = {"t": now(), "who": who, "msg": msg, "level": level}
    state["events"].insert(0, ev)
    del state["events"][store.EVENTS_VIEW:]
    state.setdefault("_pending", []).append(ev)  # added to the events table on the next save() (never truncated)


def manager(state, mid):
    mid = mid.upper()
    m = state["managers"].setdefault(mid, {"id": mid})
    for k, v in {"session": "", "surface": "", "domain": "", "scope": [], "role": "", "status": "idle",
                 "task": "", "branch": "", "files": [], "worktrees": [], "real_files": [], "progress": 0,
                 "history": [], "push_ok": False, "stopped": False, "workers": {}, "updated": now(),
                 "red_checks": 0, "fresh_n": 0, "fresh_task": ""}.items():
        m.setdefault(k, v)
    return m


def norm(path):
    # Strip leading "./", not every dot: lstrip("./") turned ".env" into "env".
    return re.sub(r"^(\./)+", "", path.replace("\\", "/"))


def matches(path, globs):
    # fnmatch: "*" also crosses "/", so "src/worker/*" covers the whole subtree.
    return any(fnmatch.fnmatchcase(path, g) for g in globs)


def is_common(path):
    return matches(path, COMMON)


def allowed(m, path):
    """None if allowed, else the reason for the refusal."""
    if is_common(path):
        return None if m["role"] == "integrator" else "common file: ask the integrator (sup.py request)"
    if not m["scope"]:
        return "no scope assigned"
    return None if matches(path, m["scope"]) else "out of scope"


def open_criteria(state, m):
    """Unmet completion criteria of the manager's tasks cited by its current task ("T7 …" or "T7, T8").

    A task without criteria does not block (older usage); a framed task is delivered only once its
    criteria are ticked (`sup.py task T7 --pass N`): an agent no longer declares "done" on a hunch."""
    cur = f" {m.get('task') or ''} ".replace(",", " ")
    out = []
    for t in state.get("tasks", {}).values():
        if t.get("manager") not in ("", m["id"]) or f" {t['id']} " not in cur:
            continue
        out += [f"{t['id']} #{i} {c['text']}" for i, c in enumerate(t.get("criteria", []), 1) if not c["passes"]]
    return out


def _prefix(glob):
    """Literal part of a glob, before the first wildcard ("src/a/*" → "src/a/")."""
    cut = min((i for i in (glob.find(c) for c in "*?[") if i >= 0), default=len(glob))
    return glob[:cut]


def scopes_overlap(a, b):
    """Can two scope globs cover the same file? Conservative test on the literal prefixes."""
    pa, pb = _prefix(a), _prefix(b)
    return pa.startswith(pb) or pb.startswith(pa)


def host_braked():
    """'CPU', 'memory' or '': the host brake that is tight (CPU first)."""
    return next((label for key, label in (("cpu", "CPU"), ("mem", "memory")) if key in BRAKES and BRAKES[key].on), "")


def worker_limit(state):
    return BRAKED_WORKERS if state.get("braked") or host_braked() else MAX_WORKERS


def is_active(w):
    return w["status"] not in ("done", "idle")


CHECK_STAGES = ["typecheck", "lint", "test", "build"]  # order of `npm run check` (config: check.quick + build)
CHECK_BASELINE_FILE = RUN_DIR / "check-baseline.json"  # number of tests of the last full check


def check_progress(log_path):
    """Progress of an `npm run check` from its log: current step and tests passed (✔ / ✖)."""
    try:
        text = Path(log_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    stage = ""
    for line in text.splitlines():
        if not line.startswith("> "):
            continue
        # npm writes "> package@version script"; sup.py writes "> <project> <step>" (explicit commands included).
        name = line.split()[-1] if "@" in line else line[2:].partition(" ")[2]
        if name in CHECK_STAGES:
            stage = name
    ok = sum(1 for l in text.splitlines() if l.lstrip().startswith("✔"))
    ko = sum(1 for l in text.splitlines()
             if l.lstrip().startswith("✖") and not re.match(r"✖ \d+ problems? \(", l.lstrip()))  # ESLint summary
    done = "Compiled successfully" in text or "Generating static pages" in text
    try:
        base = json.loads(CHECK_BASELINE_FILE.read_text(encoding="utf-8")).get("tests", 0)
    except (OSError, ValueError):
        base = 0
    # Tests are over as soon as the build starts: their total becomes the reference for the next check.
    if stage == "build" and ok + ko and ok + ko != base:
        config.guard_write(CHECK_BASELINE_FILE).write_text(json.dumps({"tests": ok + ko, "t": now()}), encoding="utf-8")
        base = ok + ko
    total = max(base, ok + ko)
    return {"stage": stage, "stage_n": CHECK_STAGES.index(stage) + 1 if stage else 0, "stages": len(CHECK_STAGES),
            "ok": ok, "ko": ko, "total": total or None,
            "pct": round(100 * (ok + ko) / total) if total else None, "built": done}

def check_lock():
    if not CHECK_LOCK.is_dir():
        return None
    owner = CHECK_LOCK / "owner"
    who = owner.read_text(encoding="utf-8", errors="replace").strip()[:80] if owner.is_file() else ""
    # Start = date of the owner file (the directory's date moves as soon as the `log` file is added).
    since = int((owner if owner.is_file() else CHECK_LOCK).stat().st_mtime)
    out = {"owner": who or "unknown (no owner file)", "since": since}
    log = CHECK_LOCK / "log"  # path of the check log, written by `sup.py lock take X --log <file>`
    if log.is_file():
        out["progress"] = check_progress(log.read_text(encoding="utf-8", errors="replace").strip())
    return out


def push_history(m):
    h = m["history"]
    point = [now(), m["progress"], len(m["real_files"])]
    if h and h[-1][1:] == point[1:]:
        return
    h.append(point)
    del h[:-HISTORY_MAX]


def screen_tail(surface, lines=16):
    try:
        text = json.loads(wmux("read-screen", "--lines", str(lines), "--surface", surface, out=True) or "{}").get("text", "")
    except ValueError:
        return ""
    rows = [r.rstrip() for r in text.splitlines()]
    while rows and not rows[-1].strip():
        rows.pop()
    return "\n".join(rows[-lines:])


def sticky_ask(sticky, mid, st, declared, answered):
    """wmux clears the declared question at the end of the turn (blockedReason null, state idle) while the agent is
    still waiting: keep it until an answer or a resumption (working). → declared, reason, choices, since"""
    reason, choices = st.get("blockedReason") or "", st.get("choices") or []
    since = (st.get("blockedSince") or 0) // 1000
    if declared:
        sticky[mid] = {"reason": reason, "choices": choices, "since": since}
    elif mid in sticky and (answered or st.get("state") == "working"):
        sticky.pop(mid)
    elif mid in sticky:
        k = sticky[mid]
        return True, k["reason"], k["choices"], k["since"]
    return declared, reason, choices, since


PERMISSION_RE = re.compile(r"needs your (permission|attention)|waiting for your input", re.I)
STALE_POLLS = 3  # readings in a row without a prompt on screen (AGENT_SECONDS each) before dropping a permission
STALE_SEEN = {}


def stale_permission(seen, mid, reason, prompt_on_screen):
    """A Claude Code permission ("Claude needs your permission") always has its prompt on screen while it is open.
    Still flagged with no visible prompt (auto mode answered, signal never cleared), it used to stay on the board
    forever for an idle agent. A question declared as text by report-agent is not concerned (it has no prompt on
    screen). → True when it is stale."""
    if not PERMISSION_RE.search(reason or "") or prompt_on_screen:
        seen.pop(mid, None)
        return False
    seen[mid] = seen.get(mid, 0) + 1
    return seen[mid] >= STALE_POLLS


def answered_here(answered_asks, mid, since):
    """Has the open question of `mid` (identified by its blockedSince) already been answered from the board?
    The agent only unblocks itself (report-agent --unblocked) after applying the answer — a push, for instance:
    until then, wmux still says "blocked", and the question came back to the board, notification included."""
    return bool(since) and answered_asks.get(mid) == since


def shown_ask(declared, answered, prompt_on_screen, reason, choices):
    """What the card offers. Declared question answered but a prompt still on screen (typically the Claude Code
    permission for the `git push` that follows D's "Yes, push"): show the prompt and its keys, not the stale choices
    of the question, which failed on the second click. → reason, choices"""
    if answered and not declared:
        return ("Prompt on screen (the declared question already has its answer)" if prompt_on_screen else ""), []
    return reason, choices


# --- Agent state from their Claude Code hooks (sup-hook.js) ----------------------------------------------------------
# Each agent launched with SUP_AGENT posts its events (POST /api/hook). As long as it sends some, its state comes from
# them; the screen is only read to show an open prompt, or as a fallback for an agent silent for HOOK_FRESH_SECONDS
# (older manager without the hook, lost hook). Nothing is logged here: it is live state, like AGENTS.
HOOKS = {}                 # agent (A, S, A1…) → {"state", "t", "seen", "event", "tool", "subs", "stop_ts", "since", "reason"}
HOOK_FRESH_SECONDS = 120   # no event for 2 min: the pane reading takes over again (fallback)
HOOK_AFTER_STOP = 1.0      # async hooks: a work event dated less than 1 s after Stop belongs to the finished turn
HOOK_SUB_MAX = 30 * 60     # subagent without SubagentStop for 30 min: forgotten (lost event)
HOOK_REST = ("done", "idle")
HOOK_WORK = ("UserPromptSubmit", "PreToolUse", "PostToolUse", "PostToolUseFailure", "SubagentStart", "SubagentStop")
RUN_ID = ""                # token of the running supervision (run/<project>/run.id), set by serve


RUN_ID_MAX_AGE = 20 * 3600  # a token older than one supervision day is replaced when the server starts


def new_run_id(t=None):
    """Token of the running supervision, in run/<project>/run.id: kept across server restarts (a --restart must not
    cut off the managers already launched), replaced if older than 20 h, removed by `sup.py end`."""
    import uuid
    f = RUN_DIR / "run.id"
    t = time.time() if t is None else t
    try:
        if t - f.stat().st_mtime < RUN_ID_MAX_AGE:
            rid = f.read_text(encoding="utf-8").strip()
            if rid:
                return rid
    except OSError:
        pass
    rid = uuid.uuid4().hex[:12]
    tmp = config.guard_write(RUN_DIR / "run.id.tmp")
    tmp.write_text(rid, encoding="utf-8")
    os.replace(tmp, f)
    return rid


def hook_state(event, tool, kind, h):
    """Hook event → new state (working, waiting, done, idle, blocked, ended), or None if it says nothing."""
    if event == "PermissionRequest" or (event == "PreToolUse" and tool == "AskUserQuestion"):
        return "waiting"
    if event == "Notification":
        return "waiting" if kind == "permission_prompt" else ("done" if kind == "idle_prompt" and not h["subs"] else None)
    if event == "PostToolUse" and tool == "AskUserQuestion":
        return "working"  # answer given
    if event in HOOK_WORK:
        return "working"
    if event == "Stop":
        return "working" if h["subs"] else "done"
    if event == "StopFailure":
        return "blocked"
    if event == "SessionStart":
        return "idle"
    if event == "SessionEnd":
        return "ended"
    return None


def hook_event(body, t=None):
    """Apply a hook event. Returns (agent, state before, state after) if it changes the state, None otherwise.
    ValueError if the agent is not from this project. An event older than the last applied one is ignored (replayed
    spool, async hooks arriving out of order), and so is a hook from another launch (SUP_RUN token)."""
    mid = agent_id(body.get("agent"))
    if not mid:
        raise ValueError("agent not in this project")
    if body.get("run") and RUN_ID and body["run"] != RUN_ID:
        return None
    t = time.time() if t is None else t
    ev, tool, sub = str(body.get("event") or ""), str(body.get("tool") or ""), str(body.get("sub") or "")
    try:
        ts = float(body.get("ts") or t)
    except (TypeError, ValueError):
        ts = t
    h = HOOKS.setdefault(mid, {"state": "", "t": 0.0, "seen": 0.0, "event": "", "tool": "", "subs": {},
                               "stop_ts": 0.0, "since": 0.0, "reason": ""})
    h["seen"] = t
    if ts < h["t"]:
        return None
    if ev in ("SessionStart", "SessionEnd"):
        h["subs"].clear()
    elif ev == "SubagentStart" and sub:
        h["subs"][sub] = ts
    elif ev == "SubagentStop" and sub:
        h["subs"].pop(sub, None)
    for k in [k for k, since in h["subs"].items() if ts - since > HOOK_SUB_MAX]:
        del h["subs"][k]
    if (h["state"] in HOOK_REST and ev in HOOK_WORK and ev != "UserPromptSubmit" and sub not in h["subs"]
            and ts < h["stop_ts"] + HOOK_AFTER_STOP):
        return None  # end of the previous turn, arrived after its Stop
    new = hook_state(ev, tool, str(body.get("kind") or ""), h)
    if ev == "Stop":
        h["stop_ts"] = ts
    if new is None:
        return None
    before = h["state"]
    h.update(t=ts, event=ev, tool=tool)
    if new == before:
        return None
    h["state"], h["since"] = new, ts
    h["reason"] = (("Question (AskUserQuestion)" if tool == "AskUserQuestion" else f"Permission requested: {tool or 'tool'}")
                   if new == "waiting" else "Turn failed (StopFailure)" if new == "blocked" else "")
    return mid, before, new


def hook_live(mid, t=None):
    """Hook state of `mid` if it is authoritative: an event less than HOOK_FRESH_SECONDS old (or, without pane
    reading, the last known one whatever its age). None otherwise."""
    h = HOOKS.get(mid)
    if not h or h["state"] in ("", "ended"):
        return None
    t = time.time() if t is None else t
    if backend.enabled() and t - h["seen"] > HOOK_FRESH_SECONDS:
        return None
    return h


def agents_view():
    """AGENTS (pane reading) completed and corrected by the hook state, without waiting for the next reading."""
    with AGENTS_LOCK:
        out = {k: dict(v) for k, v in AGENTS.items()}
    for mid in list(HOOKS):
        h = hook_live(mid)
        if not h:
            continue
        a = out.setdefault(mid, {"state": "", "reason": "", "choices": [], "since": 0, "waiting": False, "screen": "", "surface": ""})
        a["via"] = "hook"
        a["state"] = {"working": "working", "waiting": "blocked", "blocked": "blocked"}.get(h["state"], "idle")
        if h["state"] == "waiting":
            a["waiting"] = True
            a["reason"] = a.get("reason") or h["reason"]
            a["since"] = a.get("since") or int(h["since"])
        elif not a.get("declared"):  # a declared question (sup.py ask) stays on the board until it is answered
            a["waiting"], a["reason"], a["choices"], a["screen"] = False, "", [], ""
    return out


def replay_spool():
    """Re-read run/<project>/hook-spool.jsonl (events posted while the board did not answer), then delete it."""
    f = RUN_DIR / "hook-spool.jsonl"
    try:
        lines = f.read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0
    rows = []
    for line in lines:
        try:
            rows.append(json.loads(line))
        except ValueError:
            pass
    n = 0
    for r in sorted(rows, key=lambda r: float(r.get("ts") or 0) if isinstance(r, dict) else 0):
        try:
            hook_event(r, t=float(r.get("ts") or 0) or None)
            n += 1
        except (ValueError, TypeError, AttributeError):
            pass
    try:
        f.unlink()
    except OSError:
        pass
    return n


def on_hook(state, mid, before, after):
    """Consequences of a state change through a hook: open prompt → log and notification right away (no more waiting
    for the reading); back to rest → pointer to the mailbox."""
    who = "Supervisor" if mid == "S" else (f"Manager {mid}" if len(mid) == 1 else f"worker {mid}")
    if after == "waiting":
        reason = HOOKS[mid]["reason"]
        log(state, who, f"⏳ waiting for your answer: {reason}", "warn")
        if NOTIFY and signals.AGENT_NOTIFY.get(after) and signals.COOLDOWN.allow((mid, after)):
            notify(f"{who} is waiting for your answer: {reason}")
    elif after == "blocked":
        log(state, who, "Turn ended in failure (StopFailure)", "warn")
    if after in HOOK_REST:
        deliver_pointer(state, mid)


def agent_poller():
    """Read the wmux state of each manager pane; spot those waiting for the user."""
    waiting_before = set()
    while not lifecycle.STOP.wait(AGENT_SECONDS):
        try:
            with LOCK:
                st = load()
                mans = {m["surface"]: m["id"] for m in st["managers"].values() if m.get("surface")}
                sv = supervisor(st)
            SUPERVISOR_SURFACE, DASHBOARD_SURFACE = sv["surface"], sv["dashboard"]
            if SUPERVISOR_SURFACE:
                mans[SUPERVISOR_SURFACE] = "S"  # the supervisor may wait for the user too
            raw = json.loads(wmux("agent-state", out=True) or "{}").get("states", [])
            found = {}
            for st in raw:
                if st.get("surfaceId") == SUPERVISOR_SURFACE:
                    CONV.attach(st.get("sessionId"))  # "Supervisor" tab: transcript of this session
                mid = mans.get(st.get("surfaceId"))
                if not mid:
                    continue
                hv = hook_live(mid)  # agent speaking through its hooks: the screen only serves to show an open prompt
                if hv:
                    screen = screen_tail(st["surfaceId"]) if hv["state"] == "waiting" else ""
                    prompt_on_screen = hv["state"] == "waiting"
                else:
                    screen = screen_tail(st["surfaceId"]) if st.get("state") != "working" or st.get("blockedReason") else ""
                    # Visible prompt: only look at the end of the screen (after the last input line).
                    prompt_on_screen = bool(screen) and bool(PROMPT_RE.search("\n".join(screen.splitlines()[-12:])))
                # A question declared to wmux stays "blocked" until the agent unblocks itself:
                # once answered (answeredAt ≥ blockedSince), it no longer calls for the user.
                answered = (st.get("answeredAt") or 0) >= (st.get("blockedSince") or 1) and st.get("answeredAt")
                if answered_here(ANSWERED_ASKS, mid, (st.get("blockedSince") or 0) // 1000):
                    answered = True
                declared = (st.get("state") == "blocked" or bool(st.get("blockedReason"))) and not answered
                declared, reason, choices, since = sticky_ask(STICKY_ASKS, mid, st, declared, answered)
                if declared and stale_permission(STALE_SEEN, mid, reason, prompt_on_screen):
                    STICKY_ASKS.pop(mid, None)
                    declared, reason, choices = False, "", []
                reason, choices = shown_ask(declared, answered, prompt_on_screen, reason, choices)
                if hv and hv["state"] == "waiting" and not reason:
                    reason = hv["reason"]
                waiting = declared or prompt_on_screen
                state_now = ({"working": "working", "waiting": "blocked", "blocked": "blocked"}.get(hv["state"], "idle")
                             if hv else st.get("state"))
                found[mid] = {"state": state_now, "reason": reason, "declared": bool(declared),
                              "choices": choices, "since": since, "hooked": bool(hv),
                              "waiting": waiting, "screen": screen if waiting else "", "surface": st["surfaceId"]}
            # Browsers outside the allowed panes (a manager's pane, or the board's): usually a `wmux browser …`
            # run without --surface, which creates one more pane.
            stray = []
            try:
                mgr_panes, dash_pane = set(), None
                panes = json.loads(wmux("list-panes", out=True) or "{}").get("panes", [])
                for p in panes:
                    ids = {s["id"] for s in p.get("surfaces", [])}
                    if ids & {sf for sf, who in mans.items() if who != "S"}:  # the supervisor's pane is not one of them
                        mgr_panes.add(p["paneId"])
                    if DASHBOARD_SURFACE and DASHBOARD_SURFACE in ids:
                        dash_pane = p["paneId"]
                for p in panes:
                    if p["paneId"] in mgr_panes or p["paneId"] == dash_pane:
                        continue
                    stray += [s["id"] for s in p.get("surfaces", []) if s.get("type") == "browser"]
            except (ValueError, KeyError):
                pass
            with AGENTS_LOCK:
                AGENTS.clear()
                AGENTS.update(found)
                STRAY_BROWSERS[:] = confirm_stray(_STRAY_PREV, stray)
                _STRAY_PREV.clear()
                _STRAY_PREV.update(stray)
            now_waiting = {mid for mid, a in found.items() if a["waiting"]}
            # A prompt flagged by a hook is already logged and notified on arrival (on_hook): no duplicate.
            hook_waits = {mid for mid, a in found.items() if a.get("hooked") and a["waiting"] and not a.get("declared")}
            for mid in sorted(now_waiting - waiting_before - hook_waits):
                reason = found[mid]["reason"] or "a prompt is waiting for your answer in its pane"
                who ="Supervisor" if mid == "S" else f"Manager {mid}"
                with LOCK:
                    s = load()
                    log(s, who, f"⏳ waiting for your answer: {reason}", "warn")
                    save(s, "panes")
                if NOTIFY and signals.COOLDOWN.allow((mid, "waiting")):
                    notify(f"{who} is waiting for your answer: {reason}")
            waiting_before = now_waiting
        except Exception as e:  # noqa: BLE001 — the pane reading must never die
            print("panes:", e)


def type_into(surface, text):
    """Type a message into an agent's pane, as the user would."""
    return bool(surface) and wmux("send", "--surface", surface, text) and wmux("send-key", "enter", "--surface", surface)


MAILBOX_MAX = 50  # messages kept per agent; beyond, the oldest go
POINTER_REMIND_SECONDS = 10 * 60  # unread pointer: reminded once after 10 min, then reported to S


def mailbox_push(state, target, text):
    seq = int(state.get("mail_seq") or 0) + 1
    state["mail_seq"] = seq
    box = state.setdefault("mailbox", {}).setdefault(target, [])
    box.append({"id": seq, "t": now(), "text": str(text)[:2000]})
    del box[:-MAILBOX_MAX]


def mailbox_pop(state, target):
    """Pending messages for an agent (manager X or S), and empties its mailbox (read: no more pointer or reminder)."""
    box = (state.get("mailbox") or {}).get(target) or []
    if box:
        state["mailbox"][target] = []
    (state.get("pointers") or {}).pop(target, None)
    return box


def surface_of(state, target):
    return supervisor(state)["surface"] if target == "S" else (state["managers"].get(target) or {}).get("surface", "")


def pointer_mode(target):
    """The agent speaks through its hooks (sup-hook.js): it gets a pointer, at rest, instead of text typed at any
    time. An older agent without the hook keeps direct typing (backward compatible during a wave)."""
    h = HOOKS.get(target)
    return bool(h) and h["state"] not in ("", "ended")


def pointer_text(target, n):
    return f"[Board] {n} message(s) for you: run `$S inbox {target}` and apply them."


def deliver_pointer(state, mid, force=False):
    """Type the pointer into `mid`'s pane if it has unread mail AND is at rest (Stop, session start). Never during a
    turn, nor on an open prompt (Enter would pick an option there). Once per batch; `force`: reminder.
    Returns True if a pointer was typed."""
    box = (state.get("mailbox") or {}).get(mid) or []
    h = HOOKS.get(mid)
    if not box or not h or h["state"] not in HOOK_REST:
        return False
    sf = surface_of(state, mid)
    if not (sf and backend.enabled()):
        return False
    ptrs = state.setdefault("pointers", {})
    p = ptrs.get(mid) or {}
    if p.get("n") == len(box) and not force:
        return False  # already pointed for this batch
    if not type_into(sf, pointer_text(mid, len(box))):
        return False
    ptrs[mid] = {"t": now(), "n": len(box), "first": p.get("first") or now(), "reminded": bool(p.get("reminded") or force),
                 "signaled": bool(p.get("signaled"))}
    return True


def pointer_followup(state, t=None):
    """Watcher: late pointer (agent went to rest without us being able to type); batch unread 10 min after the
    pointer → reminder, once; 10 more min → reported to the supervisor. Returns True if the state changed."""
    t = now() if t is None else t
    changed = False
    for mid, box in list((state.get("mailbox") or {}).items()):
        if not box or not pointer_mode(mid):
            continue
        p = (state.get("pointers") or {}).get(mid)
        if not p:
            changed |= deliver_pointer(state, mid)
        elif t - p["t"] >= POINTER_REMIND_SECONDS and not p.get("reminded"):
            changed |= deliver_pointer(state, mid, force=True)
        elif t - p["t"] >= POINTER_REMIND_SECONDS and not p.get("signaled"):
            p["signaled"] = True
            who = "Supervisor" if mid == "S" else f"Manager {mid}"
            log(state, "Board", f"{who} has not read {len(box)} message(s) despite the pointer and its reminder", "warn")
            if mid != "S":
                send_to(state, "S", f"[Board] {who} has not read its {len(box)} message(s) for "
                        f"{(t - p['first']) // 60} min (pointer reminded): check its pane.")
            changed = True
    return changed


def send_to(state, target, text, urgent=False):
    """Message from the board to an agent (manager X or S). Agent with hooks: dropped into its mailbox, and a short
    pointer is typed into its pane when it is at rest; `urgent` keeps immediate typing of the text (stop).
    Agent without hook: typed into its pane with wmux as before. Without wmux, or silent pane: mailbox, which
    its next `sup.py` call displays. Returns 'pointer', 'pane' or 'mailbox'."""
    sf = surface_of(state, target)
    if sf and backend.enabled() and pointer_mode(target) and not urgent:
        mailbox_push(state, target, text)
        deliver_pointer(state, target)
        return "pointer"
    if sf and backend.enabled() and type_into(sf, text):
        return "pane"
    mailbox_push(state, target, text)
    return "mailbox"


def tell_blocked(state, m, why, question=False):
    """Warn the supervisor that a manager goes blocked or asks a question (sup.py ask): the card alone was not
    enough, a question could stay unread for minutes. Returns 'pane' or 'mailbox'."""
    head = f"Question from Manager {m['id']}" if question else f"Manager {m['id']} blocked"
    return send_to(state, "S", f"[Board] {head}: {why or '—'}. It is waiting for your answer "
                   f"(it clears it afterwards with `sup.py ask {m['id']} --done`).")


def requires_wmux(what):
    if not backend.enabled():
        raise ValueError(f"{what}: unavailable without wmux (answer in the agent's terminal)")


def agent_of(body):
    """Agent (manager X or S) issuing a `sup.py` call, from the request body; '' if unknown."""
    kind, ident = body.get("kind"), str(body.get("id") or "")
    if kind in ("manager", "worker", "files", "submit", "check", "note", "request", "remove-worker", "inbox", "ask") and ident:
        return ident[0].upper()
    if kind in ("supervisor", "assign", "task", "merge", "plan", "request-done", "event", "project", "objective"):
        return "S" if body.get("from") in ("superviseur", None, "?") else ""
    return ""


IDEMPOTENT = ("submit", "request")  # resending the same request_id returns the recorded result
RECEIPTS_MAX = 200


def apply(state, r):
    """Apply a /api/report call. `submit` and `request` carrying an already seen `request_id` are not replayed:
    the recorded result is returned (sup.py retry after a lost answer, double click)."""
    rid = str(r.get("request_id") or "")[:64]
    if not rid or r.get("kind") not in IDEMPOTENT:
        return _apply(state, r)
    rec = state.setdefault("receipts", {})
    if rid in rec:
        return rec[rid]["result"]
    result = _apply(state, r)  # a refusal (ValueError) is not recorded: the corrected resend will go through
    rec[rid] = {"t": now(), "kind": r["kind"], "result": result}
    for k in sorted(rec, key=lambda k: rec[k]["t"])[:-RECEIPTS_MAX]:
        del rec[k]
    return result


def _apply(state, r):
    kind = r.get("kind")
    who = r.get("from", "?")
    if kind == "supervisor":  # supervisor: pane, session, board tab (launch.py, sup.py assign S)
        sv = state.setdefault("supervisor", {})
        for k in ("surface", "session", "dashboard"):
            if r.get(k):
                sv[k] = r[k]
        log(state, who, "Supervisor: " + ", ".join(f"{k} {sv[k]}" for k in ("surface", "session", "dashboard") if sv.get(k)))
    elif kind == "project":
        state["project"] = r.get("name", "")
        log(state, who, f"Project: {state['project']}")
    elif kind == "assign":  # supervisor: domain, scope, role, pane
        m = manager(state, r["id"])
        for k in ("domain", "session", "surface", "role"):
            if k in r:
                m[k] = r[k]
        if "scope" in r:
            m["scope"] = [norm(p) for p in r["scope"] if str(p).strip()]  # --scope "" empties the scope ([''] overlapped everything)
        m["updated"] = now()
        log(state, who, f"Manager {m['id']}: domain \"{m['domain']}\", scope {', '.join(m['scope']) or '—'}"
            + (" — integrator of the common files" if m["role"] == "integrator" else ""))
    elif kind == "manager":
        m = manager(state, r["id"])
        if "task" in r and r["task"] != m["task"]:
            m["red_checks"] = 0  # another task: the reds of the previous one no longer count
        for k in ("session", "task", "branch"):
            if k in r:
                m[k] = r[k]
        if "worktree_remove" in r:  # remove a worktree from the card (sup.py manager X --worktree-remove <path>)
            gone = [w for w in m["worktrees"] if same_path(w, r["worktree_remove"])]
            if not gone:
                raise ValueError(f"worktree not on Manager {m['id']}'s card: {r['worktree_remove']} "
                                 f"(worktrees: {', '.join(m['worktrees']) or 'none'})")
            m["worktrees"] = [w for w in m["worktrees"] if w not in gone]
            log(state, who, f"Manager {m['id']}: worktree removed from the card ({gone[0]})")
        if "worktree" in r:
            wt = str(Path(r["worktree"]).resolve())
            if wt not in m["worktrees"]:
                m["worktrees"].append(wt)
        if "progress" in r:
            m["progress"] = max(0, min(100, int(r["progress"])))
        was = m["status"]
        if "status" in r:
            if r["status"] not in STATUSES:
                raise ValueError(f"unknown status: {r['status']}")
            m["status"] = r["status"]
            if m["status"] == "review" and m["branch"] and not config.test_mode():  # test: prereview_now() directly
                threading.Thread(target=prereview_now, args=(m["branch"],), daemon=True).start()
        m["updated"] = now()
        push_history(m)
        if r.get("msg"):
            log(state, f"Manager {m['id']}", r["msg"], r.get("level", "info"))
        if m["status"] == "blocked" and was != "blocked":
            tell_blocked(state, m, r.get("msg") or m["task"])
    elif kind == "ask":  # a manager's question (sup.py ask): to the board and the supervisor; wmux on the sup.py side
        m = manager(state, r["id"])
        if r.get("done"):
            q = m.pop("question", None) or {}
            if m["status"] == "blocked":
                m["status"] = q.get("was") or "working"
            m["updated"] = now()
            log(state, f"Manager {m['id']}", "Question settled" + (f": {q['text']}" if q.get("text") else ""))
            return None
        text = str(r.get("question") or "").strip()[:500]
        if not text:
            raise ValueError("empty question")
        choices = [str(c.get("label") or c.get("id") or "")[:80] for c in (r.get("choices") or []) if isinstance(c, dict)]
        was = (m.get("question") or {}).get("was") or (m["status"] if m["status"] != "blocked" else "working")
        m["question"] = {"text": text, "choices": choices, "t": now(), "was": was}
        m["status"], m["updated"] = "blocked", now()
        push_history(m)
        log(state, f"Manager {m['id']}", f"Question: {text}" + (f" [{' / '.join(choices)}]" if choices else ""), "warn")
        return tell_blocked(state, m, text, question=True)
    elif kind == "worker":
        wid = r["id"].upper()  # e.g. B3
        m = manager(state, wid[0])
        if m["stopped"] and r.get("status") == "working":
            raise ValueError(f"Manager {m['id']} is stopped by the user: no worker starts")
        w = m["workers"].setdefault(wid, {"id": wid, "status": "idle", "task": "", "verified": False, "updated": now()})
        w.setdefault("model", "opus")
        if "model" in r:
            if r["model"] not in MODELS:
                raise ValueError(f"unknown model: {r['model']} (expected: {', '.join(MODELS)})")
            if r["model"] != w["model"] and r["model"] != "opus":
                log(state, f"Manager {m['id']}", f"worker {wid} lowered to {r['model']}")
            w["model"] = r["model"]
        if r.get("status") in STATUSES - {"done", "idle"} and not is_active(w):
            busy = sum(is_active(o) for o in m["workers"].values())
            if busy >= worker_limit(state):
                if not w["task"] and w["status"] == "idle":
                    del m["workers"][wid]
                why = (f"quota brake (5 h ≥ {QUOTA_BRAKE} %)" if state.get("braked")
                       else f"host {host_braked()} brake" if host_braked() else "limit")
                raise ValueError(f"Manager {m['id']} already has {busy} active workers: {why}, max {worker_limit(state)}")
            total = sum(is_active(o) for mm in state["managers"].values() for o in mm["workers"].values())
            if WORKERS_TOTAL and total >= WORKERS_TOTAL:
                if not w["task"] and w["status"] == "idle":
                    del m["workers"][wid]
                raise ValueError(f"{total} active workers on the host (max {WORKERS_TOTAL}: {HOST.get('why', '')}): "
                                 "wait for a worker to finish, or raise `workers_total` in the brain")
        if "task" in r:
            w["task"] = r["task"]
        if "status" in r:
            if r["status"] not in STATUSES:
                raise ValueError(f"unknown status: {r['status']}")
            w["status"] = r["status"]
            w["verified"] = bool(r.get("verified")) if r["status"] == "done" else False
        w["updated"] = now()
        m["updated"] = now()
        if r.get("msg"):
            log(state, f"worker {wid}", r["msg"], r.get("level", "info"))
    elif kind == "files":
        m = manager(state, r["id"])
        add = [norm(p) for p in r.get("add", [])]
        refused = {p: why for p in add if (why := allowed(m, p))}
        if refused:
            msg = "; ".join(f"{p} ({why})" for p, why in refused.items())
            log(state, f"Manager {m['id']}", f"Declaration refused: {msg}", "error")
            save(state, f"Manager {m['id']}")
            raise ValueError(f"refused: {msg}")
        files = set(m["files"]) | set(add)
        files -= {norm(p) for p in r.get("remove", [])}
        m["files"] = [] if r.get("clear") else sorted(files)
        m["updated"] = now()
    elif kind == "task":
        tid = r["id"]
        old = (state["tasks"].get(tid) or {}).get("criteria") or []
        passed = sum(bool(c.get("passes")) for c in old)
        if r.get("criteria_add") and not r.get("criteria_clear") and passed:  # reused id: inherited criteria
            raise ValueError(f"{tid} has {len(old)} criterion/criteria from a previous task ({passed} ticked): "
                             "run again with --clear-criteria")
        t = state["tasks"].setdefault(tid, {"id": tid, "title": "", "manager": "", "status": "todo", "note": ""})
        for k in ("title", "manager", "status", "note"):
            if k in r:
                t[k] = r[k]
        if "brief" in r:  # framing: goal, files, deliverable, out of scope
            t["brief"] = str(r["brief"])[:1500]
        crit = t.setdefault("criteria", [])
        if r.get("criteria_clear"):
            crit.clear()
        crit.extend({"text": str(c)[:200], "passes": False} for c in r.get("criteria_add", []))
        for key, value in (("criteria_pass", True), ("criteria_fail", False)):
            for n in r.get(key, []):
                if not 1 <= int(n) <= len(crit):
                    raise ValueError(f"task {tid}: criterion {n} does not exist ({len(crit)} criterion/criteria)")
                crit[int(n) - 1]["passes"] = value
        t["updated"] = now()
        todo = [i for i, c in enumerate(crit, 1) if not c["passes"]]
        log(state, who, f"Task {tid} → {t['manager'] or '—'} [{t['status']}] {t['title']}"
            + (f" · criteria {len(crit) - len(todo)}/{len(crit)}" if crit else ""))
    elif kind == "request":  # a manager's request to the integrator
        rid = f"R{len(state['requests']) + 1}"
        state["requests"][rid] = {"id": rid, "from": r["id"].upper(), "what": r["what"],
                                  "files": [norm(p) for p in r.get("files", [])], "status": "open",
                                  "t": now(), "answer": ""}
        log(state, f"Manager {r['id'].upper()}", f"Request {rid} to the integrator: {r['what']}")
        return rid
    elif kind == "request-done":
        q = state["requests"][r["rid"]]
        q["status"] = r.get("status", "done")
        q["answer"] = r.get("msg", "")
        log(state, who, f"Request {q['id']} → {q['status']} {q['answer']}")
    elif kind == "merge":  # merge queue to the base branch, kept by the supervisor
        q = state["merge_queue"]
        item = next((x for x in q if x["branch"] == r["branch"]), None)
        if r.get("op") == "rm":
            state["merge_queue"] = [x for x in q if x["branch"] != r["branch"]]
            log(state, who, f"Merge: {r['branch']} removed from the queue")
            return None
        if item is None:
            item = {"branch": r["branch"], "sha": "", "manager": "", "note": "", "status": "waiting", "t": now()}
            q.append(item)
        for k in ("sha", "manager", "note", "status"):
            if k in r:
                item[k] = r[k]
        if r.get("status") == "conflict":  # `merge --abort` by the integrator: counted, flagged by alerts()
            item["conflicts"] = item.get("conflicts", 0) + 1
        if "pos" in r:  # position starting at 1
            q.remove(item)
            q.insert(max(0, min(len(q), int(r["pos"]) - 1)), item)
        item["t"] = now()
        log(state, who, f"Merge #{q.index(item) + 1}: {item['branch']} [{item['status']}] {item['note']}")
    elif kind == "note":  # notes repeated in the resume sheet (.relay-supervision/<project>/<X>.md)
        target = state.setdefault("supervisor_notes", []) if r["id"].upper() == "S" else manager(state, r["id"]).setdefault("notes", [])
        if r.get("defaut"):  # defect found by a verification (metrics), dedicated kind in events
            STORE.append_event(who, str(r["defaut"])[:500], "warn", kind="defaut", subject=r["id"].upper())
            log(state, who, f"Defect found ({r['id'].upper()}): {str(r['defaut'])[:500]}", "warn")
            if not r.get("text") and not r.get("clear"):
                return None
        if r.get("clear"):
            target.clear()
        else:
            target.append({"t": now(), "text": str(r["text"])[:500]})
            del target[:-30]
    elif kind == "plan":  # plan of the current wave, cited in every resume sheet
        state["plan"] = r.get("path", "")
        log(state, who, f"Wave plan: {state['plan']}")
    elif kind == "event":
        log(state, who, r["msg"], r.get("level", "info"))
    elif kind == "objective":  # current goal of the project (sup.py objectif): title and tasks, progress computed on read
        log(state, who, objectif.update(state, r))
        return objectif.line(objectif.status(state, STORE)) or "goal removed"
    elif kind == "submit":  # end of a manager's task: SHA, check, files, deviations (sup.py submit)
        m = manager(state, r["id"])
        sha = str(r.get("sha") or "").strip()
        if not sha:
            raise ValueError("SHA required (git rev-parse HEAD)")
        if not m["branch"]:
            raise ValueError(f"Manager {m['id']}: no branch declared (sup.py manager {m['id']} --branch …)")
        open_crit = open_criteria(state, m)
        if open_crit:
            raise ValueError("delivery refused: completion criteria not met: " + "; ".join(open_crit)
                             + " (sup.py task <T> --pass N, after verification)")
        if r.get("check") != "green":
            raise ValueError("delivery refused: check not green (sup.py check X); otherwise --status blocked and warn the supervisor")
        lc = m.get("last_check") or {}
        if lc.get("rc") != 0 or now() - lc.get("t", 0) > CHECK_FRESH_SECONDS:  # the proof comes from the board, not the agent
            raise ValueError("delivery refused: no green check recorded in the last "
                             f"{CHECK_FRESH_SECONDS // 3600} hours (run `sup.py check {m['id']} --cwd <worktree>`)")
        m["submitted"] = {"sha": sha[:40], "check": "green", "files": int(r.get("files") or 0), "note": str(r.get("note") or "")[:300], "t": now()}
        m["status"], m["progress"], m["updated"] = "review", 100, now()
        push_history(m)
        log(state, f"Manager {m['id']}", f"Delivery {sha[:12]} · branch {m['branch']} · green check · {m['submitted']['files']} file(s)"
            + (f" · deviations: {m['submitted']['note']}" if m["submitted"]["note"] else ""))
        # The supervisor is warned: the card alone was not enough, deliveries could wait a long time for review.
        send_to(state, "S", f"[Board] Delivery from Manager {m['id']} to review: branch {m['branch']} @ {sha[:12]}, "
                f"{m['submitted']['files']} file(s), green check" + (f". Deviations: {m['submitted']['note']}" if m["submitted"]["note"] else "")
                + ". Pre-review running on the board.")
        if not config.test_mode():
            threading.Thread(target=prereview_now, args=(m["branch"],), daemon=True).start()
    elif kind == "check":  # end of a check (sup.py check): counts a manager's consecutive reds
        m = manager(state, r["id"])
        m["red_checks"] = 0 if int(r.get("rc", 1)) == 0 else m.get("red_checks", 0) + 1
        m["last_check"] = {"rc": int(r.get("rc", 1)), "t": now(), "full": bool(r.get("full"))}
    elif kind == "remove-worker":
        manager(state, r["id"][0])["workers"].pop(r["id"].upper(), None)
    elif kind == "pushed":  # single-use go-ahead: consumed by the push it allowed
        m = manager(state, r["id"])
        was = m["push_ok"]
        m["push_ok"] = False
        log(state, who, f"Manager {m['id']}: push done" + (" — go-ahead consumed" if was else "")
            + (f" ({r['msg']})" if r.get("msg") else ""))
    elif kind == "remove-manager":
        state["managers"].pop(r["id"].upper(), None)
        log(state, who, f"Manager {r['id'].upper()} removed from the board")
    elif kind == "reset":
        keep = state.get("project", "")
        state.clear()
        state.update(empty_state())
        state["project"] = keep
        state["_reset"] = True  # the log view restarts from here; the events table keeps everything
        state["cleared_t"] = now()  # start of the wave for the report (sup.report_body)
        log(state, who, "State reset")
    elif kind == "clear":  # end-of-day close (cloture.py): blank page, registrations kept; ≠ reset
        return clear_board(state, who)
    elif kind == "inbox":  # mailbox read: the HTTP handler empties it and returns the messages
        pass
    else:
        raise ValueError(f"unknown kind: {kind}")


CLEAR_KEEP = ("id", "session", "surface", "role")  # a manager's registration: avoids re-running launch.py


def clear_board(state, who):
    """Empty the board for the next wave.

    Emptied: manager cards (except CLEAR_KEEP), workers, tasks and criteria, merge queue, requests,
    questions, goal. Kept: `events`, sessions, quota, project, supervisor, plan, S's notes, mailbox,
    close. Tasks and goal are first archived in `events` (kind `task_archive`)."""
    tasks, obj = state.get("tasks") or {}, state.get("objective")
    if tasks or obj:
        STORE.append_event(who, f"Close archive: {len(tasks)} task(s)" + (", goal" if obj else ""),
                           kind="task_archive", subject="clear", after={"tasks": tasks, "objective": obj})
    n = {"task(s)": len(tasks), "queued branch(es)": len(state.get("merge_queue") or []),
         "request(s)": len(state.get("requests") or {}),
         "worker(s)": sum(len(m.get("workers") or {}) for m in state["managers"].values())}
    for mid, m in list(state["managers"].items()):
        state["managers"][mid] = {k: m[k] for k in CLEAR_KEEP if k in m}
        manager(state, mid)  # default values: idle status, nothing in progress
    state["tasks"], state["requests"], state["merge_queue"] = {}, {}, []
    state.pop("objective", None)
    state["_reset"] = True  # the log view and `watch` restart from here
    state["cleared_t"] = now()
    msg = "Board cleared (close): " + ", ".join(f"{v} {k}" for k, v in n.items())
    log(state, who, msg)
    return msg


COMMANDS = {
    "greenlight": ("Push go-ahead", "[Board — user decision] PUSH GO-AHEAD for your branch {branch}, "
                   "valid for ONE push only, within 30 min. Before pushing: git log origin/{base}..HEAD contains only "
                   "your commits, fast-forward, green npm run check (under check.lock). Right after the push: sup.py pushed {id} "
                   "(the go-ahead is then consumed)."),
    "revoke": ("Go-ahead revoked", "[Board — user decision] Push go-ahead REVOKED: do not push anything."),
    "stop": ("Stop", "[Board — user decision] STOP: stop your workers, push nothing, "
             "leave the files as they are, go blocked (sup.py manager {id} --status blocked) and wait."),
    "resume": ("Resume", "[Board — user decision] Resumption allowed: continue your task where you stopped."),
}


def merge_go(state, branch):
    """User go-ahead to merge a branch of the queue: passed on to the supervisor, who orchestrates."""
    q = state["merge_queue"]
    item = next((x for x in q if x["branch"] == branch), None)
    if item is None:
        raise ValueError("branch not in the queue")
    pos = q.index(item) + 1
    via = send_to(state, "S", f"[Board — user decision] MERGE GO-AHEAD #{pos}: "
                  f"{branch} ({item['sha'] or 'SHA ?'}) into {BASE_BRANCH}.")
    item["go"] = now()
    log(state, "User", f"Merge go-ahead #{pos} → {branch}" + (" (in the supervisor's mailbox)" if via == "mailbox" else ""))
    return True


def sticky_choice(mid, cid):
    """Choice `cid` of the kept question of `mid` (text or key declared to wmux), or None."""
    with AGENTS_LOCK:
        choices = (STICKY_ASKS.get(mid) or {}).get("choices") or (AGENTS.get(mid) or {}).get("choices") or []
    return next((c for c in choices if str(c.get("id")) == str(cid)), None)


def send_choice(surface, choice):
    """answer-agent fallback: send to the pane what the choice declares, as the user would."""
    if not choice:
        return False
    if choice.get("text"):
        return type_into(surface, str(choice["text"]).rstrip("\r\n"))
    return choice.get("key") in KEYS and wmux("send-key", choice["key"], "--surface", surface)


def answer(state, mid, body):
    """Answer a pane prompt: wmux choice, key, or free text."""
    sf = supervisor(state)["surface"] if mid == "S" else state["managers"][mid]["surface"]
    if "text" in body and not backend.enabled():  # without wmux: the text goes to the agent's mailbox
        text = str(body["text"])[:2000]
        mailbox_push(state, mid, text)
        log(state, "User", f"Message for {'the supervisor' if mid == 'S' else 'Manager ' + mid} (mailbox): \"{text[:80]}\"")
        return True
    if "choice" in body or "key" in body:
        requires_wmux("answering a prompt by choice or key")
    if not sf:
        raise ValueError("unknown pane")
    if "choice" in body:
        ok = wmux("answer-agent", "--surface", sf, "--choice", str(body["choice"]))
        if not ok:  # question kept by STICKY_ASKS: wmux cleared it at the end of the turn and refuses the choice
            ok = send_choice(sf, sticky_choice(mid, body["choice"]))
        what = f"choice \"{body.get('label') or body['choice']}\""
    elif "key" in body:
        if body["key"] not in KEYS:
            raise ValueError("key refused")
        ok = wmux("send-key", body["key"], "--surface", sf)
        what = f"key {body['key']}" + (f" (\"{body['label']}\")" if body.get("label") else "")
    elif "text" in body:
        text = str(body["text"])[:2000]
        ok = type_into(sf, text)
        what = f"answer \"{text[:80]}\""
    else:
        raise ValueError("empty answer")
    log(state, "User", f"Answer to Manager {mid}'s prompt: {what}" + ("" if ok else " (send failed)"))
    with AGENTS_LOCK:
        since = (STICKY_ASKS.get(mid) or AGENTS.get(mid) or {}).get("since")
        if since:
            ANSWERED_ASKS[mid] = since  # stop asking for it until the agent has unblocked itself
        STICKY_ASKS.pop(mid, None)  # answered from the board
        if mid in AGENTS:
            AGENTS[mid]["waiting"] = False  # the next reading will tell whether the prompt is really closed
    return ok


FRESH_PROMPT = ("[Board — user decision] Resuming after a fresh restart (/clear). Your resume sheet "
                "has just been injected by Relay. Re-read {protocole}, re-check git and the board, then confirm in one line "
                "to the supervisor (SendMessage {sup_session}): where you are and what you do next.")
FRESH_PROMPT_S = ("[Board — user decision] Resuming after a fresh restart (/clear). Your supervisor resume sheet "
                  "has just been injected. Re-read {protocole} and the wave plan, re-check git (origin/{base}) "
                  "and the board, then sum up for the user in 5 lines where the supervision stands.")


def fresh_blockers(state, mid):
    """Reasons to forbid a fresh restart now (empty = allowed)."""
    out = []
    a = agents_view().get(mid, {})
    if a.get("waiting"):
        out.append("a question is waiting for an answer")
    if a.get("state") == "working":
        out.append("the session is working")
    lk = check_lock()
    if lk and lk["owner"].startswith(f"Manager {mid} "):
        out.append("it holds the check lock")
    if mid != "S":
        m = state["managers"][mid]
        if m.get("status") == "working":
            out.append("status \"working\": wait for review, done or waiting")
        if any(is_active(w) for w in m.get("workers", {}).values()):
            out.append("a worker is active")
        if m.get("push_ok"):
            out.append("a push go-ahead is not consumed")
    return out


def fresh(state, mid):
    """Fresh restart: /clear in the pane, then the resume instruction. Relay re-injects the sheet on /clear."""
    requires_wmux("fresh restart")  # it types /clear into the agent's terminal
    why = fresh_blockers(state, mid)
    if why:
        raise ValueError("restart refused: " + "; ".join(why))
    sv = supervisor(state)
    sf = sv["surface"] if mid == "S" else state["managers"][mid]["surface"]
    if mid != "S":  # same task restarted fresh several times in a row: "resume_loop" signal
        m = state["managers"][mid]
        m["fresh_n"] = m.get("fresh_n", 0) + 1 if m.get("fresh_task") == m.get("task") else 1
        m["fresh_task"] = m.get("task", "")
    reprise.write_all(state, sv, CFG, objective_status(state))  # sheet up to date BEFORE the /clear
    prompt = (FRESH_PROMPT_S if mid == "S" else FRESH_PROMPT).format(
        protocole=reprise.protocole(mid, mid != "S" and state["managers"][mid].get("role") == "integrator"), base=BASE_BRANCH, sup_session=sv["session"] or "the supervisor")

    def run():
        type_into(sf, "/clear")
        time.sleep(6)  # let /clear and the SessionStart(clear) hook finish
        type_into(sf, prompt)
    threading.Thread(target=run, daemon=True).start()
    log(state, "User", f"Fresh restart → {'Supervisor' if mid == 'S' else 'Manager ' + mid}", "warn")
    return True


def command(state, action, mid):
    m = state["managers"][mid]
    label, text = COMMANDS[action]
    if action == "stop":
        wmux("send-key", "escape", "--surface", m["surface"]) if m["surface"] else None
        m["stopped"], m["push_ok"], m["status"] = True, False, "blocked"
    elif action == "resume":
        m["stopped"] = False
        m["status"] = "working"
    elif action == "greenlight":
        m["push_ok"] = True
        m["push_ok_at"] = now()
    elif action == "revoke":
        m["push_ok"] = False
    via = send_to(state, mid, text.format(branch=m["branch"] or "(not declared)", id=mid, base=BASE_BRANCH),
                  urgent=action == "stop")  # the stop does not wait for the end of the turn
    log(state, "User", f"{label} → Manager {mid}" + (" (in its mailbox: it will read it on its next sup.py call)" if via == "mailbox"
                                                    else " (mailbox, pointer at the end of its turn)" if via == "pointer" else ""),
        "warn" if action == "stop" else "info")
    send_to(state, "S", f"[Board] the user clicked \"{label}\" for Manager {mid}.")
    return True


TRAIN_STATUSES = ("ready", "merged")  # queue branches the integrator merges into its train


def train_refs(state):
    """Delivered SHAs (and names) of the ready or merged branches of the merge queue: the content of the train."""
    out = []
    for x in state.get("merge_queue", []):
        if x.get("status") in TRAIN_STATUSES:
            out += [r for r in (x.get("sha"), x.get("branch")) if r and not str(r).startswith("-")]
    return out


def git_files(wt, others=(), train=None):
    """Files changed by the commits OWN to this worktree, plus its uncommitted changes.

    Does not count what comes from elsewhere through a merge: other managers' branches (`others`), integration
    branches (origin/integ/*) and the merge commits themselves. Otherwise the integrator merging B, or B taking
    the integration branch back, would be credited with the others' files.

    `train` (integrator only, possibly empty list): SHAs or branches of the merge queue (`train_refs`). Their commits
    are not the integrator's, even if their managers switched branches or did not push them (otherwise a normal
    merge train raised many scope alerts). Its own integ/* is then NOT excluded: a real change by the integrator,
    even pushed, always alerts.
    """
    def run(*a):
        r = subprocess.run(["git", "-C", wt, *a], capture_output=True, text=True, timeout=20, encoding="utf-8",
                           creationflags=config.NO_WINDOW)
        return r.stdout if r.returncode == 0 else ""
    excl = [] if train is not None else ["--remotes=origin/integ/*"]
    excl += [f"--remotes=origin/{b}" for b in others if b]
    for ref in train or ():
        # Unknown ref skipped one by one: otherwise `git log` would fail entirely and hide everything.
        for cand in (ref, f"origin/{ref}"):
            sha = run("rev-parse", "-q", "--verify", f"{cand}^{{commit}}").strip()
            if sha:
                excl.append(sha)
                break
    files = set(run("log", "--no-merges", "--name-only", "--format=", f"origin/{BASE_BRANCH}..HEAD", "--not", *excl).splitlines())
    merging = bool(run("rev-parse", "-q", "--verify", "MERGE_HEAD").strip())
    for line in run("status", "--porcelain", "--untracked-files=all").splitlines():
        # During a merge in progress, the index holds the merged branch's files: only conflicts count.
        if merging and line[:2] not in ("UU", "AA", "DD", "AU", "UA", "DU", "UD"):
            continue
        p = line[3:].split(" -> ")[-1].strip('"')
        if p:
            files.add(p)
    return sorted(norm(f) for f in files if f.strip())


def same_path(a, b):
    # realpath: Windows 8.3 short paths and long paths designate the same directory
    return os.path.normcase(os.path.realpath(str(a))) == os.path.normcase(os.path.realpath(str(b)))


MISSING_SCANS = 2  # consecutive readings without the directory before removing a worktree from the card


def prune_worktrees(state, missing, exists=lambda p: Path(p).is_dir()):
    """Remove from the cards the worktrees whose directory no longer exists (MISSING_SCANS readings in a row).

    `missing`: counter {(manager, path): readings without directory}, kept from one reading to the next by the caller.
    Returns the list of removed (manager, path).
    """
    removed = []
    seen = set()
    for mid, m in state["managers"].items():
        for wt in list(m.get("worktrees", [])):
            key = (mid, wt)
            seen.add(key)
            if exists(wt):
                missing.pop(key, None)
                continue
            missing[key] = missing.get(key, 0) + 1
            if missing[key] >= MISSING_SCANS:
                m["worktrees"].remove(wt)
                missing.pop(key, None)
                removed.append(key)
                log(state, "Board", f"Manager {mid}: vanished worktree removed from the card ({wt})")
    for key in [k for k in missing if k not in seen]:
        missing.pop(key)
    return removed


def prereview_now(branch):
    """Immediate pre-review of a branch (going to review): git outside the lock, storing under LOCK."""
    try:
        with LOCK:
            snap = load()
        res = prereview.compute(snap, sys.modules[__name__], only=branch)
        with LOCK:
            s = load()
            if prereview.store_results(s, res, lambda who, msg, lvl: log(s, who, msg, lvl)):
                save(s, "pre-review")
        return res.get(branch)
    except Exception as e:  # noqa: BLE001 — helper thread: never stops the server
        print("pre-review:", e)
        return None


def scan_real_files(snap):
    """Real files of each manager according to git (outside the lock): {manager: [files]}, worktrees included.

    The integrator is not credited with the content of the train (`train_refs`) it merges."""
    mans = snap["managers"]
    branches = {mid: m.get("branch", "") for mid, m in mans.items()}
    train = train_refs(snap)
    found = {}
    for mid, m in mans.items():
        others = [b for o, b in branches.items() if o != mid and b != branches[mid]]
        mine = train if m.get("role") == "integrator" else None
        found[mid] = sorted({f for wt in m.get("worktrees", []) if Path(wt).is_dir() for f in git_files(wt, others, mine)})
    return found


DRIFT_SHOWN = 5  # commit subjects of the base shown in the sheet


def base_drift(wt, base):
    """Commits of origin/<base> missing from the worktree's branch: {"n", "subjects"} (the last 5), None if git
    cannot tell. Reads the local refs (no fetch): reflects the last `git fetch`."""
    def run(*args):
        r = subprocess.run(["git", "-C", str(wt), *args], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=10, creationflags=config.NO_WINDOW)
        return r.stdout.strip() if r.returncode == 0 else None
    try:
        n = run("rev-list", "--count", f"HEAD..origin/{base}")
        if n is None:
            return None
        subj = run("log", "--format=%s", f"-{DRIFT_SHOWN}", f"HEAD..origin/{base}") if int(n) else ""
        return {"n": int(n), "subjects": (subj or "").splitlines()[:DRIFT_SHOWN]}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def scan_drift(snap):
    """Base drift of each manager, from its first present worktree: {manager: drift or None}."""
    out = {}
    for mid, m in snap["managers"].items():
        wt = next((w for w in m.get("worktrees", []) if Path(w).is_dir()), None)
        out[mid] = base_drift(wt, BASE_BRANCH) if wt else None
    return out


def scanner():
    missing = {}
    while not lifecycle.STOP.wait(SCAN_SECONDS):
        try:
            with LOCK:
                snap = load()
                wts = {mid: list(m.get("worktrees", [])) for mid, m in snap["managers"].items()}
            pre = prereview.compute(snap, sys.modules[__name__])  # git read-only, outside the lock
            found = scan_real_files(snap)
            drift = scan_drift(snap)
            with LOCK:
                s = load()
                prune_worktrees(s, missing)
                for mid, files in found.items():
                    # Without worktree (all removed), the real files are emptied instead of staying frozen.
                    if mid in s["managers"] and (wts[mid] or s["managers"][mid].get("real_files")):
                        m = manager(s, mid)
                        m["real_files"] = files
                        push_history(m)
                for mid, d in drift.items():
                    if mid in s["managers"] and d is not None and s["managers"][mid].get("drift") != d:
                        s["managers"][mid]["drift"] = d
                prereview.store_results(s, pre, lambda who, msg, lvl: log(s, who, msg, lvl))
                save(s, "scanner")
        except Exception as e:  # noqa: BLE001 — the scanner must never die
            print("scan:", e)


def notify_plan(mem, current, t, cooldown=None):
    """Red alerts to log and notify now. Key (code, agent), plus the text: a message that changes ("for 17 min",
    "91 %", one more file) does not re-notify; nor does an alert raised then back within `cooldown` s (5 s,
    signals.COOLDOWN_SECONDS). A really new key always does.
    `mem`: {"open": open keys, "closed": {key: closing time}}, maintained here."""
    cooldown = signals.COOLDOWN_SECONDS if cooldown is None else cooldown
    opened, closed = mem.setdefault("open", set()), mem.setdefault("closed", {})
    now_err = {(a.get("code") or a["msg"], a.get("who")): a for a in current if a["level"] == "error"}
    out = []
    for k, a in now_err.items():
        if k in opened:
            continue
        opened.add(k)
        if t - closed.get(k, -1e18) >= cooldown:
            out.append(a)
    for k in [k for k in opened if k not in now_err]:
        opened.discard(k)
        closed[k] = t
    return out


def quota_windows(state, t):
    """Track the exhausted quota windows (≥ 100 %) in state["quota_out"] = {window: resets_at}.
    Returns (exhausted just now, reset just now). A window whose reset time has passed is not counted again as
    exhausted: the statusline may keep the old 100 % until its next render."""
    out = state.setdefault("quota_out", {})
    spent, reset = [], []
    for k, v in state["quota"].items():
        at = v.get("resets_at")
        if k not in out and (v.get("used_percentage") or 0) >= 100 and at and at > t:
            out[k] = at
            spent.append(k)
    for k, at in list(out.items()):
        if t >= at:
            del out[k]
            reset.append(k)
    return spent, reset


def watcher():
    """Notify each new red alert, maintain the signals (events) and tighten/release the quota brake."""
    open_sig = signals.replay(STORE.events(kind=signals.KIND))
    # A fault already open before a restart is neither logged nor notified again.
    mem = {"open": {k for k, (_msg, level) in open_sig.items() if level == "error"}}
    while not lifecycle.STOP.wait(WATCH_SECONDS):
        try:
            with LOCK:
                s = load()
                current = alerts(s)
                fresh = sorted(a["msg"] for a in notify_plan(mem, current, time.time()))
                for msg in fresh:
                    log(s, "alert", msg, "error")
                to_open, to_close = signals.diff(open_sig, current)
                for a in to_open:
                    STORE.append_event(a["who"], a["msg"], a["level"], kind=signals.KIND, subject=a["code"], after="open")
                for (code, who), (msg, _level) in to_close:
                    STORE.append_event(who, msg, "info", kind=signals.KIND, subject=code, after="resolved")
                expired = False
                for m in s["managers"].values():  # single-use go-ahead: expires by itself after 30 min
                    if m.get("push_ok") and now() - m.get("push_ok_at", 0) > PUSH_OK_SECONDS:
                        m["push_ok"] = False
                        log(s, "Board", f"Push go-ahead of Manager {m['id']} expired (30 min without a push)")
                        expired = True
                if pointer_followup(s):  # late pointers, reminders, batches never read
                    expired = True
                if expired:
                    save(s, "watcher")
                five = s["quota"].get("five_hour", {}).get("used_percentage")
                braked = five is not None and five >= QUOTA_BRAKE
                changed = braked != s["braked"]
                if changed:
                    s["braked"] = braked
                    log(s, "quota brake", f"Brake {'TIGHTENED' if braked else 'released'}: 5 h quota at {five or 0:.0f} %, "
                        f"{worker_limit(s)} active workers max per manager", "warn" if braked else "info")
                spent, reset = quota_windows(s, now())
                for k in spent:
                    at = time.strftime("%H:%M", time.localtime(s["quota_out"][k]))
                    log(s, "quota", f"Quota {QUOTA_LABELS.get(k, k)} exhausted: resets at {at}, "
                        + ("managers resumed automatically" if QUOTA_RESUME else "resume by hand (quota_resume: false)"),
                        "warn")
                for k in reset:
                    log(s, "quota", f"Quota {QUOTA_LABELS.get(k, k)} reset"
                        + (": managers resumed" if QUOTA_RESUME else ""), "info")
                pc = sysstats.snapshot()
                host_changes = []
                for key, label, metric in (("cpu", "CPU", "cpu"), ("mem", "memory", "mem")):
                    b = BRAKES.get(key)
                    if b and b.update(pc.get(metric)):
                        host_changes.append((label, b.on, b.value))
                        log(s, f"{label} brake", f"{label} brake {'TIGHTENED' if b.on else 'released'}: {b.value} % "
                            f"(thresholds {b.high} / {b.low} %), {worker_limit(s)} active workers max per manager",
                            "warn" if b.on else "info")
                if fresh or changed or host_changes or spent or reset:
                    save(s, "watcher")
            try:  # resume sheets (.relay-supervision/), rewritten only when they change
                reprise.write_all(s, supervisor(s), CFG, objective_status(s))
            except Exception as e:  # noqa: BLE001
                print("resume sheets:", e, flush=True)
            if NOTIFY:
                for msg in fresh:
                    notify(f"Supervision: {msg}")
            for label, on, value in host_changes:
                text = ("[Board — automatic rule wanted by the user] " + (
                        f"Host {label} brake TIGHTENED ({value} %): at most {BRAKED_WORKERS} active workers per manager. "
                        "Do not launch a new one, start neither `npm ci` nor a build; let the others finish."
                        if on else f"Host {label} brake released: {MAX_WORKERS} workers max again."))
                if NOTIFY:
                    notify(text.split("] ", 1)[1])
                if NOTIFY or not backend.enabled():
                    with LOCK:
                        s2 = load()
                        for mid in s2["managers"]:
                            send_to(s2, mid, text)
                        save(s2, "watcher")
            if reset and QUOTA_RESUME:
                text = (f"[Board — automatic rule wanted by the user] Quota "
                        f"{', '.join(QUOTA_LABELS.get(k, k) for k in reset)} reset: resume your task where the "
                        "cap stopped you. If you were waiting for an answer from the user, keep waiting.")
                if NOTIFY:
                    notify(text.split("] ", 1)[1])
                if NOTIFY or not backend.enabled():
                    with LOCK:
                        s2 = load()
                        for mid in s2["managers"]:
                            send_to(s2, mid, text)
                        save(s2, "watcher")
            if changed:
                text = (f"[Board — automatic rule wanted by the user] Quota brake TIGHTENED (5 h ≥ {QUOTA_BRAKE} %): "
                        f"at most {BRAKED_WORKERS} active workers. Do not launch a new one; let the others finish."
                        if braked else
                        f"[Board — automatic rule wanted by the user] Quota brake released: {MAX_WORKERS} workers max again.")
                if NOTIFY:
                    notify(text.split("] ", 1)[1])
                if NOTIFY or not backend.enabled():  # wmux: typing into panes; without wmux: mailboxes
                    with LOCK:
                        s2 = load()
                        for mid in s2["managers"]:
                            send_to(s2, mid, text)
                        save(s2, "watcher")
        except Exception as e:  # noqa: BLE001 — the watcher must never die
            print("watch:", e)


def alerts(state):
    out = []
    view = agents_view()
    declared, real = {}, {}
    for m in state["managers"].values():
        for f in m["files"]:
            declared.setdefault(f, []).append(m["id"])
        undeclared, refused = [], {}
        for f in m["real_files"]:
            real.setdefault(f, []).append(m["id"])
            if f not in m["files"]:
                undeclared.append(f)
            if (why := allowed(m, f)):
                refused.setdefault(why, []).append(f)
        # One line per manager and per reason: a branch merge must not flood the board.
        brief = lambda fs: ", ".join(fs[:3]) + (f" and {len(fs) - 3} more" if len(fs) > 3 else "")
        if undeclared:
            out.append({"level": "warn", "code": "undeclared", "who": m["id"],
                        "msg": f"Manager {m['id']}: {len(undeclared)} file(s) changed without declaration ({brief(undeclared)})"})
        for why, fs in refused.items():
            out.append({"level": "error", "code": "scope_refused", "who": m["id"],
                        "msg": f"Manager {m['id']}: {len(fs)} file(s) {why} ({brief(fs)})"})
        active = [w for w in m["workers"].values() if is_active(w)]
        if len(active) > worker_limit(state):
            out.append({"level": "error", "code": "workers_limit", "who": m["id"],
                        "msg": f"Manager {m['id']}: {len(active)} active workers (max {worker_limit(state)}"
                        + (", quota brake" if state.get("braked") else f", {host_braked()} brake" if host_braked() else "") + ")"})
        for w in m["workers"].values():
            n = w["id"][1:]
            if not n.isdigit() or not 1 <= int(n) <= MAX_WORKERS:
                out.append({"level": "error", "code": "worker_name", "who": w["id"],
                            "msg": f"Badly named worker: {w['id']} (expected {m['id']}1 to {m['id']}{MAX_WORKERS})"})
            if w["status"] == "done" and not w.get("verified"):
                lowered = w.get("model", "opus") != "opus"
                out.append({"level": "error" if lowered else "warn", "code": "worker_unverified", "who": w["id"],
                            "msg": f"worker {w['id']} ({w.get('model', 'opus')}) finished, not yet verified by its manager"})
        # "Silent" = declared working, no news on the board AND pane idle in wmux.
        # A branch under verification waiting for its merge turn is not silent.
        pane_busy = view.get(m["id"], {}).get("state") == "working"
        virtual = CFG.get("lite") and not m["surface"]  # lite-mode manager A: no pane to watch
        if m["status"] == "working" and not pane_busy and not virtual and now() - m["updated"] > STALE_SECONDS:
            out.append({"level": "warn", "code": "stall", "who": m["id"],
                        "msg": f"Manager {m['id']} silent for {(now() - m['updated']) // 60} min"})
        for w in m["workers"].values():  # active worker without news: double threshold, its manager does not always report it
            if is_active(w) and now() - w.get("updated", now()) > 2 * STALE_SECONDS:
                out.append({"level": "warn", "code": "worker_stall", "who": w["id"],
                            "msg": f"Worker {w['id']} without news for {(now() - w['updated']) // 60} min"})
        if m.get("red_checks", 0) >= CHECK_RED_MAX:
            out.append({"level": "error", "code": "check_red", "who": m["id"],
                        "msg": f"Manager {m['id']}: {m['red_checks']} red checks in a row: look for the root cause "
                               "(red base?) before running again"})
        if m.get("fresh_n", 0) >= 2 and m.get("fresh_task") == m["task"]:
            out.append({"level": "warn", "code": "resume_loop", "who": m["id"],
                        "msg": f"Manager {m['id']}: {m['fresh_n']} fresh restarts on the same task: the framing is at fault"})
        if m["status"] == "blocked" and not m["stopped"]:
            out.append({"level": "warn", "code": "blocked", "who": m["id"], "msg": f"Manager {m['id']} blocked: {m['task']}"})
    mans = sorted(state["managers"].values(), key=lambda x: x["id"])
    for i, ma in enumerate(mans):  # overlapping scopes: to fix BEFORE two managers end up in the same files
        for mb in mans[i + 1:]:
            clash = [(ga, gb) for ga in ma["scope"] for gb in mb["scope"] if scopes_overlap(ga, gb)]
            if clash:
                out.append({"level": "warn", "code": "scope_overlap", "who": f"{ma['id']},{mb['id']}",
                            "msg": f"Scopes of {ma['id']} and {mb['id']} overlap: "
                                   + ", ".join(f"{ga} / {gb}" for ga, gb in clash[:3])})
    for kind, owners in (("declared", declared), ("changed", real)):
        for f, ids in owners.items():
            if len(set(ids)) > 1:
                out.append({"level": "error", "code": "collision", "who": ",".join(sorted(set(ids))),
                            "msg": f"Collision: {f} {kind} by {', '.join(sorted(set(ids)))}"})
    if not any(m["role"] == "integrator" for m in state["managers"].values()) and state["managers"] and not CFG.get("lite"):
        out.append({"level": "warn", "code": "no_integrator", "who": "S", "msg": "No integrator designated for the common files"})
    for mid, p in (state.get("pointers") or {}).items():  # mail unread despite the pointer and its reminder
        box = (state.get("mailbox") or {}).get(mid) or []
        if p.get("signaled") and box:
            out.append({"level": "warn", "code": "mail_unread", "who": mid,
                        "msg": f"{'Supervisor' if mid == 'S' else 'Manager ' + mid}: {len(box)} board message(s) unread "
                               f"for {(now() - p.get('first', now())) // 60} min (pointer reminded)"})
    with AGENTS_LOCK:
        stray = list(STRAY_BROWSERS)
    if stray:
        out.append({"level": "error", "code": "stray_browser", "who": "?", "msg": f"{len(stray)} browser(s) outside the allowed panes ({', '.join(s[:13] for s in stray)}): "
                    "a `wmux browser` without --surface; redo it in the manager's \"Browser X\" tab"})
    for x in state.get("merge_queue", []):
        if x.get("conflicts", 0) >= 1 and x.get("status") != "merged":
            out.append({"level": "error" if x["conflicts"] >= 2 else "warn", "code": "merge_conflict",
                        "who": x.get("manager") or x["branch"],
                        "msg": f"Merge: {x['conflicts']} conflict(s) on {x['branch']}: take it out of the train, warn the manager"})
    lk = check_lock()
    if lk and now() - lk["since"] > LOCK_STALE_SECONDS:
        out.append({"level": "error", "code": "lock_stale", "who": lk["owner"].split(" · ")[0],
                    "msg": f"check.lock held by {lk['owner']} for {(now() - lk['since']) // 60} min: forgotten?"})
    for key, label, what in (("cpu", "CPU", "CPU"), ("mem", "memory", "RAM")):
        b = BRAKES.get(key)
        if b and b.on:
            out.append({"level": "warn", "code": f"host_{key}", "who": "S",
                        "msg": f"Host {what} at {b.value} % ({label} brake tight, at most {BRAKED_WORKERS} workers per manager)"})
    q = state.get("quota", {})
    for k, label in (("five_hour", "5 h"), ("seven_day", "7 days")):
        if q.get(k, {}).get("used_percentage", 0) >= 90:
            out.append({"level": "error", "code": f"quota_{k}", "who": "S", "msg": f"Quota {label} at {q[k]['used_percentage']:.0f} %"})
    return out


def agent_id(agent):
    """Manager (or S) named by SUP_AGENT=<project>/<X> of this project, '' otherwise: identity of agents without wmux."""
    proj, _, who = str(agent or "").partition("/")
    return who.upper() if proj == PROJECT and who else ""


def surface_manager(state, surface):
    for m in state["managers"].values():
        if surface and m["surface"] and (surface.startswith(m["surface"]) or m["surface"].startswith(surface)):
            return m["id"]
    return "superviseur" if surface and surface == supervisor(state)["surface"] else ""


def statusline(state, d):
    sid = d.get("session_id") or "?"
    cw = d.get("context_window") or {}
    state["sessions"][sid] = {
        "who": surface_manager(state, d.get("_surface", "")) or agent_id(d.get("_agent", "")), "surface": d.get("_surface", ""),
        "cwd": d.get("cwd", ""), "model": (d.get("model") or {}).get("display_name", ""),
        "ctx": cw.get("used_percentage"), "cost": (d.get("cost") or {}).get("total_cost_usd"), "t": now(),
    }
    for k in [k for k, v in state["sessions"].items() if now() - v["t"] > 6 * 3600]:
        del state["sessions"][k]
    rl = d.get("rate_limits") or {}
    for k in ("five_hour", "seven_day"):
        if isinstance(rl.get(k), dict) and rl[k].get("used_percentage") is not None:
            state["quota"][k] = {"used_percentage": rl[k]["used_percentage"], "resets_at": rl[k].get("resets_at"), "t": now()}
    for k in [k for k, v in state["quota"].items() if (v.get("resets_at") or 1e12) < now()]:
        del state["quota"][k]


def actor(path, body):
    """Author of a write (`who` column of the change rows in events)."""
    if path == "/api/command":
        return "User"
    if path == "/api/statusline":
        return "statusline"
    if body.get("from"):
        return str(body["from"])
    return f"Manager {str(body['id'])[:1].upper()}" if body.get("id") else "?"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def local_host(self):
        # Against DNS rebinding: only a local Host is served.
        return self.headers.get("Host", "").split(":")[0] in ("127.0.0.1", "localhost")

    def do_GET(self):
        if not self.local_host():
            return self.send(403, '{"error":"host"}')
        if self.path in ("/", "/index.html"):
            page = PAGE.read_text(encoding="utf-8").replace("__SUP_TOKEN__", TOKEN)
            self.send(200, page, "text/html; charset=utf-8")
        elif self.path.startswith("/api/conversation"):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            try:
                since = int(q.get("since", ["-1"])[0])
            except ValueError:
                since = -1
            self.send(200, json.dumps(CONV.since(since), ensure_ascii=False))
        elif self.path.startswith("/api/metrics"):  # history (read-only access to the database)
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            try:
                self.send(200, json.dumps(metrics.compute(STORE, q.get("scope", ["day"])[0]), ensure_ascii=False))
            except ValueError as e:
                self.send(400, json.dumps({"error": str(e)}, ensure_ascii=False))
        elif self.path == "/api/state":
            with LOCK:
                s = load()
            s["alerts"] = alerts(s)
            s["now"] = now()
            s["common"] = COMMON
            s["check_lock"] = check_lock()
            s["worker_limit"] = worker_limit(s)
            s["quota_brake"] = QUOTA_BRAKE
            s["jira_url"] = CFG.get("jira_url") or ""
            s["pc"] = sysstats.snapshot()
            s["host"] = {**HOST, "workers_total": WORKERS_TOTAL, "braked": host_braked(),
                         "cpu_brake": CFG["cpu_brake"], "mem_brake": CFG["mem_brake"],
                         "slots": hostload.slot_state(RUN_DIR / "slots")}
            # Fresh restart: what prevents it for each one (the board button is active only if the list is empty).
            s["fresh"] = {mid: fresh_blockers(s, mid) for mid in [*s["managers"], "S"]}
            s["agents"] = agents_view()
            s["backend"] = backend.mode()  # "wmux" or "none": the board hides what requires wmux
            s["mailbox_pending"] = {k: len(v) for k, v in (s.get("mailbox") or {}).items() if v}
            s["cloture"] = cloture.status(s, CFG, cloture.clock())  # evening reminder banner
            s["objective_status"] = objective_status(s)  # goal bar at the top of the board
            self.send(200, json.dumps(s, ensure_ascii=False))
        elif self.path == "/api/island":  # versioned summary for an external app: island.py
            with LOCK:
                s = load()
            s["now"], s["project"] = now(), PROJECT
            agents = agents_view()
            url = f"http://127.0.0.1:{self.server.server_address[1]}/"
            self.send(200, json.dumps(island.build(s, alerts(s), objective_status(s), agents, url), ensure_ascii=False))
        elif self.path == "/api/cloture":  # close proposals (nothing is written)
            try:
                self.send(200, json.dumps(cloture.draft(sys.modules[__name__]), ensure_ascii=False))
            except Exception as e:  # noqa: BLE001
                self.send(500, json.dumps({"error": str(e)}, ensure_ascii=False))
        else:
            self.send(404, '{"error":"not found"}')

    def do_POST(self):
        # JSON Content-Type required: a third-party web page cannot send it without a CORS preflight (refused).
        if not self.local_host() or "application/json" not in self.headers.get("Content-Type", ""):
            return self.send(403, '{"error":"origin refused"}')
        try:
            raw = self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}"
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:  # Git Bash curl on Windows sends accented characters in cp1252
                text = raw.decode("cp1252", errors="replace")
            body = json.loads(text)
            if self.path == "/api/hook":  # sup-hook.js: a hook event from an agent (state)
                with LOCK:
                    res = hook_event(body)
                    if res and res[2] != "working":  # prompt, failure or rest: log, notification, pointer
                        s = load()
                        on_hook(s, *res)
                        save(s, "hooks")
                return self.send(200, '{"ok":true}')
            with LOCK:
                s = load()
                if self.path == "/api/report":
                    out = apply(s, body)
                    if body.get("kind") in ("assign", "supervisor"):  # an agent launched right after must find its sheet
                        try:
                            reprise.write_all(s, supervisor(s), CFG, objective_status(s))
                        except Exception as e:  # noqa: BLE001
                            print("resume sheets:", e, flush=True)
                elif self.path == "/api/statusline":
                    out = statusline(s, body)
                elif self.path == "/api/shutdown":  # clean stop (launch.py --restart); token required
                    if not secrets.compare_digest(self.headers.get("X-Sup-Token", ""), TOKEN) or STOPPER is None:
                        return self.send(403, '{"error":"invalid token"}')
                    log(s, "User", "Server stop requested (clean stop)", "warn")
                    out = "stopping"
                    STOPPER()
                elif self.path == "/api/cloture":  # close actions ticked and confirmed; token required
                    if not secrets.compare_digest(self.headers.get("X-Sup-Token", ""), TOKEN):
                        return self.send(403, '{"error":"invalid token"}')
                    body["from"] = "User"
                    out = cloture.execute(sys.modules[__name__], s, body)
                elif self.path == "/api/island/answer":  # external app: one choice for the open question, nothing else
                    if not secrets.compare_digest(self.headers.get("X-Island-Token", ""), ISLAND_TOKEN):
                        return self.send(403, '{"error":"invalid token"}')
                    mid = str(body.get("id", ""))
                    code, why = island.check_answer(agents_view(), mid, body)
                    if code != 200 or mid not in s["managers"] and mid != "S":
                        return self.send(code if code != 200 else 400, json.dumps({"error": why or "unknown manager"}, ensure_ascii=False))
                    out = answer(s, mid, {"choice": str(body["choice"]), "label": str(body.get("label", ""))[:80]})
                    log(s, "User", f"Answer given from the external app (Manager {mid})")
                elif self.path == "/api/command":
                    if not secrets.compare_digest(self.headers.get("X-Sup-Token", ""), TOKEN):
                        return self.send(403, '{"error":"invalid token"}')
                    if body.get("action") == "merge-go":
                        out = merge_go(s, body.get("branch", ""))
                    elif body.get("action") == "review":  # "Review" button
                        out = review_request(s, body.get("id", ""), body.get("branch", ""))
                    elif body.get("action") == "answer" and body.get("id") == "S":
                        out = answer(s, "S", body)
                    elif body.get("action") == "fresh" and (body.get("id") == "S" or body.get("id") in s["managers"]):
                        out = fresh(s, body["id"])
                    elif body.get("id") not in s["managers"]:
                        raise ValueError("unknown manager")
                    elif body.get("action") == "answer":
                        out = answer(s, body["id"], body)
                    elif body.get("action") in COMMANDS:
                        out = command(s, body["action"], body["id"])
                    else:
                        raise ValueError("unknown command")
                else:
                    return self.send(404, '{"error":"not found"}')
                msgs = []
                if self.path == "/api/report":  # the calling agent receives what waits in its mailbox (without wmux, mostly)
                    who_calls = agent_of(body)
                    msgs = mailbox_pop(s, who_calls) if who_calls else []
                    if body.get("kind") == "inbox":  # explicit read: the result carries the messages, no duplicate
                        out, msgs = [m["text"] for m in msgs], []
                save(s, actor(self.path, body))
            self.send(200, json.dumps({"ok": True, "result": out, **({"messages": [m["text"] for m in msgs]} if msgs else {})},
                                      ensure_ascii=False))
        except Exception as e:  # noqa: BLE001 — always answer, never cut the connection
            self.send(400, json.dumps({"error": str(e)}, ensure_ascii=False))


STOPPER = None  # clean stop (lifecycle.stopper), set by serve()


def review_request(state, mid, branch=""):
    """"Review" button: ready-made message to the supervisor (read-only sonnet subagent, launched by it)."""
    q = state["merge_queue"]
    item = next((x for x in q if branch and x["branch"] == branch), None)
    mid = (mid or (item or {}).get("manager", "")).upper()[:1]
    m = state["managers"].get(mid)
    if m is None and item is None:
        raise ValueError("unknown manager or branch")
    m = m or {"id": mid, "branch": branch}
    if not (branch or m.get("branch")):
        raise ValueError(f"Manager {mid}: no branch declared")
    pre = (item or {}).get("prereview") or m.get("prereview")
    via = send_to(state, "S", prereview.review_prompt(mid, m, item, pre, BASE_BRANCH))
    log(state, "User", f"Review requested → {(item or {}).get('branch') or m['branch']}"
        + (" (in the supervisor's mailbox)" if via == "mailbox" else ""))
    return True


def finish():
    """Last write then store close (clean stop), under LOCK: no request in the middle of a write."""
    global STORE
    with LOCK:
        if STORE is None:
            return
        s = load()
        log(s, "Board", "Server stopped cleanly")
        save(s, "server")
        STORE.close()
        STORE = None


def make_server(port):
    """HTTP server on 127.0.0.1:port (0 = free port). In test mode, the live server's port is refused."""
    return ThreadingHTTPServer(("127.0.0.1", config.check_port(port)), Handler)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=None, help=f"brain project (default: SUP_PROJECT or {config.DEFAULT_PROJECT})")
    ap.add_argument("--port", type=int, default=None, help="default: port from the config (8765 by default)")
    a = ap.parse_args()
    configure(a.project)
    a.port = config.check_port(a.port or PORT)
    lifecycle.setup_log(RUN_DIR, console=sys.stderr.isatty())  # run/<project>/server.log, rotating; console if a terminal
    print = lifecycle.logprint  # noqa: A001 — module global: every print of the server goes to the log
    print(f"Project: {PROJECT} · config: {CFG['source']} · state: {STORE.path}", flush=True)
    RUN_ID = new_run_id()  # token of this supervision: start-manager.ps1 sets it as SUP_RUN
    n = replay_spool()  # hook events posted while the board was stopped
    if n:
        print(f"Hooks: {n} queued event(s) replayed", flush=True)
    threading.Thread(target=scanner, daemon=True).start()
    threading.Thread(target=watcher, daemon=True).start()
    if backend.enabled():  # without wmux, no pane to read
        threading.Thread(target=agent_poller, daemon=True).start()
    print(f"Backend: {backend.mode()}" + ("" if backend.enabled() else " (without wmux: mailbox, SUP_AGENT=<project>/<X> for the agents)"), flush=True)
    threading.Thread(target=cloture.reminder_loop, args=(sys.modules[__name__],), daemon=True).start()  # evening reminder
    sysstats.start()
    print(f"Supervision: http://127.0.0.1:{a.port}/")
    httpd = make_server(a.port)
    STOPPER = lifecycle.stopper(httpd, finish)  # signal or POST /api/shutdown → clean stop
    lifecycle.install_signals(STOPPER)
    port_file = config.guard_write(RUN_DIR / "server.port")  # read by statusline.py to find this server
    port_file.write_text(str(httpd.server_address[1]), encoding="utf-8")
    try:
        httpd.serve_forever()
        STOPPER.done.wait(15)  # serve_forever returned: wait for the last write
    finally:
        port_file.unlink(missing_ok=True)
