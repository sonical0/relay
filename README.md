# Relay

A portable coordination framework for multi-agent AI CLI workflows — built around one
constraint: **context is the budget**.

[Lire en français](README-fr.md)

---

## Overview

AI CLI agents — Claude Code, Gemini CLI, and their peers — each handle complex,
multi-step work well on their own. The problem surfaces when several of them work on
the same projects over months: no shared memory, no awareness of what the other did,
no cheap way to arrive cold and know where things stand.

The obvious fix is to make every agent read the shared state on startup. That is what
Relay did in its first version, and it is the wrong fix: a mandatory "read the brain,
read the log" protocol cost 12,000 to 15,000 tokens per session before any work
began, and grew every time the framework did.

Relay now does the opposite. A hook injects a **pre-digested state block, capped at
1.4 KB, once per session**, chosen from the directory you are standing in. Everything
else is on demand, through a local search index rather than by opening files. Project
status is derived from disk instead of hand-maintained, so it cannot silently go
stale. What an agent reads is the answer, not the corpus.

---

## How it works

**`AGENTS.md` is the single source of the rules.** Every rule has exactly one owning
file; duplication is treated as a bug. Agent-specific capabilities live in `CLAUDE.md`
or `GEMINI.md`, thin files that inherit from it. There is no second document restating
the protocol — that is what the previous version had, in three places, and they had
drifted apart.

**State is injected, not fetched.** `TOOLS/relay-state.mjs` builds `.relay-state.md`
(the general view) and one `.relay-state/<project>.md` per project: current status,
the last sessions *on that project*, which local rule files exist, which on-demand
skills fit what the project contains. A `UserPromptSubmit` hook picks one from the
`cwd` and injects it once per session, keyed on the session id.

**Status is derived, intent is declared.** `relay-brain.yml` is manual and carries
human intent — a project is `delivered` or `abandoned` because you said so.
`TOOLS/project-scan.mjs` derives active / idle / dormant from the last git commit or
file mtime and writes `relay-projects.derived.yml`. The two files cannot contradict
each other in silence: declare a project done while it keeps moving and the scan
reports the conflict.

**Search before read.** `TOOLS/rag-index.mjs` indexes every markdown section and YAML
block into a BM25 index; `rag-query.mjs` answers from it. Plain JavaScript, no native
dependency, so a clone runs anywhere. Use `--full` when a snippet is not enough — an
excerpt that forces you to open the file behind it costs more than no index at all.

**Skills are budgeted.** A skill's `description:` enters the system prompt on every
session, at roughly 0.37 token per byte. So skills load one of two ways: `native`
(linked into `.claude/skills/`, triggers on its own, paid for every session) or `rag`
(absent from the prompt, found through the index, read on demand). The state generator
raises an alert when the linked descriptions pass 4,000 bytes.

---

## What it measures

Everything above came out of measuring the version before it. The numbers are from the
internal system Relay is the public form of:

| Finding | Measurement |
|---|---|
| A conversation turn is the expensive unit | 4,000-11,000 tokens per turn, against 614 for the whole context consolidation |
| Working from the project directory beats the root | Same question: 3 turns / 96,665 tokens from the root, **1 turn / 25,728 tokens** from the project directory |
| A startup order costs more than what it injects | ~600 tokens injected, 12-15k tokens of reads provoked |
| Skill descriptions are a recurring cost | ~0.37 token per byte, every session, whether the skill fires or not |
| Context drifts when nobody watches | The `@include` chain reached 21 KB unnoticed |
| A routing skill does not self-trigger | 0 auto-triggers out of 3 projects — removed the same day |

The last two are the reason for the rule that matters most: **before adding anything
to the context loaded on every session, measure.**

---

## Quick start

```bash
git clone <repo-url> relay && cd relay
cp relay-brain.template.yml relay-brain.yml
npm run bootstrap
npm run hooks
```

Then add your machine and your projects to `relay-brain.yml`, and run `npm run state`.
Full procedure, including permissions: [`SETUP.md`](SETUP.md).

| Command | What it does |
|---|---|
| `npm run bootstrap` | Install, link skills, derive status, build state and index |
| `npm run hooks` | Install the three hooks into the agent's configuration |
| `npm run state` | Refresh derived status and the injected state |
| `npm run index` | Rebuild the search index |
| `npm run rag -- --query "…"` | Query it |
| `npm run rotate` | Archive log entries older than two months |

`npm run hooks` backs your settings up before touching them, adds only the hooks —
theme, permissions and existing hooks are preserved — refuses an invalid
`settings.json` rather than overwriting it, and is safe to re-run. **Hook
configuration is local to each machine**: neither Git nor cloud sync carries it, and
neither do NTFS junctions — which is also why `skills-link.mjs` has to run on every
machine. If you run two profiles (a personal and a work `CLAUDE_CONFIG_DIR`),
`RELAY_PROPAGATE_HOOKS=1` copies the hooks, and only the hooks, to the other one. It
is off by default, because overwriting the `hooks` key of a config dir the user did
not name is a surprising thing for a setup script to do.

---

## Layout

```
relay/
  AGENTS.md                  Single source of the rules — read first by every agent
  CLAUDE.md / GEMINI.md      Per-agent capabilities and limits (thin)
  relay-brain.template.yml   Machines, agents, projects, intent — copy to relay-brain.yml
  RELAY_LOG.md               Shared log, newest first, rotated monthly
  relay-session-hook.js      UserPromptSubmit — injects the state once per session
  relay-setup-hook.js        Installs the hooks; re-checks them on SessionStart
  relay-stop-hook.js         Refreshes status in the background at session end

  TOOLS/                     Committed: the framework does not work without them
    project-scan.mjs         Derives project status from disk
    relay-state.mjs          Builds the injected state
    rag-index.mjs            Builds the BM25 index
    rag-query.mjs            Queries it
    skills-link.mjs          Links native skills into .claude/skills/
    skills-all-native.mjs    Rollback to all-native
    log-rotate.mjs           Archives old log entries
    new-project.mjs          Creates and registers a project
    lib/atomic.mjs           Atomic writes (Windows lock retry)
    lib/rag.mjs              Tokenizer and BM25

  PROJECTS/_template/        Starter files for a project
  SKILLS/skills.manifest.json  Which skills exist and how each one loads
  SKILLS/_template/SKILL.md  Starter skill

  generated, never committed:
  relay-projects.derived.yml  .relay-state.md  .relay-state/  .relay-rag.json
```

---

## Design rules

These are load-bearing. Each one exists because its opposite was tried.

- **One owner per rule.** Three documents describing the same protocol will drift.
- **Generated and manual never mix.** A file is written by a script or by a human,
  never both. Measured facts go in generated files; intent stays manual.
- **Write atomically.** `lib/atomic.mjs`: temp file suffixed with the PID, retry on
  Windows lock. Without it, roughly one run in six failed with `EPERM` while cloud
  sync held the file.
- **Validate before the swap.** The temp file is re-read and parsed; an invalid result
  never replaces a valid original.
- **No absolute paths.** `RELAY_ROOT` resolves from the script's own location.
- **Compare both forms of a path.** On Windows the same directory arrives sometimes in
  8.3 short form (`C:\Users\JOHNDO~1\…`), sometimes long, and case distinguishes
  nothing while `path.resolve()` preserves it. The hook compares both forms,
  lowercased: without that the per-project state is never picked, and the failure is
  silent — the mechanism looks alive while it serves the general view.
- **Measure before adding.** Context loaded every session is the one thing that grows
  without anyone deciding to grow it.

---

## Adding a CLI agent

1. Create `{AGENT}.md` at the root — include `AGENTS.md`, then declare capabilities
   and limits.
2. Add an entry under `agents:` in `relay-brain.yml`.
3. If the CLI supports slash commands, add `.{agent}/commands/framework-init.{ext}`.
4. Log it in `RELAY_LOG.md`.

Relay assumes nothing about which agents are present. Any agent that reads `AGENTS.md`
is a first-class participant. Non-Claude agents do not read `.claude/skills/` — point
them at `SKILLS/<name>/SKILL.md` by path.

---

## What was removed, and why

Kept here because a framework that only documents its additions teaches nothing.

- **`claims[]`** — a task-claiming protocol in the brain. Zero writers in four months.
  Coordination happened through the log, which agents actually read.
- **`needs_bootstrap_sync`** — a flag for propagating bootstrap changes to projects.
  Nothing ever read it.
- **`tandem-bootstrap.md`** — a second copy of the entry protocol, merged into
  `AGENTS.md`.
- **The 7-step mandatory entry protocol** — replaced by the injected state block.
- **Per-project skill scope** — `git status -uall` follows junctions, so `git add .`
  would have committed a diverging copy of each skill into each project repo.
- **A routing skill toward on-demand skills** — never self-triggered. The suggestion
  now rides in the project's state block, which always arrives.

---

## License

MIT.
