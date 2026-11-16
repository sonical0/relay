# TOOLS — shared scripts

Standalone Node scripts, usable by any agent on any project. Unlike project data,
these **are** committed: they contain no personal information, and the framework does
not work without them — the startup state, the derived status, the merged log and the
search index are all produced here.

## Catalogue

| Script | What it does | Writes |
|---|---|---|
| `relay-init.mjs` | One-command machine setup: brain entry, deps, hooks, links, state, index. Idempotent | `relay-brain.yml`, `settings.json` (via the setup hook) |
| `relay-after-turn.mjs` | End-of-turn work run by the Stop hook: rotate, brain, log merge, scan, then state + index only if their inputs changed | — (runs the others) |
| `relay-log.mjs` | Merges `LOG/<host>.md` (one file per machine) into the generated `RELAY_LOG.md`; moves hand-written entries to this machine's file | `RELAY_LOG.md`, `LOG/<host>.md` |
| `log-rotate.mjs` | Keeps this machine's log to two months; `--brain` moves old `cross_project_log` entries out | `LOG/ARCHIVE/`, `relay-brain-history.yml` |
| `relay-brain.mjs` | Opt-in split brain: one chunk per machine, project and cross-project entry under `BRAIN/`; edits reported back by fingerprint, conflicts set aside | `BRAIN/`, `relay-brain.yml` |
| `project-scan.mjs` | Derives real project status from disk (git commits, mtimes) | `relay-projects.derived.yml` |
| `relay-state.mjs` | Builds the pre-digested state injected at startup, with its alerts | `.relay-state.md`, `.relay-state/` |
| `rag-index.mjs` | Indexes markdown and YAML (rules, `RULES/`, `DECISIONS.md`, brain, log, projects, skills) into a BM25 index | `.relay-rag.json` |
| `rag-query.mjs` | Queries that index — **use before opening a file** | — |
| `skills-link.mjs` | Links manifest skills into `.claude/skills/`, copies `RULES/*.md` into `.claude/rules/` | `.claude/skills/`, `.claude/rules/` |
| `new-project.mjs` | Creates a project from the template and registers it | `PROJECTS/<name>/`, `relay-brain.yml` |
| `lib/brain.mjs` | Root, host identity, brain loading, project path resolution — the only owner of that logic | — |
| `lib/atomic.mjs` | Atomic writes that survive Windows file locks; `writeIfChanged`, `withLock`, `swapDir` | — |
| `lib/skills.mjs` | Reads skill frontmatter (loading mode) | — |
| `lib/rag.mjs` | Tokenizer and BM25 scorer | — |
| `tests/` | `node --test TOOLS/tests/` — every test runs in a temp root | — |

A skill's loading mode lives in its own `SKILL.md` frontmatter, not in the manifest:
`disable-model-invocation: true` keeps it out of the system prompt (on demand, `/name`),
`paths:` loads it when matching files are touched, nothing makes it native.

## Environment

| Variable | Effect |
|---|---|
| `RELAY_ROOT` | Data root the tools read and write (default: the directory above `TOOLS/`) |
| `RELAY_HOST` | This machine's identity in the brain and in `LOG/` (default: the hostname) |
| `RELAY_CLONE_DIRS` | Where a project clone missing at its declared path is looked up, absolute dirs separated by `;` (Windows) or `:` (default: `~/Documents/GitHub`, `~/Documents/Git`) |
| `CLAUDE_CONFIG_DIR` | Claude Code config dir used by the setup hook, `relay-init` and `skills-link --user` |

A machine whose brain entry says `role: satellite` (a VM or container running unattended
agents) regenerates its local files but never rotates or rewrites the shared brain.

## Interface contract

- Errors → **stderr**, results → **stdout**
- Exit code **0** on success, **1** on failure
- `--json` where machine-readable output makes sense
- `--dry-run` on anything that writes
- No hardcoded paths: data paths come from `lib/brain.mjs` (`ROOT_DIR`), and a tool that
  runs another one launches it from its own directory, so the framework runs from
  wherever it is cloned and tests can point it at a temp root

## Writing rules

- **Write atomically** through `lib/atomic.mjs`. Plain `writeFileSync` on a file a
  hook may read concurrently is a bug: cloud sync clients and antivirus hold files
  open, and a rename into a locked destination fails roughly 1 run in 6 on Windows.
- **Validate before the swap.** `writeAtomic(path, text, validate)` re-reads the temp
  file; if the validator throws, the original is never replaced.
- **Do not rewrite identical content.** `writeIfChanged` keeps the file and its mtime,
  which the end-of-turn threshold compares; the Stop hook runs after every answer.
- **One writer per shared file.** Each machine writes only its own `LOG/<host>.md`; the
  generated files (`RELAY_LOG.md`, the state, the index) are rebuilt locally.
- **Never write to `relay-brain.yml` automatically.** It carries human intent. The
  exceptions are explicit and line-based so comments survive: `new-project.mjs`,
  `relay-init.mjs` (adds this machine), `log-rotate.mjs --brain`, and `relay-brain.mjs`
  once you opted in to the split brain.

## Adding a tool

A script earns its place here when it is generally useful (not tied to one project),
standalone, follows the interface above, and has proven necessary on at least two
projects. Add a row to the catalogue and a test under `tests/` in the same commit.

Dependencies are deliberately minimal — `js-yaml` only. The RAG is plain JavaScript
rather than SQLite FTS5 precisely to avoid a native build on every machine.
