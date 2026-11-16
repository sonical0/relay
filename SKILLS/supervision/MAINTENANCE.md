# supervision — maintaining the tool

> These notes only serve to modify `TOOLS/supervision/`, not to supervise; they are kept out of `SKILL.md` so the skill body reinjected after compaction stays small.

## Files

`server.py` (http board, state, `alerts()`, `watcher()`, `/api/hook`), `sup.py` (agents' client), `sup-hook.js` (state hook), `shared.py` (shared directories), `watch.py` (supervisor's monitor, `sup.py watch`), `launch.py` (`npm run supervision -- <project>`), `config.py` (the brain's `supervision:` block, read through `brain.mjs`), `store.py` (SQLite, the only writer), `reprise.py` (sheets in `.relay-supervision/`, injected by `session-sheet.js`), `prereview.py`, `cloture.py`, `metrics.py`, `lifecycle.py`, `wmux-tab.mjs` / `wbrowser.mjs` (safe browser tabs), `statusline.py` (quota, context), `supervision-guard.js` (push guard), `board-alive.js`.

## Starting a manager session: `start-manager.ps1`

A manager or worker session is started with `powershell -NoExit -File TOOLS\supervision\start-manager.ps1 -Role X -Project <project> -Dir <repo> [-PromptFile <instruction>] [-Config <CLAUDE_CONFIG_DIR>]`, not with a bare `claude`: the script sets `SUP_AGENT`, `SUP_PROJECT` and `SUP_RUN`, and loads the state hooks with `--settings`. By default it keeps the current Claude configuration; `-Config <dir>` picks another one (use the same account as the supervisor, or the board's quota is wrong). Outside Windows, `launch.py` prints the equivalent shell commands.

## State

- SQLite: `run/<project>/supervision.db` (WAL). `events` is append-only, nothing is purged. `/api/state` keeps its shape. Backup: `$S backup` (to `BACKUPS/<date>/`).
- Resume sheets are generated: the per-role protocol is cited in them by `reprise.protocole()`. Renaming a skill file means changing that function (and `sup.GUIDES`).

## Without wmux (backend; agent side: `no-wmux.md`)

`backend.py` picks `wmux` (the Node client answers "pong") or `none`, once per process; `SUP_BACKEND=auto|wmux|none` forces it (tests set `wmux`, the fake client of `tests/base.py`). `backend.wmux()` does nothing without wmux, `backend.notify()` calls wmux or `SUP_NOTIFY_CMD` (text in `SUP_MSG`). What wmux did by typing into a pane goes through `server.send_to()`: typed if possible, else left in `state["mailbox"][X]` (50 messages at most, not logged: `store.SILENT_META`); the `/api/report` handler returns `messages` to the calling agent (`server.agent_of`), `sup.py` shows them; `sup.py inbox X` reads them without duplicates. Unavailable without wmux: answering a prompt by choice or key, fresh restart (`server.requires_wmux`), `sup.py browser`, pane scans (the poller does not start), the "browser outside its pane" alert. Identity: `SUP_AGENT=<project>/<X>` read by `session-sheet.js` (sheet), `supervision-guard.js` (push guard) and `statusline.py`; the registered pane wins, the identity is the fallback. `launch.py` without wmux: `plain_steps` (default browser, agents registered without a pane).

## Portability (Linux, macOS)

npm scripts go through `py.mjs` (python3, python or `py -3`); the resume sheet cites `python3` outside Windows. `sysstats.py` reads `/proc` on Linux (nothing elsewhere; disk activity stays Windows-only), `cloture.py` uses `ss` and `kill` outside Windows (`parse_ss`), `wmuxcli._default_wmux()` finds a `wmux` on PATH. The suite passes on Linux (Debian, Python 3.11, node 22, e.g. in `docker run -v <root>:/repo:ro node:22-bookworm-slim` after `apt-get install python3 git iproute2`). Python < 3.12: no `onexc` in `shutil.rmtree` (tests). WSL traps: PATH inherits the Windows `wmux` (`/mnt/c/…`), unusable from Linux (Windows named pipe), so the backend is correctly `none`.

## Host load (`hostload.py`)

`profile()` derives from threads and RAM: `workers_total` (threads ÷ 2, at least 2), `npm_ci` (1 under 8 threads, 2 above), `lite_suggested` (fewer than 8 threads or 16 GB); the brain wins (`workers_total`, `npm_ci_max`, `host_limits: false` lifts the caps). `Brake`: tightens after `cpu_window` s at ≥ `cpu_brake`, releases after 60 s at ≤ `cpu_release` (memory: `mem_*`); `server.watcher()` feeds them with `sysstats.snapshot()` and warns the managers as for the quota brake; `host_braked()` reduces `worker_limit()` to `BRAKED_WORKERS`, and `alerts()` raises `host_cpu` / `host_mem` (signals, `signals.CODES`). Total cap: refused in `apply()` (not in tests unless `workers_total` is set in config). `Slots`: slots `run/<project>/slots/<name>-<i>/` (atomic mkdir, JSON `owner` with PID; a slot of a dead PID or older than 3 h is taken over) and `wait-*` queues; `sup.py install` and `sup.py slot` use them; the board reads `s["host"]["slots"]`. The check keeps its single lock (`check.lock`): it already serialises quick and full checks, so no quick-check semaphore is needed. `cloture.proposals()` offers the processes (`kind: proc`) whose command line cites a worktree git lists but which no longer exists (limit: an `npm ci` without a path in its command line is not recognised; its node children are). Tests: `tests/test_hostload.py`.

## Launch overrides

`launch.py` (`--base`, `--managers`, `--integrator`, `--lite`, `--default`) writes `run/<project>/overrides.json`; `config.load()` applies it after the brain (keys: `config.OVERRIDABLE`) and exposes `cfg["overrides"]` and `cfg["lite"]`. Lite mode = `config.LITE`: virtual manager A (no pane, integrator role), 3 workers; `alerts()` raises neither `stall` nor `no_integrator` there. An override changes what the server, `sup.py` and the sheets read: a running server keeps the old config until `--restart`.

## Commands that carry the rules

- `sup.py submit` (kind `submit`): structured delivery, refused without branch, SHA or green check; the delivered SHA feeds the pre-review (`prereview.compute`).
- `sup.py browser X open|eval|close`: the only path to `wmux browser`; one tab per agent, id in `run/<project>/browser-<X>.id`; `sup.create_tab` calls `wmux-tab.mjs`.
- `sup.py improve` / `improve.py`: recurring patterns → `INBOX/<host>/…-supervision-signal-<code>.md` (root overridable: `SUP_INBOX_DIR`). Text per pattern: `signals.CODES`. A pattern added to `alerts()` is also added to `signals.CODES`.
- Check proof: `kind: check` records `last_check` (rc, time); `submit` requires it green and less than `CHECK_FRESH_SECONDS` (2 h) old. Scope overlap: `server.scopes_overlap()` (literal prefixes of the globs, conservative), alert `scope_overlap`.
- Evaluation scenarios: `tests/scenarios.json` (replayed by `tests/test_scenarios.py`): a refusal or an alert promised by the skill is added there as one JSON entry.
- Diagnosis: `improve.diagnosis_prompt()` (text instruction, `improve.ROLE_FILES`: skill file per pattern).
- Framing and criteria: `task` accepts `brief`, `criteria_add`, `criteria_pass`, `criteria_fail`, `criteria_clear`; `server.open_criteria()` blocks `submit` (the manager's tasks cited by its current task); the sheets show the checkboxes (`reprise._task_lines`).
- Push guard: `supervision-guard.js` (PreToolUse hook on Bash, fail-open, exit 2 = refusal) reads `.relay-supervision/index.json` and `<project>.guard.json` (written by `reprise.write_all`: base, role and branch of each manager). Installed as a global `PreToolUse` hook by `relay-setup-hook.js`; active in sessions started afterwards. Unreadable rules: only forced, deleting or global pushes are refused.
- Integrator's train: `server.scan_real_files()` (called by `scanner()`) passes to `git_files()` the SHAs and names of the queue's branches in `ready` or `merged` (`train_refs`) for the integrator only: their commits are not its own, even if their managers switched branches or did not push them. Its own `integ/*` is then no longer excluded: its real change outside the scope still raises an alert. Unknown refs are ignored one by one (otherwise `git log` fails and hides everything). Tests: `tests/test_train.py` (no JSON scenario: `real_files` comes from git).
- `sup.py ask X "<question>" [--choices '<json>']` / `--done`: kind `ask` (card `blocked`, `m["question"]`, previous status restored by `--done`, supervisor told by `server.tell_blocked`), then `report-agent --blocked|--unblocked` through `wmuxcli.wmux` (resolved path, never the `wmux` on PATH), surface `WMUX_SURFACE_ID` else the registered one; without wmux, the question stays on the board and in S's mailbox. Any switch to `--status blocked` also tells S (`tell_blocked`, once per switch). Sheets: `reprise._ask_line()` (the `ask` command, `watch` for S, absolute wmux path with wmux), pending question in the manager's sheet.
- `sup.py watch [--once] [--interval 30] [--no-screens]` / `watch.py`: monitor for the Monitor tool, one line per change (`Watch._diff`: status, progress, last log line of each manager, questions, open requests, new alerts per code + agent, S's mailbox); with wmux, `read-screen` of the registered surfaces and `screen_question()` (idle pane, last useful line ending with "?"). Board unreachable: one line per outage. Tests: `tests/test_ask_watch.py`.
- Close audit: `cloture.audit()` (read-only), shown on the close page (`/api/cloture` → `audit`).
- `setup:` key of the brain's `supervision:` block: how to set up a worktree, repeated in the sheets.
- Clearing the board at the close: kind `clear` → `server.clear_board()`, distinct from `reset` (which resets everything to `empty_state()`, panes and sessions included). Keeps the managers' registration (`CLEAR_KEEP`: session, surface, role), `events`, sessions, quota, project, supervisor, plan, S's notes, mailbox; empties cards, workers, tasks, queue, requests, questions, goal. Tasks and goal are archived first in `events` (kind `task_archive`, JSON in `after`); sets `_reset` and `cleared_t`. Close side: action `clear`, run last, refused without a successful `backup` in the same validation, and if `cloture.clear_blockers()` finds an active agent or worker, a question, an unmerged queue or a held lock, unless `force_clear`. Rollback: the day's backup (`BACKUPS/<date>/supervision-<project>.db`) put back in place of `run/<project>/supervision.db`, server stopped (`store` has no restore function).
- Wave report: `sup.wave(s, start)` keeps only tasks (`updated`), queue (`t`) and requests (`t`) later than the start of the day and `cleared_t`; used by `report_body` (hence `sup.py report` and `cloture.note_draft`) and `cloture.log_draft`.
- Inherited criteria: `task` with `criteria_add` on a task with a ticked criterion is refused without `criteria_clear` (`--clear-criteria`).

## Hooks, mailbox and launch token

- **State through hooks**: `sup-hook.js` (11 events, async) is loaded only in manager sessions: `start-manager.ps1` runs `claude --settings <run/sup-hook.settings.json>` (written by `node sup-hook.js --emit-settings`); installed globally it would cost 2 extra node processes per tool call in every session. Without `SUP_AGENT`, it exits at once; otherwise `POST /api/hook` (0.5 s at most), spool `run/<project>/hook-spool.jsonl` (256 KB) replayed by `replay_spool()` at start. `server.hook_event()` → `HOOKS` (not logged); `hook_state()`: permission or AskUserQuestion → waiting, Stop → done (unless a subagent is active), StopFailure → blocked. Async hooks: an older event is ignored, work dated less than `HOOK_AFTER_STOP` after Stop is ignored. `agents_view()` = scan + hooks; `agent_poller()` reads the screen only for an agent without a hook for `HOOK_FRESH_SECONDS` (fallback) or to show an open prompt. Without wmux, hook state is enough. Tests: `tests/test_hooks.py`.
- **Pointer**: `send_to()` to an agent with hooks → mailbox (`mailbox_push`, id) + `deliver_pointer()` only when idle (never during a turn nor over a prompt); `urgent=True` (STOP) types the text. `pointer_followup()` (watcher): reminder at 10 min, then signal `mail_unread` and a message to S. Agent without hooks: direct typing as before. `sup.py inbox` shows plain text.
- **Launch token**: `run/<project>/run.id` (`server.new_run_id()`, stable 20 h, removed by `sup.py end`), `SUP_RUN` set by `start-manager.ps1` and `launch.start_cmds`, `run` in `<project>.guard.json`. The guard and `/api/hook` ignore another token; without a token on one side, previous behaviour.
- **Shared directories**: `shared_dirs` (config); `shared.link_shared()` on `sup.py manager X --worktree` or `sup.py share`; npm rewriting `node_modules` refused by `sup.py install` and `supervision-guard.js` (`sharedNpm`).
- **Idempotence**: `submit` and `request` carry a `request_id` (`sup.py`); `apply()` returns the receipt (`receipts`, 200 at most) instead of replaying.
- **Notifications**: `notify_plan()` keyed (code, agent), delay `signals.COOLDOWN_SECONDS`; prompts: `signals.COOLDOWN`, "waiting" notified, "done" not (`signals.AGENT_NOTIFY`). Tests: `tests/test_orca6.py`.
- **Served guide**: `sup.py guide <role> [--section]` (`GUIDES`: `common.md` + the role's file; worker: 4 sections of `manager.md`), calls logged in `run/<project>/guide-calls.jsonl`. Sheets: `reprise.preamble()` (≤ `PREAMBLE_MAX`), base drift (`server.base_drift`, read by the scanner), unread messages. `SKILL.md` ≤ 2,500 bytes (test). Tests: `tests/test_guide.py`.

## Kept questions and stale permissions

`sticky_ask()` keeps on the board a declared question that wmux clears at the end of a turn, until the answer or the resume. Exception, `stale_permission()`: a Claude Code permission (`PERMISSION_RE`, "needs your permission") with no prompt visible on screen for `STALE_POLLS` scans is lifted (otherwise a stopped agent stays "waiting" for good). Tests: `tests/test_server.py`, `QuestionPersistante`.

## Signals

A detected failure pattern (browser outside its pane, refused scope, collision, unverified worker, repeated red checks, merge conflict, silence, repeated resume, forgotten lock, quota) is recorded in `events` with `kind='signal'`, `subject` = pattern code, `who` = the agent. A signal stays **open** while its condition lasts; it is deduplicated after a server restart. The weekly note counts them per pattern.

## Tests

`npm run supervision:test` (= `node TOOLS/supervision/py.mjs -m unittest discover -s TOOLS/supervision/tests -t TOOLS/supervision`; temporary roots mandatory, port 8765 refused). An added check = a test in `TOOLS/supervision/tests/`.
