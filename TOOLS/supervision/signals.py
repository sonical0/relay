"""Structured error signals: an alert from `alerts()` that carries a `code` becomes an `events` row
(`kind='signal'`, `subject` = code, `who` = agent concerned, `after` = 'open' then 'resolved').

Why: alerts are free text (`kind='log'`), impossible to count by pattern. A signal stays **open** as long as its
condition lasts: when the server restarts, the table is replayed to know which ones still are, so the same fault
is counted only once (before: one alert per restart).
Rows are append-only like all of `events`; `metrics.py` counts them by pattern and by week.
"""

import json
import time

KIND = "signal"

# Known patterns: code -> remedy to examine when it recurs (used by `sup.py improve`).
CODES = {
    "scope_refused": "scope badly framed: widen it at assignment, or sharpen the task framing",
    "undeclared": "files modified without declaration: declare before modifying (`$S files --add`)",
    "collision": "two managers on the same file: cut disjoint scopes",
    "scope_overlap": "scopes overlapping at assignment: make them disjoint before launching the managers",
    "workers_limit": "too many workers launched: review the limit or the split",
    "worker_name": "badly named worker: the skill was not read, or the instruction is ambiguous",
    "worker_unverified": "worker finished without verification: strengthen the review before `--verified`",
    "stall": "silent manager: threshold too short, or task badly bounded",
    "worker_stall": "worker without news: task too broad, or worker looping",
    "check_red": "repeated red checks: red base, or task too big for a single pass",
    "merge_conflict": "merge conflicts: overlapping scopes, or branches living too long",
    "resume_loop": "repeated fresh restarts: the task framing is at fault",
    "stray_browser": "browser outside its pane: use `$S browser` (never bare `wmux browser`)",
    "lock_stale": "check lock left behind: an interrupted check, or a crashed agent",
    "quota_five_hour": "5-hour quota reached: fewer workers in parallel, or lighter models",
    "quota_seven_day": "7-day quota reached: space out supervision days",
    "host_cpu": "host CPU saturated: fewer installs and builds in parallel, or `--lite`",
    "host_mem": "host memory saturated: fewer workers in parallel, or close forgotten dev servers",
    "no_integrator": "no integrator designated: incomplete launch",
    "blocked": "manager blocked: see the cause in the manager's note",
    "mail_unread": "board messages unread despite the pointer: agent not running `$S inbox`, or pane closed",
}


def state_of(row):
    """'open' or 'resolved': `events.after` is stored as JSON by store.py ('"open"')."""
    try:
        return json.loads(row["after"])
    except (TypeError, ValueError):
        return row["after"]


def replay(rows):
    """Signals still open according to the `kind='signal'` rows (chronological order): {(code, who): (msg, level)}."""
    open_ = {}
    for r in rows:
        key = (r["subject"], r["who"])
        state = state_of(r)
        if state == "open":
            open_[key] = (r["msg"], r["level"])
        elif state == "resolved":
            open_.pop(key, None)
    return open_


def diff(open_, alerts):
    """Compare open signals with current alerts.

    Returns (to open, to close): new `code` alerts, and open keys whose condition is gone.
    `open_` is updated (current message, opened, closed): the caller only has to write the rows.
    """
    now = {(a["code"], a["who"]): a for a in alerts if a.get("code")}
    to_open = [a for k, a in now.items() if k not in open_]
    to_close = [(k, open_[k]) for k in list(open_) if k not in now]
    for k, a in now.items():
        open_[k] = (a["msg"], a["level"])
    for k, _ in to_close:
        del open_[k]
    return to_open, to_close


COOLDOWN_SECONDS = 5.0
# Agent state -> notify the user? "waiting" (a prompt, a question) calls for it; "done" (end of turn) does not:
# the mailbox pointer uses it. Distinct keys: a "done" does not clear a "waiting".
AGENT_NOTIFY = {"waiting": True, "done": False, "idle": False, "working": False, "blocked": False}


class Cooldown:
    """Notification de-duplication: a key (agent, type) is notified at most once per `seconds`.
    A burst (flapping alert, prompt reopened right away) yields a single notification."""

    def __init__(self, seconds=COOLDOWN_SECONDS, clock=time.monotonic):
        self.seconds, self.clock, self.last = seconds, clock, {}

    def allow(self, key, t=None):
        t = self.clock() if t is None else t
        last = self.last.get(key)
        if last is not None and t - last < self.seconds:
            return False
        self.last[key] = t
        return True


COOLDOWN = Cooldown()
