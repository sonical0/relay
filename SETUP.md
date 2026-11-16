# SETUP — Relay on a new machine

## Requirements

- **Node.js 20+** — the scripts in `TOOLS/` and the hooks
- **Git**
- At least one agent CLI: [Claude Code](https://claude.com/claude-code),
  [Gemini CLI](https://github.com/google-gemini/gemini-cli), or another
- Optional: **Python 3.10+**, only for multi-agent supervision (`TOOLS/supervision/`)
- Optional: a sync tool (Syncthing, Nextcloud…) for the personal layer when you work on
  several machines — see "What lives where" below and `RULES/synchro.md`

---

## 1. Clone and initialise

```bash
git clone <repo-url> relay
cd relay
npm run init
```

`npm run init` (`TOOLS/relay-init.mjs`) does, in order:

1. checks Node 20+;
2. creates `relay-brain.yml` from the template if missing, with this machine under
   `machines:` (keyed by `$RELAY_HOST`, else the hostname) and its `relay_root`;
3. installs the one dependency (`npm ci`);
4. installs the Claude Code hooks (`relay-setup-hook.js`, see below);
5. links skills into `.claude/skills/` and copies `RULES/` into `.claude/rules/`;
6. derives project status, builds the search index and the injected state;
7. prints what remains manual (permissions, step 3).

It is idempotent: a re-run only redoes what is missing. `--dry-run` shows what it would
do; `--config-dir <dir>` targets another Claude config dir.

Then review `relay-brain.yml`: keep under `agents:` only the CLIs you actually have.

---

## 2. The hooks

Written into `~/.claude/settings.json` (or `$CLAUDE_CONFIG_DIR`) by the init, or by
`npm run hooks` on its own:

| Event | Script | Role |
|---|---|---|
| `SessionStart` `startup\|resume\|clear\|compact` | `relay-start-hook.js` | injects the state targeted at the `cwd`'s project; re-injects it after every compaction |
| `SessionStart` (async) | `relay-setup-hook.js` | repairs missing or stale hooks at every start, re-links skills and rules |
| `Stop` | `relay-stop-hook.js` | asks once per session for today's journal entry; launches the detached after-turn work (journal merge, brain sync, state and index only if their inputs changed) |
| `PreToolUse` `Bash` | `TOOLS/supervision/supervision-guard.js` | push guard during supervision; exits immediately otherwise |

The script backs up your settings first, merges only its own entries — theme,
permissions and other tools' hooks are preserved, a stale path is fixed in place — and
refuses to touch an invalid `settings.json`. Upgrading from Relay 2: it removes the old
`UserPromptSubmit` entry (`relay-session-hook.js`) by itself. Restart your agent afterwards.

Two profiles (a personal and a work `CLAUDE_CONFIG_DIR`)? `RELAY_PROPAGATE_HOOKS=1`
merges the hooks into the other one too. Off by default.

Scheduled, unattended runs: set `RELAY_UNATTENDED=1` so the Stop hook never blocks them.

**Hook configuration is local to each machine** and never committed or synced — nor are
links, which is why the setup hook re-links skills at every start.

---

## 3. Permissions (Claude Code)

From a project directory, reading `SKILLS/` and running `TOOLS/` leaves the working
directory and triggers an approval prompt every time. Add to your user `settings.json`
(every `CLAUDE_CONFIG_DIR` you use) — the init prints it with your real path:

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
npm test
npm run rag -- --query "atomic write validate before swap"
```

Then open your agent **inside a project directory** and check the injected block names
that project rather than giving the general view. At the end of the session, the Stop
hook asks for a journal entry in `LOG/<host>.md` once.

---

## Upgrading from Relay 2

1. `git pull`, then `npm run init` — it swaps the hooks and rebuilds everything generated.
2. Your old `RELAY_LOG.md` entries are moved into `LOG/<host>.md` on the first merge;
   `RELAY_LOG.md` is generated from then on.
3. Skills: the manifest `load` key is gone. Add `disable-model-invocation: true` to the
   frontmatter of every skill that was `load: rag` (`RULES/skills.md`).
4. Several machines sharing one brain: `npm run brain -- --migrate` once (`RULES/brain.md`).

---

## Optional: a token-optimizing CLI proxy

A proxy such as RTK intercepts shell commands through a `PreToolUse` hook and filters
their output — typically 60–90 % fewer tokens on `git`, `npm` and similar. It is
independent of Relay; if you install one, prefix shell commands with it, including inside
`&&` chains. When you need a file's exact bytes, use its raw passthrough mode.

## Optional: a visible browser panel

If your terminal has a browser panel the user can watch (e.g. wmux), prefer it over
headless browsing. With several agents in one workspace, each drives its own tab
(`CLAUDE.md`, "Browser panel").

---

## What lives where

| Content | Git | Sync between machines |
|---|---|---|
| `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `DECISIONS.md`, `RULES/`, `SETUP.md`, `README*` | yes | — |
| `TOOLS/`, `relay-*-hook.js`, `package.json`, `package-lock.json` | yes | — |
| `relay-brain.template.yml`, `SKILLS/skills.manifest.json`, shipped skills (`inbox`, `supervision`) | yes | — |
| `PROJECTS/_template/`, `SKILLS/_template/` | yes | — |
| `relay-brain.yml`, `BRAIN/`, `relay-brain-history.yml` | no | yes |
| `LOG/<host>.md`, `LOG/ARCHIVE/` | no | yes (one writer per file) |
| `INBOX/<host>/` | no | yes |
| `PROJECTS/<name>/` (unversioned), your own `SKILLS/<name>/` | no | yes |
| Versioned projects (their own git clone, outside `PROJECTS/`) | their own remote | **no** |
| `RELAY_LOG.md`, `relay-projects.derived.yml`, `.relay-state*`, `.relay-rag.json`, `.claude/skills/`, `.claude/rules/`, `.relay-supervision/` | no — generated | **no** — rebuilt on each machine |
| `~/.claude/settings.json` (hooks) | no — per machine | no |

The Git repository carries no personal data and can be shared publicly. The sync layer
carries the living state. The generated layer is rebuilt locally and belongs to neither:
synced, it would carry one machine's absolute paths to another (DECISIONS, 2026-10-19).
