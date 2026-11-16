---
description: "Set Relay up on this machine: brain, hooks, skills, status, index."
---

Run the Relay initialization for this machine and report what happened. Do not skip a
step silently — if one fails, say so and continue with the rest.

## 1. Check

- `node --version` — Relay needs Node 20+. Stop here if it is older.
- Hostname (`$RELAY_HOST` if set, else `$env:COMPUTERNAME` / `hostname`): the key under
  `machines:` in the brain.

## 2. Preview, then run

```bash
npm run init -- --dry-run
npm run init
```

`TOOLS/relay-init.mjs` creates `relay-brain.yml` with this machine if missing, installs
the dependency, installs the hooks (respecting `$CLAUDE_CONFIG_DIR`), links skills and
copies rules, derives status, builds the index and the injected state. Show the user its
output. It is idempotent.

## 3. The brain

Open `relay-brain.yml` and, with the user, keep under `agents:` only the CLIs actually
installed (test `claude --version`, `gemini --version`, any other they mention). Leave
`projects:` alone — `node TOOLS/new-project.mjs <name>` adds them. Never invent an
`intent:` value: it is the user's to declare.

## 4. Permissions

The init prints a `permissions` block with the real path. If the user `settings.json`
lacks it, ask the user (with `AskUserQuestion`) whether to add it. Without it, every skill
load or tool run from a project directory costs an approval prompt.

## 5. Verify and report

```bash
npm test
npm run rag -- --query "atomic write validate before swap"
```

Summarise: OS and hostname, agents found, on-demand vs native skills, projects detected,
hooks and permissions in place, anything that failed. Remind the user that the hooks only
apply after restarting Claude Code.

Finally, the one habit that matters: **open the agent inside the project directory, not at
the Relay root** — the injected state is then about that project.
