"""Self-improvement of the supervision: recurring signals become proposals in `INBOX/`.

`sup.py improve` reads the error signals (events `kind='signal'`, see signals.py) over the last days.
A pattern that comes back `min_n` times or more gives ONE proposal `INBOX/<host>/YYYY-MM-DD-supervision-signal-<code>.md`;
nothing enters the skill or `AGENTS.md` without a human review of the inbox.
Guards: no second proposal for a pattern already proposed or handled in the last 30 days; at most `max_new`
proposals per week, all projects together; no LLM (the text comes from the `signals.CODES` table).
"""
import datetime
import os
import socket
import time
from pathlib import Path

import config
import metrics
import signals

RECENT_DAYS = 30   # a pattern already proposed or handled within this delay is not proposed again
WEEK_DAYS = 7


def host():
    return os.environ.get("RELAY_HOST") or socket.gethostname()


def _slug(code):
    return f"supervision-signal-{code.replace('_', '-')}"


def _inbox_files():
    root = Path(config.inbox_dir())
    return [p for p in root.rglob("*.md")] if root.is_dir() else []


def _file_date(p):
    try:
        return datetime.date.fromisoformat(p.name[:10])
    except ValueError:
        return None


def already(code, today):
    """True if this pattern already has a proposal (pending or handled) younger than RECENT_DAYS days."""
    slug = _slug(code)
    for p in _inbox_files():
        d = _file_date(p)
        if slug in p.name and d and (today - d).days <= RECENT_DAYS:
            return True
    return False


def written_this_week(today):
    n = 0
    for p in _inbox_files():
        d = _file_date(p)
        if "supervision-signal-" in p.name and d and 0 <= (today - d).days < WEEK_DAYS:
            n += 1
    return n


def examples(src, code, start, end, limit=3):
    rows = src.query("SELECT who, msg FROM events WHERE kind = 'signal' AND subject = ? AND after = '\"open\"' "
                     "AND t >= ? AND t < ? ORDER BY id DESC LIMIT ?", (code, int(start), int(end), limit))
    return [f"{who}: {msg}" for who, msg in rows]


def proposal(code, n, days, project, exs, today):
    hint = signals.CODES.get(code, "unknown pattern: to be qualified")
    ex = "\n".join(f"- {e}" for e in exs) or "- (no example kept)"
    return f"""---
type: rule
title: Supervision — the pattern "{code}" keeps coming back ({n} times in {days} days)
target: SKILLS/supervision/ (SKILL.md, common.md, manager.md, supervisor.md, integrator.md) or TOOLS/supervision/ (server.py)
session: {today.isoformat()}, `sup.py improve` ({host()}, project {project})
---

## Why
The board opened {n} "{code}" signals in {days} days on project {project}. Leads: {hint}.

## Proposal
Look at the rule or the check behind this pattern:
1. if the agent could have avoided the fault with a clearer instruction, fix it in the skill (file of the role concerned);
2. if the fault remains possible, make it impossible in the tool (server refusal or dedicated command);
3. if the signal is noise (threshold too short, false positive), adjust the threshold or remove the pattern.

## Example
{ex}
"""


ROLE_FILES = {  # skill file most concerned by each pattern, to steer the diagnosis
    "scope_refused": "supervisor.md", "scope_overlap": "supervisor.md", "undeclared": "manager.md", "collision": "supervisor.md",
    "workers_limit": "manager.md", "worker_name": "manager.md", "worker_unverified": "manager.md",
    "stall": "common.md", "worker_stall": "manager.md", "check_red": "manager.md", "merge_conflict": "integrator.md",
    "resume_loop": "supervisor.md", "stray_browser": "manager.md", "lock_stale": "manager.md",
    "quota_five_hour": "supervisor.md", "quota_seven_day": "supervisor.md", "host_cpu": "supervisor.md", "host_mem": "supervisor.md", "no_integrator": "supervisor.md",
    "blocked": "common.md",
}


def diagnosis_prompt(src, code, project, now=None, days=14):
    """Instruction to hand to a subagent (sonnet, read-only) to diagnose a recurring pattern and propose a PRECISE
    fix: text only, nothing is launched here. An agent given a prompt and its failure mode can diagnose why an agent
    fails and suggest an improvement; here the evidence is taken from the board, not guessed."""
    now = int(now or time.time())
    start = now - days * 86400
    rows = src.query("SELECT t, who, msg FROM events WHERE kind = 'signal' AND subject = ? AND after = '\"open\"' "
                     "AND t >= ? AND t < ? ORDER BY id", (code, start, now + 1))
    defects = src.query("SELECT t, who, msg FROM events WHERE kind = 'defaut' AND t >= ? AND t < ? ORDER BY id DESC LIMIT 10",
                        (start, now + 1))
    fmt = lambda t: datetime.datetime.fromtimestamp(t).strftime("%d/%m %H:%M")
    ev = "\n".join(f"- {fmt(t)} {who}: {msg}" for t, who, msg in rows) or "- (none)"
    df = "\n".join(f"- {fmt(t)} {who}: {msg}" for t, who, msg in defects) or "- (none)"
    role = ROLE_FILES.get(code, "SKILL.md")
    return f"""# Diagnosis of the pattern "{code}" — supervision {project}

You are a READ-ONLY subagent. Do not modify any file of the repository or of the skill.

## Findings
{len(rows)} "{code}" signals opened in {days} days. Known leads: {signals.CODES.get(code, 'unknown')}.

Signals:
{ev}

Defects found by the reviews (same period):
{df}

## To do
1. Read `SKILLS/supervision/{role}` (and `SKILL.md` if different) and the code that detects this pattern (`alerts()` in `TOOLS/supervision/server.py`).
2. Say, with evidence, why the agents make this mistake: missing, ambiguous or ignored instruction, or a tool that leaves it possible.
3. Propose ONE minimal fix, in order of preference: (a) a refusal or a command in the tool, (b) one sentence of instruction in `{role}`, (c) an adjusted threshold, (d) remove the pattern if it is noise.
4. Write the proposal to `INBOX/<host>/YYYY-MM-DD-supervision-diagnostic-{code.replace('_', '-')}.md` (type, title, target, session; Why / Proposal with the exact text or diff / Example). One single proposal, 15 lines at most.
"""


def run(src, project, now=None, days=14, min_n=3, max_new=3, dry=False):
    """Returns {"written": [paths], "skipped": [(code, reason)], "unused": [codes never triggered over 28 days]}."""
    now = int(now or time.time())
    today = datetime.date.fromtimestamp(now)
    start = now - days * 86400
    counts = metrics.signals_by_code(src, start, now + 1)
    out = {"written": [], "skipped": [], "unused": metrics.unused_codes(src, now - 28 * 86400, now + 1)}
    budget = max_new - written_this_week(today)
    for code, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        if n < min_n:
            continue
        if already(code, today):
            out["skipped"].append((code, "already proposed or handled in the last 30 days"))
            continue
        if budget <= 0:
            out["skipped"].append((code, f"cap of {max_new} proposals per week reached"))
            continue
        text = proposal(code, n, days, project, examples(src, code, start, now + 1), today)
        path = Path(config.inbox_dir()) / host() / f"{today.isoformat()}-{_slug(code)}.md"
        if not dry:
            path = config.guard_write(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = config.guard_write(path.with_suffix(".tmp"))
            tmp.write_text(text, encoding="utf-8", newline=chr(10))
            os.replace(tmp, path)
        out["written"].append(str(path))
        budget -= 1
    return out
