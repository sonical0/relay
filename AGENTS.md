# Relay — conventions

> **The single source of Relay's rules.** Every rule has exactly one owning file; duplication
> is a bug. This file is loaded in **every** session (~0.37 token per byte), so it keeps only
> universal rules. Rules that matter only when touching certain files live in `RULES/`
> (copied to `.claude/rules/`, loaded the first time a matching file is touched). Reasons and
> dated measurements live in `DECISIONS.md` — "DECISIONS, YYYY-MM-DD" points to an entry.
> Before adding a line here: measure.

Every CLI agent reads this file. Agent-specific capabilities live in `CLAUDE.md`, `GEMINI.md`
or `{AGENT}.md`, which are thin and include this one.

## Who owns what

- **Manual**: universal rules `AGENTS.md` · per-area rules `RULES/*.md` · reasons `DECISIONS.md` · machines, projects, `intent:` `relay-brain.yml` (optionally split in `BRAIN/`) · journal per machine `LOG/<host>.md` (newest on top) · agents' proposals `INBOX/<host>/` (reviewed with `/inbox`).
- **Generated** — never edit, change the script or its input: real status `relay-projects.derived.yml` (`TOOLS/project-scan.mjs`) · injected state `.relay-state.md` and `.relay-state/<project>.md` (`TOOLS/relay-state.mjs`) · merged journal `RELAY_LOG.md` (`TOOLS/relay-log.mjs`) · search index `.relay-rag.json` (`TOOLS/rag-index.mjs`) · `.claude/skills/`, `.claude/rules/` (`TOOLS/skills-link.mjs`) · supervision sheets `.relay-supervision/`.

Never write to `relay-brain.yml` automatically: it carries human intent; measured facts go to
the generated files. What should last but touches a rule or a skill: a proposal in
`INBOX/<host>/`. Several machines: `RULES/synchro.md`.

## Startup

The `SessionStart` hook injects a pre-digested state picked from the `cwd`:
`.relay-state/<project>.md` inside a project (status, recent sessions *of that project*, local
rules), otherwise `.relay-state.md`. It is re-injected after every compaction. Nothing else is
read by default.

**Work from the project directory, not the root**: the targeted state already holds the
answer — 1 turn instead of 3 (DECISIONS, 2026-09-15).

Then, as needed:
- **Query the RAG before opening a file**: `node TOOLS/rag-query.mjs --query "…" [--full] [--type <kind>]`.
  Kinds: `rules` · `convention` · `project` · `skill` · `log` · `brain`. An excerpt that forces
  you to open the file behind it costs more than no RAG: use `--full` when in doubt.
- Machines and projects: `relay-brain.yml`. History: `RELAY_LOG.md`. A project's local rules:
  `PROJECTS/<project>/AGENTS.md`.

**First run on a machine** (no `relay-brain.yml`, only the template): do not enter the normal
protocol. Tell the user, then run `npm run init` (or `/framework-init`, or `SETUP.md`).

## Paths

`RELAY_ROOT` is the directory holding this file (`$RELAY_ROOT` wins when set). In `.md` and
`.yml` files, paths are relative to it; in `relay-brain.yml`, `{{relay_root}}` resolves through
`machines[<host>].relay_root` (`<host>` = `$RELAY_HOST`, else the hostname; add your machine if
absent). Never hardcode an absolute path in the framework.

**Versioned projects live outside `PROJECTS/` and outside sync**: the git remote is the source
of truth, the clone is disposable. In the brain, `path` = the **real absolute path of the
clone, without `~`**, otherwise no targeted state. Look in `~/Documents/GitHub/` and
`~/Documents/Git/` before concluding a clone is missing; absent from this machine = `absent`,
expected — do not recreate the folder (DECISIONS, 2026-09-18 and 2026-09-19).

## Writing conventions

- **Atomic**: read → modify → write through tmp + rename, format re-validated (YAML/JSON)
  before the swap. Reference: `TOOLS/lib/atomic.mjs`.
- **Journal**: at the end of a session, one entry **on top** of `LOG/<host>.md` (never in
  `RELAY_LOG.md`, generated): `## YYYY-MM-DD HH:MM — Agent (project: subject)` then at most 5
  bullets of 15 words, `- [project] action → measured result`. The time is **read from the
  clock** (`date +"%H:%M"`), never estimated. Detail: `RULES/journal.md`.
- **Encoding**: UTF-8 without BOM. Never write accented text with Windows PowerShell 5 — use
  the agent's file tool or Node.
- **Clarify before generating**: for a substantial deliverable with real degrees of freedom,
  2 to 5 targeted questions first — through the agent's structured question tool (clickable
  choices, recommendation first), never a numbered list in the text. Not for a trivial or
  already-scoped task (DECISIONS, 2026-10-27).
- **Verification gate**: after a delegated agent reports back on anything that matters,
  re-run the checks yourself (build, typecheck, diff, read the output). Never trust a
  self-declared "done".
- **A session commits only what it changed**: `git add` with explicit paths, never
  `git add .` or `-A` — several sessions share the working copy. And `git push` publishes
  **the whole branch**: before pushing, `git log origin/<branch>..HEAD`; if it holds another
  session's commits, tell the user instead of pushing (DECISIONS, 2026-10-12).
- **Never overwrite another agent's work** without saying so in the journal.
- **Previews stay local** (`python -m http.server` + a browser panel); the repo is the source
  of truth, not a hosted copy (DECISIONS, 2026-11-09).
- **Durable facts go into Relay, not into an agent's private memory** (local to one machine,
  invisible to the others): a project fact → the project's notes; a rule or preference → a
  proposal in `INBOX/<host>/`.

## Agents

Relay makes no assumption about which agents are present. The **orchestrator** is the agent
with full filesystem access that can spawn the others; sandboxed agents are workers. Delegate
to another agent what it alone can do rather than improvising a degraded equivalent. Record
each agent's capabilities in its `{AGENT}.md` and under `agents:` in `relay-brain.yml`.

**Adding a CLI agent**: create `{AGENT}.md` (includes this file, then capabilities and
limits), add it under `agents:` in the brain, add `.{agent}/commands/framework-init.{ext}` if
the CLI has slash commands, log it.

Subagents: past 3 files, on a batch task, or for speculative search across unknown files.

**Multi-agent supervision** (several sessions on one project, with a board, scopes, and
deliveries refused without a recorded green check): `npm run supervision -- <project>`, rules
in the on-demand skill `/supervision`, details in `TOOLS/supervision/README.md`.

## Skills

Sources live in `SKILLS/`, but Claude Code only discovers `.claude/skills/`:
`node TOOLS/skills-link.mjs` creates the links (and copies `RULES/` to `.claude/rules/`). The
setup hook re-runs it at every start, because links do not survive sync.

On-demand skills (`disable-model-invocation: true`, zero tokens until invoked) are listed by
name in the injected state: the user types `/name`, the agent reads `SKILLS/<name>/SKILL.md`.
Loading modes, description budget, third-party skills: `RULES/skills.md`.

## Token economy

This is what Relay is for. Two levers: **the RAG** (query before you read) and **a
token-optimizing CLI proxy** if one is installed (e.g. RTK: prefix shell commands with it,
including inside `&&` chains). Before adding anything to the context loaded every session,
measure (`/context`): an `@include` chain once reached 21 KB without anyone noticing
(DECISIONS, 2026-10-14).

## Starting a project

`node TOOLS/new-project.mjs <name>` creates `PROJECTS/<name>/` from `_template/` and registers
it in the brain. Then fill in its `AGENTS.md` (role split, structure, local rules), set
`intent:` only for a terminal state, `npm run state`, and log the start. A versioned project:
clone it outside `PROJECTS/` and declare its absolute `path`. Do not assume an existing
project's role split applies to a new one.
