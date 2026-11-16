"""Launch the supervision of a project: server, board (dashboard), registration of the agents.

    npm run supervision -- <project> [options]        (from the Relay root)
    python TOOLS/supervision/launch.py <project> [--port N] [--session A=agent-name …] [--no-browser] [--no-panes]
    python TOOLS/supervision/launch.py example-app --dry-run --panes-json panes.json [--state-json s.json] [--caller surf-…]

Without wmux (default backend): the board opens in the default browser, the agents are registered without a pane, and
each one is started with its identity (SUP_AGENT=<project>/<X>); the start commands are printed.
With wmux, run it from the SUPERVISOR's wmux terminal (WMUX_SURFACE_ID):
1. starts `server.py --project <p>` detached, unless it already answers on its port (log: run/<p>/server.log);
2. opens the board in the caller's browser pane, or reloads the tab already registered;
3. registers the panes: the caller = supervisor (S); the other terminals of the current wmux workspace = managers,
   in config order (`managers`). A manager whose registered pane still exists keeps it: launching again has no
   effect on a running supervision. Sessions (SendMessage names) cannot be read from wmux:
   `--session X=name`, otherwise they stay as registered;
4. opens NO browser tab for the managers (skill procedure: a new tab with `--show`, closed afterwards).

--restart: clean stop of the running server (POST /api/shutdown + token), wait for the port to be free, start again.
Checks before any start (lifecycle.preflight): wmux reachable (wmux backend), brain readable, repository present,
port free.
--dry-run: acts on nothing (neither server, nor wmux, nor board) and prints the registration plan; with
--panes-json (output of `wmux list-panes`) and --state-json (output of /api/state), it calls nothing at all.
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import backend  # noqa: E402
import config  # noqa: E402
import hostload  # noqa: E402
import lifecycle  # noqa: E402
from wmuxcli import wmux  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")


def get_state(url):
    try:
        with urllib.request.urlopen(url + "/api/state", timeout=3) as r:
            return json.load(r)
    except (urllib.error.URLError, OSError, ValueError):
        return None


def start_server(project, port, run_dir):
    config.guard_write(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    # server.log is kept by the server itself (rotation, lifecycle.py): the console log only keeps crashes.
    out = open(run_dir / "server-console.log", "a", encoding="utf-8")
    kw = {"stdout": out, "stderr": subprocess.STDOUT, "stdin": subprocess.DEVNULL, "cwd": str(HERE)}
    if os.name == "nt":
        # NEW_PROCESS_GROUP | NO_WINDOW, without DETACHED_PROCESS: the server keeps a hidden console inherited by
        # git, wmux and node; detached, each of these calls (every 20 s) would open its own window.
        kw["creationflags"] = 0x00000200 | 0x08000000
    else:
        kw["start_new_session"] = True
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    subprocess.Popen([sys.executable, str(HERE / "server.py"), "--project", project, "--port", str(port)], env=env, **kw)


def restart(url, port, run_dir, timeout=20):
    """--restart: clean stop of the running instance (POST /api/shutdown with the token of run/<p>/.token), then
    wait for the port to be free. Nothing running: nothing to stop. Exits with a clear message on failure."""
    if lifecycle.port_free(port):
        return print("0. restart: no server running on port " + str(port))
    token = lifecycle.read_token(run_dir)
    if not token:
        sys.exit(f"cannot restart: token missing ({run_dir / '.token'})")
    ok, msg = lifecycle.request_shutdown(url, token)
    if not ok:
        sys.exit(f"cannot restart: stop refused by {url} ({msg})")
    if not lifecycle.wait_port_free(port, timeout):
        sys.exit(f"restart: port {port} is still taken after {timeout} s (server stuck?)")
    print(f"0. restart: server stopped cleanly, port {port} free")


def apply_overrides(project, a):
    """Remember the launch's config options. Returns the new overrides if they changed (or --default), else None.
    With --dry-run nothing is written: the caller applies them to its copy of the config."""
    if a.default:
        if config.read_overrides(project) and not a.dry_run:
            config.write_overrides(project, {})
        return {}
    ov = config.read_overrides(project)
    new = dict(ov)
    if a.lite:
        new.update(config.LITE)
    if a.base:
        new["base_branch"] = a.base
    if a.managers:
        new["managers"] = [m.strip().upper() for m in a.managers.replace(",", " ").split()]
        new.pop("lite", None) if not a.lite else None
    if a.integrator:
        new["integrator"] = a.integrator.strip().upper()
    mans, integ = new.get("managers"), new.get("integrator")
    if mans is not None and integ is not None and integ and integ not in mans:
        sys.exit(f"--integrator {integ} is not in --managers ({', '.join(mans)})")
    if new == ov:
        return None
    if not a.dry_run:
        config.write_overrides(project, new)
    return new


def start_cmds(p, x):
    """Start command of an agent (bash, PowerShell). Same Claude account as the launching session (the supervisor):
    its CLAUDE_CONFIG_DIR is reused if it has one, otherwise no override (Claude Code's default config)."""
    cfg_dir = os.environ.get("CLAUDE_CONFIG_DIR", "")
    try:  # token of the running supervision, written by the server at start
        run = (config.state_dir(p) / "run.id").read_text(encoding="utf-8").strip()
    except OSError:
        run = ""
    # State hooks loaded for this session only, never in the global settings.
    hook = (Path(__file__).resolve().parent / "sup-hook.js").as_posix()
    bash = ((f"CLAUDE_CONFIG_DIR='{cfg_dir}' " if cfg_dir else "") + f"SUP_AGENT={p}/{x} SUP_PROJECT={p} "
            + (f"SUP_RUN={run} " if run else "") + f"claude --settings \"$(node '{hook}' --emit-settings)\"")
    ps = ((f"$env:CLAUDE_CONFIG_DIR='{cfg_dir}'; " if cfg_dir else "") + f"$env:SUP_AGENT='{p}/{x}'; $env:SUP_PROJECT='{p}'; "
          + (f"$env:SUP_RUN='{run}'; " if run else "") + f"claude --settings (node '{hook}' --emit-settings)")
    return bash, ps


def plain_steps(cfg, a, p, url, state, sessions):
    """Without wmux: board in the default browser, agents registered without a pane, identity through SUP_AGENT."""
    known = state.get("managers", {})
    if a.no_browser or a.dry_run:
        print(f"2. board: {url}/ (not opened)")
    else:
        import webbrowser
        webbrowser.open(url + "/")
        print(f"2. board opened in the default browser ({url}/)")
    if a.no_panes:
        return print("3. agents: not registered (--no-panes)")
    if sessions.get("S"):
        sup(p, "assign", "S", "--session", sessions["S"], dry=a.dry_run)
    for mid in cfg["managers"]:
        args = ["assign", mid]
        if sessions.get(mid):
            args += ["--session", sessions[mid]]
        if mid == cfg["integrator"] and (known.get(mid) or {}).get("role") != "integrator":
            args += ["--role", "integrator"]
        sup(p, *args, dry=a.dry_run)
    print("3. agents registered without a pane (no wmux). Start each agent with its identity:")
    who = ["S"] + ([] if cfg["lite"] else cfg["managers"])
    for x in who:
        bash, ps = start_cmds(p, x)
        print(f"   {x:<2} bash: {bash}      PowerShell: {ps}")
    print("   The board talks to each agent through its mailbox: `sup.py inbox <X>` (and every sup.py call "
          "prints the pending messages). Answers to prompts: in the agent's terminal.")
    print("   Desktop notification (optional): SUP_NOTIFY_CMD='notify-send Supervision \"$SUP_MSG\"' before launching.")
    if cfg["lite"]:
        print("   lite mode: no manager; S launches 2 to 3 workers (A1…A3) and keeps the queue (skill: supervisor.md § Lite mode)")


def plan(panes, caller, managers, state, sessions=None):
    """Registration plan, without side effects. panes: output of `wmux list-panes`; state: /api/state (or {}).

    Returns {"S": {...}, "assign": [{"id", "surface", "kept", "session"}], "unassigned": [...], "spare": [...]}.
    """
    sessions = sessions or {}
    terminals = [s["id"] for p in panes for s in p.get("surfaces", []) if s.get("type") == "terminal"]
    live = {s["id"] for p in panes for s in p.get("surfaces", [])}
    known = (state or {}).get("managers", {})
    kept = {}
    for mid in managers:
        sf = (known.get(mid) or {}).get("surface", "")
        if sf and sf in live and sf != caller and sf not in kept.values():
            kept[mid] = sf
    free = [t for t in terminals if t != caller and t not in kept.values()]
    out = {"S": {"surface": caller, "session": sessions.get("S", "")}, "assign": [], "unassigned": [], "spare": []}
    for mid in managers:
        if mid in kept:
            out["assign"].append({"id": mid, "surface": kept[mid], "kept": True, "session": sessions.get(mid, "")})
        elif free:
            out["assign"].append({"id": mid, "surface": free.pop(0), "kept": False, "session": sessions.get(mid, "")})
        else:
            out["unassigned"].append(mid)
    out["spare"] = free
    return out


def sup(project, *args, dry=False):
    cmd = [sys.executable, str(HERE / "sup.py"), "--project", project, *args]
    if dry:
        print("  (dry run) sup.py", " ".join(cmd[3:]))
        return True
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print("  failed:", " ".join(args), "→", (r.stderr or r.stdout).strip()[:200])
    return r.returncode == 0


def browser_surfaces(panes):
    return [s["id"] for p in panes for s in p.get("surfaces", []) if s.get("type") == "browser"]


def main():
    ap = argparse.ArgumentParser(description="Launch the supervision of a project (server, board, panes).")
    ap.add_argument("project", nargs="?", default=None)
    ap.add_argument("--port", type=int)
    ap.add_argument("--session", action="append", default=[], metavar="X=name",
                    help="SendMessage name of a session (S, A, B…), repeatable")
    ap.add_argument("--caller", help="supervisor pane (default: WMUX_SURFACE_ID)")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--new-dashboard", action="store_true",
                    help="open a new board even if a tab is registered (registered tab closed)")
    ap.add_argument("--no-panes", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--base", metavar="BRANCH", help="base branch for this supervision (default: the brain), remembered")
    ap.add_argument("--managers", metavar="A,D", help="managers of this supervision (default: the brain), remembered")
    ap.add_argument("--integrator", metavar="D", help="integrator (must be in --managers), remembered")
    ap.add_argument("--lite", action="store_true",
                    help="lite mode: no managers and no panes, S launches 2 to 3 workers (virtual manager A, also integrator)")
    ap.add_argument("--default", action="store_true", help="clear the remembered overrides: back to the brain")
    ap.add_argument("--restart", action="store_true", help="clean stop of the running server (token), then start again")
    ap.add_argument("--skip-checks", action="store_true", help="skip the launch checks (wmux, brain, repository, port)")
    ap.add_argument("--panes-json", help="dry run: simulated pane list (format of `wmux list-panes`)")
    ap.add_argument("--state-json", help="dry run: simulated state (format of /api/state)")
    a = ap.parse_args()

    cfg = config.load(a.project)
    changed = apply_overrides(cfg["project"], a)
    if changed is not None:
        cfg = config.load(a.project)
        if a.dry_run:  # dry run: nothing written, show what the config would become
            cfg = {**cfg, **changed, "overrides": changed, "lite": bool(changed.get("lite"))}
    p, port = cfg["project"], a.port or cfg["port"]
    if config.test_mode():  # a test neither contacts nor registers anything on the live server
        config.check_isolation()
        config.check_port(port)
    url = f"http://127.0.0.1:{port}"
    os.environ["SUP_URL"] = url  # for the sup.py calls
    sessions = {}
    for kv in a.session:
        k, _, v = kv.partition("=")
        if not v:
            sys.exit(f"--session expects X=name: {kv}")
        sessions[k.strip().upper()] = v.strip()
    caller = a.caller or os.environ.get("WMUX_SURFACE_ID", "")
    bk = backend.mode()
    print(f"Supervision {p} · config: {cfg['source']} · base {cfg['base_branch']} · managers {', '.join(cfg['managers'])} "
          f"(integrator {cfg['integrator'] or '—'}) · {url}/" + (" · LITE MODE" if cfg["lite"] else "")
          + f" · backend {bk}" + ("" if bk == "wmux" else " (no wmux: mode without panes)"))
    host = hostload.profile(cfg=cfg)
    print(f"   host: {host['why']} · at most {host['workers_total'] or 'no cap'} active workers in total, "
          f"{host['npm_ci'] or 'no cap'} npm ci at a time (`sup.py install`)")
    if host["lite_suggested"] and not cfg["lite"] and not cfg["overrides"].get("managers"):
        print("   ⚠ modest host: launch again with --lite (S acts as manager, 3 workers) or keep the brain's managers, "
              "knowing that the CPU/memory brakes will tighten quickly")
    if cfg["overrides"]:
        print("   remembered overrides (run/<project>/overrides.json): " + ", ".join(f"{k}={v}" for k, v in cfg["overrides"].items())
              + " · `--default` to go back to the brain")
    if changed is not None and not a.restart and not a.dry_run and not lifecycle.port_free(port):
        print("   WARNING: a server is running with the old config; launch again with --restart to apply it.")

    # 0. Restart requested: clean stop of the running instance, wait for the port to be free
    if a.restart and not a.dry_run:
        restart(url, port, Path(cfg["run_dir"]))

    # 1. Server
    state = json.loads(Path(a.state_json).read_text(encoding="utf-8")) if a.state_json else (None if a.dry_run and a.panes_json else get_state(url))
    if state is None and not a.dry_run:
        problems = [] if a.skip_checks else lifecycle.preflight(cfg, port, need_wmux=bk == "wmux" and not (a.no_panes and a.no_browser))
        if problems:
            sys.exit(lifecycle.describe(problems))
        start_server(p, port, Path(cfg["run_dir"]))
        for _ in range(40):
            time.sleep(0.5)
            state = get_state(url)
            if state is not None:
                break
        if state is None:
            sys.exit(f"the server does not answer on {url}: see {Path(cfg['run_dir']) / 'server.log'} "
                     f"and {Path(cfg['run_dir']) / 'server-console.log'}")
        print(f"1. server started ({url})")
    else:
        print("1. server: " + ("already running" if state is not None and not a.state_json else "not contacted (dry run)"))
    state = state or {}

    if a.no_panes and a.no_browser:
        return
    if bk == "none":
        return plain_steps(cfg, a, p, url, state, sessions)
    if not caller:
        sys.exit("WMUX_SURFACE_ID missing: launch from the supervisor's wmux terminal, or give --caller")
    if a.panes_json:
        panes = json.loads(Path(a.panes_json).read_text(encoding="utf-8")).get("panes", [])
    else:
        panes = json.loads(wmux("list-panes", out=True) or "{}").get("panes", [])
    if caller not in {s["id"] for pn in panes for s in pn.get("surfaces", [])}:
        sys.exit(f"pane {caller} is not in the current wmux workspace")
    dry = a.dry_run

    # 2. Board in the caller's browser pane
    dash = ((state.get("supervisor") or {}).get("dashboard")) or ""
    if a.no_browser:
        print("2. board: not opened (--no-browser)")
    elif dash and dash in browser_surfaces(panes):
        if not dry:
            wmux("browser", "eval", f"location.href='{url}/'; 'ok'", "--surface", dash)
        print(f"2. board reloaded in the registered tab {dash[:13]}")
    elif dash and not a.new_dashboard:
        # Registered tab missing from list-panes: usually moved to the side panel (Ctrl+Shift+I), which wmux does not
        # list. Neither open (duplicate in a pane) nor eval (hidden tab: empty panes).
        print(f"2. board: registered tab {dash[:13]} outside the panes (side panel?), left as is; "
              "--new-dashboard to open a new one")
    elif dry:
        print("2. board: (dry run) wmux browser open " + url + "/  — new tab registered as the board")
    else:
        before = set(browser_surfaces(panes))
        wmux("browser", "open", url + "/")
        time.sleep(1.5)
        after = json.loads(wmux("list-panes", out=True) or "{}").get("panes", [])
        new = [s for s in browser_surfaces(after) if s not in before]
        dash = new[0] if new else ""
        print("2. board opened" + (f" (tab {dash[:13]})" if dash else " (tab not found: a 'browser outside its pane' alert is possible)"))
        panes = after or panes

    # 3. Pane registration
    if a.no_panes:
        return print("3. panes: not registered (--no-panes)")
    pl = plan(panes, caller, [] if cfg["lite"] else cfg["managers"], state, sessions)
    args = ["assign", "S", "--surface", caller]
    if pl["S"]["session"]:
        args += ["--session", pl["S"]["session"]]
    if dash:
        args += ["--dashboard", dash]
    sup(p, *args, dry=dry)
    known = state.get("managers", {})
    if cfg["lite"]:  # S acts as manager: registered without a pane, integrator of its own common files
        for mid in cfg["managers"]:
            if (known.get(mid) or {}).get("role") != "integrator":
                sup(p, "assign", mid, "--role", "integrator", dry=dry)
    for x in pl["assign"]:
        args = ["assign", x["id"]]
        if not x["kept"]:
            args += ["--surface", x["surface"]]
        if x["session"]:
            args += ["--session", x["session"]]
        if x["id"] == cfg["integrator"] and (known.get(x["id"]) or {}).get("role") != "integrator":
            args += ["--role", "integrator"]
        if len(args) > 2:
            sup(p, *args, dry=dry)

    # Summary
    print("3. panes:")
    sv_session = pl["S"]["session"] or (state.get("supervisor") or {}).get("session") or "session ?"
    print(f"   S  {caller}  supervisor ({sv_session})")
    for x in pl["assign"]:
        sess = x["session"] or (known.get(x["id"]) or {}).get("session") or "session ? (--session " + x["id"] + "=name)"
        role = " integrator" if x["id"] == cfg["integrator"] else ""
        print(f"   {x['id']}  {x['surface']}  {'kept' if x['kept'] else 'registered'}{role} ({sess})")
    for mid in pl["unassigned"]:
        print(f"   {mid}  — no free terminal in this workspace")
    for sf in pl["spare"]:
        print(f"   ·  {sf}  terminal not registered")
    if cfg["lite"]:
        print("   lite mode: no manager; S launches 2 to 3 workers (A1…A3) and keeps the queue (skill: supervisor.md § Lite mode)")
    elif pl["assign"]:
        print("   start each manager in its pane (same Claude account as S): " + start_cmds(p, "<X>")[1])
    print("Next: assign domains and scopes (sup.py assign X --domain … --scope …), rules: /supervision.")


if __name__ == "__main__":
    main()
