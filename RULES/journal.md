---
paths:
  - "LOG/**"
  - "RELAY_LOG.md"
---

# Session journal

- **One file per machine**: each machine writes **only** `LOG/<host>.md` (`<host>` = `$RELAY_HOST`, else the hostname). `RELAY_LOG.md` is **generated** by merging every `LOG/*.md` at the end of each reply (`node TOOLS/relay-log.mjs` to force). Never write in it: the next merge rewrites it.
- **New entry on top**, right after the file header. Format: `## YYYY-MM-DD HH:MM — Agent (project: subject)` then at most 5 bullets of 15 words, `- [project] action → measured result`. No sub-lists.
- **The time is the local end-of-session time, read from the clock** (`date +"%H:%M"`), never estimated.
- **Fix or remove an entry** in the `LOG/<host>.md` that holds it, never in `RELAY_LOG.md`.
- **Rotation**: `LOG/<host>.md` keeps the current and the previous month; older entries go to `LOG/ARCHIVE/` (`TOOLS/log-rotate.mjs`, monthly, automatic). The brain's `cross_project_log`: 30 days, then `relay-brain-history.yml`.
- The `Stop` hook blocks once per session while this machine has no entry for today (`RELAY_UNATTENDED=1` for scheduled runs).

Why one file per machine, why the time: `DECISIONS.md`, 2026-09-19 and 2026-10-13.
