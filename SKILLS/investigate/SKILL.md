---
name: investigate
description: "Root-cause debugging for any bug, error, crash or failing test not solved at first look: evidence first, one hypothesis at a time, 3-strike escalation, fix with a regression test. Use before a second fix."
---

## When to use

Any non-trivial bug (not an obvious typo), especially a recurring or intermittent one, or one whose cause is not immediately visible. Goal: avoid the "guess a fix, it fails, guess another" pattern that wastes turns and sometimes leaves the real cause in place.

## Phase 1 — Root-cause investigation

Gather evidence BEFORE forming a hypothesis:

1. **Collect the symptoms**: error messages, stack trace, reproduction steps. If insufficient, ask one question at a time. A tool's own **diagnosis** ("probable cause", "to fix this: …") is its author's hypothesis, not a symptom: read the raw trace (full output, log, database record) before adopting it.
2. **Read the code**: trace the path from the symptom to the possible causes (grep every reference, read to understand the logic).
3. **Check recent changes**:
   ```bash
   git log --oneline -20 -- <affected-files>
   ```
   Did it work before? What changed? A regression points to the diff.
4. **Reproduce**: can the bug be triggered deterministically? If not, gather more evidence before going on.
5. **Check the history**: search the project's journal and notes (`node TOOLS/rag-query.mjs --query "…"`) for a similar bug in the same file or module — a recurring bug in one place is an architectural signal, not a coincidence.

Output: **"Root-cause hypothesis: …"** — a precise, testable statement of what is wrong and why. If several candidates remain, rank them rather than pick: for each, evidence for and against (cited as `path:line` or a log line) and the test that would kill it. With equal evidence, present both.

## Phase 2 — Pattern analysis

Check whether the bug matches a known pattern:

| Pattern | Signature | Where to look |
|---|---|---|
| Race condition | Intermittent, timing-dependent | Concurrent access to shared state |
| null/undefined propagation | TypeError, access on undefined | Missing guards on optional values |
| State corruption | Inconsistent data, partial update | Transactions, callbacks, hooks |
| Integration failure | Timeout, unexpected response | External API calls, service limits |
| Config drift | Works locally, fails in prod/staging | Env vars, feature flags, DB state |
| Stale cache | Shows old data, fixed by a clear | Local cache, CDN, browser |

Also check the project's TODO/notes for related known issues, and `git log` for earlier fixes in the same place.

## Phase 3 — Hypothesis testing

Before writing any fix: **confirm the hypothesis**. Add a temporary log or assertion at the suspected point, reproduce, check that the evidence matches.

**Intermittent behaviour or a measurement** (failure rate, duration, comparing two tools or two settings): at least **4 runs per case**, in a controlled state (same warm-up, machine idle between runs), before stating a cause or a ranking. With 1 or 2 runs, write "observed once", never "X is more reliable" nor "the cause is Y". A rate, a drift or a correlation is **computed** (script, query), never eyeballed; before correlating two logs, align their clocks (time zone, offset between machines).

**Investigation log**: each discarded hypothesis, with its evidence, goes into the resume card (`.relay-reprise/<project>/…`) the moment it falls, so the 3-strike count survives a compaction or a `/clear`.

**3-strike rule**: if 3 hypotheses fail, STOP. Offer the user:
- continue with a new hypothesis (describe it);
- escalate for human review — this smells architectural, not a simple bug;
- add logging and wait for the next occurrence.

**Slow-down signals**: "quick fix for now" (there is no "for now" — fix it properly or escalate); proposing a fix before tracing the data flow (that is guessing); each fix reveals a new problem elsewhere (wrong layer, not wrong code).

## Phase 4 — Implementation

Once the cause is confirmed:

1. **Fix the root cause, not the symptom.** The smallest change that removes the real problem.
2. **Minimal diff**: as few files as possible. Do not refactor adjacent code on the way.
3. **Write a regression test** that fails without the fix and passes with it.
4. **Run the full test suite.** No regression tolerated.
5. **If the fix touches more than 5 files**: report the blast radius to the user before going on (proceed / split / rethink).

**Light scope**: once the hypothesis is confirmed, restrict yourself to the affected folder — resist "fixing" adjacent code met on the way.

## Phase 5 — Verification and report

Replay the original bug scenario to confirm it is fixed — not optional. Run the test suite.

Structured report:
```
DEBUG REPORT
════════════════════════════════════════
Symptom:          [what the user observed]
Root cause:       [what was actually wrong]
Fix:              [what changed, with path:line]
Evidence:         [test output, reproduction showing the fix]
Regression test:  [path:line of the new test]
Related:          [TODO/notes entries, earlier bugs in the same place]
Status:           DONE | DONE_WITH_CONCERNS | BLOCKED
════════════════════════════════════════
```

If the finding is notable (recurring architectural bug, undocumented trap), record it in the project's notes or journal so a future Phase 1 finds it.
