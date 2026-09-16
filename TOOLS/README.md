# TOOLS — shared scripts

Standalone Node scripts, usable by any agent on any project. Unlike project data,
these **are** committed: they contain no personal information, and the framework does
not work without them — the startup state, the derived status and the search index
are all produced here.

## Catalogue

| Script | What it does | Writes |
|---|---|---|
| `project-scan.mjs` | Derives real project status from disk (git commits, mtimes) | `relay-projects.derived.yml` |
| `relay-state.mjs` | Builds the pre-digested state injected at startup | `.relay-state.md`, `.relay-state/` |
| `rag-index.mjs` | Indexes markdown and YAML into a searchable BM25 index | `.relay-rag.json` |
| `rag-query.mjs` | Queries that index — **use before opening a file** | — |
| `skills-link.mjs` | Links `load: native` skills into `.claude/skills/` | `.claude/skills/` |
| `skills-all-native.mjs` | Rollback: puts every skill back to native | `SKILLS/skills.manifest.json` |
| `log-rotate.mjs` | Archives log entries older than 2 months | `LOG/ARCHIVE/YYYY-MM.md` |
| `new-project.mjs` | Creates a project from the template and registers it | `PROJECTS/<name>/`, `relay-brain.yml` |
| `lib/atomic.mjs` | Atomic writes that survive Windows file locks | — |
| `lib/rag.mjs` | Tokenizer and BM25 scorer | — |

Everyday use is through npm scripts: `npm run state`, `npm run index`,
`npm run rag -- --query "…"`, `npm run bootstrap`.

## Interface contract

- Errors → **stderr**, results → **stdout**
- Exit code **0** on success, **1** on failure
- `--json` where machine-readable output makes sense
- `--dry-run` on anything that writes
- No hardcoded paths: `path.dirname(fileURLToPath(import.meta.url))` plus relative
  navigation, so the framework runs from wherever it is cloned

## Writing rules

- **Write atomically** through `lib/atomic.mjs`. Plain `writeFileSync` on a file a
  hook may read concurrently is a bug: cloud sync clients and antivirus hold files
  open, and a rename into a locked destination fails roughly 1 run in 6 on Windows.
- **Validate before the swap.** `writeAtomic(path, text, validate)` re-reads the temp
  file; if the validator throws, the original is never replaced.
- **Never write to `relay-brain.yml` automatically.** It carries human intent.
  `new-project.mjs` is the one exception, it is invoked explicitly, and it edits the
  file line by line so your comments survive.

## Adding a tool

A script earns its place here when it is generally useful (not tied to one project),
standalone, follows the interface above, and has proven necessary on at least two
projects. Add a row to the catalogue in the same commit.

Dependencies are deliberately minimal — `js-yaml` only. The RAG is plain JavaScript
rather than SQLite FTS5 precisely to avoid a native build on every machine.
