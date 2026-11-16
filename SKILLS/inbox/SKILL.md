---
name: inbox
description: "Relay proposal box (INBOX/): format of a proposal (skill, rule, note, learned fact) and batch review (accept, fix, drop). /inbox to review pending proposals."
disable-model-invocation: true
---

# INBOX — proposals awaiting validation

A session that learns something useful beyond itself (a trap, a repeatable procedure, a missing rule) does not write it straight into Relay: it **proposes** it. One owner per rule, nothing enters without review. This is how Relay improves itself without letting every session rewrite its rules.

## Propose

One file per proposal: `INBOX/<host>/YYYY-MM-DD-<slug>.md`.

```markdown
---
type: skill | rule | note | fact
title: <one line>
target: <file aimed at, e.g. SKILLS/<name>/SKILL.md, RULES/<x>.md, AGENTS.md>
session: <date and machine>
---

## Why
What happened, and why it will matter again (one to three sentences, with the number or the error message).

## Proposal
The exact text to add or change (or the whole SKILL.md for a new skill).

## Evidence
The session excerpt, command or output that proves it.
```

Rules: one idea per file; never a secret; do not propose what serves only once; check with the RAG first (`node TOOLS/rag-query.mjs --query "…"`) that it is not already written.

## Review (/inbox)

On a workstation, with the user:

1. List `INBOX/*/` (except `INBOX/_done/`), oldest first; announce the count.
2. For each proposal: summarise it in two lines (type, target, what changes), then ask **Accept / Fix / Drop** (with the agent's question tool when it has one).
3. **Accept**: apply it to the target following its owner's rules (`AGENTS.md`, `RULES/*.md`; brain: never written automatically). **Fix**: apply the user's corrected version. **Drop**: nothing.
4. In every case move the file to `INBOX/_done/YYYY-MM/`, adding `decision: accepted | fixed | dropped` and the date at the top; never delete it (a trace of what was learned or refused).
5. One journal line: `- [relay] /inbox: N reviewed → X accepted, Y dropped`.
