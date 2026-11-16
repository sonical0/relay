"""Current goal of a project: a title and a list of tasks, its progress and its estimated time left.

Storage: `state["objective"] = {"title", "tasks": [ids], "t"}`, a `meta` key like `plan` (store.py writes any key
outside the tables into `meta` and logs its changes): no table and no schema version to migrate, a database
without a goal simply lacks the key. Only the server writes it (kind `objective`, `sup.py objectif`).

Progress: each task weighs 1; done = 1; review = 0.9 (reviewed, not delivered yet); doing and blocked = fraction
of ticked criteria, 0 without criteria, capped at 0.9 (a task is only finished when done); todo, unknown: 0.
Time left (ESTIMATE): remaining work (sum of 1 - weight) / recent throughput. Throughput = finished weight (done 1,
review 0.9, at the LAST transition into that status, read from the `change` rows of events) over the last
WINDOW_SECONDS seconds of activity, or since the first finished task if that is shorter (throughput()). Fewer than
MIN_MEASURED finished tasks in the window: no estimate.
Why not a median of per-task durations: tasks moved to doing early, suspended then resumed, or created hours before
being started, measured the waiting time; they all hit the cap. The per-task duration (LAST doing -> done, capped
at CAP_SECONDS) only feeds the tooltip. The end time is wall-clock time: it does not skip the night.
"""
import json
import statistics
import time

WINDOW_SECONDS = 3 * 3600  # sliding throughput window (recent hours of activity)
CAP_SECONDS = 3 * 3600     # cap of a per-task duration: beyond it, mostly time outside activity
MIN_MEASURED = 2           # below that, the throughput means nothing
DOING_CAP = 0.9


def ids_of(values):
    """Cleaned task ids, in order, without duplicates (case is kept: T19c != T19C)."""
    out = []
    for v in values or []:
        for x in str(v).replace(",", " ").split():
            if x not in out:
                out.append(x)
    return out


def update(state, r):
    """Apply an `objective` request (title + tasks: replace; add; remove; clear); return the log line."""
    if r.get("clear"):
        state.pop("objective", None)
        return "Goal removed"
    cur = state.get("objective") or {}
    if "tasks" in r or "title" in r and not cur:
        title = str(r.get("title") or cur.get("title") or "").strip()[:200]
        if not title:
            raise ValueError("goal: title required (--title)")
        tasks = ids_of(r.get("tasks")) if "tasks" in r else list(cur.get("tasks", []))
        cur = {"title": title, "tasks": tasks}
    elif "title" in r:
        cur = {**cur, "title": str(r["title"]).strip()[:200]}
    if not cur:
        raise ValueError("no goal defined: sup.py objectif --title \"...\" --tasks T1 T2 ...")
    tasks = list(cur.get("tasks", []))
    tasks += [x for x in ids_of(r.get("add")) if x not in tasks]
    gone = set(ids_of(r.get("remove")))
    cur = {**cur, "tasks": [x for x in tasks if x not in gone], "t": int(time.time())}
    state["objective"] = cur
    return f"Goal: {cur['title']} ({len(cur['tasks'])} task(s): {' '.join(cur['tasks']) or '—'})"


def weight(task):
    """Done share of a task, between 0 and 1: done 1; review 0.9; doing and blocked = fraction of ticked criteria,
    capped at 0.9; todo, unknown or without criteria: 0."""
    if not task:
        return 0.0
    st = task.get("status")
    if st == "done":
        return 1.0
    if st == "review":  # reviewed, not delivered yet: even without criteria
        return DOING_CAP
    if st not in ("doing", "blocked"):
        return 0.0
    crit = task.get("criteria") or []
    if not crit:
        return 0.0
    return min(DOING_CAP, sum(1 for c in crit if c.get("passes")) / len(crit))


def history(store, ids):
    """{id: {"created", "doing", "review", "done"}} from the `change` rows of events: LAST transition into each
    status (a task suspended then resumed counts from its resumption)."""
    ids = list(ids)
    if not ids or store is None:
        return {}
    subjects = [f"task:{i}" for i in ids] + [f"task:{i}.status" for i in ids]
    out = {}
    for n in range(0, len(subjects), 400):  # SQLite parameter limit
        part = subjects[n:n + 400]
        rows = store.query("SELECT t, subject, before, after FROM events WHERE kind = 'change' AND subject IN "
                           f"({', '.join('?' * len(part))}) ORDER BY id", part)
        for t, subject, before, after in rows:
            tid, _, field = subject[5:].partition(".")
            h = out.setdefault(tid, {})
            try:
                val = json.loads(after) if after is not None else None
            except ValueError:
                continue
            if not field:  # creation (before = nothing): the whole object
                if before is not None or not isinstance(val, dict):
                    continue
                h.setdefault("created", t)
                val = val.get("status")
            if val in ("doing", "review", "done"):
                h[val] = t
    return out


def durations(ids, tasks, hist, cap=CAP_SECONDS):
    """Durations (seconds, capped) of finished tasks: last transition into doing -> done (tooltip only)."""
    out = []
    for tid in ids:
        h = hist.get(tid) or {}
        if (tasks.get(tid) or {}).get("status") != "done" or "done" not in h:
            continue
        start = h.get("doing", h.get("created"))
        if start is None or h["done"] <= start:
            continue
        out.append(min(cap, h["done"] - start))
    return out


def completions(ids, tasks, hist):
    """Sorted [(t, weight)] of finished or reviewed tasks: done -> 1 at the last move to done, review -> 0.9 at its own."""
    out = []
    for tid in ids:
        st, h = (tasks.get(tid) or {}).get("status"), hist.get(tid) or {}
        if st in ("done", "review") and st in h:
            out.append((h[st], 1.0 if st == "done" else DOING_CAP))
    return sorted(out)


def throughput(done, now, window=WINDOW_SECONDS, min_n=MIN_MEASURED):
    """Recent throughput: (tasks per hour, window length in s, tasks finished in the window), or None.

    The window ends now, or at the last finished task if nothing moved for more than `window` (night, break: not
    counted). It starts `window` earlier, or at the first finished task if that is more recent; in that case
    this first task only bounds the window and does not count in the throughput (its work happened before).
    Fewer than `min_n` finished tasks in the window: None.
    """
    if not done:
        return None
    end = now if now - done[-1][0] <= window else done[-1][0]
    start = max(end - window, done[0][0])
    inside = [(t, w) for t, w in done if start <= t <= end]
    if len(inside) < min_n or end <= start:
        return None
    counted = inside[1:] if start == done[0][0] else inside
    work = sum(w for _, w in counted)
    if work <= 0:
        return None
    return work * 3600 / (end - start), end - start, len(inside)


def fmt_duration(sec):
    m = int(round(sec / 60))
    return f"{m} min" if m < 60 else f"{m // 60} h {m % 60:02d}"


def fmt_end(t, now):
    lt, ln = time.localtime(t), time.localtime(now)
    return time.strftime("%H:%M" if lt[:3] == ln[:3] else "%d/%m %H:%M", lt)


def fmt_rate(rate, window_s):
    """"observed throughput: 1.5 tasks/h over the last 2.5 hours" ("over the last 40 min" under 1 h)."""
    r = f"{rate:.1f}"
    if window_s >= 3600:
        return f"observed throughput: {r} tasks/h over the last {window_s / 3600:.1f} hours"
    return f"observed throughput: {r} tasks/h over the last {fmt_duration(window_s)}"


def compute(objective, tasks, hist, now=None):
    """Computed progress of the goal (None without a goal): what /api/state, the board and sup.py read."""
    if not objective:
        return None
    now = int(now or time.time())
    ids = list(objective.get("tasks") or [])
    rows = []
    for tid in ids:
        t = tasks.get(tid)
        crit = (t or {}).get("criteria") or []
        rows.append({"id": tid, "status": (t or {}).get("status", "unknown"), "title": (t or {}).get("title", ""),
                     "weight": round(weight(t), 3), "criteria": [sum(1 for c in crit if c.get("passes")), len(crit)]})
    n = len(rows)
    done = sum(1 for x in rows if x["status"] == "done")
    total = sum(x["weight"] for x in rows)
    remaining = sum(1 - x["weight"] for x in rows)
    d = durations(ids, tasks, hist)
    out = {"title": objective.get("title", ""), "n": n, "done": done, "percent": round(100 * total / n) if n else 0,
           "remaining_work": round(remaining, 3), "measured": 0, "median_s": int(statistics.median(d)) if d else None,
           "rate_per_h": None, "window_s": None, "rate_text": None, "remaining_s": None, "end_t": None, "eta": None,
           "tasks": rows, "left": [x for x in rows if x["status"] != "done"]}
    if n and done == n:
        out.update(remaining_s=0, eta="finished")
        return out
    tp = throughput(completions(ids, tasks, hist), now)
    if tp:
        rate, win, k = tp
        rest = int(remaining / rate * 3600)
        out.update(measured=k, rate_per_h=round(rate, 2), window_s=int(win), rate_text=fmt_rate(rate, win),
                   remaining_s=rest, end_t=now + rest,
                   eta=f"≈ {fmt_duration(rest)} left, done around {fmt_end(now + rest, now)} (estimate)")
    return out


def line(o):
    """One line for the supervisor's sheet and the CLI: "Goal: ... 64 %, ≈ 2 h 10"."""
    if not o:
        return ""
    est = ("finished" if o["eta"] == "finished" else f"≈ {fmt_duration(o['remaining_s'])} (estimate)"
           if o["remaining_s"] is not None else "time left — (fewer than 2 tasks finished recently)")
    return f"Goal: {o['title']} · {o['percent']} %, {o['done']}/{o['n']} tasks, {est}"


def status(state, store, now=None):
    """Progress of the goal of `state` (None without a goal), history read from `store`."""
    obj = state.get("objective")
    if not obj:
        return None
    return compute(obj, state.get("tasks") or {}, history(store, obj.get("tasks") or []), now)
