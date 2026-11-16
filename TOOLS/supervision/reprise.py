"""Generated resume sheets, in the format of the Relay state (`.relay-state/`).

One sheet per supervision agent (managers, supervisor S), written to
`<Relay root>/.relay-supervision/<project>/<X>.md`, plus ONE `index.json` shared by all
projects (`.relay-supervision/index.json`, wmux surface -> "<project>/<X>.md").
`TOOLS/supervision/session-sheet.js` injects it at start, after `/clear` and after compaction: an agent
can start over from scratch without losing anything. GENERATED files: never edit them by hand
(a manager's notes go through `sup.py note X "..."`).

Everything project-specific (base branch, common files, commands, sessions) comes from the config (brain)
and the board: the /supervision skill contains none of it.
SUP_RELAY_STATE (alias SUP_REPRISE_DIR): overrides the `.relay-supervision` directory (tests), read on every
write; the hook reads the same variable. Writes are checked by config.guard_write (test mode).
"""
import json
import os
import time
from pathlib import Path

import backend
import config
import objectif

HERE = Path(__file__).resolve().parent
RELAY_ROOT = config.RELAY_ROOT
# Directory OWNED by the supervision: `.relay-state/` belongs to relay-state.mjs, which regenerates it whole
# (by swap) and would erase our sheets. Override: SUP_RELAY_STATE (config.py).
def out_dir():
    return config.relay_state_dir()


SKILL_DIR = "SKILLS/supervision"


def protocole(mid, integrator=False):
    """What an agent rereads after a restart: its preamble (in its sheet), then if needed its role guide,
    served by `$S guide` from the skill (no rereading of the whole SKILL.md on every restart)."""
    if mid == "S":
        return f"the preamble of your sheet, then if needed `$S guide supervisor` ({SKILL_DIR}/supervisor.md)"
    if integrator:
        return f"the preamble of your sheet, then if needed `$S guide integrator` ({SKILL_DIR}/manager.md, integrator.md)"
    return f"the preamble of your sheet, then if needed `$S guide manager` ({SKILL_DIR}/manager.md)"


PREAMBLE_MAX = 1536  # bytes; beyond that, the preamble is no longer "short"
PRE_M = """Manager preamble (firm rules; detail: `$S guide {role}`, once):
- Board first: `$S manager {X} --status ...` on every change; `$S files {X} --add` BEFORE editing.
- No progress tracking in prose: the board carries it. Reports of 10 lines at most.
- End of task: criteria ticked (`$S task T --pass N`), `$S check {X} --cwd <wt>` green, push YOUR branch, then ONE `$S submit {X} --sha ... --check green --files n --note "<deviations>"`.
- Failure: `--status blocked --msg "<cause>"` + `$S note {X} --defaut "..."`; never hidden in prose. 3 red checks: root cause.
- Question: `$S ask {X} "..."`, then end your turn; the answer wakes you up. No waiting loop.
- "[Board] N message(s)": `$S inbox {X}`, then apply them.
- Workers {X}1...{X}{n}: diff reviewed and check rerun before `--verified`; `npm ci` through `$S install`, browser through `$S browser`.
- Out of scope, common file, tool refusal: ask, do not work around it."""
PRE_D = """
- Integrator: the only one who writes common files and pushes to the base, through the merge train (`$S guide integrator`): one full check, one single question to the user, fast-forward push."""
PRE_S = """Supervisor preamble (firm rules; detail: `$S guide supervisor`, once):
- Split the work, frame it (`$S task T --brief ... --criterion ...`, 2 to 5 verifiable criteria), review every branch, keep the merge queue. Do not code in the managers' domains.
- Disjoint scopes (`$S assign X --scope ...`) before the first task; widen on request, never silently.
- A delivery arrives through `$S submit` with its pre-review: the "ready" decision stays yours.
- "[Board] N message(s)": `$S inbox S`, then apply them. Without hooks (session opened by hand): `$S watch` in Monitor.
- Repeated failure (3 red checks, conflict, restart loop): root cause before restarting.
- Messages marked "user decision": apply them, do not reopen the debate."""


def preamble(mid, integrator=False, max_workers=4):
    """Firm rules of the role, at the top of the sheet (<= 1.5 KB)."""
    if mid == "S":
        return PRE_S
    text = PRE_M.format(X=mid, n=max_workers, role="integrator" if integrator else "manager")
    return text + (PRE_D if integrator else "")


PROTOCOLE = protocole("S")  # historical alias
_last = {}


def _hm(t):
    return time.strftime("%H:%M", time.localtime(t)) if t else "?"


def _atomic(path, text):
    tmp = config.guard_write(path.with_suffix(path.suffix + ".tmp"))
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def _steps(steps):
    """Check steps for the sheet: `npm run <script>` or the explicit command."""
    steps = steps if isinstance(steps, list) else [steps]
    return ", ".join(f"npm run {s}" if isinstance(s, str) else config.step_label(s) for s in steps)


def _sup_cmd(cfg):
    py = "python" if os.name == "nt" else "python3"
    return f'{py} "{(HERE / "sup.py").as_posix()}" --project {cfg["project"]}'  # quotes: the path may contain a space


def _ask_line(mid):
    """How to ask a question (managers) or follow the board (S); absolute wmux path, never the one on PATH."""
    import wmuxcli
    wm = f"; wmux on this host: `\"{Path(wmuxcli.WMUX).as_posix()}\"`" if backend.enabled() else ""
    if mid == "S":
        return (f"Mail: managers with hooks reach you through your mailbox (`$S inbox S`, pointer at the end of your turn "
                f"if you were launched by start-manager.ps1); otherwise `$S watch` in the Monitor tool, re-armed when it expires{wm}.")
    return (f"Question to the supervisor or the user: `$S ask {mid} \"<question>\" [--choices '<json>']`, then "
            f"end your turn (the answer wakes you up); `$S ask {mid} --done` once settled. A question written "
            f"only in your terminal is read by nobody{wm}.")


def _common(state, sv, cfg, who, mid="X"):
    plan = state.get("plan") or ""
    base = cfg["base_branch"]
    lines = [f"Project: {cfg['project']}{' (' + state['project'] + ')' if state.get('project') else ''} · repository "
             f"`{Path(cfg['path']).as_posix()}` · base branch `{base}` · board: http://127.0.0.1:{cfg['port']}/",
             f"After a restart: {who}.",
             f"`$S` = `{_sup_cmd(cfg)}` · quick check `$S check X --cwd <worktree>` ({_steps(cfg['check']['quick'])})"
             f" · full `--full` ({_steps(cfg['check']['full'])}), under lock.",
             _ask_line(mid),
             f"Common files (integrator {cfg['integrator'] or '?'} only): {', '.join(f'`{c}`' for c in cfg['common']) or 'none'}.",
             f"Limits: {cfg['max_workers']} active workers per manager; quota brake at {cfg['quota_brake']} % of the 5-hour window."]
    if cfg.get("setup"):
        lines.append(f"Worktree setup: {cfg['setup']}")
    if not backend.enabled():
        lines.append("Without wmux (read no-wmux.md in the skill): the board talks to you through your mailbox. `$S inbox <X>` "
                     "reads it (S for the supervisor); every `$S` call also prints pending messages: a "
                     "\"[Board — user decision]\" message must be applied.")
    if plan:
        lines.append(f"Plan of the current wave and user decisions: `{plan}`.")
    lines += ["", "**Before any action: check again, do not trust the context.** Git (branch, `git status`, "
              f"`git log origin/{base}..HEAD`), the board (status, scope, lock, merge queue) and its recent "
              f"messages. When in doubt: `--status blocked` and a message to the supervisor "
              f"({sv.get('session') or 'session not registered'})."]
    return lines


def _task_lines(t, head):
    """Task line, its brief and its completion criteria (checkboxes: `sup.py task T --pass N`)."""
    out = [head]
    if t.get("brief"):
        out += ["  " + l for l in str(t["brief"]).splitlines()[:12]]
    out += [f"  - [{'x' if c['passes'] else ' '}] {i}. {c['text']}" for i, c in enumerate(t.get("criteria", []), 1)]
    return out


def _manager(state, m, sv, cfg):
    mid = m["id"]
    integ = m.get("role") == "integrator"
    L = [f"# Resume — Manager {mid}{' (integrator)' if integ else ''}",
         f"_Sheet generated by the board at {_hm(time.time())}; do not edit._", "",
         preamble(mid, integ, cfg.get("max_workers", 4)), ""]
    L += _common(state, sv, cfg, protocole(mid, integ), mid) + ["", "## Where I stand"]
    L.append(f"- Session: {m.get('session') or '?'} · status **{m.get('status')}** · progress {m.get('progress', 0)} %")
    L.append(f"- Domain: {m.get('domain') or '—'}")
    L.append(f"- Task: {m.get('task') or '—'}")
    if (m.get("question") or {}).get("text"):
        L.append(f"- **Pending question** (asked at {_hm(m['question'].get('t'))}): {m['question']['text']} "
                 f"(`$S ask {mid} --done` once settled)")
    if m.get("branch"):
        L.append(f"- Branch: `{m['branch']}`")
    for wt in m.get("worktrees", []):
        L.append(f"- Worktree: `{wt}`")
    L.append(f"- Scope: {', '.join(f'`{p}`' for p in m.get('scope') or []) or 'none (wait for the assignment)'}")
    if m.get("files"):
        L.append(f"- Declared files: {', '.join(m['files'])}")
    if m.get("real_files"):
        L.append(f"- Files modified according to git: {len(m['real_files'])}")
    ws = [w for w in m.get("workers", {}).values() if w.get("status") not in ("done", "idle")]
    if ws:
        L.append("- Active workers: " + "; ".join(f"{w['id']} ({w.get('model', 'opus')}) {w.get('task', '')}" for w in ws))
    if m.get("push_ok"):
        L.append("- **Push go-ahead active** (single use): consume it with `sup.py pushed` after the push.")
    unread = len((state.get("mailbox") or {}).get(mid) or [])
    if unread:
        L.append(f"- **{unread} unread board message(s)**: `$S inbox {mid}`, then apply them.")
    drift = m.get("drift") or {}
    if drift.get("n"):
        L += ["", f"## Base drift: {drift['n']} commit(s) of origin/{cfg['base_branch']} missing from your branch"]
        L += [f"- {s}" for s in drift.get("subjects", [])[:5]]
        L.append("Before delivering: rebase on or merge the base, then rerun the check.")
    tasks = [t for t in state.get("tasks", {}).values() if t.get("manager") == mid and t.get("status") != "done"]
    if tasks:
        L += ["", "## My open tasks"]
        for t in tasks:
            L += _task_lines(t, f"- {t['id']} [{t['status']}] {t.get('title', '')} — {t.get('note', '')}")
    q = [x for x in state.get("merge_queue", []) if x.get("manager") == mid and x.get("status") != "merged"]
    if q:
        L += ["", "## My branches in the merge queue"] + [f"- `{x['branch']}` {x.get('sha', '')} [{x['status']}] {x.get('note', '')}" for x in q]
    reqs = [r for r in state.get("requests", {}).values() if r.get("status") == "open"
            and (r.get("from") == mid or m.get("role") == "integrator")]
    if reqs:
        L += ["", "## Open requests (integrator)"] + [f"- {r['id']} from {r['from']}: {r['what']}" for r in reqs]
    if m.get("notes"):
        L += ["", "## My notes (sup.py note)"] + [f"- {_hm(n['t'])} {n['text']}" for n in m["notes"][-15:]]
    ev = [e for e in state.get("events", []) if e.get("who") in (f"Manager {mid}",) or f"Manager {mid}" in e.get("msg", "")][:8]
    if ev:
        L += ["", "## Latest events about me"] + [f"- {_hm(e['t'])} {e['msg'][:160]}" for e in ev]
    return "\n".join(L) + "\n"


def _supervisor(state, sv, cfg, objective=None):
    L = ["# Resume — Supervisor (S)", f"_Sheet generated by the board at {_hm(time.time())}; do not edit._", "",
         preamble("S"), ""]
    L += _common(state, sv, cfg, protocole("S"), "S")
    unread = len((state.get("mailbox") or {}).get("S") or [])
    if unread:
        L.append(f"**{unread} unread board message(s)**: `$S inbox S`.")
    if objective:  # current goal (sup.py objectif): progress and estimated time left, one line
        L += ["", objectif.line(objective) + "."]
    mans = sorted(state.get("managers", {}).values(), key=lambda m: m["id"])
    if cfg.get("lite"):
        first = mans[0]['id'] if mans else 'A'
        L += ["", "**Lite mode**: no manager and no pane. You are S and manager A (integrator): launch yourself "
              f"2 to 3 workers {first}1...{first}{cfg['max_workers']} in worktrees "
              "(`$S worker ...`), review their diffs, keep the queue and the merge train (skill: supervisor.md § Lite mode).",
              "", "## Managers"]
    else:
        L += ["", "Role: split the work, assign scopes, review every branch, keep the merge queue, relay "
              "the user. Do not code in the managers' domains. Messages to managers: SendMessage to "
              + (", ".join(f"{m.get('session') or '?'} ({m['id']})" for m in mans) or "—") + ".", "", "## Managers"]
    for m in mans:
        L.append(f"- **{m['id']}** ({m.get('session') or '?'}) [{m.get('status')} {m.get('progress', 0)} %] "
                 f"{m.get('task') or '—'} · branch `{m.get('branch') or '—'}`")
    tasks = [t for t in state.get("tasks", {}).values() if t.get("status") != "done"]
    if tasks:
        L += ["", "## Open tasks"]
        for t in tasks:
            L += _task_lines(t, f"- {t['id']} → {t.get('manager')} [{t['status']}] {t.get('title', '')} — {t.get('note', '')}")
    q = [x for x in state.get("merge_queue", []) if x.get("status") != "merged"]
    if q:
        L += ["", "## Merge queue (not merged)"] + [f"- `{x['branch']}` {x.get('sha', '')} [{x['status']}] {x.get('manager', '')} — {x.get('note', '')}" for x in q]
    reqs = [r for r in state.get("requests", {}).values() if r.get("status") == "open"]
    if reqs:
        L += ["", "## Open requests to the integrator"] + [f"- {r['id']} from {r['from']}: {r['what']}" for r in reqs]
    notes = state.get("supervisor_notes", [])
    if notes:
        L += ["", "## My notes (sup.py note S)"] + [f"- {_hm(n['t'])} {n['text']}" for n in notes[-25:]]
    ev = state.get("events", [])[:12]
    if ev:
        L += ["", "## Latest events"] + [f"- {_hm(e['t'])} {e['who']} — {e['msg'][:160]}" for e in ev]
    return "\n".join(L) + "\n"


def write_all(state, sv, cfg, objective=None):
    """Regenerate the project's sheets that changed; update its part of the shared index.

    sv: {"surface", "session"} of the supervisor; cfg: config.load(project); objective: objectif.status() or None.
    """
    project = cfg["project"]
    root = out_dir()
    out = config.guard_write(root / project)
    out.mkdir(parents=True, exist_ok=True)
    mine = {}
    docs = {"S": _supervisor(state, sv, cfg, objective)}
    if sv.get("surface"):
        mine[sv["surface"]] = f"{project}/S.md"
    for mid, m in state.get("managers", {}).items():
        if cfg.get("lite") and not m.get("surface"):
            continue  # virtual manager of lite mode: no pane, so nobody to inject its sheet into
        docs[mid] = _manager(state, m, sv, cfg)
        if m.get("surface"):
            mine[m["surface"]] = f"{project}/{mid}.md"
    for mid, text in docs.items():
        body = text.split("\n", 2)[2]  # without the timestamp line: write only if the content changes
        key = f"{project}/{mid}"
        if _last.get(key) != body or not (out / f"{mid}.md").exists():
            _atomic(out / f"{mid}.md", text)
            _last[key] = body
    # Push guard rules (supervision-guard.js): base, role and branch of every manager. One file per project,
    # at the root (not in the sheets directory), rewritten only when it changes.
    # `port`: the board's, so that the guard and the start hook ignore a registration once the board is stopped
    # (board-alive.js).
    try:  # token of the running supervision (server.new_run_id): the guard ignores a session from another launch
        run = (Path(cfg["run_dir"]) / "run.id").read_text(encoding="utf-8").strip()
    except (OSError, KeyError):
        run = ""
    guard = {"project": project, "base": cfg["base_branch"], "integrator": cfg["integrator"], "port": cfg.get("port"),
             **({"run": run} if run else {}),
             "managers": {mid: {"role": m.get("role", ""), "branch": m.get("branch", "")}
                          for mid, m in state.get("managers", {}).items()}}
    gtext = json.dumps(guard, indent=2, sort_keys=True)
    gfile = config.guard_write(root / f"{project}.guard.json")
    if _last.get(f"{project}/guard") != gtext or not gfile.exists():
        _atomic(gfile, gtext)
        _last[f"{project}/guard"] = gtext
    # Index shared by all projects: replace our own part (and legacy entries without a sub-directory),
    # keep the other projects' entries, except a pane that moved to this project.
    idx_file = root / "index.json"
    try:
        current = json.loads(idx_file.read_text(encoding="utf-8"))
        current = current if isinstance(current, dict) else {}
    except (OSError, ValueError):
        current = {}
    index = {sf: f for sf, f in current.items()
             if "/" in str(f) and not str(f).startswith(project + "/") and sf not in mine}
    index.update(mine)
    if index != current:
        _atomic(idx_file, json.dumps(index, indent=2))


def unregister(project):
    """End of supervision: remove this project's panes from the shared `index.json` and delete `<project>.guard.json`.

    The resume sheets stay (trace of the last wave); without registration, nothing injects them any more.
    Returns the number of entries removed.
    """
    root = out_dir()
    idx_file = root / "index.json"
    try:
        current = json.loads(idx_file.read_text(encoding="utf-8"))
        current = current if isinstance(current, dict) else {}
    except (OSError, ValueError):
        current = {}
    index = {sf: f for sf, f in current.items() if not str(f).startswith(project + "/")}
    if index != current:
        _atomic(config.guard_write(idx_file), json.dumps(index, indent=2))
    gfile = root / f"{project}.guard.json"
    if gfile.exists():
        config.guard_write(gfile).unlink()
    for key in [k for k in _last if k.startswith(project + "/")]:
        del _last[key]
    return len(current) - len(index)
