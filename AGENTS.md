# Relay — conventions

> **The single source of Relay's rules.** Every rule has exactly one owning file;
> duplication is a bug. This file replaces `tandem-bootstrap.md`, merged here on
> 2026-09-16 — the two described the same protocol and had drifted apart.

Every CLI agent reads this file. Agent-specific capabilities and limits live in
`CLAUDE.md`, `GEMINI.md`, or `{AGENT}.md`, which are thin and inherit from here.

---

## Who owns what

| Information | Owning file | Nature |
|---|---|---|
| Rules, conventions, protocol | `AGENTS.md` (this file) | manual |
| Machines, agents, projects, `intent:` | `relay-brain.yml` | manual |
| Real project status | `relay-projects.derived.yml` | **generated** (`TOOLS/project-scan.mjs`) |
| State injected at startup | `.relay-state.md`, `.relay-state/` | **generated** (`TOOLS/relay-state.mjs`) |
| Search index | `.relay-rag.json` | **generated** (`TOOLS/rag-index.mjs`) |
| Session journal | `RELAY_LOG.md` | manual, **newest entry on top** |
| Skill loading modes | `SKILLS/skills.manifest.json` | manual |

Never write to `relay-brain.yml` automatically: it carries human intent.
Measured facts go in the generated files. A generated file you edit by hand is
overwritten on the next run — change the script or its input instead.

---

## Startup

A `UserPromptSubmit` hook injects a pre-digested state block **once per session**,
picked from the `cwd`: `.relay-state/<project>.md` when you are inside a project
(status, recent sessions *for that project*, local rules), otherwise
`.relay-state.md` (the general view). Nothing else is read by default.

> **Work from the project directory, not the root.** Measured on the internal
> system this framework comes from, 2026-09-15, same question asked twice: from
> the root, 3 turns and 96,665 tokens; from the project directory, **1 turn and
> 25,728 tokens** — the targeted state block already held the answer. One avoided
> turn is worth 4,000 to 11,000 tokens, against 614 for the whole context
> consolidation. The lever is the exploration turn you skip, not the block size.

Then, as needed:

- **Query the RAG before opening a file** —
  `node TOOLS/rag-query.mjs --query "…" [--full] [--type <kind>]`.
  Kinds: `rules` · `project` · `skill` · `log` · `brain`.
  Open the source file only if the RAG answer was not enough.
- Machine and project detail: `relay-brain.yml`. History: `RELAY_LOG.md`.
- A project's local rules: `PROJECTS/<project>/AGENTS.md`.

**First run on a machine** — if `relay-brain.yml` does not exist (only
`relay-brain.template.yml` does), do not enter the normal protocol. Tell the user
this is a first run and launch `/framework-init`, or follow `SETUP.md`.

**A machine that is already configured but has no hooks**: `npm run bootstrap`
(installs dependencies, links skills, computes status, builds the index), then
`node relay-setup-hook.js` once to install the hooks.

---

## RELAY_ROOT — path resolution

`RELAY_ROOT` is the directory containing this `AGENTS.md`.

- In `.md` and `.yml` files: paths relative to `RELAY_ROOT` (e.g. `./relay-brain.yml`).
- In Node scripts: `path.dirname(fileURLToPath(import.meta.url))` plus relative navigation.
- The `$RELAY_ROOT` environment variable is optional; it wins when set.
- In `relay-brain.yml`, `{{relay_root}}` resolves through `machines[<hostname>].relay_root`
  (Windows `$env:COMPUTERNAME`, Linux/macOS `hostname`). If your machine is absent, add it.

Never hardcode an absolute path in this framework.

```
RELAY_ROOT/
├── AGENTS.md, relay-brain.yml, RELAY_LOG.md
├── PROJECTS/<project>/     SKILLS/<skill>/SKILL.md     TOOLS/
├── .claude/skills/ (links, generated)                  .relay-state/ (generated)
```

---

## Writing conventions

- **Atomic**: read → modify → write through tmp + rename. Re-validate the format
  (YAML/JSON) before the swap. Reference implementation: `TOOLS/lib/atomic.mjs`.
- **RELAY_LOG.md**: current month plus the previous one only; older entries move to
  `LOG/ARCHIVE/` (monthly rotation by `TOOLS/log-rotate.mjs`). New entry **on top**.
  Format `## YYYY-MM-DD — Agent (project: subject)`, then at most 5 bullets of 15
  words, `- [project] action → measured result`. No sub-lists. One batch summary per
  significant task, never one line per file touched.
- **Encoding**: UTF-8 without BOM. Never use PowerShell to write accented text —
  use the agent's file-write tool or Node.
- **Never overwrite another agent's work** without documenting it in `RELAY_LOG.md`.
- **Clarify before generating**: for a substantial deliverable with real degrees of
  freedom, ask 2 to 5 targeted questions first. Not for a trivial or already-scoped task.
- **Verification gate**: after a delegated agent reports back on anything that matters,
  re-run the checks yourself (build, typecheck, diff, read the output). Never trust a
  self-declared "done".

---

## Agents

Relay makes no assumption about which agents are present. The orchestrator is
whichever agent has full filesystem access and can spawn the others; sandboxed
agents are workers.

Delegate to another agent the capabilities it alone has, rather than improvising a
degraded equivalent yourself. Record each agent's capabilities and limits in its
`{AGENT}.md` and under `agents:` in `relay-brain.yml`.

**Adding a CLI agent**
1. Create `{AGENT}.md` at `RELAY_ROOT` — it starts by including `AGENTS.md`, then
   declares capabilities and limits.
2. Add an entry under `agents:` in `relay-brain.yml`.
3. If the CLI supports slash commands, add `.{agent}/commands/framework-init.{ext}`.
4. Log it in `RELAY_LOG.md`.

Subagents: delegate past 3 files, on a batch task, or for speculative search across
files you do not know.

---

## Skills

Sources live in `SKILLS/`, but **Claude Code only discovers `.claude/skills/`**.
`node TOOLS/skills-link.mjs` creates the links from `SKILLS/skills.manifest.json`.
Neither NTFS junctions nor symlinks survive cloud sync: **re-run the script on every
machine**.

**A skill's `description:` is paid for on every session** — it enters the system
prompt (~0.37 token per byte, measured). The body is only loaded on invocation: put
all the method detail there and keep the description near 200 bytes, just enough to
trigger selection, **in quotes** (descriptions contain `:`, without quotes the
frontmatter stops being valid YAML for our own tooling).
`TOOLS/relay-state.mjs` raises an alert past 4,000 bytes total.

**Two loading modes**, via `load` in `SKILLS/skills.manifest.json`:

- **`native`** — linked into `.claude/skills/`, triggers on its own when relevant.
  Reserve it for what you would not think to go looking for.
- **`rag`** — no link, absent from the system prompt. The skill is found with
  `node TOOLS/rag-query.mjs --query "…" --type skill`, and you read its `SKILL.md`
  before applying it. Reserve it for what you invoke deliberately.

`rag` skills are listed by name in the injected startup state (~25 tokens): without
that, they would be unreachable — you do not search for what you do not know exists.

> **Do not build a routing skill.** Tried on the internal system 2026-09-15 and
> removed the same day: auto-triggering was 0 out of 3 projects, even with the
> suggestion present in the injected block. The per-project scope
> (`PROJECTS/<x>/.claude/skills/`) was also tried and abandoned: `git status -uall`
> follows junctions and saw `SKILL.md`, so a `git add .` would have committed a
> **copy** of the skill into the project repo, bound to diverge from `SKILLS/`.
>
> **`rag` mode is an experiment, not a settled state.** If it gets in the way — a
> skill you can no longer reach, friction on load — put everything back to native:
> `node TOOLS/skills-all-native.mjs && node TOOLS/skills-link.mjs`.

> **Prerequisite on each machine**: from a project directory, reading `SKILLS/` and
> running `TOOLS/` leaves the working directory and triggers an approval prompt. Add
> this to the user `settings.json` — and to every `CLAUDE_CONFIG_DIR` in use:
> ```json
> "permissions": {
>   "additionalDirectories": ["<relay_root>"],
>   "allow": ["Bash(node \"<relay_root>/TOOLS/*)", "Bash(npm run --prefix \"<relay_root>\"*)"]
> }
> ```
> Without it, `rag` mode costs an approval prompt on every skill load.

---

## Token economy

This is what Relay is for, not a bonus. Two levers:

- **RAG** — query before you read. An excerpt that forces you to open the file behind
  it costs more than no RAG at all: use `--full` when in doubt.
- **A token-optimizing CLI proxy** (e.g. RTK) if one is installed on the machine —
  prefix shell commands with it, including inside `&&` chains.

Before adding anything to the context loaded on every session, measure. The `@include`
chain on the internal system reached 21 KB before anyone noticed.

---

## Starting a new project

```bash
node TOOLS/new-project.mjs <name>
```

Creates `PROJECTS/<name>/` from `_template/` and registers the project under
`projects:` in `relay-brain.yml`. Then:

- [ ] Fill in `PROJECTS/<name>/AGENTS.md` (role split, structure, local conventions)
- [ ] Set `intent:` in `relay-brain.yml` — the human intent; status is derived from disk
- [ ] Create `SKILLS/<name>/SKILL.md` and declare it in the manifest, if the project
      has recurring modes or commands
- [ ] `npm run state` to refresh the derived status and the injected state
- [ ] Log the start in `RELAY_LOG.md`

**Who does what on a new project**: read `relay-brain.yml → conventions` for the
default owners. Do not assume an existing project's split applies to the new one.
Record the split you settle on in `PROJECTS/<name>/AGENTS.md`.
