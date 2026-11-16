# Supervision — supervisor (S)

Served by `$S guide supervisor` (after `common.md`).

## When to start a team

From **4 independent work items** (disjoint scopes) or a domain that needs several workers. Below that (2 to 3 items of under ~200 lines), a single agent chains them or starts 2 to 3 workers in worktrees: the setup (worktree, `npm ci` or `.venv`, reading the protocol) costs ~15 min and tokens a small job does not pay back.

## Launch and on-the-fly configuration

`npm run supervision -- <project>` reads the brain's `supervision:` block. For a day that departs from it, without editing it:
`--base <branch>` (base branch, e.g. a feature branch), `--managers A,D`, `--integrator D`. These options are **remembered** (`run/<project>/overrides.json`, read by the server, `sup.py` and the sheets): `--default` goes back to the brain, `--restart` applies a change to a server already running.

**Claude account**: managers and workers start with the supervisor's `CLAUDE_CONFIG_DIR` (unset = `~/.claude`), unless the user asks otherwise; `launch.py` prints the command. Another account does not feed the board's quota (the board would show a fraction of the real consumption).

## Lite mode (`--lite`)

For 2 to 3 work items: no managers and no panes. You are both S and manager A (registered as integrator, without a pane):
- start 2 to 3 workers A1 to A3 yourself in worktrees (`$S worker A1 --status working --task "…" --model …`), each with its list of paths, its checks under the lock (`$S check A --cwd <worktree>`), its diff review before `--verified`;
- board, alerts, signals and resume sheets are kept; no "silent" or "no integrator" alert for A;
- merging follows `integrator.md`: a single full check, a single question to the user;
- beyond 3 items, or if a domain needs several workers, switch back to a full team (`--default`, then `--restart`).

## Watching during a wave

Managers started by `start-manager.ps1`: their hooks report their state to the board, and their questions, deliveries and blocks reach your mailbox; if you were started that way yourself (`-Role S`), a pointer "N message(s): `$S inbox S`" is typed into your pane at the end of your turn: no `watch` to re-arm. Otherwise (session opened by hand, or a manager without hooks): keep a **monitor** on `$S watch` (Monitor tool), re-armed every 30 min; without it, a question written in a terminal is seen by nobody. The board is not read through `read-screen` alone: a hidden pane's screen is truncated, and the grey text in the input box is a suggestion, not a message.

## Review and merge queue

- When a manager moves to `review` with a branch, the server runs a **pre-review**: SHA on origin, base up to date, foreign commits, files outside the scope or common, declared vs real, lock, last check → ✓/⚠ on the card and in the queue.
- The board's review button sends you a review request for a read-only sonnet subagent. **You keep the "ready" decision**: review the branch, then mark it in the queue.
- A delivery arrives through `$S submit` (SHA, green check, files, deviations): the manager's card moves to `review` with its pre-review.
- A merge conflict or a branch that fails the pre-review leaves the train; warn the manager, do not resolve it in their place.
- After the train is pushed, **verify again**: base SHA, result of the full check announced by D.

## Framing the goal of a work stream

Beyond 4 tasks, frame the goal like a spec before creating the tasks, and write it in the wave's plan:
- **Why**: who is affected, current behaviour (verified), wanted behaviour, why now, **how we will know it is done** (observable);
- **Scope**: explicit out of scope, systems touched, ordering constraints, smallest useful version, failure modes and rollback.

Then `$S objectif --title "…" --tasks T…` (bar at the top of the board), and `--add` for each task created for it. A task counts as `done` only **after the train push is verified**; reviewed and ready, it stays in `review` (otherwise the bar overstates what is delivered). A split task (engine / UI…) becomes two tasks, each with its criteria.

## Framing a task

A one-line instruction leads to duplicated or forgotten work. Every task handed out is framed:

```
$S task T7 --title "CSV export" --to A --status doing   --brief "Goal: … Files: … Deliverable: … Out of scope: …"   --criterion "the export downloads from the list page" --criterion "test on the column headers"
```

- A ticket (Jira or other tracker) provides the framing: read it and copy its goal and acceptance criteria as they are into `--brief` and `--criterion`, with the ticket key in the title; do not rewrite them from memory.
- 2 to 5 **verifiable done criteria** (a command, an observation), not "well done". They appear as checkboxes in the manager's sheet.
- The manager ticks them after verifying: `$S task T7 --pass 1` (`--fail N` to reopen). **`submit` is refused while a criterion of the task is open**: no more "done" on an impression.
- A task without criteria is delivered as before; do not leave any without, except a trivial chore.
- Weak framing = fresh restarts, red checks, conflicts: those are the signals the board counts.

## Splitting the work

- Give each manager a domain and a list of paths (`sup.py assign X --domain … --scope …`) before its first task; widen on request, never silently. **Disjoint scopes**: the board raises `scope_overlap` as soon as two lists may cover the same file (`src/*` and `src/a/*`): fix it before starting the managers, not after the collision.
- Do not code in the managers' domains. Messages through SendMessage to the manager's session (in your sheet).
- Quota brake tightened: start no new worker; let the others finish.
- Host brakes (config `cpu_brake` 90 % for `cpu_window` 300 s, released under `cpu_release` 75 %; `mem_brake`): same rule, 2 workers per manager at most. The board also caps active workers **in total** (`workers_total`, default threads ÷ 2) and refuses the next one. At launch, `launch.py` says if the host is modest (fewer than 8 threads or 16 GB): relaunch with `--lite`.
- End-of-day close: the page offers to stop node/npm processes whose worktree no longer exists.

## Board tools

- **Restart the server**: `npm run supervision -- <project> --restart` (clean stop by token), never `taskkill`. Log: `run/<project>/server.log`.
- **Defects and metrics**: `$S note X --defaut "…"`; history in the board tab; weekly note `$S metrics --weekly --vault` (signals per pattern, patterns never triggered).
- **Improvement**: `$S improve` (at the end of the week, or at the close) turns recurring patterns (3 signals or more in 14 days) into proposals in `INBOX/<host>/`, 3 per week at most, for the user to review. For a stubborn pattern: `$S improve --diagnose <code>` writes the instruction for a read-only sonnet subagent (evidence from the board, the skill file concerned, a minimal fix to propose in INBOX). Never edit the skill in the user's place.
- **End-of-day close**: reminder at 18:00 (banner + notification), close button on the board: **audit of the final state** (non-empty queue, agents or workers still active, unvalidated criteria, open signals, dirty or unpushed worktree, forgotten browser tab, lock held: read-only), report (LOG, vault note), cleanup, tickets to close, backup, then **clearing the board** (cards, alerts, queue, tasks and criteria, sheet notes; defects and metrics kept) — each action ticked and validated by the user. Clearing is refused without a successful backup in the same validation, or while an agent, a worker, a question, the queue or the lock is still running ("force" box). Backup only: `$S backup`.
- **End of supervision**: board stopped, `$S end` removes the project's panes from `.relay-supervision/index.json` and deletes `<project>.guard.json` (the sheets stay). If forgotten it no longer blocks: the push guard and the start hook ignore a pane whose board does not answer (`board-alive.js`).
- User messages flagged as a user decision from the board: apply them, do not argue them again.

Tool internals (SQLite, tests, adding a check): `MAINTENANCE.md`, for whoever modifies `TOOLS/supervision/`, not for supervising.
