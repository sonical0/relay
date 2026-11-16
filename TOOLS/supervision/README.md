# Supervision — a board for a team of coding agents

An optional Relay module to run several Claude Code sessions on one project at the same time without them
stepping on each other: a supervisor (S) splits the work, managers (A, B, C, D) each own a scope and launch
workers in their own git worktrees, and one integrator merges everything through a single merge train.

A small local HTTP server keeps the shared state (tasks, scopes, locks, checks, merges, quota) and serves a
dashboard at `http://127.0.0.1:<port>/`. Agents talk to it through `sup.py`; the board raises alerts (scope
overlap, silent manager, red checks, merge conflicts, quota or host load brakes) and writes a **resume sheet**
per agent so a `/clear` or a context compaction loses nothing.

The agent-side rules are the `/supervision` skill (`SKILLS/supervision/`): `SKILL.md` is the short core,
`sup.py guide <role>` serves the rest on demand.

## Prerequisites

- Node.js 20+ and `npm install` at the Relay root (the config is read from `relay-brain.yml` with js-yaml).
- **Python 3.10+** (stdlib only, no pip install). `py.mjs` picks `python3`, `python` or `py -3`.
- git, and a git clone of the supervised project declared in `relay-brain.yml`.
- Optional: wmux, a terminal multiplexer with a browser panel (see below).

## Configure and launch

Add a `supervision:` block to the project in `relay-brain.yml` (commented example: `brain-example.yml`;
every key is optional), then:

```bash
npm run supervision -- example-app            # server + dashboard, registers the sessions it finds
npm run supervision -- example-app --lite     # lite mode: S acts as manager A, 3 workers, no panes
npm run supervision -- example-app --base feat/x --managers A,D --integrator D   # per-launch overrides
npm run supervision -- example-app --default  # forget the overrides, back to the brain
npm run supervision -- example-app --restart  # clean stop of the running server, then start again
npm run supervision -- example-app --dry-run  # show what would happen
```

The launcher does not start agent sessions: open them yourself (one per manager), then in each one run
`/supervision`. Agents use `python TOOLS/supervision/sup.py --project example-app <command>` (`$S` in the
skill): `manager`, `assign`, `worker`, `files`, `check`, `submit`, `merge`, `lock`, `inbox`, `report`, `end`…
(`sup.py --help`).

## Roles

| Role | Who | Does |
|---|---|---|
| Supervisor (S) | one session | splits the goal into tasks, assigns scopes, reviews, keeps the merge queue |
| Manager (A–D) | one session each | owns a scope, launches workers, reviews their diffs, submits branches |
| Integrator | one manager (`integrator:`) | only one allowed to edit the `common:` files and push to the base branch |
| Worker (A1, A2…) | subagent of a manager | works in its own worktree, runs the quick check |

## Without wmux (default) and with wmux

The backend is detected at launch (`SUP_BACKEND=auto`); `SUP_BACKEND=none` or `wmux` forces it.

- **Without wmux** (any OS, any terminal): the dashboard opens in your browser; each agent is identified by
  `SUP_AGENT=<project>/<X>` set in its environment when you start it (e.g. `SUP_AGENT=example-app/A claude`);
  messages from the board go to a mailbox that every `sup.py` call prints; notifications run `SUP_NOTIFY_CMD`
  with the text in `SUP_MSG` (e.g. `notify-send Supervision "$SUP_MSG"`).
- **With wmux**: panes are registered automatically, the board can type into a session, answer its prompts,
  do a fresh restart (`/clear` + resume) from a button, and give each agent its own browser tab
  (`sup.py browser`, `wmux-tab.mjs`). Nothing else changes.

On Windows, `start-manager.ps1` starts a manager session with `SUP_AGENT`, `SUP_PROJECT` and the per-session
state hooks (`claude --settings` from `sup-hook.js --emit-settings`).

## Hooks

`node relay-setup-hook.js` (`npm run hooks`) installs one supervision hook, and Relay's own start hook
carries the other half; both do nothing outside a supervision:

- `PreToolUse` / `Bash` → `supervision-guard.js`: in a supervised session, refuses force pushes and
  `--all`, a push to the base branch by anyone but the integrator, and a manager pushing a branch other than
  the one it declared. Fail-open: outside a supervision or at the slightest doubt, the command goes through.
- `SessionStart` → `relay-start-hook.js` (root) appends the session's resume sheet (from `.relay-supervision/`)
  after the Relay state, at start, after `/clear` and after compaction. `session-sheet.js` does the same on its
  own, for an installation that does not use Relay's start hook.

`sup-hook.js` (agent state from its own hook events) is **not** global: it is loaded per manager session
through `claude --settings "$(node TOOLS/supervision/sup-hook.js --emit-settings)"`.
`statusline.py` is an optional Claude Code status line that also sends quota and context usage to the board
(needed for the quota brake): `"statusLine": {"type": "command", "command": "python \"<relay>/TOOLS/supervision/statusline.py\""}`.

## State

Generated, never committed: `TOOLS/supervision/run/<project>/` (state database, token, logs, reports) and
`.relay-supervision/` (resume sheets, `index.json`). End-of-day notes go to `PROJECTS/<project>/supervision/`,
the weekly metrics note to `LOG/supervision/`.

## Tests

```bash
npm run supervision:test
# or: node TOOLS/supervision/py.mjs -m unittest discover -s TOOLS/supervision/tests -t TOOLS/supervision
```

The tests never touch real state: every write goes under a temporary root (`SUP_TEST=1`, `SUP_STATE_ROOT`,
`SUP_RELAY_STATE`, …) and `config.guard_write` refuses anything else, including the live server's port 8765.
