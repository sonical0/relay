"""Client of the supervision board (stdlib).

Project: `python sup.py [--project <p>] <command> …` (default: SUP_PROJECT, else example-app). Project config
(port, base branch, check scripts, lock, directory): the `supervision:` block of the brain, through config.py.

Managers:
  python sup.py manager B --status working --task "TASK-1 login form" --branch feat/task-1 --progress 30 --msg "started"
  python sup.py manager B --worktree <worktree path>   (scanned by git every 20 s; one per worktree)
  python sup.py manager B --worktree-remove <path>     removes a worktree from the card (deleted, merged);
                                                       a vanished directory is removed by itself after two scans
  python sup.py files B --add src/worker/lease.js      BEFORE editing; refused outside the scope
  python sup.py files B --remove src/worker/lease.js | --clear
  python sup.py worker B2 --status working --task "lease tests" [--model opus|sonnet|haiku]
  python sup.py worker B2 --status done --verified --msg "reviewed, check green"
  python sup.py request B "add the leaseDaily feature flag to shared/feats.js" --files shared/feats.js
  python sup.py ask B "Widen my scope to x.js?" [--choices '[{"id":"yes","label":"Yes","key":"1"}]']
                                                       question to the board, the supervisor and wmux (report-agent)
  python sup.py ask B --done                           question settled: the blocked state is lifted
Integrator:
  python sup.py request-done R3 --msg "done in 4f2a1c0"   (--status refused to refuse)
Lock of `npm run check` (replaces a manual mkdir/rmdir; records who holds it):
  python sup.py lock take B      -> exit code 1 and the holder's name if already taken: wait and retry
  python sup.py lock release B
  python sup.py lock status
Supervisor:
  python sup.py merge feat/x --sha abc1234 --to B --note "..." [--pos 3] [--status waiting|ready|merged|blocked]
  python sup.py merge feat/x --rm
  python sup.py watch [--once]     monitor: one line per change (statuses, requests, alerts, waiting screens)
  python sup.py report [--vault]   today's report (reports/YYYY-MM-DD.md; --vault also copies it to the vault dir)
  python sup.py backup [--dest <file>]   database backup (default SUP_BACKUP_DIR/<date>/supervision-<project>.db)
  python sup.py assign B --domain "Worker & lease" --scope "src/worker/*" "test/worker/*" --surface surf-... --session s-2
  python sup.py assign D --role integrator
  python sup.py assign S --surface surf-... --session s-1 [--dashboard surf-...]   (the supervisor itself)
  python sup.py task T1 --title "..." --to B --status todo|doing|review|done|blocked
  python sup.py event "message" [--level info|warn|error] [--from B]
  python sup.py objectif --title "…" --tasks T16 T17 …   current goal (replaces); --add T34 …, --remove T17, --clear;
                                                         no option: progress and estimated time left

Check (under the lock, queued): python sup.py check B [--cwd <worktree>] [--full] [--dry-run]
  quick = the config's `check.quick` steps, full = `check.full`; default cwd = the project directory.
  Step = an npm script name, or an argument list run as is (projects without npm, e.g. a Python project).

Statuses: idle, working, waiting, review, blocked, done. Progress: 0 to 100.
Server: SUP_URL variable (default http://127.0.0.1:<config port>, 8765 by default).
Writes (lock, reports): the roots of config.py (SUP_CHECK_LOCK, SUP_STATE_ROOT, SUP_VAULT_DIR); in test mode,
any write outside them and any call to port 8765 are refused.
"""
import argparse
import datetime
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import config  # noqa: E402
import hostload  # noqa: E402
import objectif  # noqa: E402
import store  # noqa: E402

# Set by setup(project): brain config, loaded once (Node, ~0.3 s).
CFG = {}
BASE = os.environ.get("SUP_URL", "http://127.0.0.1:8765")
URL = BASE + "/api/report"
CHECK_LOCK = Path(os.environ.get("SUP_CHECK_LOCK") or config.RELAY_ROOT / "PROJECTS" / config.DEFAULT_PROJECT / ".claude" / "check.lock")


def setup(project):
    global CFG, BASE, URL, CHECK_LOCK
    CFG = config.load(project)
    BASE = os.environ.get("SUP_URL") or f"http://127.0.0.1:{CFG['port']}"
    URL = BASE + "/api/report"
    CHECK_LOCK = Path(CFG["lock"])
    if config.test_mode():  # never the live server from a test
        config.check_isolation()
        config.check_port(urllib.parse.urlsplit(BASE).port or 80)


def show_inbox(text):
    """`sup.py inbox`: messages in plain text, one per block (escaped JSON cost ~10 % more to read)."""
    try:
        msgs = json.loads(text).get("result") or []
    except (ValueError, AttributeError):
        return text
    if not msgs:
        return "[] (no message)"
    head = f"=== {len(msgs)} message(s) from the board, to handle now ==="
    return "\n".join([head, "\n---\n".join(map(str, msgs))])


def post(payload, render=None):
    req = urllib.request.Request(URL, json.dumps(payload).encode("utf-8"), {"Content-Type": "application/json"})
    # The server may restart for a few seconds: retry instead of failing (a failure broke the manager's command
    # chain, and an incomplete commit once went out for that reason).
    for attempt in range(8):
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                text = r.read().decode("utf-8")
                print(render(text) if render else text)
                try:  # without wmux, the board cannot type into the terminal: it leaves here what is waiting for you
                    msgs = json.loads(text).get("messages") or []
                except (ValueError, AttributeError):
                    msgs = []
                if msgs:
                    print("=== Messages from the board (handle now) ===")
                    print(*msgs, sep="\n---\n")
                return None
        except urllib.error.HTTPError as e:
            sys.exit(f"refused: {e.read().decode('utf-8')}")
        except urllib.error.URLError as e:
            if attempt == 7:
                sys.exit(f"server unreachable ({URL}) after 8 attempts: {e.reason}")
            time.sleep(2)


def lock(op, who, log=None):
    owner = CHECK_LOCK / "owner"
    if op == "status":
        if not CHECK_LOCK.is_dir():
            return print("free")
        return print(owner.read_text(encoding="utf-8") if owner.is_file() else "taken (unknown holder)")
    if op == "take":
        try:
            config.guard_write(CHECK_LOCK).mkdir(parents=True)  # atomic: a single winner; parent directory created if missing
        except FileExistsError:
            held = owner.read_text(encoding="utf-8").strip() if owner.is_file() else "unknown"
            sys.exit(f"lock already taken by: {held}")
        owner.write_text(f"Manager {who.upper()} · {time.strftime('%H:%M')}", encoding="utf-8")
        if log:  # the board then follows the check progress (step, tests passed)
            (CHECK_LOCK / "log").write_text(str(Path(log).resolve()), encoding="utf-8")
        return print(f"lock taken by Manager {who.upper()}")
    if op == "release":
        config.guard_write(CHECK_LOCK)
        held = owner.read_text(encoding="utf-8") if owner.is_file() else ""
        if held and not held.startswith(f"Manager {who.upper()} "):
            sys.exit(f"refused: the lock belongs to {held}")
        owner.unlink(missing_ok=True)
        (CHECK_LOCK / "log").unlink(missing_ok=True)
        CHECK_LOCK.rmdir()
        return print("lock released")


def step_command(step, cwd):
    """One check step -> (argv, label). String: npm script (`npm run <step>`). List: explicit command, for projects
    without npm (e.g. pytest, ruff); a first argument containing a path separator is resolved from the check
    directory, so the worktree's venv is used rather than the project's."""
    if isinstance(step, str):
        return ["npm.cmd" if os.name == "nt" else "npm", "run", step], step
    argv = [str(a) for a in step]
    if "/" in argv[0] or "\\" in argv[0]:
        argv[0] = str((Path(cwd) / argv[0]).resolve())
    return argv, config.step_label(step)


def summary_lines(text):
    """Summary lines of a check log: Node test runner, Next.js build, pytest."""
    import re
    return [l.strip() for l in text.splitlines() if l.startswith(("ℹ tests", "ℹ pass", "ℹ fail", "ℹ skipped"))
            or "Compiled successfully" in l or re.search(r"\b\d+ (passed|failed|errors?)\b", l)
            or l.startswith("All checks passed") or re.match(r"Found \d+ errors?", l)]


def check(who, cwd, full, dry_run=False):
    """Check under the lock, queued: quick (no build) by default, full with --full.

    Config scripts: `check.quick` (e.g. typecheck, lint, test), `check.full` (e.g. check).
    The build (slow on a loaded host) runs only once, on the merged tree of the merge train.
    Log in %TEMP%/<project>-check-<X>.log, followed live by the board.
    """
    import subprocess
    import tempfile
    who = who.upper()
    project = CFG["project"]
    cwd = cwd or CFG["path"]
    full_script = CFG["check"]["full"]
    steps = (full_script if isinstance(full_script, list) else [full_script]) if full else list(CFG["check"]["quick"])
    # Log in %TEMP%; in test mode, under the test state root (no write outside the roots).
    log = (Path(CFG["run_dir"]) if config.test_mode() else Path(tempfile.gettempdir())) / f"{project}-check-{who}.log"
    if dry_run:
        return print(json.dumps({"project": project, "config": CFG["source"], "mode": "full" if full else "quick",
                                 "steps": [step_command(x, cwd)[0] for x in steps], "cwd": str(cwd), "lock": str(CHECK_LOCK),
                                 "log": str(log)}, ensure_ascii=False, indent=2))
    owner = CHECK_LOCK / "owner"
    waited = False
    config.guard_write(CHECK_LOCK)
    while True:
        try:
            CHECK_LOCK.mkdir()
            break
        except FileExistsError:
            if not waited:
                held = owner.read_text(encoding="utf-8").strip() if owner.is_file() else "unknown"
                print(f"lock taken by {held}: waiting for my turn…", flush=True)
                waited = True
            time.sleep(15)
    rc, t0 = 1, time.time()
    try:
        owner.write_text(f"Manager {who} · {time.strftime('%H:%M')}", encoding="utf-8")
        (CHECK_LOCK / "log").write_text(str(log), encoding="utf-8")
        with open(log, "w", encoding="utf-8", errors="replace") as out:
            for step in steps:
                argv, label = step_command(step, cwd)
                out.write(f"\n> {project} {label}\n")  # step marker read by the board (in full mode, npm writes them itself)
                out.flush()
                # A step that never returns (handle left open after the tests) used to hold the lock forever.
                limit = (45 if full else 20) * 60
                try:
                    rc = subprocess.run(argv, cwd=cwd, stdout=out, stderr=subprocess.STDOUT, timeout=limit).returncode
                except subprocess.TimeoutExpired:
                    out.write(f"\n✖ {label}: no end after {limit // 60} min, stopped by sup.py\n")
                    rc = 124
                if rc != 0:
                    break
    finally:
        # On Windows, the server reading "owner" at the same instant makes the deletion fail (WinError 32).
        for f in (owner, CHECK_LOCK / "log"):
            _unlink_retry(f)
        CHECK_LOCK.rmdir()
    text = log.read_text(encoding="utf-8", errors="replace")
    summary = summary_lines(text)
    _post_quiet({"kind": "check", "id": who, "rc": rc, "full": full})  # counts consecutive red checks (signal check_red)
    print(f"check {'full' if full else 'quick (no build)'}: rc={rc} · log {log}")
    print(" · ".join(summary))
    if full:  # metrics: trains and full checks, read back by metrics.py ("Full check: rc=…, … min")
        _event_quiet(f"Manager {who}", f"Full check: rc={rc}, {round((time.time() - t0) / 60)} min",
                     "info" if rc == 0 else "warn")
    sys.exit(rc)


def slot_run(name, who, argv, cwd=None, n=None, dry_run=False):
    """Run `argv` when a slot of semaphore `name` is free (hostload.Slots): one `npm ci` at a time under 8 threads,
    two above. The queue is visible on the board. The slot is released even if the command fails or is interrupted;
    a slot whose `sup.py` died is taken over by the next one. Returns the command's exit code."""
    import subprocess
    who = who.upper()
    prof = hostload.profile(cfg=CFG)
    n = n or (prof["npm_ci"] if name == "install" else 1)
    cwd = cwd or CFG["path"]
    if dry_run or not n:  # host_limits: false (or dry run): no cap
        print(json.dumps({"slot": name, "places": n, "cwd": str(cwd), "argv": argv, "poste": prof["why"]}, ensure_ascii=False))
        return 0 if dry_run else subprocess.run(argv, cwd=cwd).returncode
    slots = hostload.Slots(Path(CFG["run_dir"]) / "slots", name, n, guard=config.guard_write)
    d = slots.acquire(who, say=lambda m: print(m, flush=True))
    try:
        print(f"slot {name} taken ({n} at most on this host: {prof['why']})", flush=True)
        return subprocess.run(argv, cwd=cwd).returncode
    finally:
        slots.release(d)


def wave(s, start):
    """Tasks, merge queue and requests of the wave: touched since `start` (start of day) and since the last
    clearing (`clear` or `reset`: `cleared_t`), so an old wave is not counted again."""
    since = max(start, s.get("cleared_t") or 0)
    tasks = [t for t in s["tasks"].values() if (t.get("updated") or 0) >= since]
    queue = [m for m in s["merge_queue"] if (m.get("t") or 0) >= since]
    reqs = [r for r in s["requests"].values() if (r.get("t") or 0) >= since]
    return tasks, queue, reqs


def report_body(s, cfg, start, host=None):
    """Body of today's report (Markdown), from a state shaped like /api/state; shared with cloture.py."""
    hm = lambda t: datetime.datetime.fromtimestamp(t).strftime("%H:%M")
    ev = [e for e in s["events"] if e["t"] >= start][::-1]
    ms = sorted(s["managers"].values(), key=lambda m: m["id"])
    tasks, queue, reqs = wave(s, start)
    done = [t for t in tasks if t["status"] == "done"]
    merged = [m for m in queue if m["status"] == "merged"]
    incidents = [e for e in ev if e["who"] in ("alert", "alerte")]
    workers = sum(len(m["workers"]) for m in ms)
    p, base = cfg["project"], cfg["base_branch"]
    L = [f"## Draft entry for LOG/{host or socket.gethostname()}.md (to review; read the time from the clock)", "",
         f"- [{p}] {len(ms)} managers supervised, {workers} workers → {len(done)}/{len(tasks)} tasks done",
         f"- [{p}] Merge queue: {len(merged)}/{len(queue)} branches merged into {base}",
         f"- [{p}] {len(incidents)} red alert(s) raised by the board",
         f"- [{p}] Integrator: {sum(r['status'] == 'done' for r in reqs)}/{len(reqs)} requests on common files handled",
         "", "## Managers", ""]
    for m in ms:
        L.append(f"- **Manager {m['id']}**{' (integrator)' if m['role'] == 'integrator' else ''} — {m['domain'] or '—'} : "
                 f"{m['task'] or '—'} · {m['status']} · {m['progress']} % · branch `{m['branch'] or '—'}` · "
                 f"{len(m['real_files'])} file(s) changed · workers "
                 + (", ".join(f"{k} ({w.get('model', 'opus')})" for k, w in sorted(m["workers"].items())) or "none"))
    L += ["", "## Tasks", "", "| # | Task | Manager | Status | Note |", "|---|---|---|---|---|"]
    L += [f"| {t['id']} | {t['title']} | {t['manager']} | {t['status']} | {t['note']} |" for t in tasks] or ["| — | | | | |"]
    L += ["", f"## Merge queue into {base}", "", "| # | Branch | SHA | Manager | Status | Note |", "|---|---|---|---|---|---|"]
    L += [f"| {i} | `{m['branch']}` | `{m['sha']}` | {m['manager']} | {m['status']} | {m['note']} |"
          for i, m in enumerate(queue, 1)] or ["| — | | | | | |"]
    L += ["", "## Requests to the integrator", ""]
    L += [f"- {r['id']} ({r['from']}, {r['status']}) : {r['what']}" + (f" → {r['answer']}" if r["answer"] else "") for r in reqs] or ["- none"]
    L += ["", "## Today's red alerts", ""]
    L += [f"- {hm(e['t'])} {e['msg']}" for e in incidents] or ["- none"]
    L += ["", "## Today's log", ""]
    L += [f"- {hm(e['t'])} **{e['who']}** — {e['msg']}" for e in ev]
    return "\n".join(L) + "\n"


def report(to_vault):
    with urllib.request.urlopen(BASE + "/api/state", timeout=10) as r:
        s = json.load(r)
    today = datetime.date.today().isoformat()
    start = datetime.datetime.combine(datetime.date.today(), datetime.time()).timestamp()
    body = report_body(s, CFG, start)
    p = CFG["project"]
    out = config.guard_write(Path(CFG["run_dir"]) / "reports" / f"{today}.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(f"# Supervision {p} — {today}\n\n" + body, encoding="utf-8")
    print(out)
    if to_vault:
        dst = config.guard_write(Path(CFG["vault_dir"]) / f"Supervision-{today}.md")
        dst.parent.mkdir(parents=True, exist_ok=True)
        fm = (f"---\ntags: [{p}, supervision, journal]\nupdated: {today}\n"
              f"status: \"Report generated by the supervision board, to review\"\n---\n\n")
        dst.write_text(fm + f"# {p.upper()} — supervision of {today}\n\n" + body, encoding="utf-8")
        print(dst)


def objectif_show():
    """Current goal and its progress, read from /api/state (computed by objectif.py)."""
    try:
        with urllib.request.urlopen(BASE + "/api/state", timeout=10) as r:
            o = json.load(r).get("objective_status")
    except urllib.error.URLError as e:
        sys.exit(f"server unreachable ({BASE}): {e.reason}")
    if not o:
        return print("no goal (sup.py objectif --title \"…\" --tasks T1 T2 …)")
    print(objectif.line(o))
    print(o["eta"] or "time left: — (fewer than 2 tasks finished in the last 3 hours of activity)")
    if o["rate_text"]:
        print(f"{o['rate_text']} ({o['measured']} task(s) finished in the window) · work left: {o['remaining_work']:g} task(s)")
    if o["median_s"]:
        print(f"for information, median duration per task (last doing → done, capped at {objectif.CAP_SECONDS // 3600} h): "
              f"{objectif.fmt_duration(o['median_s'])}")
    for x in o["left"]:
        c = x["criteria"]
        print(f"  {x['id']:<8} {x['status']:<8} {round(100 * x['weight']):>3} %" + (f"  criteria {c[0]}/{c[1]}" if c[1] else "")
              + (f"  {x['title']}" if x["title"] else ""))


def backup(dest=None):
    """Database backup through sqlite3's .backup API, READ-ONLY, without going through the server.

    Why not through the server: in WAL mode a reader sees a consistent snapshot without disturbing the single writer;
    the backup therefore works with the server stopped (switch-over, crash) as well as running, with no new HTTP
    route, and sup.py never writes to the database (only to the backup file, under SUP_BACKUP_DIR).
    """
    db = Path(CFG["run_dir"]) / store.DB_NAME
    out = store.backup_file(db, Path(dest) if dest else store.backup_path(CFG["project"]))
    print(out)


def metrics_cmd(scope, weekly, to_vault):
    """Metrics, READ-ONLY on the database, without going through the server (like backup).

    --weekly: weekly Markdown note; --vault writes it to metrics.vault_note_path() (root overridable with
    SUP_VAULT_DIR, written through config.guard_write: never the real directory in test mode).
    """
    import metrics
    db = Path(CFG["run_dir"]) / store.DB_NAME
    if not db.exists():
        return print(f"No data yet: database missing ({db}), the server has not written it yet.")
    src = metrics.FileSource(db)
    try:
        if not weekly:
            return print(json.dumps(metrics.compute(src, scope), ensure_ascii=False, indent=2))
        text = metrics.weekly_note(src, CFG["project"])
    finally:
        src.close()
    print(metrics.write_note(text) if to_vault else text)


def create_tab(title):
    """New browser tab, SHOWN, in the caller's pane: wmux-tab.mjs returns {"surfaceId", "paneId"}."""
    import subprocess
    import wmuxcli
    base, env = wmuxcli.wmux_cmd()
    r = subprocess.run([base[0], str(HERE / "wmux-tab.mjs"), "--title", title, "--show"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=30, env=env, creationflags=config.NO_WINDOW)
    if r.returncode != 0:
        sys.exit(f"tab not created: {(r.stderr or r.stdout).strip()[:200]} (fallback: work without a browser, do not loop)")
    return json.loads(r.stdout)["surfaceId"]


def browser(who, op, arg):
    """`sup.py browser X open <url> | eval <js> | close`: the only trap-free path to wmux browser.

    One tab per agent (id in run/<project>/browser-<X>.id), always driven with --surface, navigated by eval, never
    `open`, never towards a hidden or closed tab (which creates three empty panes): what the agent can no longer do
    wrong, the skill no longer has to repeat."""
    import backend
    import wmuxcli
    if not backend.enabled():
        sys.exit("without wmux, `sup.py browser` is not available: use your session's browser tool "
                 "(your own tab, closed at the end) and do not touch the others' browser.")
    idf = config.guard_write(Path(CFG["run_dir"]) / f"browser-{who}.id")
    sid = idf.read_text(encoding="utf-8").strip() if idf.is_file() else ""
    if op == "open":
        if not arg.startswith(("http://", "https://")):
            sys.exit("browser open expects an http(s) URL")
        if sid:
            sys.exit(f"a tab is already open for {who} ({sid[:13]}): `browser {who} eval` or `browser {who} close`")
        sid = create_tab(f"Check {who}")
        idf.parent.mkdir(parents=True, exist_ok=True)
        idf.write_text(sid, encoding="utf-8")
        wmuxcli.wmux("browser", "eval", f"location.href={json.dumps(arg)}; 'ok'", "--surface", sid)
        return print(f"tab {sid} open on {arg}: `browser {who} eval \"<js>\"` to read, `browser {who} close` at the end")
    if not sid:
        sys.exit(f"no tab open for {who}: `browser {who} open <url>` first")
    if op == "eval":
        print(wmuxcli.wmux("browser", "eval", arg, "--surface", sid, out=True) or "(no answer: tab closed? `close`, then `open`)")
    else:  # close
        wmuxcli.wmux("close-surface", sid)
        idf.unlink(missing_ok=True)
        print(f"tab {sid} closed: your terminal is back in front")


def _state_quiet():
    """/api/state, or {} if the server does not answer (side read: a manager's registered surface)."""
    try:
        with urllib.request.urlopen(BASE + "/api/state", timeout=5) as r:
            return json.load(r)
    except (OSError, ValueError):
        return {}


def ask(who, question, choices, done):
    """`sup.py ask X "<question>" [--choices '<json>']` / `sup.py ask X --done`: a manager's question.

    Always to the board (card blocked, question in the log, supervisor notified), and to wmux when possible:
    `report-agent --blocked|--unblocked` through the client resolved by wmuxcli (never the `wmux` on PATH, missing
    outside wmux terminals and broken on some hosts). Surface: WMUX_SURFACE_ID, else the registered one."""
    import backend
    import wmuxcli
    if done:
        post({"kind": "ask", "from": f"Manager {who}", "id": who, "done": True})
        args = ["report-agent", "--unblocked"]
    else:
        if not question:
            sys.exit('question expected: sup.py ask X "<question>" [--choices \'<json>\']')
        parsed = []
        if choices:
            try:
                parsed = json.loads(choices)
            except ValueError as e:
                sys.exit(f"--choices is not valid JSON: {e}")
            if not isinstance(parsed, list):
                sys.exit("--choices expects a JSON list: [{\"id\":\"yes\",\"label\":\"Yes\",\"key\":\"1\"}, …]")
        post({"kind": "ask", "from": f"Manager {who}", "id": who, "question": question, "choices": parsed})
        args = ["report-agent", "--blocked", question] + (["--choices", json.dumps(parsed, ensure_ascii=False)] if parsed else [])
    if not backend.enabled():
        return print("without wmux: " + ("question lifted on the board." if done else
                     "question left on the board and in the supervisor's mailbox; the answer arrives with your next "
                     "`sup.py` call (or `sup.py inbox " + who + "`)."))
    if not os.environ.get("WMUX_SURFACE_ID"):
        sf = ((_state_quiet().get("managers") or {}).get(who) or {}).get("surface", "")
        if not sf:
            return print("wmux: neither WMUX_SURFACE_ID nor a registered pane, question on the board only")
        args += ["--surface", sf]
    ok = wmuxcli.wmux(*args)
    print(f"wmux ({wmuxcli.WMUX}) : " + ("question shown (\"Needs you\")" if ok and not done else "state lifted" if ok
                                          else "failed, the question stays on the board"))


def improve_cmd(days, min_n, max_new, dry, diagnose=None):
    """INBOX proposals from recurring signals (improve.py), READ-ONLY on the database."""
    import improve
    import metrics
    db = Path(CFG["run_dir"]) / store.DB_NAME
    if not db.exists():
        return print(f"No data yet: database missing ({db}).")
    src = metrics.FileSource(db)
    if diagnose:
        try:
            text = improve.diagnosis_prompt(src, diagnose, CFG["project"], days=days)
        finally:
            src.close()
        out = config.guard_write(Path(CFG["run_dir"]) / f"diagnose-{diagnose}.md")
        out.write_text(text, encoding="utf-8", newline=chr(10))
        return print(f"instruction written: {out} (hand it to a read-only sonnet subagent: \"read {out} and do it\")")
    try:
        res = improve.run(src, CFG["project"], days=days, min_n=min_n, max_new=max_new, dry=dry)
    finally:
        src.close()
    for p in res["written"]:
        print(("(dry run) " if dry else "proposal written: ") + p)
    for code, why in res["skipped"]:
        print(f"skipped: {code} ({why})")
    if not res["written"] and not res["skipped"]:
        print(f"no pattern reached {min_n} signals in {days} days")
    if res["unused"]:
        print("never triggered in 28 days: " + ", ".join(res["unused"]))


SKILL_DIR = config.RELAY_ROOT / "SKILLS" / "supervision"
# Files served per role, in order; a tuple restricts to the named sections.
GUIDES = {
    "manager": ["common.md", "manager.md"],
    "integrator": ["common.md", "manager.md", "integrator.md"],
    "supervisor": ["common.md", "supervisor.md"],
    "worker": [("manager.md", ("Worktrees", "Checks", "Browser", "Workers"))],
    "no-wmux": ["no-wmux.md"],
    "common": ["common.md"],
}
GUIDE_ALIASES = {"s": "supervisor", "superviseur": "supervisor", "integrateur": "integrator",
                 "sans-wmux": "no-wmux", "commun": "common"}


def guide_role(role):
    """Guide role from a name or a letter: S -> supervisor, the config's integrator -> integrator, A1 -> worker."""
    r = str(role).strip().lower().replace("é", "e")
    if r in GUIDES:
        return r
    if r in GUIDE_ALIASES:
        return GUIDE_ALIASES[r]
    if len(r) >= 2 and r[0].isalpha() and r[1:].isdigit():
        return "worker"
    if len(r) == 1 and r.isalpha():
        return "integrator" if r.upper() == str(CFG.get("integrator") or "").upper() else "manager"
    sys.exit(f"guide: unknown role '{role}' (expected: {', '.join(GUIDES)}, or a letter)")


def _sections(text):
    """[(title, block)] of the `## ` sections of a skill file (the preamble before the first one has no title)."""
    out, title, buf = [], "", []
    for line in text.splitlines():
        if line.startswith("## "):
            out.append((title, "\n".join(buf).strip()))
            title, buf = line[3:].strip(), [line]
        elif not line.startswith("# "):
            buf.append(line)
    out.append((title, "\n".join(buf).strip()))
    return [(t, b) for t, b in out if b]


def guide(role, section=None):
    """Text of a role's guide (`$S guide`), optionally reduced to the sections whose title contains `section`.
    Each call is logged in run/<project>/guide-calls.jsonl: if it comes back more than twice per session, the guide
    deserves to be injected into the resume sheet."""
    r = guide_role(role)
    parts = []
    for item in GUIDES[r]:
        name, keep = item if isinstance(item, tuple) else (item, None)
        for title, block in _sections((SKILL_DIR / name).read_text(encoding="utf-8")):
            if keep and not any(title.startswith(k) for k in keep):
                continue
            if section and section.lower() not in title.lower():
                continue
            parts.append(block)
    try:
        log = config.guard_write(Path(CFG["run_dir"]) / "guide-calls.jsonl")
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": int(time.time()), "role": r, "section": section or "",
                                "agent": os.environ.get("SUP_AGENT", "")}) + "\n")
    except (OSError, RuntimeError, KeyError):
        pass
    if not parts:
        return f"guide {r}: no section contains '{section}'"
    return f"# Guide {r}" + (f" — sections '{section}'" if section else "") + "\n\n" + "\n\n".join(parts)


def new_request_id():
    import uuid
    return uuid.uuid4().hex[:16]


def share(who, worktree):
    """Link the config's `shared_dirs` directories into `worktree` (shared.py) and say what was done."""
    import shared
    dirs = CFG.get("shared_dirs") or []
    if not dirs:
        return print("share: no shared directory in the config (`shared_dirs` of the supervision block)")
    for d, what in shared.link_shared(CFG["path"], worktree, dirs):
        print(f"share {who.upper()}: {d} {what}")


def _unlink_retry(path, tries=10):
    """Delete a file another process may hold open for an instant (Windows: PermissionError)."""
    for i in range(tries):
        try:
            return path.unlink(missing_ok=True)
        except PermissionError:
            if i == tries - 1:
                raise
            time.sleep(0.2)


def _post_quiet(body):
    """POST to the board without retry or exit if the server does not answer."""
    req = urllib.request.Request(URL, json.dumps(body).encode("utf-8"), {"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=5).close()
    except (OSError, ValueError):
        pass


def _event_quiet(who, msg, level="info"):
    """Log line on the board, without retry or exit if the server does not answer (metrics: full checks)."""
    req = urllib.request.Request(URL, json.dumps({"kind": "event", "from": who, "msg": msg, "level": level}).encode("utf-8"),
                                 {"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=5).close()
    except (OSError, ValueError):
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=None, help=f"brain project (default: SUP_PROJECT, else {config.DEFAULT_PROJECT})")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("manager")
    p.add_argument("id")
    for k in ("status", "task", "branch", "session", "worktree", "msg", "level"):
        p.add_argument(f"--{k}")
    p.add_argument("--worktree-remove", dest="worktree_remove", help="remove this worktree from the manager's card")
    p.add_argument("--progress", type=int)

    p = sub.add_parser("assign")
    p.add_argument("id")
    for k in ("domain", "session", "surface", "role", "dashboard"):
        p.add_argument(f"--{k}")
    p.add_argument("--scope", nargs="+")

    p = sub.add_parser("worker")
    p.add_argument("id")
    for k in ("status", "task", "msg", "level"):
        p.add_argument(f"--{k}")
    p.add_argument("--model", choices=["opus", "sonnet", "haiku"])
    p.add_argument("--verified", action="store_true")
    p.add_argument("--remove", action="store_true")

    p = sub.add_parser("files")
    p.add_argument("id")
    p.add_argument("--add", nargs="+", default=[])
    p.add_argument("--remove", nargs="+", default=[])
    p.add_argument("--clear", action="store_true")

    p = sub.add_parser("submit")  # end of a manager's task: moves to review with SHA, check, files, deviations
    p.add_argument("id")
    p.add_argument("--request-id", dest="request_id", help="id of the submission (default: new); sent again as is, "
                   "the board returns the result already recorded instead of delivering twice")
    p.add_argument("--sha", required=True, help="git rev-parse HEAD of the pushed branch")
    p.add_argument("--check", required=True, choices=["green", "red"], help="result of `check X` (red: refused)")
    p.add_argument("--files", type=int, default=0, help="number of files changed")
    p.add_argument("--note", default="", help="deviations, if any (one line)")

    p = sub.add_parser("guide")  # role guide (SKILLS/supervision/), served on demand instead of rereading the skill
    p.add_argument("role", help="manager, integrator, supervisor, worker, no-wmux, common; or a letter (S, A, D, A1)")
    p.add_argument("--section", help="only the sections whose title contains this text")

    p = sub.add_parser("inbox")  # pending messages for X (or S): without wmux, every sup.py call shows them too
    p.add_argument("id", nargs="?", default="S")

    p = sub.add_parser("ask")  # a manager's question: board + supervisor + wmux report-agent (resolved path)
    p.add_argument("id")
    p.add_argument("question", nargs="?", default="")
    p.add_argument("--choices", help="wmux JSON list: [{\"id\":…,\"label\":…,\"key\":\"1\"}]")
    p.add_argument("--done", action="store_true", help="question settled: lifts the blocked state (board and wmux)")

    p = sub.add_parser("watch")  # supervisor's monitor: one line per useful change (Monitor tool)
    p.add_argument("--once", action="store_true", help="a single pass (tests, quick look)")
    p.add_argument("--interval", type=int, default=30, help="seconds between two passes")
    p.add_argument("--no-screens", action="store_true", help="without reading the managers' screens")

    p = sub.add_parser("browser")  # safe browser tab: a new tab, in front, in your pane, closed at the end
    p.add_argument("id")
    p.add_argument("op", choices=["open", "eval", "close"])
    p.add_argument("arg", nargs="?", default="", help="open: the URL; eval: the JavaScript")

    p = sub.add_parser("improve")  # recurring signals -> proposals in INBOX/ (reviewed by a human)
    p.add_argument("--days", type=int, default=14)
    p.add_argument("--min", dest="min_n", type=int, default=3, help="minimum occurrences of a pattern")
    p.add_argument("--max", dest="max_new", type=int, default=3, help="proposals per week at most")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--diagnose", metavar="CODE", help="write the instruction for a subagent that diagnoses this pattern (run/<project>/diagnose-<code>.md)")

    p = sub.add_parser("request")
    p.add_argument("id")
    p.add_argument("what")
    p.add_argument("--files", nargs="+", default=[])
    p.add_argument("--request-id", dest="request_id", help="id of the submission (default: new), as for submit")

    p = sub.add_parser("share")  # shared directories (config shared_dirs): junction to those of the main checkout
    p.add_argument("id")
    p.add_argument("--cwd", required=True, help="worktree to link")

    p = sub.add_parser("request-done")
    p.add_argument("rid")
    p.add_argument("--msg", default="")
    p.add_argument("--status", default="done")
    p.add_argument("--from", dest="frm", default="integrator")

    p = sub.add_parser("task")
    p.add_argument("id")
    for k in ("title", "to", "status", "note"):
        p.add_argument(f"--{k}")
    p.add_argument("--brief", help="brief: goal, files, deliverable, out of scope (free text, 1500 characters at most)")
    p.add_argument("--criterion", action="append", default=[], metavar="TEXT", help="verifiable done criterion, repeatable")
    p.add_argument("--pass", dest="passed", type=int, action="append", default=[], metavar="N", help="criterion N verified (1, 2…)")
    p.add_argument("--fail", dest="failed", type=int, action="append", default=[], metavar="N", help="criterion N to verify again")
    p.add_argument("--clear-criteria", action="store_true")

    p = sub.add_parser("objectif")  # the project's current goal: progress bar at the top of the board
    p.add_argument("--title")
    p.add_argument("--tasks", nargs="+", help="ids of the goal's tasks (replaces the list)")
    p.add_argument("--add", nargs="+", default=[])
    p.add_argument("--remove", nargs="+", default=[])
    p.add_argument("--clear", action="store_true")

    p = sub.add_parser("event")
    p.add_argument("msg")
    p.add_argument("--level", default="info")
    p.add_argument("--from", dest="frm", default="superviseur")

    p = sub.add_parser("project")
    p.add_argument("name")

    sub.add_parser("reset")
    sub.add_parser("end")  # end of supervision, board stopped: removes the project's panes from index.json

    p = sub.add_parser("note")  # note repeated in X's resume sheet (S = supervisor)
    p.add_argument("id")
    p.add_argument("text", nargs="?", default="")
    p.add_argument("--clear", action="store_true")
    p.add_argument("--defaut", help="defect found by a verification (counted in the metrics)")

    p = sub.add_parser("plan")  # plan of the current wave, cited in the resume sheets
    p.add_argument("path")

    p = sub.add_parser("check")  # check under the lock, queued; quick by default, --full for the build
    p.add_argument("id")
    p.add_argument("--cwd", default=None, help="worktree to run the check in (default: project directory)")
    p.add_argument("--full", action="store_true", help="full check, build included (merge train)")
    p.add_argument("--dry-run", action="store_true", help="show scripts, directory and lock without running anything")

    p = sub.add_parser("install")  # npm ci under a semaphore: one at a time under 8 threads, two above
    p.add_argument("id")
    p.add_argument("--cwd", default=None, help="worktree to install (default: project directory)")
    p.add_argument("--dry-run", action="store_true")

    p = sub.add_parser("slot")  # any heavy command on the same principle: slot NAME X -- <command…>
    p.add_argument("name")
    p.add_argument("id")
    p.add_argument("--cwd", default=None)
    p.add_argument("--places", type=int, default=None, help="concurrent slots (default: 1; `install`: depends on the host)")
    p.add_argument("--dry-run", action="store_true")

    p = sub.add_parser("pushed")  # right after a push: consumes the go-ahead (single use)
    p.add_argument("id")
    p.add_argument("--msg")

    p = sub.add_parser("drop")  # remove a manager (test, session closed)
    p.add_argument("id")

    p = sub.add_parser("lock")
    p.add_argument("op", choices=["take", "release", "status"])
    p.add_argument("id", nargs="?", default="?")
    p.add_argument("--log", help="log of npm run check (redirected): the board shows its progress")

    p = sub.add_parser("merge")
    p.add_argument("branch")
    for k in ("sha", "to", "note", "status"):
        p.add_argument(f"--{k}")
    p.add_argument("--pos", type=int)
    p.add_argument("--rm", action="store_true")

    p = sub.add_parser("report")
    p.add_argument("--vault", action="store_true")

    p = sub.add_parser("backup")  # database backup (read-only, server running or not)
    p.add_argument("--dest", help="backup file (default: SUP_BACKUP_DIR/<date>/supervision-<project>.db)")

    p = sub.add_parser("metrics")  # metrics (read-only on the database); --weekly: weekly note
    p.add_argument("--scope", choices=["day", "wave", "week"], default="day")
    p.add_argument("--weekly", action="store_true")
    p.add_argument("--vault", action="store_true", help="with --weekly: writes the weekly note (metrics.vault_note_path())")

    argv, tail = sys.argv[1:], []
    if "--" in argv:  # `slot NAME X [options] -- command…`: everything after `--` is the command, argparse leaves it alone
        i = argv.index("--")
        argv, tail = argv[:i], argv[i + 1:]
    a = ap.parse_args(argv)
    a.argv = tail
    setup(a.project)
    d = {k: v for k, v in vars(a).items() if v is not None and v is not False and k not in ("cmd", "project")}

    if a.cmd == "manager":
        post({"kind": "manager", "from": f"Manager {a.id}", **d})
        if a.worktree and CFG.get("shared_dirs"):
            share(a.id, a.worktree)
    elif a.cmd == "assign":
        if a.id.upper() == "S":  # the supervisor is not a manager: its pane, its session, the board tab
            return post({"kind": "supervisor", "from": "superviseur",
                         **{k: d[k] for k in ("surface", "session", "dashboard") if k in d}})
        d.pop("dashboard", None)
        post({"kind": "assign", "from": "superviseur", **d})
    elif a.cmd == "worker":
        if a.remove:
            return post({"kind": "remove-worker", "id": a.id})
        post({"kind": "worker", **d})
    elif a.cmd == "files":
        post({"kind": "files", "id": a.id, "add": a.add, "remove": a.remove, "clear": a.clear})
    elif a.cmd == "submit":
        post({"kind": "submit", "from": f"Manager {a.id.upper()}", "id": a.id, "sha": a.sha, "check": a.check,
              "files": a.files, "note": a.note, "request_id": a.request_id or new_request_id()})
    elif a.cmd == "inbox":
        post({"kind": "inbox", "from": f"Manager {a.id.upper()}", "id": a.id.upper()}, render=show_inbox)
    elif a.cmd == "ask":
        ask(a.id.upper(), a.question, a.choices, a.done)
    elif a.cmd == "guide":
        print(guide(a.role, a.section))
    elif a.cmd == "watch":
        import watch
        watch.run(BASE, once=a.once, interval=a.interval, screens=not a.no_screens)
    elif a.cmd == "browser":
        browser(a.id.upper(), a.op, a.arg)
    elif a.cmd == "improve":
        improve_cmd(a.days, a.min_n, a.max_new, a.dry_run, a.diagnose)
    elif a.cmd == "request":
        post({"kind": "request", "id": a.id, "what": a.what, "files": a.files, "request_id": a.request_id or new_request_id()})
    elif a.cmd == "share":
        share(a.id, a.cwd)
    elif a.cmd == "request-done":
        post({"kind": "request-done", "from": a.frm, "rid": a.rid, "msg": a.msg, "status": a.status})
    elif a.cmd == "task":
        if "to" in d:
            d["manager"] = d.pop("to").upper()
        for k in ("criterion", "passed", "failed", "clear_criteria"):
            d.pop(k, None)
        extra = {"criteria_add": a.criterion, "criteria_pass": a.passed, "criteria_fail": a.failed,
                 "criteria_clear": a.clear_criteria}
        post({"kind": "task", "from": "superviseur", **d, **{k: v for k, v in extra.items() if v}})
    elif a.cmd == "objectif":
        if a.title is None and a.tasks is None and not (a.add or a.remove or a.clear):
            return objectif_show()
        post({"kind": "objective", "from": "superviseur", "add": a.add, "remove": a.remove, "clear": a.clear,
              **({"title": a.title} if a.title is not None else {}), **({"tasks": a.tasks} if a.tasks is not None else {})})
    elif a.cmd == "event":
        post({"kind": "event", "from": a.frm, "msg": a.msg, "level": a.level})
    elif a.cmd == "project":
        post({"kind": "project", "from": "superviseur", "name": a.name})
    elif a.cmd == "reset":
        post({"kind": "reset", "from": "superviseur"})
    elif a.cmd == "end":
        # The board rewrites the index on every change: while it runs, unregistering would not hold.
        port = urllib.parse.urlsplit(BASE).port or 80
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.8).close()
            sys.exit(f"The board of {CFG['project']} still answers (port {port}): stop it first, then run `end` again.")
        except OSError:
            pass
        import reprise
        n = reprise.unregister(CFG["project"])
        _unlink_retry(Path(CFG["run_dir"]) / "run.id")  # launch token: the next supervision gets a new one
        print(f"{CFG['project']}: {n} pane(s) unregistered, push rules removed.")
    elif a.cmd == "note":
        post({"kind": "note", "from": f"Manager {a.id.upper()}", "id": a.id, "text": a.text, "clear": a.clear,
              **({"defaut": a.defaut} if a.defaut else {})})
    elif a.cmd == "plan":
        post({"kind": "plan", "from": "superviseur", "path": a.path})
    elif a.cmd == "check":
        check(a.id, a.cwd, a.full, a.dry_run)
    elif a.cmd == "install":
        import shared
        if shared.is_link(Path(a.cwd or CFG["path"]) / "node_modules"):
            sys.exit("install refused: this worktree's node_modules is shared with the main checkout (shared_dirs); "
                     "an `npm ci` here would rewrite it for every worker. To add a package: ask the integrator.")
        npm = "npm.cmd" if os.name == "nt" else "npm"
        sys.exit(slot_run("install", a.id, [npm, "ci", "--prefer-offline", "--no-audit", "--no-fund"], a.cwd, dry_run=a.dry_run))
    elif a.cmd == "slot":
        argv = a.argv
        if not argv:
            sys.exit("slot: command expected after `--`")
        sys.exit(slot_run(a.name, a.id, argv, a.cwd, a.places, a.dry_run))
    elif a.cmd == "pushed":
        post({"kind": "pushed", "from": f"Manager {a.id.upper()}", "id": a.id, **({"msg": a.msg} if a.msg else {})})
    elif a.cmd == "drop":
        post({"kind": "remove-manager", "from": "superviseur", "id": a.id})
    elif a.cmd == "lock":
        lock(a.op, a.id, a.log)
    elif a.cmd == "merge":
        if a.rm:
            return post({"kind": "merge", "from": "superviseur", "branch": a.branch, "op": "rm"})
        d.pop("rm", None)
        if "to" in d:
            d["manager"] = d.pop("to").upper()
        post({"kind": "merge", "from": "superviseur", **d})
    elif a.cmd == "report":
        report(a.vault)
    elif a.cmd == "backup":
        try:
            backup(a.dest)
        except (OSError, RuntimeError, ValueError, store.sqlite3.Error) as e:
            sys.exit(f"backup failed: {e}")
    elif a.cmd == "metrics":
        try:
            metrics_cmd(a.scope, a.weekly, a.vault)
        except (OSError, RuntimeError, ValueError, store.sqlite3.Error) as e:
            sys.exit(f"metrics failed: {e}")


if __name__ == "__main__":
    main()
