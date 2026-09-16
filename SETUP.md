# SETUP — Relay on a new machine

## Requirements

- **Node.js 20+** — the scripts in `TOOLS/`
- **Git**
- At least one agent CLI: [Claude Code](https://claude.com/claude-code),
  [Gemini CLI](https://github.com/google-gemini/gemini-cli), or another
- Optional: a cloud sync folder (MEGA, Nextcloud, Syncthing…) for the personal layer —
  `PROJECTS/`, real `SKILLS/`, `relay-brain.yml`, the full log

---

## 1. Clone and bootstrap

```bash
git clone <repo-url> relay
cd relay
cp relay-brain.template.yml relay-brain.yml     # Copy-Item on PowerShell
npm run bootstrap
```

`npm run bootstrap` installs the one dependency, links native skills into
`.claude/skills/`, derives project status, builds the injected state, and indexes the
RAG. It is idempotent — re-run it whenever something looks stale.

Then edit `relay-brain.yml`:

- add your machine under `machines:`, keyed by hostname
  (`$env:COMPUTERNAME` on Windows, `hostname` elsewhere)
- set `relay_root` to the absolute path of this directory
- adjust `agents:` to the CLIs you actually have

Re-run `npm run state` after editing.

---

## 2. Install the hooks

```bash
node relay-setup-hook.js
```

This writes three entries into `~/.claude/settings.json` (or `$CLAUDE_CONFIG_DIR`):

| Hook | Script | Role |
|---|---|---|
| `UserPromptSubmit` | `relay-session-hook.js` | injects the pre-digested state, once per session |
| `SessionStart` | `relay-setup-hook.js` | re-checks the hooks on every start, re-adds them if missing |
| `Stop` | `relay-stop-hook.js` | refreshes status in the background at session end |

It backs up your settings first, adds only the hooks — theme, permissions and any
existing hooks are preserved — and refuses to touch an invalid `settings.json` rather
than overwrite it. It is idempotent, so re-running it is safe. Restart your agent
afterwards.

If you run two profiles (a personal and a work `CLAUDE_CONFIG_DIR`), set
`RELAY_PROPAGATE_HOOKS=1` to copy the hooks — and only the hooks — to the other one.
It is off by default.

**Hook configuration is local to each machine** and never committed. Cloud sync does
not carry it, and neither do NTFS junctions or symlinks — which is also why
`skills-link.mjs` has to run on every machine.

---

## 3. Permissions (Claude Code)

From inside a project directory, reading `SKILLS/` and running `TOOLS/` leaves the
working directory and triggers an approval prompt every time. Add this to your user
`settings.json` — and to every `CLAUDE_CONFIG_DIR` you use:

```json
"permissions": {
  "additionalDirectories": ["/absolute/path/to/relay"],
  "allow": [
    "Bash(node \"/absolute/path/to/relay/TOOLS/*)",
    "Bash(npm run --prefix \"/absolute/path/to/relay\"*)"
  ]
}
```

---

## 4. Verify

```bash
npm run state
npm run rag -- --query "atomic write validate before swap"
```

The first prints the derived project status and the size of the injected state.

The second returns ranked sections. This page comes first — it contains the query you
just typed, which is the index working correctly — and `AGENTS.md → Writing
conventions` comes right after. That second hit is the real answer, delivered without
opening a file.

Then open your agent inside a project directory and check the injected block names
that project rather than giving the general view.

---

## Optional: a token-optimizing CLI proxy

A proxy such as RTK (Rust Token Killer) intercepts shell commands through a
`PreToolUse` hook and filters their output — typically 60-90% fewer tokens on `git`,
`npm` and similar. It is independent of Relay; if you install one, prefix your shell
commands with it, including inside `&&` chains.

Note that such a proxy filters output aggressively: when you need a file's exact
bytes (copying a script, checking encoding), use its raw passthrough mode rather than
the filtered one.

## Optional: a visible browser panel

If your terminal has a browser panel the user can watch (e.g. wmux), prefer it over
headless browsing so the work is visible. Probe it once per session rather than
assuming, and record the result in `relay-brain.yml → machines[<host>].browser_panel`.

---

## What lives where

| Content | Git | Cloud sync |
|---|---|---|
| `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `SETUP.md`, `README*` | yes | — |
| `TOOLS/*.mjs`, `relay-*-hook.js`, `package.json` | yes | — |
| `relay-brain.template.yml`, `SKILLS/skills.manifest.json` | yes | — |
| `PROJECTS/_template/`, `SKILLS/_template/` | yes | — |
| `relay-brain.yml` (active) | no | yes |
| `RELAY_LOG.md` (real history), `LOG/ARCHIVE/` | stub only | yes |
| `PROJECTS/<name>/`, real `SKILLS/<name>/SKILL.md` | no | yes |
| `relay-projects.derived.yml`, `.relay-state*`, `.relay-rag.json` | no — generated | no |
| `~/.claude/settings.json` (hooks) | no — per machine | no |

The Git repository carries no personal data and can be shared publicly. The cloud
layer carries the living state. The generated layer is rebuilt by `npm run bootstrap`
and belongs to neither.
