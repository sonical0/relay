"""Supervision history and metrics: READ-ONLY queries on the database (store.py).

    import metrics
    m = metrics.compute(STORE, "day")                 # server: GET /api/metrics?scope=day|wave|week
    src = metrics.FileSource(db)                       # sup.py metrics: database file opened read-only
    text = metrics.weekly_note(src, "example-app")     # weekly note (LOG/supervision/supervision-weekly.md)

Where the numbers come from (everything is read back from `events`, `quota`, `sessions`, `tasks`, `waves`):
  - phases of a task: `change` rows of `task:<id>` (creation = framing, status `review`, `ready`,
    `done`/`merged`) and of `merge:<branch>` when the branch or the queue note cites the task id;
  - review round trips: moving from `review` to a status other than ready or merged;
  - scope widenings: `manager:<X>.scope` gaining patterns; refusals: the manager's "Declaration refused" log
    line; collisions: "Collision: ... by A, C" alerts; all attached to the manager's active tasks;
  - trains: moves to `merged` in the queue, grouped (gap <= TRAIN_GAP); duration from the start of the full check
    before them ("Full check" line written by `sup.py check --full`), else from the first merge;
  - quota used: sum of the increases of `used_percentage` between two samples of the same window;
  - cost: cumulated cost (statusline) of each session, attached to the period of its last sample
    (the database keeps only the last sample of each session: no intra-session delta);
  - defects: event rows of kind `defaut` (`sup.py note X --defaut "..."`).
The log line prefixes are matched in English and in their older French form (databases written before the port).
A state migrated from state.json only has a log: the tables then say "no data yet".
"""
import datetime
import json
import os
import re
import time
from pathlib import Path

import config
import signals
import store

SCOPES = ("day", "wave", "week")
PERIODS = {"day": 14, "week": 8}
TRAIN_GAP = 30 * 60         # two merges less than 30 min apart: same train
CHECK_BEFORE = 3 * 3600     # a full check at most 3 h before the train's first merge belongs to it
REVIEW = {"review"}
READY = {"ready", "prête", "prete"}
MERGED = {"merged", "done", "fusionnée", "fusionnee"}
NOTE_NAME = "supervision-weekly.md"
# Log line prefixes (English, then the older French form kept for existing databases).
REFUSED = ("Declaration refused", "Déclaration refusée")
FULL_CHECK = ("Full check", "Check complet")
COLLISION = ("Collision",)
ALERT_WHO = ("alerte", "alert")


class FileSource:
    """Database opened read-only (sup.py metrics, server stopped or not)."""

    def __init__(self, db):
        if not Path(db).exists():
            raise FileNotFoundError(f"database missing: {db}")
        self._conn = store.connect(db, readonly=True)

    def query(self, sql, args=()):
        return self._conn.execute(sql, args).fetchall()

    def close(self):
        self._conn.close()


def _phase(status):
    s = str(status or "").lower()
    return "review" if s in REVIEW else "ready" if s in READY else "merged" if s in MERGED else None


def _load(v):
    try:
        return json.loads(v) if v is not None else None
    except ValueError:
        return v


def _id_re(tid):
    return re.compile(rf"(?<![A-Za-z0-9]){re.escape(tid)}(?![0-9])", re.I)


def _events(src):
    rows = src.query(
        "SELECT id, t, who, kind, subject, msg, before, after FROM events WHERE "
        "(kind = 'change' AND (subject LIKE 'task:%' OR subject LIKE 'merge:%' OR subject LIKE 'manager:%.scope')) "
        "OR (kind = 'log' AND (" + " OR ".join("msg LIKE ?" for _ in (*REFUSED, *FULL_CHECK)) +
        f" OR (who IN ({', '.join('?' * len(ALERT_WHO))}) AND msg LIKE ?))) OR kind = 'defaut' ORDER BY t, id",
        (*(p + "%" for p in (*REFUSED, *FULL_CHECK)), *ALERT_WHO, COLLISION[0] + "%"))
    return [{"id": i, "t": t, "who": w, "kind": k, "subject": s or "", "msg": m or "", "before": _load(b),
             "after": _load(a)} for i, t, w, k, s, m, b, a in rows]


def _collision_ids(msg):
    m = re.search(r" (?:by|par) ([A-Z0-9, ]+)$", msg)
    return [x.strip() for x in m.group(1).split(",")] if m else []


def _manager_of(who):
    m = re.match(r"Manager (\S+)", who or "")
    return m.group(1).upper() if m else None


def history(src):
    """Dated facts taken from the events: tasks, merges, widenings, refusals, collisions, checks, defects."""
    evs = _events(src)
    # An id is reused from one wave to the next (yesterday's T1, deleted, then today's T1): every creation
    # that follows a deletion opens a GENERATION, its own task. Without it, yesterday's tasks closed in the
    # morning counted as delivered today, and today's tasks got mixed up with them.
    tasks = {}  # internal key (id#n) -> task
    gens = {}   # id -> keys of its generations, the last one is current
    gone = set()  # ids whose current generation was deleted
    branches = {}  # branch -> text where ids are looked for (name + note)
    merges, widen, refus, coll, checks, defects = [], [], [], [], [], []

    def blank(tid):
        return {"id": tid, "manager": "", "status": "", "title": "", "start": None,
                "review": None, "ready": None, "merged": None, "review_rounds": 0}

    def task(tid, fresh=False):
        if tid not in gens or fresh:
            key = f"{tid}#{len(gens.get(tid, []))}"
            gens.setdefault(tid, []).append(key)
            tasks[key] = blank(tid)
            gone.discard(tid)
        return tasks[gens[tid][-1]]

    def mark(t, phase, when):
        if not phase:
            return
        if t[phase] is None or (phase == "merged" and when < t[phase]):
            t[phase] = when  # merged: the earliest (queue merge before a late close of the status)

    for e in evs:
        subj, t = e["subject"], e["t"]
        if e["kind"] == "change" and subj.startswith("task:"):
            tid, _, field = subj[5:].partition(".")
            if not field:
                if e["after"] is None:  # task deleted: its id may be reused
                    if tid in gens:
                        gone.add(tid)
                    continue
                if isinstance(e["after"], dict):
                    x = task(tid, fresh=not isinstance(e["before"], dict))
                    x["start"] = x["start"] or t
                    x["manager"] = x["manager"] or e["after"].get("manager") or ""
                    x["title"] = x["title"] or e["after"].get("title") or ""
                    mark(x, _phase(e["after"].get("status")), t)
                continue
            x = task(tid)
            x["start"] = x["start"] or t
            if field == "status":
                if _phase(e["before"]) == "review" and _phase(e["after"]) not in ("ready", "merged", "review"):
                    x["review_rounds"] += 1
                mark(x, _phase(e["after"]), t)
        elif e["kind"] == "change" and subj.startswith("merge:"):
            b, _, field = subj[6:].partition(".")
            if not field and isinstance(e["after"], dict):
                branches[b] = f"{b} {e['after'].get('note') or ''}"
                st = e["after"].get("status")
            elif field == "note":
                branches[b] = f"{b} {e['after'] or ''}"
                continue
            elif field == "status":
                st = e["after"]
            else:
                continue
            ph = _phase(st)
            if ph in ("ready", "merged"):
                merges.append({"t": t, "branch": b, "phase": ph, "text": branches.get(b, b)})
        elif e["kind"] == "change" and subj.startswith("manager:"):
            before, after = e["before"] or [], e["after"] or []
            if isinstance(after, list) and set(after) - set(before if isinstance(before, list) else []):
                widen.append({"t": t, "manager": subj[8:].split(".")[0]})
        elif e["kind"] == "defaut":
            defects.append({"t": t, "who": e["who"], "manager": e["subject"], "msg": e["msg"]})
        elif e["msg"].startswith(REFUSED):
            refus.append({"t": t, "manager": _manager_of(e["who"])})
        elif e["msg"].startswith(COLLISION):
            coll.append({"t": t, "managers": _collision_ids(e["msg"])})
        elif e["msg"].startswith(FULL_CHECK):
            m = re.search(r"(\d+) min", e["msg"])
            checks.append({"t": t, "start": t - 60 * int(m.group(1)) if m else t, "ok": "rc=0" in e["msg"]})
    # Tasks of the table without events (migrated state): one generation each; title and manager filled in.
    for tid, man, status, title in src.query("SELECT id, manager, status, title FROM tasks"):
        x = task(tid)
        x["manager"], x["title"] = x["manager"] or man or "", x["title"] or title or ""
        x["status"] = status or x["status"]
    # Queue branches: ready / merged apply to the generation of the cited task that was current at that time.
    regs = {tid: _id_re(tid) for tid in gens}
    for mg in merges:
        for tid, rx in regs.items():
            if not rx.search(mg["text"]):
                continue
            live = [tasks[k] for k in gens[tid] if tasks[k]["start"] is None or tasks[k]["start"] <= mg["t"]]
            if live:
                mark(live[-1], mg["phase"], mg["t"])
    for x in tasks.values():
        end = x["merged"] or time.time()
        inside = lambda ev, who: x["start"] is not None and x["start"] <= ev["t"] <= end and who
        x["widenings"] = sum(1 for w in widen if inside(w, w["manager"] == x["manager"]))
        x["refusals"] = sum(1 for r in refus if inside(r, r["manager"] == x["manager"]))
        x["collisions"] = sum(1 for c in coll if inside(c, x["manager"] in c["managers"]))
        x["d_cadrage"] = _d(x["start"], x["review"] or x["ready"])
        x["d_review"] = _d(x["review"], x["ready"])
        x["d_merge"] = _d(x["ready"], x["merged"])
        x["d_total"] = _d(x["start"], x["merged"])
    return {"tasks": tasks, "merges": merges, "widen": widen, "refus": refus, "coll": coll, "checks": checks,
            "defects": defects, "trains": _trains(merges, checks)}


def _d(a, b):
    return b - a if a is not None and b is not None and b >= a else None


def _trains(merges, checks):
    out = []
    for mg in sorted((m for m in merges if m["phase"] == "merged"), key=lambda m: m["t"]):
        if out and mg["t"] - out[-1]["end"] <= TRAIN_GAP:
            out[-1]["end"] = mg["t"]
            out[-1]["branches"].append(mg["branch"])
        else:
            out.append({"first": mg["t"], "end": mg["t"], "branches": [mg["branch"]]})
    for tr in out:
        before = [c for c in checks if tr["first"] - CHECK_BEFORE <= c["t"] <= tr["first"]]
        tr["start"] = before[-1]["start"] if before else tr["first"]
        tr["duration"] = tr["end"] - tr["start"]
    return out


def _quota(src, start, end):
    """Quota points used in [start, end[, per window (five_hour, seven_day)."""
    out = {}
    for kind in ("five_hour", "seven_day"):
        rows = src.query("SELECT used_percentage, resets_at, t FROM quota WHERE kind = ? AND t < ? ORDER BY t, id",
                         (kind, end))
        used, prev = 0.0, None
        for v, r, t in rows:
            if v is None:
                continue
            if t >= start:
                if prev is None or prev[1] != r:
                    used += v if prev is not None else 0
                else:
                    used += max(0.0, v - prev[0])
            prev = (v, r)
        out[kind] = round(used, 1)
    return out


def _periods(src, scope, now):
    day0 = datetime.datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0)
    if scope == "day":
        return [(d.strftime("%Y-%m-%d"), d.strftime("%d/%m"), d.timestamp(), (d + datetime.timedelta(days=1)).timestamp())
                for d in (day0 - datetime.timedelta(days=i) for i in range(PERIODS["day"] - 1, -1, -1))]
    if scope == "week":
        monday = day0 - datetime.timedelta(days=day0.weekday())
        out = []
        for i in range(PERIODS["week"] - 1, -1, -1):
            d = monday - datetime.timedelta(weeks=i)
            y, w, _ = d.isocalendar()
            out.append((f"{y}-W{w:02d}", f"W{w:02d}", d.timestamp(), (d + datetime.timedelta(weeks=1)).timestamp()))
        return out
    return [(str(i), name or f"wave {i}", s, e or now + 1)
            for i, name, s, e in src.query("SELECT id, name, start_t, end_t FROM waves ORDER BY id")]


def compute(src, scope="day", now=None):
    """Metrics per period (day, week or wave) and per task. `src`: Store or FileSource (query)."""
    if scope not in SCOPES:
        raise ValueError(f"unknown scope: {scope} (expected: {', '.join(SCOPES)})")
    now = int(now or time.time())
    h = history(src)
    sessions = [dict(zip(("sid", "who", "model", "cost", "first_t", "t"), r))
                for r in src.query("SELECT sid, who, model, cost, first_t, t FROM sessions ORDER BY t")]
    periods = []
    for key, label, a, b in _periods(src, scope, now):
        within = lambda t: t is not None and a <= t < b
        trains = [tr for tr in h["trains"] if within(tr["end"])]
        checks = [c for c in h["checks"] if within(c["t"])]
        sess = [x for x in sessions if within(x["t"]) and x["cost"] is not None]
        periods.append({
            "key": key, "label": label, "start": int(a), "end": int(b),
            "delivered": sum(1 for x in h["tasks"].values() if within(x["merged"])),
            "trains": len(trains),
            "train_min": round(sum(tr["duration"] for tr in trains) / len(trains) / 60) if trains else None,
            "full_checks": len(checks), "full_checks_ok": sum(c["ok"] for c in checks),
            "quota": _quota(src, a, b),
            "cost": round(sum(x["cost"] for x in sess), 2), "sessions": len(sess),
            "defects": sum(1 for d in h["defects"] if within(d["t"])),
            "refusals": sum(1 for r in h["refus"] if within(r["t"])),
            "collisions": sum(1 for c in h["coll"] if within(c["t"])),
            "widenings": sum(1 for w in h["widen"] if within(w["t"])),
        })
    tasks = sorted((x for x in h["tasks"].values() if x["start"] is not None),
                   key=lambda x: x["start"], reverse=True)[:60]
    keys = ("delivered", "trains", "full_checks", "cost", "defects", "refusals", "collisions", "widenings")
    empty = not tasks and not any(p[k] for p in periods for k in keys) \
        and not any(v for p in periods for v in p["quota"].values())
    lo = periods[0]["start"] if periods else 0
    return {"scope": scope, "now": now, "periods": periods, "tasks": tasks, "empty": empty,
            "defects": [d for d in h["defects"] if d["t"] >= lo][-50:],
            "sessions": sorted((x for x in sessions if (x["t"] or 0) >= lo and x["cost"] is not None),
                               key=lambda x: -x["cost"])[:20]}


# --- weekly note ------------------------------------------------------------------------------------------

def _dur(s):
    if s is None:
        return "—"
    m = int(s // 60)
    return f"{m // 60} h {m % 60:02d}" if m >= 60 else f"{m} min"


def weekly_note(src, project, now=None):
    """Markdown note with a small frontmatter (tags, updated)."""
    m = compute(src, "week", now)
    today = datetime.date.fromtimestamp(m["now"]).isoformat()
    L = ["---", "tags: [relay, supervision, metrics]", f"updated: {today}", "---", "",
         "# Supervision — weekly review", "",
         f"> Generated by `sup.py metrics --weekly --vault` from the supervision database ({project}); do not edit. "
         "Used to follow whether the system improves from one week to the next.", ""]
    if m["empty"] and not _signals_section(src, m["periods"], quiet=True):
        L += ["No data yet: the history (status changes, merge queue, quota, defects) "
              "accumulates as the board runs.", ""]
        return "\n".join(L)
    L += ["## Per week", "",
          "| Week | Tasks delivered | Trains | Avg train duration | Full checks (green) | Quota 5 h | Quota 7 d "
          "| Session cost | Defects | Refusals | Collisions | Widenings |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for p in m["periods"]:
        L.append(f"| {p['key']} | {p['delivered']} | {p['trains']} | "
                 f"{'—' if p['train_min'] is None else str(p['train_min']) + ' min'} | "
                 f"{p['full_checks']} ({p['full_checks_ok']}) | {p['quota']['five_hour']} pts | "
                 f"{p['quota']['seven_day']} pts | {p['cost']:.2f} $ | {p['defects']} | {p['refusals']} | "
                 f"{p['collisions']} | {p['widenings']} |")
    week = m["periods"][-1]
    done = [x for x in m["tasks"] if x["merged"] and week["start"] <= x["merged"] < week["end"]]
    L += ["", f"## Tasks delivered in {week['key']}", ""]
    L += ["| Task | Manager | Framing → review | Review → ready | Ready → merged | Total | Round trips "
          "| Widenings | Refusals | Collisions |", "|---|---|---|---|---|---|---|---|---|---|"] if done else ["- none"]
    L += [f"| {x['id']} {x['title']} | {x['manager'] or '—'} | {_dur(x['d_cadrage'])} | {_dur(x['d_review'])} | "
          f"{_dur(x['d_merge'])} | {_dur(x['d_total'])} | {x['review_rounds']} | {x['widenings']} | {x['refusals']} | "
          f"{x['collisions']} |" for x in done]
    defects = [d for d in m["defects"] if d["t"] >= week["start"]]
    L += ["", "## Defects found by the reviews", ""]
    L += [f"- {datetime.datetime.fromtimestamp(d['t']).strftime('%d/%m %H:%M')} ({d['manager'] or '—'}) {d['msg']}"
          for d in defects] or ["- none"]
    L += _signals_section(src, m["periods"])
    return "\n".join(L) + "\n"


def signals_by_code(src, start, end):
    """Signals opened in [start, end[, per pattern: {code: count} (events kind='signal', after='"open"': JSON, see store._insert_event)."""
    rows = src.query("SELECT subject, COUNT(*) FROM events WHERE kind = 'signal' AND after = '\"open\"' "
                     "AND t >= ? AND t < ? GROUP BY subject", (int(start), int(end)))
    return {code: n for code, n in rows}


def unused_codes(src, start, end):
    """Known patterns (signals.CODES) with no opening at all in [start, end[."""
    seen = signals_by_code(src, start, end)
    return sorted(c for c in signals.CODES if c not in seen)


def _signals_section(src, periods, quiet=False):
    """Error signals per pattern, current and previous week: a pattern that does not go down = a rule to revisit."""
    if not periods:
        return []
    cur = signals_by_code(src, periods[-1]["start"], periods[-1]["end"])
    prev = signals_by_code(src, periods[-2]["start"], periods[-2]["end"]) if len(periods) > 1 else {}
    if quiet:  # only: are there signals to show?
        return list(cur) + list(prev)
    L = ["", "## Error signals per pattern", ""]
    if not cur and not prev:
        return L + ["- none"]
    L += [f"| Pattern | {periods[-1]['key']} | {periods[-2]['key'] if len(periods) > 1 else 'previous'} |", "|---|---|---|"]
    for code in sorted(set(cur) | set(prev), key=lambda c: (-cur.get(c, 0), c)):
        L.append(f"| {code} | {cur.get(code, 0)} | {prev.get(code, 0)} |")
    unused = unused_codes(src, periods[0]["start"], periods[-1]["end"])
    if unused:  # an alert that never fires is a rule to question (protocol noise, or nothing to watch)
        L += ["", f"Never triggered over {len(periods)} weeks: " + ", ".join(unused) + "."]
    return L


def vault_note_path():
    """`<root>/supervision/supervision-weekly.md`; root LOG/ by default, overridable by SUP_VAULT_DIR (tests)."""
    root = os.environ.get("SUP_VAULT_DIR") or str(config.RELAY_ROOT / "LOG")
    return Path(root) / "supervision" / NOTE_NAME


def write_note(text, dest=None):
    """Atomic write (tmp + rename), UTF-8 without BOM, LF line endings; through config.guard_write."""
    dest = config.guard_write(Path(dest or vault_note_path()))
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = config.guard_write(dest.with_name(dest.name + ".tmp"))
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, dest)
    return dest
