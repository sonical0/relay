---
name: qa
description: "QA-test a web app in a real browser: triage findings by severity, fix one at a time with before/after screenshots and a regression test, with a runaway-loop guard. Use for a QA pass on a web project."
disable-model-invocation: true
---
> Adapted from [gstack](https://github.com/garrytan/gstack) (MIT).

## When to use

For a real (not simulated) QA pass on a web app (its URL: the project's `AGENTS.md` or `relay-brain.yml`). Needs a browser you can drive and screenshot: preferably one the user can see (e.g. a multiplexer's browser panel), else Playwright or another headless browser. With neither, tell the user the limits and propose to postpone the QA.

**Tiers** — ask the user which one before starting:
- **Quick**: fix only critical + high. The rest = "deferred".
- **Standard**: fix critical + high + medium. Low = "deferred".
- **Exhaustive**: fix everything, cosmetic included.

Mark "deferred" anything that cannot be fixed from the source code (a third-party component bug, infrastructure), whatever the tier.

## Phase 1 — Baseline pass

Open the browser on the app. *Optional, with a shared multiplexer browser panel (e.g. wmux)*: drive **your own** tab (`node TOOLS/supervision/wmux-tab.mjs --title "QA" --show` returns its id), pass `--surface <id>` to every command, navigate with `eval "location.href='…'"`, click and type through `eval` (snapshot refs expire between shell calls), close the tab at the end. For each page or flow:
- take a screenshot;
- read the useful text or DOM (text extraction or a JS `eval`), never a full accessibility snapshot of a big page;
- check the console (JS errors);
- note any visual inconsistency, blocked flow, console error.

Rate each problem by severity (critical / high / medium / low) with the probable source file or component when identifiable.

## Phase 2 — Triage

Sort all problems by severity and decide what to fix according to the chosen tier. Check whether a problem is already known (the project's `TODO.md` or notes; query the RAG) before treating it as new.

## Phase 3 — Fix loop

For each fixable problem, in severity order:

**a. Locate**: grep/glob for the responsible source file(s). Change ONLY the files directly related to the problem.

**b. Fix**: read the code, understand the context, make the minimal fix. Do not refactor or "improve" unrelated code.

**c. Commit**: one commit per fix, never grouped.
```bash
git add <changed-files-only>
git commit -m "fix(qa): <short description>"
```

**d. Re-test**: navigate back to the page, take an "after" screenshot to compare with the Phase 1 "before", re-check the console.

**e. Classify**:
- **verified**: the re-test confirms the fix, no new error;
- **best-effort**: fix applied but cannot be fully verified (needs an auth state, an external service);
- **reverted**: regression detected → `git revert HEAD` → mark "deferred".

**f. Regression test** (skip if: not "verified", OR a pure CSS fix with no JS behaviour, OR no test framework detected):
1. Read 2-3 existing test files of the same folder or kind to match the style (imports, describe/it structure).
2. Trace the bug's path: which precondition triggered it? which code path? which neighbouring edge cases?
3. The test MUST: reproduce the exact precondition, perform the action that exposed the bug, assert the correct behaviour (not just "does not crash").
4. Run only the new test file. Passes → separate commit (`test(qa): regression for <description>`). Fails → fix once, otherwise delete it and defer.

**g. Self-regulation (STOP AND ASSESS)** — runaway guard, computed every 5 fixes or after any revert:
```
DRIFT LEVEL:
  Base 0%
  Each revert:                          +15%
  Each fix touching >3 files:           +5%
  After the 15th fix:                   +1% per extra fix
  Everything left is low severity:      +10%
  Touching unrelated files:             +20%
```
**Above 20%: STOP at once.** Show what was done, ask the user whether to continue. **Hard cap: 50 fixes** — past it, stop whatever remains.

## Phase 4 — Final QA

After all fixes: re-run the QA on the affected pages. If the final result is WORSE than the baseline, say so clearly — something regressed.

## Phase 5 — Report

Structured report: problems found / fixed / deferred / reverted, with severity, `file:line`, status, and before/after screenshot links where relevant.

## Phase 6 — Update the TODO

Add the "deferred" items to the project's TODO (same place as in Phase 2) with their severity and the reason, so a future QA pass finds them.
