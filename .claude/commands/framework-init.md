---
description: "Set Relay up on this machine: brain, hooks, skills, status, index."
---

Run the Relay initialization for a new machine. Work through the steps in order and
report what you did at the end. Do not skip a step silently — if one fails, say so and
continue with the rest.

## 1. Detect the environment

- OS and shell (`$env:OS` / `uname -s`)
- Hostname — `$env:COMPUTERNAME` on Windows, `hostname` elsewhere. This is the key
  used under `machines:` in the brain.
- `node --version` — Relay needs Node 20+. Stop here if it is older.

## 2. The brain

If `./relay-brain.yml` is missing, copy `relay-brain.template.yml` to it.

Then edit it — it is a manual file, so make the edits explicit and show them:

- add an entry under `machines:` keyed by this hostname, with `relay_root` set to the
  absolute path of this directory, plus `os` and `shell`
- under `agents:`, keep only the CLIs actually installed (test `claude --version`,
  `gemini --version`, and any other the user mentions)
- leave `projects:` alone; projects are added with `node TOOLS/new-project.mjs`

Never invent an `intent:` value. It is the user's to declare.

## 3. Bootstrap

```bash
npm run bootstrap
```

This installs the one dependency, links `load: native` skills into `.claude/skills/`,
derives project status, builds the injected state and the search index. Report the
counts it prints.

## 4. Hooks

```bash
node relay-setup-hook.js
```

Tell the user the hooks are local to this machine, are never committed, and that the
agent has to be restarted for them to take effect.

## 5. Permissions

Check the user `settings.json` (respect `$CLAUDE_CONFIG_DIR`) for a `permissions`
block covering this directory. If it is missing, show the user the block from
`SETUP.md` section 3 with the real absolute path filled in, and offer to add it.
Without it, every skill load from a project directory costs an approval prompt.

## 6. Verify and report

```bash
npm run state
npm run rag -- --query "atomic write validate before swap"
```

The RAG query should surface the writing-conventions section of AGENTS.md without opening a file (SETUP.md ranks first, since it quotes the query itself). Then summarise:
OS and hostname, which agents were found, how many skills are native versus on demand,
how many projects were detected, whether the hooks and permissions are in place, and
anything that failed.

Finally, tell the user the one habit that matters: **open the agent inside
`PROJECTS/<name>/`, not at the Relay root** — the injected state is then about that
project.
