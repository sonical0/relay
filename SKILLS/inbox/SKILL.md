---
name: inbox
description: "Relay proposal box (INBOX/): format of a proposal (skill, rule, note, learned fact) as a measured delta, batch review (accept, fix, drop), monthly removal pass. /inbox, /inbox --prune."
disable-model-invocation: true
---

# INBOX — proposals awaiting validation

A session that learns something useful beyond itself (a trap, a repeatable procedure, a missing rule) does not write it straight into Relay: it **proposes** it. One owner per rule, nothing enters without review. This is how Relay improves itself without letting every session rewrite its rules.

## Propose

One file per proposal: `INBOX/<host>/YYYY-MM-DD-<slug>.md`.

```markdown
---
type: skill | rule | note | fact
operation: add | refine | remove
title: <one line>
target: <file aimed at, e.g. SKILLS/<name>/SKILL.md, RULES/<x>.md, AGENTS.md>
session: <date and machine>
security: <third-party skill or tool only: scanner score N · full read: clean | points found>
---

## Why
What happened, and why it will matter again (one to three sentences, with the number or the error message).

## Proposal
The exact delta: the item added, the line refined (before → after) or the item removed. Never a full rewrite of an existing file. A new skill: the complete SKILL.md. Several items: number them, so they can be accepted separately.

## Evidence
The session excerpt, command or output that proves it, then the measurement: **baseline** (current number), success **criterion**, **side effects** to watch, **if it fails** (fix, shrink or remove).
```

Rules: one idea per file; never a secret; do not propose what serves only once, what the agent already does unprompted, or generic advice ("write tests") without the concrete case that makes it necessary; check with the RAG first (`node TOOLS/rag-query.mjs --query "…"`) that it is not already written. **The measurement is mandatory**: without a baseline and a criterion, the proposal is fixed before it is accepted. Third-party skill or tool: scan, **full read whatever the score**, then adapt, never copy as is (`RULES/skills.md`); without a `security:` line, the proposal is fixed before it is accepted.

## Review (/inbox)

On a workstation, with the user:

1. List `INBOX/*/` (except `INBOX/_done/`), oldest first; announce the count.
2. For each proposal: summarise it in two lines (type, operation, target, what changes), then ask **Accept / Fix / Drop** (with the agent's question tool when it has one).
3. **Accept**: apply the delta to the target following its owner's rules (`AGENTS.md`, `RULES/*.md`; skills: `RULES/skills.md`; brain: never written automatically). **Fix**: apply the user's corrected version. **Drop**: nothing.
4. In every case move the file to `INBOX/_done/YYYY-MM/`, adding `decision: accepted | fixed | dropped` and the date at the top; never delete it (a trace of what was learned or refused).
5. One journal line: `- [relay] /inbox: N reviewed → X accepted, Y dropped`.

## Removal pass (/inbox --prune), once a month

Without removals, rules only pile up, and an overloaded context file lowers the success rate. Once a month, or as soon as the state reports `AGENTS.md` above its ceiling:

1. `node TOOLS/token-scan.mjs --skills --days 60`: real usage of each skill and rule, plus the "never seen" list. The real window stops at the transcripts' retention (the "transcripts available since" line).
2. For each line of `AGENTS.md`, look for its trace in `RELAY_LOG.md` and in `INBOX/_done/`: what was never cited, applied or violated over the period is a candidate.
3. Write one `operation: remove` proposal per candidate kept, with its measured usage, then review it like the others (Accept / Fix / Drop). An accepted removal deletes the line; its reason stays in `DECISIONS.md`.
4. **Never candidates**: safety, secrets, approval of a push, merge or ticket change, encoding, sync. These rules are rare by nature, and that is exactly why they must stay.
5. Journal: `- [relay] /inbox --prune: N candidates → X removed, AGENTS.md A → B bytes`.
