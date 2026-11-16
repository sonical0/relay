"""Directories shared between the main checkout and the workers' worktrees.

Project config (`supervision:` block of the brain): `shared_dirs: [node_modules]`. When a worktree is registered
(`sup.py manager X --worktree <path>`, or `sup.py share X --cwd <path>`), each directory of the list becomes a
junction (Windows) or a symbolic link to the main checkout's one, on three conditions: it exists in the main
checkout, git ignores it there (never a tracked directory), and the worktree does not already have one. Else nothing.
Measured effect: setting up 2 workers (Node, 175 packages) 4.2 s and 88 MB -> a few ms and 0 bytes.

Guard: in a worktree with a shared `node_modules`, `npm install|ci|uninstall|update` would change the directory of
every worker; `sup.py install` refuses it and supervision-guard.js refuses the command to a supervised agent.
"""
import os
import subprocess
from pathlib import Path

import config

NPM_MUTATING = ("install", "i", "ci", "add", "uninstall", "remove", "rm", "update", "up", "upgrade", "dedupe", "prune")


def is_link(path):
    """Junction or symbolic link (os.path.islink does not see junctions before Python 3.12)."""
    p = Path(path)
    try:
        if p.is_symlink():
            return True
        isj = getattr(os.path, "isjunction", None)
        return bool(isj and isj(p))
    except OSError:
        return False


def ignored(repo, rel):
    """Does git ignore `rel` in `repo`? (`git check-ignore -q`: 0 = ignored)."""
    try:
        r = subprocess.run(["git", "-C", str(repo), "check-ignore", "-q", rel], capture_output=True, timeout=10,
                           creationflags=config.NO_WINDOW)
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _link(src, dst):
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(src), str(dst))  # no administrator rights needed, unlike symbolic links
    else:
        os.symlink(src, dst, target_is_directory=True)


def link_shared(main, worktree, dirs):
    """Link the worktree's `dirs` to the main checkout's. Returns [(directory, result)], result among
    "linked", "already shared", "present (not linked)", "missing from main", "tracked by git (not linked)"."""
    main, worktree = Path(main).resolve(), Path(worktree).resolve()
    out = []
    if main == worktree:
        return out
    for d in dirs or []:
        rel = str(d).strip().strip("/\\")
        if not rel or ".." in Path(rel).parts:
            continue
        src, dst = main / rel, worktree / rel
        if is_link(dst):
            out.append((rel, "already shared"))
        elif dst.exists():
            out.append((rel, "present (not linked)"))
        elif not src.is_dir():
            out.append((rel, "missing from main"))
        elif not ignored(main, rel):
            out.append((rel, "tracked by git (not linked)"))
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            _link(src, dst)
            out.append((rel, "linked"))
    return out


def npm_mutates(argv):
    """Does the npm command change node_modules? (`npm ci`, `npm install x`, `npm i`…)"""
    words = [a for a in argv if not a.startswith("-")]
    names = [Path(w).name.lower() for w in words]
    for i, n in enumerate(names):
        if n in ("npm", "npm.cmd", "pnpm", "yarn") and i + 1 < len(names):
            return names[i + 1] in NPM_MUTATING or (n == "yarn" and names[i + 1] in ("add", "remove", "install"))
    return False
