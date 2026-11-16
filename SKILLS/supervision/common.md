# Supervision — common to all roles

Served by `$S guide <role>` (at the top of every guide). The firm rules are in `SKILL.md` and the preamble of your resume sheet.

## Roles

| Role | Guide |
|---|---|
| Supervisor (S) | `$S guide supervisor` |
| Managers A, B, C | `$S guide manager` |
| Manager D (integrator) | `$S guide integrator` |
| Worker | `$S guide worker` (Worktrees, Checks, Browser, Workers) |

- **Supervisor**: splits the work, assigns scopes, reviews every branch, keeps the merge queue, relays the user. Does not code in the managers' domains.
- **Managers**: one domain each, at most `max_workers` workers named X1… (diffs reviewed, checks rerun before declaring them done).
- **Manager D, integrator**: the only one who writes the common files and pushes to the base branch.

Everything project-specific (base branch, common files, checks, limits) is in the brain's `supervision:` block, repeated in your resume sheet. A task arrives as "T7 for A" plus a brief.

## Dashboard

```
$S manager X --status working|waiting|review|blocked|done --task "T7 …" --branch <b> --progress 0..100 --msg "…"
$S manager X --worktree <absolute path>        # every worktree, workers' included (also links the shared_dirs)
$S files X --add <paths>                        # BEFORE editing; refused outside the scope
$S worker X1 --status working --task "…" [--model opus|sonnet|haiku]
$S worker X1 --status done --verified           # only after reviewing the diff and rerunning the checks
$S request X "what and why" --files …           # common files -> D
```

Red alerts (notified once per pattern and per agent): file outside the scope or common, file touched by two managers, too many workers, badly named worker, sonnet/haiku worker done without verification, check lock held too long, quota, browser outside its pane.

## Scopes

The supervisor gives everyone a list of paths. Outside the list: ask them to widen it. Common files (list in your sheet) belong to D alone: D delivers requests on its `integ/…` branch, the requester merges it into theirs.

## Reserved space

The supervision workspace (a wmux workspace, or a dedicated terminal/tab without wmux) serves only the supervision. Any other work, even short, is started elsewhere. A push guard refusal outside the manager's scope is the expected behaviour: tell the user and offer to continue elsewhere. Never unregister a pane or work around the guard.

## Failures: when to stop

Switch to `blocked` (`--status blocked --msg "<cause>"`), write a note (`$S note X "…"`) and tell the supervisor:

- **3 red checks in a row** on the same task: find the root cause (is the base red?) before rerunning.
- **Merge conflict**: `merge --abort`, take the branch out of the train, warn; do not resolve it in the manager's place.
- **Worker without progress** for 15 min, or looping: stop it, review its diff, rephrase the task.
- **Resume** (`/clear`) twice in a row on the same task: the brief is at fault.
- **A refusal from the tool** (scope, lock, quota): ask, do not work around it.

Every defect found by a verification: `$S note X --defaut "…"` (counted in the weekly note).

## Questions

```
$S ask X "Short question, with the useful facts" --choices '[{"id":"yes","label":"Yes","key":"1"},{"id":"no","label":"No","key":"2"}]'
```

Then **end your turn**: no wait loop, no `watch`. The answer arrives typed into your pane (choice on the board) or as a pointer to your mailbox. A question for the user: mirror it with an AskUserQuestion whose options are **in the same order** (a click on the board sends key 1, 2…); never a `"text"` choice while an AskUserQuestion is open. Answer applied: `$S ask X --done`. `$S ask` calls wmux by its absolute path, never the `wmux` on PATH. Claude Code permission prompts are detected on their own (hooks).

## Messages and mailbox

- An agent started by `start-manager.ps1` reports its state through its hooks. Board messages (go-ahead, review, brakes, a word from the user) go to its **mailbox**; at the end of its turn, a line telling it that N message(s) wait and to run `$S inbox X` is typed into its pane. Never during a turn, never over an open prompt. STOP is still typed at once.
- Unread after 10 min: a reminder, then a signal to the supervisor. Every `$S` call also shows pending messages.
- Acknowledge rules and tasks in one line, not mere information. A question to the supervisor goes through `$S ask`: written only in your terminal, nobody reads it.

## Context and resume

- Generated resume sheet (`.relay-supervision/<project>/<X>.md`): preamble, task, branch, worktree, scope, base drift, merge queue, requests, notes. Never edit it.
- What is neither in git nor on the board and a resume must know: `$S note X "…"`, one line.
- **Fresh restart** (board button): refused while you work, hold the lock, have an active worker, an open question or an unused go-ahead. It types `/clear` then an instruction: reread the preamble, **check git and the board again before any action**, confirm in one line to the supervisor.

## Without wmux

If the launch shows "backend none" (or your sheet says "Without wmux"): `$S guide no-wmux`.
