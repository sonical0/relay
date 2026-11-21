---
name: spec
description: "Turn a vague feature request into a precise spec in phases (why, scope, code-first technical questions, draft review, tasks) before implementing. Use before a non-trivial feature, not a one-liner."
disable-model-invocation: true
---
> Adapted from [gstack](https://github.com/garrytan/gstack) (MIT).

## When to use

Before implementing a non-trivial feature or change whose scope is still fuzzy. Not for a precise one-off fix, nor a trivial request. Goal: avoid the token-costly back-and-forth caused by sloppy framing — the same spirit as the "questions before a substantial deliverable" rule of `AGENTS.md`, applied to a feature's technical framing.

**Do not skip or merge phases.**

## Phase 1 — The "why"

Answer the 5 questions before going on:

1. **Who** is affected? (end user, automated system, just you — "solo developer" is a valid answer, do not dwell on it)
2. **What** is the current behaviour? (what actually happens — checked, not assumed)
3. **What** should happen instead?
4. **Why now?** (blocks something? costs something? correctness bug? risk?)
5. **How will we know it is done?** (an observable, measurable result, not an impression)

Do not move on until all 5 are answered without vagueness.

**Deduplication**: before drafting, check that no equivalent issue or TODO entry already exists. If the project has a GitHub repo:
```bash
gh issue list --search "<keywords>" --state open --limit 10 2>/dev/null
```
Without `gh`, without auth or without a GitHub repo: search the project's `TODO.md`/notes instead (or your issue tracker, if any). Best effort — never block Phase 2 if the search fails.

## Phase 2 — Scope and limits

Answer:

1. **What is explicitly out of scope?** (lock it early, prevents scope creep)
2. **Which existing systems does it touch?** (files, tables, services, endpoints)
3. **Any ordering constraints?** (must A precede B?)
4. **What is the smallest version that delivers the value?** (always look for the MVP)
5. **What are the failure modes and rollback options?** (what breaks if it ships wrong?)

Do not move on until the scope is locked.

## Phase 3 — Technical interrogation (read the code before asking)

Before any question in this phase, read at least one piece of evidence in the code (grep, glob, read): a question backed by `path:line` costs the user a short answer, a blind question makes them do the search for us. Never ask "which file should I look at?" — find it.

- **A file or symbol is named** ("the import OOMs on this source", "auth.ts crashes"): grep the symbol, read the file, cite `path:line` in the first question.
- **A project-level request** ("rethink the sources model", "we need rate limiting"): read the project structure (`package.json`, the relevant folder, existing `README`/`TODO.md`) and cite what you found before asking.

If nothing exists yet (greenfield), say so explicitly and go on.

Then ask about whatever applies (skip what clearly does not): data model, API, background processing (queues, idempotency), UI, infrastructure, tests. Never ask what can be read in the code.

## Phase 4 — Draft review

Present a complete draft and ask: **"Does this capture what you want? What did I miss?"** Iterate until confirmed.

## Phase 5 — Tasks

Once the draft is confirmed, split it into tasks (idea from `/speckit.tasks`, github/spec-kit):

- each task fits in one session: verifiable acceptance criteria, files touched;
- explicit order and dependencies; mark parallelisable tasks `[//]`;
- if the project uses an issue tracker: propose the parent item and its sub-tasks, without creating anything before the user agrees.

## Output

Once confirmed: write the spec into the project (a local `SPEC-<slug>.md` file, or a GitHub issue if the repo has one and the user prefers it — ask). Do not start implementing in the same turn as Phase 4 unless explicitly asked: a validated spec is a natural stopping point.
