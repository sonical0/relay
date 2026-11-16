"""End-of-day close: report, cleanup, Jira closures, backup, evening reminder.

Nothing automatic: the board shows PROPOSALS (GET /api/cloture), the user ticks, edits the drafts and confirms
in two clicks (POST /api/cloture, token required). Only the actions ticked and still proposed at confirmation
time are run: the client only sends ids, never a command (the commands are recomputed here).

  - report: draft entry for `LOG/<host>.md` (dated header, <= 5 bullets of <= 15 words; the time is read again
    from the clock when writing) and draft note `Supervision-<date>.md` in the project's notes directory
    (body of `sup.py report`). Written at the top, atomically (tmp + rename);
  - cleanup: worktrees of merged branches, merged local branches (`git branch -d`, safe), dev servers still
    listening (taskkill, injectable KILL function), orphan check lock. Remote branches: command shown,
    never run;
  - Jira closures: tickets cited by the merged branches, shown only (no Jira call);
  - backup: store.backup() to SUP_BACKUP_DIR/<date>/;
  - board clearing (`clear`, kind `clear` of server.py): last, only if the backup succeeded in the same run
    and nothing is still running (clear_blockers), unless `force_clear`;
  - reminder: at the config's `cloture_reminder` time (default 18:00), once a day, notification
    (if SUP_NOTIFY != 0) and a banner on the board (`/api/state` -> `cloture`).
Roots: SUP_LOG_DIR (log), SUP_VAULT_DIR (note), SUP_BACKUP_DIR, SUP_CHECK_LOCK — through config.guard_write.
"""
import datetime
import json
import os
import re
import shutil
import socket
import subprocess
import time
from pathlib import Path

import config
import hostload
import signals
import store

DEFAULT_REMINDER = "18:00"
DEV_PORTS = (3000, 3001, 3002, 3003, 5173, 8080)  # overridable: the config's `cloture_ports`
LOCK_STALE_SECONDS = 15 * 60
# (?!-\d): "integ/closes-2026-10-15" is a date, not the ticket CLOSES-2026 (false positive).
JIRA_RE = re.compile(r"(?<![A-Za-z0-9])([A-Za-z][A-Za-z0-9]+-\d+)(?!\d)(?!-\d)")
HEADER_RE = re.compile(r"^## (\d{4}-\d\d-\d\d) (\d\d:\d\d) — (.+) \((.+?) ?: (.+)\)$")
MAX_BULLETS, MAX_WORDS = 5, 15


def clock():
    """Local time read from the clock (replaced in tests)."""
    return datetime.datetime.now()


def host():
    return os.environ.get("RELAY_HOST") or os.environ.get("COMPUTERNAME") or socket.gethostname()


def _day_start(now):
    return datetime.datetime.combine(now.date(), datetime.time()).timestamp()


def _git(repo, *args):
    try:
        r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=30, creationflags=config.NO_WINDOW)
        return r.returncode, (r.stdout if r.returncode == 0 else (r.stderr or r.stdout)).strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)


def atomic_write(path, text):
    """Atomic write (tmp + rename), UTF-8 without BOM, under the allowed roots."""
    path = config.guard_write(Path(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = config.guard_write(path.with_name(path.name + ".tmp"))
    tmp.write_bytes(text.encode("utf-8"))
    if tmp.read_bytes().decode("utf-8") != text:  # read back before the swap
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"invalid write: {path}")
    os.replace(tmp, path)
    return path


# --- Report ------------------------------------------------------------------------------------------------

def log_draft(s, cfg, events, now):
    """Log entry taken from the database: today's events, tasks, merge queue."""
    import sup  # late import: sup.py is also a script
    p, base = cfg["project"], cfg["base_branch"]
    tasks, mq, _ = sup.wave(s, _day_start(now))  # today's wave, not the whole state
    done = sum(t.get("status") == "done" for t in tasks)
    merged = sum(m.get("status") == "merged" for m in mq)
    alerts = sum(e.get("who") in ("alert", "alerte") for e in events)
    workers = sum(len(m.get("workers") or {}) for m in s["managers"].values())
    lines = [f"## {now:%Y-%m-%d %H:%M} — Claude ({p}: supervision close)",
             f"- [{p}] {len(s['managers'])} managers, {workers} workers supervised → {done}/{len(tasks)} tasks done",
             f"- [{p}] Merge queue → {merged}/{len(mq)} branches merged into {base}",
             f"- [{p}] Board → {alerts} red alert(s), {len(events)} events logged"]
    return "\n".join(lines) + "\n"


def check_log(text):
    """Log format: dated header then at most 5 bullets of 15 words, nothing else. Returns the lines."""
    lines = [l.rstrip() for l in text.strip().splitlines() if l.strip()]
    if not lines or not HEADER_RE.match(lines[0]):
        raise ValueError("expected header: ## YYYY-MM-DD HH:MM — Agent (project: subject)")
    bullets = lines[1:]
    if not bullets or len(bullets) > MAX_BULLETS:
        raise ValueError(f"between 1 and {MAX_BULLETS} bullets")
    for b in bullets:
        if not b.startswith("- "):
            raise ValueError(f"line that is not a bullet: {b[:40]}")
        if len(b[2:].split()) > MAX_WORDS:
            raise ValueError(f"bullet of more than {MAX_WORDS} words: {b[:40]}…")
    return lines


def write_log(text, now):
    """Add the entry AT THE TOP of LOG/<host>.md (right after the file header), time read again from the clock."""
    lines = check_log(text)
    g = HEADER_RE.match(lines[0]).groups()
    lines[0] = f"## {now:%Y-%m-%d %H:%M} — {g[2]} ({g[3]}: {g[4]})"
    entry = "\n".join(lines) + "\n"
    path = config.guard_write(config.log_dir() / f"{host()}.md")
    old = path.read_text(encoding="utf-8") if path.exists() else (
        f"# Relay log — {host()}\n\n> Entries written on {host()}, most recent first.\n\n")
    i = old.find("\n## ")
    new = (old[:i + 1] + entry + "\n" + old[i + 1:]) if i >= 0 else old.rstrip("\n") + "\n\n" + entry
    return atomic_write(path, new)


def note_draft(s, cfg, now):
    """Body of the note: the one of `sup.py report` (same rendering, same source)."""
    import sup  # late import: sup.py is also a script
    return sup.report_body(s, cfg, _day_start(now), host())


def write_note(text, cfg, now):
    """Note `Supervision-<date>.md`: created, or a new section added at the top (after the title)."""
    day, p = now.strftime("%Y-%m-%d"), cfg["project"]
    path = config.guard_write(Path(cfg["vault_dir"]) / f"Supervision-{day}.md")
    section = f"## Close at {now:%H:%M}\n\n{text.strip()}\n"
    if path.exists():
        old = path.read_text(encoding="utf-8")
        i = old.find("\n## ")
        new = (old[:i + 1] + section + "\n" + old[i + 1:]) if i >= 0 else old.rstrip("\n") + "\n\n" + section
    else:
        new = (f"---\ntags: [{p}, supervision, log]\nupdated: {day}\n"
               f"status: \"Close report, confirmed on the board\"\n---\n\n"
               f"# {p} — supervision of {day}\n\n" + section)
    return atomic_write(path, new)


# --- Cleanup, Jira -----------------------------------------------------------------------------------------

def parse_ss(text):
    """Output of `ss -H -ltnp` -> {port: pid} (typical line: LISTEN 0 511 127.0.0.1:3000 0.0.0.0:* users:(("node",pid=123,fd=19)))."""
    out = {}
    for line in text.splitlines():
        f = line.split()
        m = re.search(r"pid=(\d+)", line)
        if len(f) >= 4 and m:
            try:
                out.setdefault(int(f[3].rsplit(":", 1)[1]), int(m.group(1)))
            except (ValueError, IndexError):
                pass
    return out


def listeners():
    """{port: pid} of the listening TCP ports (netstat on Windows, ss elsewhere; read-only)."""
    if os.name != "nt":
        try:
            r = subprocess.run(["ss", "-H", "-ltnp"], capture_output=True, text=True, errors="replace", timeout=15)
        except (OSError, subprocess.TimeoutExpired):
            return {}
        return parse_ss(r.stdout)
    try:
        r = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True, errors="replace",
                           timeout=15, creationflags=config.NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return {}
    out = {}
    for line in r.stdout.splitlines():
        f = line.split()
        if len(f) >= 5 and f[0] == "TCP" and f[3].upper() in ("LISTENING", "ÉCOUTE", "ECOUTE"):
            try:
                out.setdefault(int(f[1].rsplit(":", 1)[1]), int(f[4]))
            except (ValueError, IndexError):
                pass
    return out


def kill(pid):
    """Stop a dev server confirmed by the user (replaced in tests: no real taskkill)."""
    if os.name != "nt":
        r = subprocess.run(["kill", str(pid)], capture_output=True, text=True, errors="replace", timeout=20)
        return r.returncode == 0, (r.stdout or r.stderr).strip()[:200]
    r = subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, text=True, errors="replace",
                       timeout=20, creationflags=config.NO_WINDOW)
    return r.returncode == 0, (r.stdout or r.stderr).strip()[:200]


LISTENERS, KILL = listeners, kill  # injectable (tests)
PROCS = hostload.list_procs  # injectable (tests): node/npm processes [{pid, name, cmd}]


def _merged(repo, ref):
    rc, out = _git(repo, "branch", "--format=%(refname:short)", "--merged", ref)
    return [b.strip() for b in out.splitlines() if b.strip()] if rc == 0 else []


def _worktrees(repo):
    rc, out = _git(repo, "worktree", "list", "--porcelain")
    wts, cur = [], {}
    for line in (out.splitlines() if rc == 0 else []) + [""]:
        if not line.strip():
            if cur:
                wts.append(cur)
            cur = {}
        elif line.startswith("worktree "):
            cur["path"] = line[9:]
        elif line.startswith("branch "):
            cur["branch"] = line[7:].removeprefix("refs/heads/")
    return wts


def proposals(cfg, lock=None, own_port=None, now=None):
    """Proposed cleanup: [{id, kind, label, cmd, run}]; run=False = command shown only."""
    repo, base = Path(cfg["path"]), cfg["base_branch"]
    keep = {base, "main", "master", "HEAD"}
    out = []
    if (repo / ".git").exists():
        # Merged into origin/<base>, not into the local base: the main clone often lags behind
        # (D pushes from its worktree), and the cleanup would only see older branches.
        _git(repo, "fetch", "-q", "origin")
        ref = f"origin/{base}" if _git(repo, "rev-parse", "--verify", "--quiet", f"origin/{base}")[0] == 0 else base
        merged = [b for b in _merged(repo, ref) if b not in keep]
        wts = _worktrees(repo)
        main_branch = wts[0].get("branch") if wts else None
        if main_branch == base and ref != base:
            rc, n = _git(repo, "rev-list", "--count", f"{base}..{ref}")
            if rc == 0 and n.isdigit() and int(n):
                out.append({"id": "ff", "kind": "ff", "ref": ref,
                            "label": f"Main clone {n} commit(s) behind {ref}: fast-forward it",
                            "cmd": f'git -C "{repo}" merge --ff-only {ref}', "run": True})
        for w in wts[1:]:
            if w.get("branch") in merged:
                out.append({"id": f"wt:{w['branch']}", "kind": "worktree", "path": w["path"], "branch": w["branch"],
                            "label": f"Worktree of the merged branch {w['branch']}",
                            "cmd": f'git -C "{repo}" worktree remove "{w["path"]}"', "run": True})
        for b in merged:
            if b != main_branch:
                out.append({"id": f"br:{b}", "kind": "branch", "branch": b, "label": f"Local branch {b}, already in {base}",
                            "cmd": f'git -C "{repo}" branch -d {b}', "run": True})
        if _git(repo, "rev-parse", "--verify", "--quiet", f"origin/{base}")[0] == 0:
            rc, rem = _git(repo, "branch", "-r", "--format=%(refname:short)", "--merged", f"origin/{base}")
            for b in (rem.splitlines() if rc == 0 else []):
                b = b.strip()
                if b.startswith("origin/") and b[7:] not in keep and b != "origin":
                    out.append({"id": f"rbr:{b[7:]}", "kind": "remote", "branch": b[7:],
                                "label": f"Remote branch {b}, already in {base} (to do by hand)",
                                "cmd": f'git -C "{repo}" push origin --delete {b[7:]}', "run": False})
    skip = {config.RESERVED_PORT, int(cfg.get("port") or 0), int(own_port or 0)}
    ports = cfg.get("cloture_ports") or DEV_PORTS
    for port, pid in sorted(LISTENERS().items()):
        if port in ports and port not in skip:
            out.append({"id": f"port:{port}", "kind": "port", "port": port, "pid": pid,
                        "label": f"Dev server still listening on port {port} (PID {pid})",
                        "cmd": f"taskkill /PID {pid} /T /F" if os.name == "nt" else f"kill {pid}", "run": True})
    dead = [w["path"] for w in (_worktrees(repo)[1:] if (repo / ".git").exists() else []) if not Path(w["path"]).exists()]
    if dead:  # worktrees deleted by hand: their node/npm processes are still running
        for o in hostload.orphans(PROCS(), dead, own_pid=os.getpid()):
            out.append({"id": f"proc:{o['pid']}", "kind": "proc", "pid": o["pid"],
                        "label": f"Orphan process {o['name']} (PID {o['pid']}): its worktree no longer exists ({o['path']})",
                        "cmd": f"taskkill /PID {o['pid']} /T /F" if os.name == "nt" else f"kill {o['pid']}", "run": True})
    lock = Path(lock or cfg["lock"])
    if lock.is_dir():
        owner = lock / "owner"
        age = (now or time.time()) - (owner if owner.is_file() else lock).stat().st_mtime
        if not owner.is_file() or age > LOCK_STALE_SECONDS:
            who = owner.read_text(encoding="utf-8", errors="replace").strip()[:60] if owner.is_file() else "no owner"
            out.append({"id": "lock", "kind": "lock", "path": str(lock),
                        "label": f"Orphan check lock ({who}, for {int(age // 60)} min)",
                        "cmd": f'rmdir /s /q "{lock}"' if os.name == "nt" else f'rm -rf "{lock}"', "run": True})
    return out


def audit(s, cfg, panes=None, open_signals=None, lock=None):
    """Audit of the day's final state: what is left lying around. Read-only, nothing is fixed from here.

    Evaluate the final state of an agent that modifies a persistent state, not only its path: non-empty queue,
    agent still at work, criteria not validated, open signals, dirty or unpushed worktree, forgotten browser tab,
    lock held. Returns [{"level": "warn", "label": ...}], empty = nothing to report."""
    out = []
    add = lambda label: out.append({"level": "warn", "label": label})
    queue = [x for x in s.get("merge_queue", []) if x.get("status") != "merged"]
    if queue:
        add(f"{len(queue)} branch(es) in the merge queue, not merged: " + ", ".join(x["branch"] for x in queue))
    for m in s.get("managers", {}).values():
        if m.get("status") in ("working", "review", "blocked", "waiting"):
            add(f"Manager {m['id']} still \"{m['status']}\": {m.get('task') or '—'}")
        busy = [w["id"] for w in (m.get("workers") or {}).values() if w.get("status") not in ("done", "idle")]
        if busy:
            add(f"Worker(s) still active: {', '.join(busy)}")
    for t in s.get("tasks", {}).values():
        if t.get("status") != "done":
            crit = t.get("criteria") or []
            todo = sum(not c["passes"] for c in crit)
            add(f"Task {t['id']} not done ({t.get('title', '')})" + (f", {todo}/{len(crit)} criterion(s) to validate" if todo else ""))
    for code, who in sorted(open_signals or {}):
        add(f"Signal still open: {code} ({who})")
    repo = Path(cfg["path"])
    for m in s.get("managers", {}).values():
        for wt in m.get("worktrees", []):
            if not Path(wt).is_dir():
                add(f"Worktree missing from disk, still on {m['id']}'s card: {wt}")
                continue
            rc, dirty = _git(wt, "status", "--porcelain")
            if rc == 0 and dirty:
                add(f"Worktree {wt}: {len(dirty.splitlines())} modified file(s) not committed")
            rc, branch = _git(wt, "rev-parse", "--abbrev-ref", "HEAD")
            if rc == 0 and branch and branch != "HEAD":
                if _git(wt, "rev-parse", "--verify", "--quiet", f"origin/{branch}")[0] != 0:
                    add(f"Branch {branch} (worktree {wt}) never pushed to origin")
                else:
                    rc, ahead = _git(wt, "rev-list", "--count", f"origin/{branch}..{branch}")
                    if rc == 0 and ahead.isdigit() and int(ahead):
                        add(f"Branch {branch}: {ahead} commit(s) not pushed")
    dash = (s.get("supervisor") or {}).get("dashboard")
    stray = [x["id"] for p in panes or [] for x in p.get("surfaces", []) if x.get("type") == "browser" and x["id"] != dash]
    if stray:
        add(f"{len(stray)} browser tab(s) left open: " + ", ".join(i[:13] for i in stray))
    lk = Path(lock or cfg["lock"])
    if lk.is_dir():
        add("Check lock still held: " + ((lk / "owner").read_text(encoding="utf-8", errors="replace").strip()[:60]
                                         if (lk / "owner").is_file() else "no owner"))
    return out


def clear_blockers(s, lock):
    """What forbids clearing the board without "force": active agent or worker, open question, unmerged queue,
    check lock held. Empty = clearing allowed."""
    out = []
    for m in s.get("managers", {}).values():
        if m.get("question"):
            out.append(f"open question from Manager {m['id']}")
        elif m.get("status") in ("working", "review", "blocked", "waiting"):
            out.append(f"Manager {m['id']} \"{m['status']}\"")
        out += [f"worker {w['id']} \"{w.get('status')}\"" for w in (m.get("workers") or {}).values()
                if w.get("status") not in ("done", "idle")]
    queue = [x["branch"] for x in s.get("merge_queue", []) if x.get("status") != "merged"]
    if queue:
        out.append(f"{len(queue)} unmerged branch(es) in the queue")
    if Path(lock).is_dir():
        out.append("check lock held")
    return out


def clear_preview(s, lock):
    """What the clearing will erase (counts) and what prevents it, for the close page."""
    return {"tasks": len(s.get("tasks") or {}), "queue": len(s.get("merge_queue") or []),
            "requests": len(s.get("requests") or {}),
            "workers": sum(len(m.get("workers") or {}) for m in s.get("managers", {}).values()),
            "managers": len(s.get("managers") or {}), "objective": bool(s.get("objective")),
            "blockers": clear_blockers(s, lock)}


def jira(s, cfg):
    """Tickets cited by the merged branches (merge queue and git): shown, never closed from here."""
    branches = [m.get("branch") or "" for m in s["merge_queue"] if m.get("status") == "merged"]
    repo = Path(cfg["path"])
    if (repo / ".git").exists():
        branches += _merged(repo, cfg["base_branch"])
    out = {}
    for b in branches:
        for t in JIRA_RE.findall(b):
            out.setdefault(t.upper(), b)
    return [{"ticket": t, "branch": b} for t, b in sorted(out.items())]


# --- Together ------------------------------------------------------------------------------------------------

def draft(srv, now=None):
    """Everything the close page shows (nothing is written)."""
    now = now or clock()
    with srv.LOCK:
        s = srv.load()
    cfg = srv.CFG
    events = srv.STORE.events(kind="log", since_t=_day_start(now))
    try:
        panes = json.loads(srv.wmux("list-panes", out=True) or "{}").get("panes", [])
    except ValueError:
        panes = []
    return {"day": now.strftime("%Y-%m-%d"), "host": host(),
            "log": {"path": str(config.log_dir() / f"{host()}.md"), "text": log_draft(s, cfg, events, now)},
            "note": {"path": str(Path(cfg["vault_dir"]) / f"Supervision-{now:%Y-%m-%d}.md"),
                     "text": note_draft(s, cfg, now)},
            "backup": {"path": str(store.backup_path(cfg["project"], now.strftime("%Y-%m-%d")))},
            "audit": audit(s, cfg, panes, set(signals.replay(srv.STORE.events(kind=signals.KIND))), srv.CHECK_LOCK),
            "cleanup": proposals(cfg, srv.CHECK_LOCK, srv.PORT, now.timestamp()),
            "jira": jira(s, cfg), "clear": clear_preview(s, srv.CHECK_LOCK), "state": status(s, cfg, now)}


def _run(p, cfg):
    repo = Path(cfg["path"])
    if p["kind"] == "ff":
        rc, out = _git(repo, "merge", "--ff-only", p["ref"])  # refused if the clone diverged
        return rc == 0, out or "main clone fast-forwarded"
    if p["kind"] == "worktree":
        rc, out = _git(repo, "worktree", "remove", p["path"])  # without --force: refused if the worktree is dirty
        return rc == 0, out or "worktree removed"
    if p["kind"] == "branch":
        rc, out = _git(repo, "branch", "-d", p["branch"])  # -d: refused if the branch is not merged
        return rc == 0, out
    if p["kind"] in ("port", "proc"):
        return KILL(p["pid"])
    if p["kind"] == "lock":
        shutil.rmtree(config.guard_write(Path(p["path"])))
        return True, "lock removed"
    return False, "action not runnable from the board"


def _clear(srv, s, res, force):
    """Board clearing: refused without a successful backup in this run; refused if something is still running,
    unless `force`. Nothing is touched on refusal."""
    if not any(r["id"] == "backup" and r["ok"] for r in res):
        return False, "refused: the backup must be ticked and succeed in the same confirmation"
    blockers = clear_blockers(s, srv.CHECK_LOCK)
    if blockers and not force:
        return False, "refused (tick \"force\" to override): " + "; ".join(blockers)
    return True, srv.apply(s, {"kind": "clear", "from": "User"}) + (" (forced: " + "; ".join(blockers) + ")" if blockers else "")


ORDER = {"ff": 0, "worktree": 1, "branch": 2, "port": 3, "proc": 3, "lock": 4, "remote": 5}  # ff first: `branch -d` compares to HEAD


def execute(srv, s, body, now=None):
    """Ticked and confirmed actions (called under srv.LOCK). Returns [{id, ok, msg}]; logs the close."""
    now = now or clock()
    cfg = srv.CFG
    want = [str(a) for a in body.get("actions") or []]
    if not want:
        raise ValueError("no action ticked")
    res = []

    def do(aid, fn):
        try:
            ok, msg = fn()
        except Exception as e:  # noqa: BLE001 — a failed action does not prevent the others
            ok, msg = False, str(e)
        res.append({"id": aid, "ok": bool(ok), "msg": str(msg)})

    if "log" in want:
        do("log", lambda: (True, str(write_log(body.get("log_text") or "", now))))
    if "note" in want:
        do("note", lambda: (True, str(write_note(body.get("note_text") or "", cfg, now))))
    if "backup" in want:
        do("backup", lambda: (True, str(srv.STORE.backup(store.backup_path(cfg["project"], now.strftime("%Y-%m-%d"))))))
    props = {p["id"]: p for p in proposals(cfg, srv.CHECK_LOCK, srv.PORT, now.timestamp())}
    rest = [a for a in want if a not in ("log", "note", "backup", "clear")]
    for aid in sorted(rest, key=lambda a: ORDER.get(props[a]["kind"], 9) if a in props else 9):
        p = props.get(aid)
        if p is None:
            res.append({"id": aid, "ok": False, "msg": "unknown action or no longer proposed"})
        elif not p["run"]:
            res.append({"id": aid, "ok": False, "msg": f"to do by hand: {p['cmd']}"})
        else:
            do(aid, lambda p=p: _run(p, cfg))
    if "clear" in want:  # last: after the backup and the cleanup (a removed lock no longer blocks)
        do("clear", lambda: _clear(srv, s, res, bool(body.get("force_clear"))))
    ok = sum(r["ok"] for r in res)
    c = dict(s.get("cloture") or {})
    c["done"], c["done_t"] = now.strftime("%Y-%m-%d"), int(now.timestamp())
    s["cloture"] = c
    srv.log(s, "User", f"End-of-day close: {ok}/{len(res)} action(s) succeeded ("
            + ", ".join(r["id"] + ("" if r["ok"] else " ✗") for r in res) + ")", "info" if ok == len(res) else "warn")
    notify_supervisor(srv, s, res, cfg, now)
    return res


def notify_supervisor(srv, s, res, cfg, now):
    """Otherwise the supervisor only learns about the close by reading the board: it gets the summary and what is left."""
    try:
        rest = proposals(cfg, srv.CHECK_LOCK, srv.PORT, now.timestamp())
    except Exception as e:  # noqa: BLE001 — the summary is sent anyway
        rest = [{"label": f"proposals unreadable: {e}"}]
    failed = [f"{r['id']}: {r['msg'][:300]}" for r in res if not r["ok"]]  # enough for the whole command
    text = (f"[Board — user decision] End-of-day close confirmed: "
            f"{sum(r['ok'] for r in res)}/{len(res)} action(s) succeeded."
            + (" Failures or to do by hand: " + "; ".join(failed) + "." if failed else "")
            + (" Still proposed: " + "; ".join(p["label"] + ("" if p.get("run", True) else f" → {p['cmd']}") for p in rest) + "." if rest else " Nothing left to tidy.")
            + " Take over what is left: commands for the user, manager sessions to stop, report.")
    try:
        srv.send_to(s, "S", text)
    except Exception as e:  # noqa: BLE001 — a completed close must not fail on the message
        srv.log(s, "Board", f"Close summary not delivered to the supervisor: {e}", "warn")


# --- Evening reminder ---------------------------------------------------------------------------------------

def reminder_at(cfg):
    v = str(cfg.get("cloture_reminder") or DEFAULT_REMINDER)
    h, m = (int(x) for x in v.split(":", 1))
    return datetime.time(h, m)


def status(s, cfg, now):
    """For /api/state: reminder time, reminder sent, close done, banner to show."""
    c = s.get("cloture") or {}
    day, at = now.strftime("%Y-%m-%d"), reminder_at(cfg)
    return {"at": at.strftime("%H:%M"), "reminded": c.get("reminded") == day, "done": c.get("done") == day,
            "banner": now.time() >= at and c.get("done") != day}


def tick(srv, now=None):
    """Once a day, at the reminder time and while the day is not closed: notification + log."""
    now = now or clock()
    day = now.strftime("%Y-%m-%d")
    with srv.LOCK:
        s = srv.load()
        st = status(s, srv.CFG, now)
        if not st["banner"] or st["reminded"]:
            return False
        s["cloture"] = {**(s.get("cloture") or {}), "reminded": day}
        msg = f"It is {st['at']}: remember to close the day (board button \"Clôturer la journée\")"
        srv.log(s, "Board", msg, "warn")
        srv.save(s, "watcher")
    if srv.NOTIFY:
        srv.notify(f"Supervision: {msg}")
    return True


def reminder_loop(srv, every=60):
    while True:
        time.sleep(every)
        try:
            tick(srv)
        except Exception as e:  # noqa: BLE001 — the reminder must never die
            print("close (reminder):", e, flush=True)
