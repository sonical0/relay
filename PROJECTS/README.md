# PROJECTS — one directory per project

Each project is a spoke of the Relay hub.

## Creating a project

```bash
node TOOLS/new-project.mjs my-project --summary "What it is for"
npm run state
```

This copies `_template/`, registers the project in `relay-brain.yml`, and refreshes
the derived status so the next session already knows about it.

## Minimum structure

```
PROJECTS/<name>/
├── AGENTS.md    ← local rules: role split, structure, data contract
├── CLAUDE.md    ← @../../CLAUDE.md + @./AGENTS.md
└── GEMINI.md    ← @../../GEMINI.md + @./AGENTS.md
```

There is no per-project bootstrap file and no per-project log stub any more. Both
existed to repeat what the framework already says; a rule has one owner. Local rules
go in `AGENTS.md`, activity goes in the global `RELAY_LOG.md`.

## Work from the project directory

Open your agent inside `PROJECTS/<name>/`, not at the Relay root. The startup hook
then injects that project's state — its status, its recent log entries, its local
rules — instead of the general view.

Measured on the internal system this framework comes from, same question asked twice:
from the root, 3 turns and 96,665 tokens; from the project directory, 1 turn and
25,728 tokens.

## Status is derived, intent is declared

You never write a `status:` field. `TOOLS/project-scan.mjs` derives active / idle /
dormant from the last git commit or file mtime. In `relay-brain.yml` you only declare
`intent:` when a project reaches a terminal state — `delivered`, `abandoned`,
`archived`, `done`. If you declare a project done and it keeps moving, the scan flags
the conflict instead of hiding it.

## Personal data

Project contents are cloud-only and gitignored. Only `_template/` ships in Git.
