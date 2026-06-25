<div align="center">

# Relay

**A portable coordination framework for multi-agent AI CLI workflows**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow?style=for-the-badge)](LICENSE)
[![Node.js](https://img.shields.io/badge/Node.js-18+-339933?style=for-the-badge&logo=node.js&logoColor=white)](https://nodejs.org)
[![Python](https://img.shields.io/badge/Python-3.8+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![Platform](https://img.shields.io/badge/Platform-Linux%20%7C%20macOS%20%7C%20Windows-lightgrey?style=for-the-badge)]()
[![Last Commit](https://img.shields.io/github/last-commit/sonical0/relay?style=for-the-badge)](https://github.com/sonical0/relay/commits/master)

[Lire en français](README-fr.md)

</div>

---

## The Problem

AI CLI agents — Claude Code, Antigravity CLI (agy), Gemini CLI — are individually capable of complex, multi-step tasks. The problem surfaces when you need several of them working on the same project: they have no shared memory, no awareness of what the other is doing, and no mechanism to divide labor cleanly. Context gets lost between sessions. Work gets duplicated. One agent undoes what another just did.

## The Solution

Relay is a hub-and-spoke coordination layer that sits between your agents and your projects. Every agent reads the same entry point on startup, consults the same shared state file, appends to the same activity log, and follows the same protocol for claiming tasks before starting them.

```
                        RELAY ROOT
                            │
          ┌─────────────────┼──────────────────┐
          │                 │                  │
      CLAUDE.md       ANTIGRAVITY.md     {agent}.md
          │                 │
     relay-brain.yml ◄──────┘    (shared state)
     RELAY_LOG.md    ◄──────┘    (shared log)
     RELAY_CHAT.md   ◄──────┘    (live channel)
          │
    ┌─────┴──────┐
    │            │
PROJECTS/     SKILLS/
project-a/    project-a/SKILL.md
project-b/    project-b/SKILL.md
```

The Git repository contains the **protocol** (agent-agnostic, shareable, stable). Personal project data lives in cloud sync. A new machine is operational in three commands.

---

## How It Works

**`AGENTS.md`** — Universal entry point. Every CLI agent reads this file first. Defines the entry protocol, directory layout, shared conventions, and first-run behavior on a new machine.

**`relay-brain.yml`** — Shared state hub. Records available agents, registered projects, and currently claimed tasks. Agents write claims before starting long tasks and check for existing ones to avoid duplication.

**`RELAY_LOG.md`** — Shared activity log. Agents write batch-summaries here (max 5 bullets, 15 words each). The primary async coordination surface between agents.

**`SKILLS/`** — Per-project command routing tables. A skill file defines available modes, which files to load for each mode, when to spin up subagents, and how to auto-detect intent from user input.

**`TOOLS/`** — Shared utility scripts. Each tool exposes a standard CLI interface (args, exit codes, optional `--json` output) with no hardcoded paths.

---

## Stack

| Layer | Technology |
|-------|------------|
| State and configuration | YAML, Markdown |
| Utility scripts | Node.js 18+ (ESM), Python 3.8+ |
| Agent integration | Claude Code CLI, Antigravity CLI (agy) / Gemini CLI |
| Message broker | HTTP + SQLite (`relay-server.py`, port 7432) |
| Hook system | Claude Code hooks (session / setup / stop) |
| Framework versioning | Git |
| Personal data sync | MEGA / Nextcloud (or any file sync service) |
| Token optimization | RTK — Rust Token Killer (optional) |

---

## Getting Started

### Prerequisites

- **Node.js 18+**
- **Claude Code CLI** and/or **Antigravity CLI** (`agy`) / **Gemini CLI**, authenticated
- **Git**

### Installation

```bash
git clone https://github.com/sonical0/relay.git
cd relay
claude .
```

Then run the initialization wizard:

```
/framework-init
```

The wizard will:
1. Detect your OS and copy `relay-brain.template.yml` → `relay-brain.yml`
2. Install the three Claude Code hooks into `~/.claude/settings.json`
3. Check for wmux, RTK, and Antigravity CLI availability
4. Print a configuration summary

> Full manual setup is documented in [`SETUP.md`](SETUP.md).

---

## Project Structure

```
relay/
  AGENTS.md                       Universal entry point — read first by all CLI agents
  CLAUDE.md                       Claude Code: @relay-brain-ref.md + capabilities, limits
  ANTIGRAVITY.md                  Antigravity CLI (agy): capabilities, limits, sandbox
  GEMINI.md                       Gemini CLI compat alias → @ANTIGRAVITY.md
  relay-bootstrap.md              Entry protocol — 8 mandatory steps, portable paths
  relay-brain.template.yml        Slim live template — copy to relay-brain.yml on setup
  relay-brain-ref.template.yml    Static reference template (agent caps, conventions)
  relay-brain-ref.md              Static reference — @included in CLAUDE.md
  relay-brain.yml                 Active state hub — gitignored, cloud-synced
  RELAY_LOG.md                    Shared activity log — cross-agent, cross-project
  RELAY_CHAT.md                   Live agent chat — gitignored, cloud-synced

  PROJECTS/
    _template/                    Starter files for a new project (committed)
    {project-name}/               One directory per project (cloud-only, gitignored)

  SKILLS/
    _template/SKILL.md            Blank skill template (committed)
    {project-name}/SKILL.md       Per-project routing table (cloud-only, gitignored)

  TOOLS/
    relay-server.py               Message broker — HTTP + SQLite, port 7432
    relay-chat.mjs                Live chat CLI — send, poll, read, status
    relay-agent-loop.sh           Agent listener — polls broker, dispatches to agy
    relay-server-start.sh         Start broker in background (idempotent)
    init-relay-project.mjs        Initialize a new project from template

  .claude/hooks/
    relay-session-hook.js         UserPromptSubmit: inject Relay boot context
    relay-setup-hook.js           SessionStart: auto-verify hooks are installed
    relay-stop-hook.js            Stop: verify RELAY_LOG.md is up to date
```

---

## Synchronization Model

The framework separates two concerns: the **protocol** (shareable, stable, in Git) and the **data** (personal, volatile, in cloud sync).

| Content | Git | Cloud sync |
|---------|:---:|:----------:|
| AGENTS.md, CLAUDE.md, ANTIGRAVITY.md, GEMINI.md | ✅ | |
| relay-bootstrap.md, relay-brain.template.yml, relay-brain-ref.md | ✅ | |
| PROJECTS/_template/, SKILLS/_template/ | ✅ | |
| TOOLS/relay-*.*, TOOLS/init-relay-project.mjs | ✅ | |
| .claude/hooks/relay-*.js | ✅ | |
| relay-brain.yml (active state) | | ✅ |
| RELAY_LOG.md (full history) | | ✅ |
| PROJECTS/{project}/ (project data) | | ✅ |
| SKILLS/{project}/SKILL.md | | ✅ |

The Git repository contains **no personal data** and can be cloned cleanly on any machine.

---

## The Framework Grows With You

Relay is designed to accumulate value organically as you work.

- **Skills** are added whenever a recurring workflow is formalized — ad hoc the first time, a skill entry by the third.
- **Tools** follow a promotion protocol — a script earns its place in `TOOLS/` only after proving useful across at least two distinct projects.
- **New agents** slot in without breaking anything — one new `{AGENT}.md` file and one entry in `relay-brain.yml`.

The framework grows heavier in the cloud and stays lean in Git.

---

## Adding a New Project

```bash
node TOOLS/init-relay-project.mjs my-project
```

Creates `PROJECTS/my-project/` from the template and registers it in `relay-brain.yml`. Then fill in `PROJECTS/my-project/AGENTS.md` to define the role split and local conventions.

---

## Adding a New CLI Agent

1. Create `{AGENT}.md` at the relay root — start with `@./AGENTS.md`, then declare capabilities and constraints.
2. Add an entry under `agents:` in `relay-brain.yml`.
3. Optionally create `.{agent}/commands/framework-init.{ext}` for a slash command wizard.
4. Log the addition in `RELAY_LOG.md`.

Any agent that reads `AGENTS.md` and follows the entry protocol is a first-class participant.

---

## Example Configuration

| Role | Agent | Responsibilities |
|------|-------|-----------------|
| Orchestrator | Claude Code | Evaluation, PDF generation, merges, long reasoning |
| Worker | Antigravity CLI (`agy`) | Scraping, batch processing, liveness checks |

```
Hooks    : relay-session-hook.js (boot context) + relay-stop-hook.js (log enforcement)
Broker   : relay-server.py on port 7432 — real-time delegation via relay-chat.mjs
Sync     : relay-brain.yml + RELAY_LOG.md + PROJECTS/ synced via cloud service
Tokens   : RTK proxy — 60-90% reduction on CLI output via PreToolUse hook
```

---

## Contributing

Contributions are welcome. Open an issue to discuss changes before submitting a pull request.

- Keep commits focused and descriptive
- No hardcoded paths — everything must resolve relative to `RELAY_ROOT`
- No personal data in committed files

---

## License

[MIT](LICENSE) — © 2026 sonical0
