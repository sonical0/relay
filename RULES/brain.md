---
paths:
  - "BRAIN/**"
  - "relay-brain.yml"
  - "relay-brain-history.yml"
  - "relay-projects.derived.yml"
---

# The brain

- **`relay-brain.yml` holds human intent**: machines, projects, `intent:`, `cross_project_log`. Edited by hand, never by a tool. Measured facts (real status, last activity) go to `relay-projects.derived.yml`, **generated** by `TOOLS/project-scan.mjs`: do not edit it.
- **Several machines? Split it** (`node TOOLS/relay-brain.mjs --migrate`, once): one file per machine, per project and per `cross_project_log` entry under `BRAIN/`. You keep editing `relay-brain.yml`; at the end of each reply `TOOLS/relay-brain.mjs` reports changed blocks to their chunk, then regenerates the file. Two machines touching two different projects no longer overwrite each other. A block changed on both sides is set aside in `BRAIN/_conflicts/` and flagged in the injected state: settle it by hand. Without `BRAIN/`, nothing happens.
- **Broken YAML is neither reported nor regenerated**: fix it first (`node -e "require('js-yaml').load(require('fs').readFileSync('relay-brain.yml','utf8'))"`).
- **A project's `path`**: the real absolute path of its clone, without `~` (`path.normalize()` does not expand it). A path missing on this machine is looked up by folder name under `~/Documents/GitHub/` and `~/Documents/Git/` (or `$RELAY_CLONE_DIRS`). A clone elsewhere: `paths: { <host>: <path> }` next to it, which wins for that machine. A clone absent from a machine is marked `absent`: expected, do not recreate the folder.
- **Machine missing** from `machines:`: add it (`relay_root`), otherwise the injected state raises an alert every session.
- After editing: `npm run state` refreshes status and injected state without waiting for the end of the reply.
