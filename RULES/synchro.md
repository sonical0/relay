---
paths:
  - ".stignore*"
  - "BRAIN/machines/**"
  - "INBOX/**"
  - "TOOLS/relay-after-turn.mjs"
  - "TOOLS/lib/brain.mjs"
---

# Several machines

- **Sync the sources, never the generated files.** Sources: `LOG/*.md`, `BRAIN/`, `relay-brain.yml`, `RULES/`, `SKILLS/`, `PROJECTS/`, `INBOX/`. Generated, rebuilt locally at the end of each reply: `RELAY_LOG.md`, `.relay-state*`, `.relay-rag.json`, `relay-projects.derived.yml`, `.claude/skills/`, `.claude/rules/`, `.relay-supervision/`. A synced generated file arrives on another machine carrying this machine's absolute paths. With Syncthing, keep that list in a synced `.stignore-relay` and `#include .stignore-relay` from each machine's own `.stignore`.
- **One writer per shared file**: `LOG/<host>.md` by its machine only; brain chunks per machine or project (`RULES/brain.md`). File-level sync tools keep the last copy and drop the other: two writers on one file lose data.
- **Locks protect one machine only** (`TOOLS/lib/atomic.mjs`): never rely on them across machines.
- **`role:`** in `machines.<host>` (read by `machineRole()`, default `workstation`): `workstation` regenerates its local files and merges the brain; `satellite` (a VM or container running unattended agents) regenerates its local files but never writes the brain.
- **Identity**: `RELAY_HOST` wins over the hostname (container, VM). A host without an entry in the brain gets an alert in the injected state.
- **Versioned projects live outside `PROJECTS/` and outside sync**: the git remote is the source of truth, the clone is disposable. Declare the clone's absolute path as the project's `path`.
- **INBOX/**: `INBOX/<host>/YYYY-MM-DD-<slug>.md` (format: skill `inbox`); reviewed on a workstation with `/inbox`, then moved to `INBOX/_done/YYYY-MM/` with the decision. Never deleted.
