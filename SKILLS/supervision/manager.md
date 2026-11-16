# Supervision — manager and workers

Served by `$S guide manager` (after `common.md`). A worker: `$S guide worker` (Worktrees, Checks, Browser, Workers).

## Worktrees

- Always created from `origin/<base branch>` (in your sheet), one branch per task.
- Setup (dependencies, venv, test database): the "worktree setup" line of your sheet.
- Removal: `$S manager X --worktree-remove <path>`; a vanished directory is removed on its own.
- **Shared directories** (`shared_dirs` in the brain, e.g. `node_modules`): registering the worktree (`--worktree`, or `$S share X --cwd <wt>`) links it to the main checkout's, without `npm ci`. In such a worktree, `npm install|ci` is refused (it would rewrite everyone's directory): a package to add goes through the integrator. Nothing to do before `git worktree remove`: the junction does not lead to deleting the main directory.
- Install: `$S install X --cwd <worktree>` (`npm ci --prefer-offline --no-audit --no-fund` under a semaphore: one at a time under 8 threads, two above; it waits its turn, the board shows the queue). Never a direct `npm ci`: several in parallel saturate the host. Any other heavy command: `$S slot <name> X --cwd <dir> -- <command…>`.
- CPU or memory brake tightened (board message): start no worker, no `npm ci`, no build; let the rest finish.

## Checks: two levels, one build

- **While working**: `$S check X --cwd <worktree>` (commands in your sheet), no build, a few minutes. It waits its turn for the lock, releases it even on failure, and the board shows the progress.
- **The build** runs only once per wave, on the merged tree of the train: `$S check D --cwd <train worktree> --full`, slow and CPU-heavy.
- Never run `npm run check` outside `$S check`: the lock prevents memory saturation.
- **Project with a CI** (GitHub Actions, GitLab CI, Woodpecker…): no local build during the day, the CI verifies the train (`integrator.md`, step 2).
- **Speeding up a slow check** (Node/Next project): measure each step; run typecheck, lint and tests in parallel; persistent caches per worktree (incremental `tsc`, `eslint --cache`, build cache); a single test runner. Guard: the same number of passing tests before and after.

## Browser

A single path, `$S browser X`, which applies the validated procedure (new tab, shown, in your pane, closed at the end) and forbids the traps (hidden or closed tab, `open`, a call without `--surface`). wmux only; without wmux use your session's own browser tool.

```
$S browser X open http://localhost:<port>/…     # new tab in front, navigates by eval
$S browser X eval "document.title"              # read, verify
$S browser X close                              # AT THE END, always: your terminal comes back in front
```

- The tab hides your terminal: short verification, then `close`. A worker uses the same command with its id (A1); the manager checks `wmux list-panes` at the end.
- Never `wmux browser`, `open`, `new-surface`, `focus-pane` or `split` by hand. "Could not open browser panel": do not loop, work without a browser and say so.

## The project's application

- **Never start it on the user's machine** (neither a test copy nor their install) without their consent asked on the board (`$S ask`) **beforehand**. `npm test` under Node stays allowed. A test instance in WSL or a VM takes a port of its own, never the one of the user's application.
- A rule received from the supervisor is already on the board (S note) or in this skill: do not write it into Claude Code's local memory.

## Workers

- Model: sonnet or haiku for well-bounded mechanical work (search, simple tests, renames); opus for design, delicate code, security, migrations.
- **Review**: whatever the model, review the diff and rerun the checks before `--verified`.
- Quota brake: above 85 % of the 5-hour window, at most 2 active workers per manager (refused by the server).
- **Red base**: if the base itself fails its checks, run no worker check, the red would come from the base. First report to the user the fix needed on the base, then rebase once it is green.

## End of task

1. Verify each **done criterion** of your task (your sheet) and tick it: `$S task T7 --pass N`. `$S check X --cwd <worktree>` green, then push **your branch only** (the push guard refuses a forced push, a push to the base, or a push of a branch other than the one you declared).
2. `$S submit X --sha $(git rev-parse HEAD) --check green --files <n> [--note "deviations"]` (a single send: a retry carries the same id and does not deliver twice): the server refuses without a branch, without a SHA, with an open done criterion, or **without a green check recorded by `$S check` in the last 2 hours** (a declared "green" proves nothing: the proof comes from the board), moves the status to `review` and starts the pre-review. No other message to write to the supervisor. Failure: `--status blocked --msg "<cause>"`, never an "almost done" in prose.
3. The supervisor reviews the branch and marks it "ready". Do not touch your branch any more.
