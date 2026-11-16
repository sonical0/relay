# Supervision — integrator (manager D): the merge train

Served by `$S guide integrator` (after `common.md` and `manager.md`). A single user approval and a single build for all the ready branches.

1. Clean worktree on `origin/<base>`. Merge, in queue order, every "ready" branch: `git merge --no-ff origin/<branch>`, message "Merge <branch> into <base> (TASK-…)". At the slightest conflict: `merge --abort`, take the branch out of the train, declare it (`$S merge <branch> --status conflict`: the board counts conflicts, red at the 2nd), warn the supervisor.
2. `$S check D --cwd <worktree> --full`: a single full check, build included. **Project with a CI** (GitHub Actions, GitLab CI, Woodpecker…, described in the `supervision:` block or present in the repository): quick check locally, then push the train branch (`integ/…`) and wait for a green CI (`gh run watch`, `glab ci status`, the CI's web UI…); the base then moves by fast-forward to the SHA the CI verified. Without a CI: full local check, as above.
3. Verify: `origin/<base>` has not moved; `git log origin/<base>..HEAD` contains only the commits of the train's branches and the merges.
4. **One single question** to the user, declared on the board:
   `$S ask D "Merge train: push <branches> to <base> (<sha before> → <sha after>)? Full check: <n> passed / <n> failed, build OK." --choices '[…yes/no…]'`, mirrored by an AskUserQuestion.
   That is the approval: no prior go-ahead to wait for, no second confirmation.
5. On "yes": `git push origin HEAD:<base>` (fast-forward, never `--force`), then `$S pushed D --msg "<sha>"` and `$S merge <branch> --status merged` for each one. On "no": push nothing, warn the supervisor.
6. Report to the supervisor: base SHA, check result. The supervisor verifies again behind you.

Migration numbers and common-file requests (`$S request`) go through D alone.
