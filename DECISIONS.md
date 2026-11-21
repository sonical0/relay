# Decisions — why Relay's rules are what they are

> One entry per decision, dated, with the measurement behind it and what was tried before.
> `AGENTS.md` and `RULES/*.md` carry the rules; this file carries the reasons, so they are not
> paid for in every session and not lost either. It is indexed by the RAG (kind `convention`):
> `node TOOLS/rag-query.mjs --query "…" --type convention`.
>
> The measurements come from the internal system Relay was extracted from: one user, five
> machines, a dozen projects, Claude Code as orchestrator. Your numbers will differ; the
> direction of each decision should not. Add your own entries on top.

## 2026-11-19 — Short sessions, deterministic checks, INBOX as deltas, third-party skills read in full

- **Measured:** the main model re-read ~250k cached tokens on every call (7 days, 99 % cache reads): about 75 % of the spend at API prices, against ~2 % for Relay's fixed files. One INBOX proposal dropped out of 74, and no way to remove a rule. The rules file grew 7 % in three days. A "never" rule (`git add .`) was broken in nearly every session: per the Claude Code docs, an instruction is followed "usually", a hook always.
- **Decision:**
  - **Short sessions**: a resume card `.relay-reprise/<project>/<agent>.md` (≤ 600 bytes, local, not synced), re-injected by the start hook when under 48 h old, then `/clear`. The status line says "hand over" from 150k tokens. `RELAY_PROJECT` / `RELAY_AGENT` pick the state and the card when an agent starts from the root; sessions are named `<project>_<X>` so a handover never guesses who is who.
  - **Hooks over prose**: `permissions.deny` for `git add .`, `-A`, `--all`, set by the setup hook; the matching sentence in `AGENTS.md` got shorter.
  - **INBOX as deltas**: `operation: add | refine | remove`, the change only, a measurement required. A monthly `/inbox --prune` pass uses `token-scan --skills` (real usage of skills and rules) and never touches safety, approval, encoding or sync rules. The state alerts when `AGENTS.md` passes its size cap.
  - **Third-party skill or tool**: scanner, then a **full read whatever the score** (a scanner without an LLM only knows known patterns), then adaptation; a `security:` line in the proposal.
- **Sources:** Claude Code best practices; ACE (ICLR 2026, incremental deltas rather than rewrites); ETH 2602.11988 (a loaded context file can lower success); Chroma (context rot).

## 2026-11-19 — Questions: one stop, "what next" announced

- **Measured** (14 days of transcripts): 2.7 stops per active hour; 62 structured questions chained within one turn; ~35 "what do we do next?" while a plan already gave the order; the first option was picked 44 times out of 66 outside sensitive approvals.
- **Decision:** one question call per stop; when a plan or a resume card gives the order, announce the next step and go on. Push, merge, deletion and ticket changes are always asked. Target: −30 % stops per hour.

## 2026-11-19 — Hooks off the critical path; the stop hook asks for the cause

- **Measured:** start hook median 1.9 s, p95 5.4 s, 2 timeouts out of 53 (state not injected). Cause: log merge and state regeneration run synchronously, node itself starting in ~0.5 s on Windows.
- **Decision:** the start hook injects the existing state and refreshes it in the background (the stop hook regenerates it after each answer anyway). Result: 2.8 s → 0.5 s. When the stop hook blocks for a missing journal entry, it now also asks for the cause (forgotten, no work, written elsewhere) and an INBOX proposal with a durable fix. `TOOLS/hook-stats.mjs` measures hook durations from transcripts.

## 2026-11-19 — Maintenance budget: no tool without a removal

- **Found:** the framework's own work had reached 24 % of the journal entries over 7 days.
- **Decision:** `RULES/tools.md`: no new tool or skill without a measured friction and a removal in exchange. The state alerts when framework work exceeds 20 % of the journal over 7 days. It also alerts on skill links that drift from the manifest and on the first sync-conflict file, so a conflict is seen the day it happens.

## 2026-11-18 — Progress bar for long tasks in the status line

- **Found:** a background scan of 20 repos and several CI runs of 2-4 minutes left the user asking "where is it at?".
- **Decision:** `TOOLS/progress.mjs <k> <N>` writes a per-session file read by the status line (`statusLine.refreshInterval: 2`, set by the setup hook, otherwise the line does not redraw during a long Bash call). `progress-run.mjs` wraps a single long command, estimating time left from the median of its last 5 successful runs; `ci-progress.mjs` follows a GitHub CI run, the bar never going backwards (the job count grows at start), merging only with `--merge` after approval in the chat. A proportional estimate would have said ~0:25 for a 3 min 09 CI run.

## 2026-11-16 — `AGENTS.md` slimmed again: per-area detail moves to `RULES/`

- **Measured:** 13.8 KB loaded every session (~5,100 tokens) out of 17.7 KB of permanent context. A merge of sync conflicts had just put a long rule back in.
- **Decision:** detail that only matters when touching certain files (skills, brain, journal, sync) lives in `RULES/*.md` with a `paths:` frontmatter. A rule that does not go through files (e.g. a ticketing tool) keeps a one-line pointer in `AGENTS.md` ordering the agent to read it first. Result: −21 %.

## 2026-11-14 — Third-party skills: scan, then rewrite condensed

- **Found:** of 27 popular skill repos reviewed, several hid risks a quick read misses: a "live" mode launching the CLI without permissions or sandbox, an installer rewriting the user's config and hooks, a pack of 300+ skills with global hooks. `npx skills add -g` and `/plugin install` install outside `SKILLS/`, hence outside sync and review.
- **Decision:** a third-party skill goes through a static scanner, is read fully past a moderate score, then rewritten condensed with `disable-model-invocation: true` (`RULES/skills.md`).

## 2026-11-09 — No hosted artifacts: preview locally, the repo is the source of truth

- **Found:** a design system kept as a hosted copy needed one more step per change (copy, re-read online, republish): four republications in one evening, and the copy had drifted from the repo twice.
- **Decision:** preview pages locally (`python -m http.server` + a browser panel); the repo is the source of truth. Exception: the user asks for a shareable link.

## 2026-10-27 — Clarifying questions through the agent's question tool

- **Found:** questions arrived as numbered lists in the text, to be retyped one by one.
- **Decision:** clarifying questions go through the agent's structured question tool (Claude Code: `AskUserQuestion`, clickable choices, recommendation first). Free text only for open questions.

## 2026-10-26 — Machine setup in one command

- **Found:** a new machine needed `npm ci`, the hook installer, a brain entry and permissions by hand; a skill still cited a script that no longer existed.
- **Decision:** `TOOLS/relay-init.mjs` does it all, idempotently (a re-run changes nothing), and prints what remains manual. Tested against a temp root and a temp `CLAUDE_CONFIG_DIR`, never the real ones.

## 2026-10-26 — Supervision: skill split by role, structured error signals

- **Measured:** the supervision skill was 11.6 KB (~4.3k tokens), re-read whole by every manager, worker and supervisor at each resume. Alerts were free text, impossible to count, and the same fault was rewritten at every server restart.
- **Decision:** a common trunk plus one file per role (manager, integrator, supervisor) and a `MAINTENANCE.md` never read to supervise. Read per role: manager −26 %, supervisor −33 %, worker −72 %. Each alert has a `code` and a `who`; one fault = one `signal` row, replayed at restart. A delivery is refused without a branch, a SHA, a green check **recorded by the board** (not declared) and closed completion criteria.
- **Sources read:** Anthropic, *How we built our multi-agent research system* and *Effective harnesses for long-running agents*; arXiv 2503.13657 (three failure classes: design, inter-agent misalignment, verification).

## 2026-10-19 — Generated files stay local; one writer per shared file

- **Found:** every machine rewrote the generated journal, state and index, and the sync tool sent them to the others: conflicts as soon as two machines worked, and a state index arrived on a machine with another machine's absolute paths.
- **Decision:** generated files are never synced; each machine rebuilds them at the end of each reply. Machine identity = `RELAY_HOST` if set, else the hostname; a `role:` per machine (`workstation`, `satellite`). See `RULES/synchro.md`.

## 2026-10-19 — Proposals instead of direct edits (INBOX/)

- **Decision:** a session that learns something durable writes a proposal in `INBOX/<host>/`, reviewed in batch with `/inbox` (accept, fix, drop) and archived with its decision. One owner per rule; nothing enters without review.

## 2026-10-15 — Supervision resume sheet, re-injected by the SessionStart hook

- **Measured:** long supervision sessions (supervisor at 74 % of its context by midday) end up compacted, and an automatic summary can lose a decision or a scope boundary.
- **Decision:** the supervision server generates one resume sheet per agent from the board and git, never by hand, in a directory of its own (`.relay-supervision/`: an earlier version lived inside `.relay-state/`, which the state tool rebuilds whole — the sheets vanished). The SessionStart hook appends the sheet of the current session. Resume rule: the truth comes from git and the board, not from the session's memory.

## 2026-10-15 — wmux: one browser tab per agent, in its own pane

- **Found:** with five sessions in one wmux workspace, `wmux new-surface --type browser` ignores the caller and lands in the first pane; a `wmux browser …` without `--surface` opens a new empty pane. Three empty panes appeared in one day.
- **Decision:** `TOOLS/supervision/wmux-tab.mjs` creates the tab in the caller's pane and returns its id; every browser command passes `--surface <id>`; navigate with `eval "location.href='…'"`. Remove it if wmux adds `--pane` to `new-surface`.

## 2026-10-14 — Universal rules only in `AGENTS.md`

- **Measured:** the fixed context of a session was ~20.6 KB (≈ 7,600 tokens), 16.2 KB of it in the conventions file alone, with 3.7 KB of justification quotes.
- **Decision:** three destinations. Universal rules in `AGENTS.md`; per-area rules in `RULES/*.md` with a `paths:` frontmatter (loaded the first time a matching file is touched); reasons here. Known limit: a `paths:` rule is not re-injected after compaction until a matching file is touched again.

## 2026-10-14 — `disable-model-invocation` replaces the home-made `rag` mode

- **Found:** the manifest's `load: rag` mode re-implemented by hand what Claude Code does natively: a skill whose frontmatter has `disable-model-invocation: true` has no description in the system prompt (zero tokens), stays invocable with `/name`, and its body loads only on invocation.
- **Decision:** every skill is linked; on-demand ones carry the frontmatter line; `skills-all-native.mjs` and the manifest `load` key are gone. Rollback: remove the line.

## 2026-10-14 — State injected at `SessionStart`, no longer at the first prompt

- **Found:** the `UserPromptSubmit` hook needed 40 lines of per-session markers to inject only once, and its context was summarised away with the rest at compaction.
- **Decision:** a `SessionStart` hook with matchers `startup|resume|clear|compact` gets `cwd` and `session_id`, returns the same `additionalContext`, and `compact` re-injects it after every compaction. Outside the Relay tree, where `AGENTS.md` is not loaded, it adds the two rules most often broken there (journal, commits).

## 2026-10-13 — Journal: one file per machine

- **Found:** two journal entries written on one machine vanished while a session on another machine wrote the same file: file-level sync keeps the last copy. Atomic writes and the time in the header protect parallel sessions on one machine, not two machines.
- **Decision:** each machine writes only `LOG/<host>.md`; `RELAY_LOG.md` is generated by merging them. Same day, same reason: the brain can be split into chunks under `BRAIN/` (`TOOLS/relay-brain.mjs`), opt-in.

## 2026-10-13 — End of reply: one process, with a threshold

- **Found:** the `Stop` hook runs at the end of every reply, not once per session, and rebuilt the state and the index unconditionally, in parallel: ~220 identical files rewritten after each reply.
- **Decision:** `TOOLS/relay-after-turn.mjs` chains rotation, brain, journal merge, status, then runs the state and the index only if the signature of their inputs (path, size, mtime, compared by equality) changed. Measured: 1.1 s per pass, detached.

## 2026-10-12 — `git push` publishes the whole branch

- **Found:** two commits made in one session left for the remote on another session's `push`, without anyone asking. `git push` publishes the branch, not the author's commits.
- **Decision:** `git add` with explicit paths, never `.` or `-A` (several sessions share the working copy); before pushing, `git log origin/<branch>..HEAD`, and if it holds other sessions' commits, say so instead of pushing.

## 2026-10-06 — Aggressive code-trimming plugin: not installed

- **Tested** blind on two real tickets: −72 to −82 % code and −10 to −15 % tokens, but 2 review points lost on each task — what it cut (tests, edge cases, actionable error messages) was the quality. Rule set before the test: worse on correctness → no plugin.

## 2026-09-19 — The time in the journal header replaces the `(n)` suffix

- **Measured:** 9 entries in one day, two collisions, three renumberings in an hour, one of them on entries written by other sessions. A global counter assumes you read the file right before writing; the time does not. It must be **read from the clock**: the same day, two entries were dated in the future.

## 2026-09-19 — A versioned project's `path`: the absolute path of its clone

- **Found:** `path` pointed at a vanished `PROJECTS/<project>` folder: the scan marked those projects `absent` and the state tool skipped their targeted state — precisely on the most active projects. Write the absolute path without `~`: `path.normalize()` does not expand it.

## 2026-09-18 — Versioned projects outside `PROJECTS/`, outside sync

- **Found:** a synced local copy of a project whose history lives on a git remote cost three real problems: silent divergence when the clone lags behind `origin` (a "folder without `.git`" became the working copy by accident), duplicates reconciled by hand, and sync copying `node_modules/` and build output (72 MB on one project).
- **Decision:** the remote is the source of truth, the clone is disposable. Compare **trees**, never commit counts: two branches 6 and 7 commits apart turned out to have identical trees.

## 2026-09-16 — Path case on Windows

- **Found:** the harness reported a `cwd` in `documents` when the brain said `Documents`; every strict comparison failed in silence and the per-project state was never picked. All path comparisons go through `canon()` (`TOOLS/lib/brain.mjs`), lowercased on Windows; the start hook also compares the 8.3 short form.

## 2026-09-15 — Startup: a pre-digested state, targeted at the project

- **Measured:** the startup hook used to order "read the brain, read the log, read the memory": 600 tokens injected, 12–15k tokens of reads provoked, plus a synchronous broker start (4.6 s). Same question asked twice: from the root, 3 turns and 96,665 tokens; from the project directory, **1 turn and 25,728 tokens** — the targeted state already held the answer. One avoided turn is worth 4,000 to 11,000 tokens, against 614 for the whole context consolidation.

## 2026-09-15 — Skills: no routing skill, no per-project scope

- **Measured:** descriptions cost ~0.37 token per byte, every session. A routing skill (suggesting the others) was tried and removed the same day: 0/3 triggers, even with the suggestion in the injected block. Per-project scope (links in `PROJECTS/<x>/.claude/skills/`) worked, but `git status -uall` followed the junctions and a `git add .` would have committed a diverging copy of the skill into the project repo.
