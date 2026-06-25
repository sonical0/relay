# Relay

A portable coordination framework for multi-agent AI CLI workflows.

[Lire en francais](README-fr.md)

---

## Overview

AI CLI agents — Claude Code, Antigravity CLI (agy), and their peers — are individually capable of handling complex, multi-step tasks. The problem surfaces when you need several of them working on the same project: they have no shared memory, no awareness of what the other is doing, and no mechanism to divide labor cleanly. Context gets lost between sessions. Work gets duplicated. One agent undoes what another just did.

Relay is a hub-and-spoke coordination layer that sits between your agents and your projects. Every agent reads the same entry point on startup, consults the same shared state file, appends to the same activity log, and follows the same protocol for claiming tasks before starting them. The result is a coherent multi-agent workspace where Claude Code handles what it does best, Antigravity CLI handles what it does best, and neither steps on the other.

The framework is built around a single constraint: it must run anywhere. No hardcoded paths, no machine-specific configuration committed to version control. The skeleton lives in Git; personal project data lives in cloud sync. A new machine is operational in three commands.

---

## How It Works

Relay uses a hub-and-spoke architecture. The hub is the Relay root directory itself — a set of shared files that all agents read before doing anything. Each project is a spoke: a subdirectory under `PROJECTS/` with its own instructions, role assignments, and local overrides.

**AGENTS.md** is the universal entry point. Every CLI agent, regardless of vendor, reads this file first. It defines the entry protocol, directory layout, shared conventions, and how to handle the first run on a new machine. Agent-specific capabilities and limits live in `CLAUDE.md` and `ANTIGRAVITY.md` respectively — thin wrappers that reference `AGENTS.md` and add what is specific to that agent.

**relay-brain.yml** is the shared state hub. It records which agents are available and what they can do, which projects are registered, and which tasks are currently claimed. Before starting a long task, an agent writes a claim entry here. Before starting any task, an agent checks for existing claims to avoid duplication.

**RELAY_LOG.md** is the shared activity log — a running record of what each agent has done across all projects. Agents write batch-summaries here (max 5 bullets, 15 words each), not per-file traces. It is the primary coordination surface between agents operating asynchronously.

**SKILLS/** stores per-project command routing tables. A skill file defines the modes available for a project, which files to load for each mode, when to spin up subagents, and how to auto-detect intent from user input. Skills accumulate over time: once a recurring workflow is identified, it becomes reusable across sessions.

**TOOLS/** contains shared utility scripts — Node.js ESM modules that have proven useful across at least two projects. Each tool exposes a standard CLI interface (args, exit codes, optional `--json` output) with no hardcoded paths.

---

## Stack

| Layer | Technology |
|-------|------------|
| State and configuration | YAML, Markdown |
| Utility scripts | Node.js 18+ (ESM), Python 3.8+ |
| Agent integration | Claude Code CLI, Antigravity CLI (agy) / Gemini CLI |
| Message broker | HTTP + SQLite (relay-server.py, port 7432) |
| Hook system | Claude Code hooks (session / setup / stop) |
| Framework versioning | Git |
| Personal data sync | MEGA / Nextcloud (or any file sync service) |
| Terminal browser panel | wmux (optional, Windows) |
| Token optimization proxy | RTK — Rust Token Killer (optional) |

---

## Getting Started

### Prerequisites

- Node.js 18 or later
- Claude Code CLI and/or Antigravity CLI (`agy`) / Gemini CLI, authenticated
- Git

### Installation

```bash
git clone <repo-url> relay
cd relay
```

Open the framework with your CLI agent:

```bash
claude .    # Claude Code
agy         # Antigravity CLI (or: gemini)
```

Then run the initialization wizard:

```
/framework-init
```

The wizard detects your operating system, copies `relay-brain.template.yml` to `relay-brain.yml`, installs the Claude Code hooks, configures agent environment settings, proposes wmux installation (Windows) or equivalent alternatives (macOS, Linux), and offers to install and configure RTK for token optimization. The full manual setup procedure is documented in `SETUP.md`.

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
  relay-brain-ref.md              Static reference markdown — @included in CLAUDE.md
  relay-brain.yml                 Active state hub — gitignored, cloud-synced
  RELAY_LOG.md                    Shared activity log — cross-agent, cross-project
  RELAY_CHAT.md                   Live chat between agents — gitignored, cloud-synced
  SETUP.md                        New machine setup guide
  README.md                       This file (English)
  README-fr.md                    French version

  PROJECTS/
    _template/                    Starter files for a new project (committed)
      AGENTS.md                   Project rules: role split, file structure, conventions
      CLAUDE.md                   Inherits from ../../CLAUDE.md
      GEMINI.md                   Inherits from ../../ANTIGRAVITY.md
      .relay-bootstrap.md         Local overrides pointing to ../../relay-bootstrap.md
      RELAY_LOG.md                Stub — activity goes to the global log
    {project-name}/               One directory per project (cloud-only, gitignored)

  SKILLS/
    _template/SKILL.md            Blank skill template (committed)
    {project-name}/SKILL.md       Per-project routing table (cloud-only, gitignored)

  TOOLS/
    README.md                     Tool catalog and interface contract (committed)
    relay-server.py               Message broker — HTTP + SQLite, port 7432
    relay-chat.mjs                Live chat CLI — send, poll, read, status
    relay-agent-loop.sh           Agent listener — polls broker, dispatches to agy
    relay-server-start.sh         Start broker in background
    init-relay-project.mjs        Initialize a new project
    html-to-pdf-generator.mjs     HTML → PDF (cloud-only, gitignored)
    fix-mojibake.mjs              Fix broken UTF-8 encoding (cloud-only, gitignored)

  .claude/hooks/
    relay-session-hook.js         UserPromptSubmit: inject Relay boot context
    relay-setup-hook.js           SessionStart: auto-verify hooks are installed
    relay-stop-hook.js            Stop: verify RELAY_LOG.md is up to date
  .claude/commands/
    framework-init.md             /framework-init wizard for Claude Code
  .gemini/commands/
    framework-init.toml           /framework-init wizard for Antigravity/Gemini CLI
```

---

## Synchronization Model

The framework separates two concerns: the protocol (agent-agnostic, shareable, stable) and the data (personal, volatile, machine-spanning).

| Content | Git | Cloud sync |
|---------|-----|------------|
| AGENTS.md, CLAUDE.md, ANTIGRAVITY.md, GEMINI.md | yes | — |
| relay-bootstrap.md, relay-brain.template.yml, relay-brain-ref.template.yml | yes | — |
| relay-brain-ref.md | yes | — |
| PROJECTS/_template/, SKILLS/_template/ | yes | — |
| TOOLS/README.md, TOOLS/relay-*.*, TOOLS/init-relay-project.mjs | yes | — |
| .claude/hooks/relay-*.js | yes | — |
| .gitignore, SETUP.md, README.md | yes | — |
| relay-brain.yml (active, with project data) | no | yes |
| RELAY_LOG.md (full working history) | no | yes |
| RELAY_CHAT.md (live agent chat) | no | yes |
| PROJECTS/{project}/ (project data) | no | yes |
| SKILLS/{project}/SKILL.md | no | yes |

The Git repository contains no personal data and can be shared publicly or cloned cleanly. The cloud-synced layer carries the living state: active projects, accumulated skills, utility scripts, and the full activity log.

---

## The Framework Grows With You

Relay is not configured once and forgotten. It is designed to accumulate value organically as you work.

Skills are added whenever a recurring workflow is formalized: the first time you run a sequence of commands, it is ad hoc; the third time, it becomes a skill entry. Tools follow a promotion protocol — a script earns its place in `TOOLS/` only after proving useful across at least two distinct projects. The activity log grows with every session, giving agents that arrive cold an immediate picture of recent cross-project activity. The brain file gains projects one by one, improving each agent's ability to assess context and avoid redundant work.

New CLI agents slot in without breaking anything. Adding Codex CLI or a future agent means creating one new file (`{AGENT}.md`) at the relay root, one entry in `relay-brain.yml`, and optionally one slash command definition. The universal entry protocol in `AGENTS.md` handles the rest — the new agent inherits the full coordination framework on its first run.

The framework grows heavier in the cloud and stays lean in Git.

---

## Adding a New Project

```bash
node TOOLS/init-relay-project.mjs my-project
```

This creates `PROJECTS/my-project/` from the template and registers the project in `relay-brain.yml`. Fill in `PROJECTS/my-project/AGENTS.md` to define the role split, file structure, and local conventions. Add a skill file at `SKILLS/my-project/SKILL.md` if the project has recurring command modes.

Manual alternative: copy `PROJECTS/_template/` to `PROJECTS/{name}/`, adapt the files, and add the project entry to `relay-brain.yml` by hand.

---

## Adding a New CLI Agent

1. Create `{AGENT}.md` at the relay root — begin with `@./AGENTS.md`, then declare agent-specific capabilities and constraints.
2. Add an entry under `agents:` in `relay-brain.yml`.
3. If the agent supports custom slash commands, create `.{agent}/commands/framework-init.{ext}` using the same wizard logic as the existing Claude and Antigravity versions.
4. Log the addition in `RELAY_LOG.md`.

The framework makes no assumptions about which agent is present. Any agent that reads `AGENTS.md` and follows the entry protocol is a first-class participant.

---

## Example Configuration

A typical Relay setup looks like this:

- **Orchestrator** : Claude Code — handles evaluation, PDF generation, merges, long reasoning
- **Worker** : Antigravity CLI (`agy`) — handles scraping, batch processing, liveness checks
- **Hooks** : `relay-session-hook.js` (boot context), `relay-stop-hook.js` (log verification)
- **Broker** : `relay-server.py` on port 7432 — real-time task delegation via `relay-chat.mjs`
- **Sync** : personal data (`relay-brain.yml`, `RELAY_LOG.md`, `PROJECTS/`) synced via cloud service
- **RTK** : token proxy reducing CLI output by 60-90%, configured via PreToolUse hook
