---
name: supervision
description: "Multi-agent supervision protocol (supervisor + managers, board, scopes, failures, resume). Invoke with /supervision when starting a supervisor or a manager."
disable-model-invocation: true
---

# Multi-agent supervision — core

Your **resume sheet** (injected at start, after `/clear` and after compaction) carries your role preamble, `$S`, the project and your state: it is the source of truth. Details are read once, on demand:

```
$S guide manager|integrator|supervisor|worker|no-wmux [--section <title>]
```

Launch: `npm run supervision -- <project>`; each agent with `SUP_AGENT=<project>/<X>` set (Windows: `TOOLS/supervision/start-manager.ps1`), or the commands `launch.py` prints.

## Rules

- **Board first**: status, files (`$S files X --add` before editing), delivery (`$S submit`). What is not on the board does not exist.
- **Scope**: outside your list or a common file, ask; never work around a refusal from the tool (scope, lock, quota, push guard).
- **Declared failure**: `--status blocked --msg "<cause>"` and `$S note X --defaut "…"`, never hidden in prose.
- **Question**: `$S ask X "…"`, then **end your turn**: the answer wakes you up. A question for the user is mirrored by an AskUserQuestion, options in the same order.
- **Messages**: a board line announcing N message(s) → `$S inbox X`, then apply them. A "user decision": apply it.
- **Reserved space**: the supervision workspace serves only the supervision; a guard refusal outside your scope is intended. Never touch `index.json` or `<project>.guard.json`.
- **Economy**: 10 lines per report; big files go to workers; `$S note X "…"` for what a resume must know.

Tool internals: `MAINTENANCE.md`.
