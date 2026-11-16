"""Deterministic pre-review of a branch in `review` (stage 1: no LLM).

    r = prereview.run(repo, "feat/x", base="dev", others=["feat/y"], allowed=lambda p: None,
                      declared=[...], lock_owner="", check_log=Path(...), expected_sha="")
    → {"ok": bool, "sign": "✓" | "⚠", "reasons": [...], "infos": [...], "sha": "...", "files": [...], "t": ...}

Read-only: no git command writes (no fetch: the `origin/*` refs are those of the repository's last fetch, the
pre-review says so in its infos). Checks:
  - SHA present on origin (and equal to the local SHA / to the SHA declared in the merge queue);
  - base up to date: origin/<branch> contains origin/<base>;
  - foreign commits: commits of the branch also present in other managers' branches or integ/*;
  - files of the own commits: out of scope or common (server's `allowed` rule), declared vs real;
  - check lock released; last check (`sup.py check` log): present, without failure, later than the commit.
The server calls it when a manager moves to `review` with a branch, then on every scan.
"""
import os
import re
import subprocess
import time
from pathlib import Path

import config

MAX_LIST = 6  # files cited per reason, beyond that "... (+n)"


def _git(repo, *a):
    """git output (str) or None on failure."""
    try:
        r = subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, timeout=20,
                           encoding="utf-8", errors="replace", creationflags=config.NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 else None


def _rev(repo, ref):
    out = _git(repo, "rev-parse", "-q", "--verify", ref + "^{commit}")
    return out.strip() if out else ""


def _cite(paths):
    paths = sorted(paths)
    return ", ".join(paths[:MAX_LIST]) + (f" … (+{len(paths) - MAX_LIST})" if len(paths) > MAX_LIST else "")


ESLINT_SUMMARY = re.compile(r"✖ \d+ problems? \(")


def last_check(log_path):
    """Last check according to its log: {"t", "ok", "ko", "fail"} or None if missing."""
    try:
        p = Path(log_path)
        text = p.read_text(encoding="utf-8", errors="replace")
        t = int(p.stat().st_mtime)
    except (OSError, TypeError):
        return None
    lines = [l.lstrip() for l in text.splitlines()]
    ok = sum(l.startswith("✔") for l in lines)
    # "✖ 1 problem (0 errors, 1 warning)" is ESLint's summary, not a failing test.
    ko = sum(l.startswith("✖") and not ESLINT_SUMMARY.match(l) for l in lines)
    fail = any(l.startswith("ℹ fail") and l.split()[-1] not in ("0",) for l in lines) or "npm ERR!" in text \
        or "npm error" in text
    return {"t": t, "ok": ok, "ko": ko, "fail": bool(ko or fail)}


def run(repo, branch, base="dev", others=(), allowed=None, declared=(), lock_owner="", manager_id="",
        check_log=None, expected_sha="", integrator=False):
    """Pre-review of `branch` in the repository `repo` (a worktree or the project clone). See the module."""
    reasons, infos = [], []
    out = {"ok": False, "sign": "⚠", "reasons": reasons, "infos": infos, "sha": "", "files": [], "t": int(time.time()),
           "branch": branch}
    if not branch:
        reasons.append("no branch declared")
        return out
    if not repo or not Path(repo).is_dir() or _git(repo, "rev-parse", "--git-dir") is None:
        reasons.append(f"repository not found: {repo or '(none)'}")
        return out
    remote, local = _rev(repo, f"refs/remotes/origin/{branch}"), _rev(repo, f"refs/heads/{branch}")
    out["sha"] = remote[:12]
    if not remote:
        reasons.append(f"branch missing from origin: origin/{branch} not found (not pushed?)")
        tip = local
    else:
        tip = remote
        if local and local != remote:
            reasons.append(f"local SHA {local[:9]} ≠ origin {remote[:9]} (missing push or late fetch)")
        if expected_sha and not remote.startswith(expected_sha.strip()[:40]):
            reasons.append(f"declared SHA {expected_sha[:9]} ≠ origin {remote[:9]}")
    if not tip:
        reasons.append(f"branch {branch} not found (neither local nor on origin)")
        return out
    base_ref = f"origin/{base}" if _rev(repo, f"refs/remotes/origin/{base}") else base
    if not _rev(repo, base_ref):
        reasons.append(f"base branch {base} not found")
        return out
    behind = (_git(repo, "rev-list", "--count", f"{tip}..{base_ref}") or "0").strip()
    if behind != "0":
        reasons.append(f"base behind: {behind} commit(s) of {base_ref} missing from the branch (rebase on or merge {base})")

    # Own vs foreign commits (also present in another manager's branch or in integ/*).
    excl = ["--remotes=origin/integ/*", "--branches=integ/*"]
    for b in others:
        if b and b != branch:
            for ref in (f"refs/remotes/origin/{b}", f"refs/heads/{b}"):
                if _rev(repo, ref):
                    excl.append(ref)
    every = set((_git(repo, "rev-list", "--no-merges", f"{base_ref}..{tip}") or "").split())
    own = set((_git(repo, "rev-list", "--no-merges", f"{base_ref}..{tip}", "--not", *excl) or "").split())
    foreign = every - own
    if not every:
        reasons.append(f"no own commit compared to {base_ref}")
    if foreign:
        msg = f"{len(foreign)} foreign commit(s) (also in another manager's branch or integ/*)"
        (infos if integrator else reasons).append(msg + (" — normal for the integrator" if integrator else ""))

    files = set()
    if own:
        names = _git(repo, "log", "--no-merges", "--name-only", "--format=", f"{base_ref}..{tip}", "--not", *excl) or ""
        files = {f.strip().replace("\\", "/") for f in names.splitlines() if f.strip()}
    out["files"] = sorted(files)
    if allowed is not None:
        bad = {}
        for f in files:
            why = allowed(f)
            if why:
                bad.setdefault(why, []).append(f)
        for why, fs in sorted(bad.items()):
            reasons.append(f"{why}: {_cite(fs)}")
    decl = {re.sub(r"^(\./)+", "", d.replace("\\", "/")) for d in declared}
    undeclared = files - decl
    if undeclared:
        reasons.append(f"{len(undeclared)} modified file(s) not declared: {_cite(undeclared)}")
    unused = decl - files
    if unused:
        infos.append(f"{len(unused)} declared file(s) missing from the branch: {_cite(unused)}")

    if lock_owner and manager_id and lock_owner.startswith(f"Manager {manager_id.upper()} "):
        reasons.append(f"check lock not released ({lock_owner})")
    chk = last_check(check_log) if check_log else None
    commit_t = int((_git(repo, "log", "-1", "--format=%ct", tip) or "0").strip() or 0)
    if chk is None:
        reasons.append("no check found (sup.py check " + (manager_id or "X") + " --cwd <worktree>)")
    else:
        out["check"] = chk
        if chk["fail"]:
            reasons.append(f"last check failed ({chk['ko']} test(s) ✖)")
        if commit_t and chk["t"] < commit_t:
            reasons.append("last check older than the last commit: rerun it")
    infos.append("origin refs from the last fetch (the server does not fetch)")
    out["ok"] = not reasons
    out["sign"] = "✓" if out["ok"] else "⚠"
    return out


def summary(r):
    """Short line for the log and the "Review" message."""
    if not r:
        return "pre-review: —"
    return f"pre-review {r['sign']} " + ("all clear" if r["ok"] else "; ".join(r["reasons"]))


def review_prompt(mid, m, item, pre, base):
    """Ready-to-use message for the supervisor ("Review" button, stage 2: review by a subagent)."""
    branch = (item or {}).get("branch") or m.get("branch", "")
    lines = [
        f"[Board — user request] REVIEW branch {branch} of Manager {mid} "
        f"(task: {m.get('task') or '—'}).",
        f"Launch a read-only sonnet subagent: git diff origin/{base}...origin/{branch}, ticket and plan of the task; "
        "it returns a short opinion (likely defects, missing tests, risks). The \"ready\" decision stays yours.",
        summary(pre) + (f" · SHA {pre['sha']}" if pre and pre.get("sha") else ""),
    ]
    return " ".join(lines)


def check_log_path(run_dir, project, mid, test_mode=False):
    """Log of the last check of `mid` (path written by sup.py check)."""
    import tempfile
    return (Path(run_dir) if test_mode else Path(tempfile.gettempdir())) / f"{project}-check-{mid.upper()}.log"


def repo_for(m, default):
    """Repository where the branch is read: the manager's first existing worktree, else the project clone."""
    for wt in m.get("worktrees") or []:
        if os.path.isdir(wt):
            return wt
    return default


# --- Server wiring (server.py calls compute() outside the lock, then store_results() under LOCK) -------------------

def targets(state):
    """{branch: mid} of the managers in `review` with a branch, plus the branches of the merge queue."""
    out = {}
    for mid, m in state.get("managers", {}).items():
        if m.get("status") == "review" and m.get("branch"):
            out[m["branch"]] = mid
    for x in state.get("merge_queue", []):
        if x.get("branch") and x.get("status") != "merged":
            mid = (x.get("manager") or "").upper()[:1]
            out.setdefault(x["branch"], mid if mid in state.get("managers", {}) else "")
    return out


def compute(state, srv, only=None):
    """Pre-reviews of all targets (read-only, without lock). srv: the server module (project config).

    Returns {branch: result}. only: compute this branch only.
    """
    lock = srv.check_lock() or {}
    branches = {mid: m.get("branch", "") for mid, m in state.get("managers", {}).items()}
    queue = {x.get("branch"): x for x in state.get("merge_queue", [])}
    out = {}
    for branch, mid in targets(state).items():
        if only and branch != only:
            continue
        m = state["managers"].get(mid) or {"id": mid, "role": "", "scope": [], "files": []}
        # The integrator's branch (merge train) contains by construction the ones it merges:
        # counting it would make every commit of a train branch "foreign" (false positive).
        others = [b for o, b in branches.items() if o != mid and b and b != branch
                  and (state["managers"].get(o) or {}).get("role") != "integrator"]
        try:
            out[branch] = run(
                repo_for(m, srv.CFG.get("path", "")), branch, base=srv.BASE_BRANCH, others=others,
                allowed=(lambda p, m=m: srv.allowed(m, p)) if mid else None, declared=m.get("files", []),
                lock_owner=lock.get("owner", ""), manager_id=mid,
                check_log=check_log_path(srv.RUN_DIR, srv.PROJECT, mid, config.test_mode()) if mid else None,
                expected_sha=(queue.get(branch) or {}).get("sha", "") or (m.get("submitted") or {}).get("sha", ""), integrator=m.get("role") == "integrator")
        except Exception as e:  # noqa: BLE001 — a failed pre-review never blocks the server
            out[branch] = {"ok": False, "sign": "⚠", "reasons": [f"pre-review impossible: {e}"], "infos": [],
                           "sha": "", "files": [], "t": int(time.time()), "branch": branch}
    return out


def _same(a, b):
    strip = lambda r: {k: v for k, v in (r or {}).items() if k != "t"}  # noqa: E731
    return strip(a) == strip(b)


def store_results(state, results, log=None):
    """Store the results in the state: `prereview` of the manager in review and of the queue entry (additions only).

    A result identical to the previous one (date aside) is not rewritten: no event on every scan.
    Returns the number of changed entries; logs the ✓ <-> ⚠ transitions through log(who, msg, level).
    """
    changed = 0
    holders = []
    for mid, m in state.get("managers", {}).items():
        if m.get("branch") in results and m.get("status") == "review":
            holders.append((f"Manager {mid}", m, results[m["branch"]]))
    for x in state.get("merge_queue", []):
        if x.get("branch") in results:
            holders.append((None, x, results[x["branch"]]))
    for who, h, r in holders:
        old = h.get("prereview")
        if _same(old, r):
            continue
        h["prereview"] = r
        changed += 1
        if who and log and (old or {}).get("sign") != r["sign"]:
            log(who, f"{r['branch']}: {summary(r)}", "info" if r["ok"] else "warn")
    return changed
