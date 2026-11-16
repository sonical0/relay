"""cloture.audit: the final state of the day (what is left over), read-only."""
import os
import shutil
import stat
import subprocess
import sys

from tests.base import PROJECT, IsolatedTestCase

import cloture
import config


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", *args], check=True,
                   capture_output=True, timeout=30)


class Audit(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        self.cfg = config.load(PROJECT)

    def labels(self, s, **kw):
        return [x["label"] for x in cloture.audit(s, self.cfg, **kw)]

    def test_etat_propre(self):
        self.assertEqual(cloture.audit({"managers": {}, "tasks": {}, "merge_queue": []}, self.cfg), [])

    def test_ce_qui_traine(self):
        s = {"merge_queue": [{"branch": "feat/a", "status": "ready"}, {"branch": "feat/b", "status": "merged"}],
             "managers": {"A": {"id": "A", "status": "working", "task": "T7", "worktrees": [],
                                "workers": {"A1": {"id": "A1", "status": "working"}, "A2": {"id": "A2", "status": "done"}}}},
             "tasks": {"T7": {"id": "T7", "title": "export", "status": "doing",
                              "criteria": [{"text": "x", "passes": True}, {"text": "y", "passes": False}]},
                       "T8": {"id": "T8", "title": "fait", "status": "done"}},
             "supervisor": {"dashboard": "surf-tableau"}}
        panes = [{"surfaces": [{"id": "surf-tableau", "type": "browser"}, {"id": "surf-oublie-123456", "type": "browser"},
                               {"id": "surf-term", "type": "terminal"}]}]
        out = "\n".join(self.labels(s, panes=panes, open_signals={("stall", "A")}))
        for attendu in ("1 branch(es) in the merge queue, not merged: feat/a", 'Manager A still "working": T7',
                        "Worker(s) still active: A1", "Task T7 not done (export), 1/2 criterion(s) to validate",
                        "Signal still open: stall (A)", "1 browser tab(s) left open: surf-oublie-1"):
            self.assertIn(attendu, out)
        self.assertNotIn("feat/b", out)  # already merged
        self.assertNotIn("T8", out)      # done
        self.assertNotIn("A2", out)      # worker finished
        self.assertNotIn("surf-tableau", out)

    def test_worktree_sale_et_branche_jamais_poussee(self):
        wt = self.root / "wt-audit"
        wt.mkdir()
        # Windows: git objects are read-only, cleaning the test root would fail on them.
        retire = {"onexc" if sys.version_info >= (3, 12) else "onerror": lambda f, p, e: (os.chmod(p, stat.S_IWRITE), f(p))}
        self.addCleanup(shutil.rmtree, wt, **retire)
        git(wt, "init", "-b", "feat/a")
        (wt / "f.txt").write_text("1", encoding="utf-8")
        git(wt, "add", "f.txt")
        git(wt, "commit", "-m", "un")
        (wt / "f.txt").write_text("2", encoding="utf-8")
        s = {"merge_queue": [], "tasks": {}, "managers": {"A": {"id": "A", "status": "done", "worktrees": [str(wt), str(wt) + "-disparu"], "workers": {}}}}
        out = "\n".join(self.labels(s))
        self.assertIn("1 modified file(s) not committed", out)
        self.assertIn("Branch feat/a", out)
        self.assertIn("never pushed", out)
        self.assertIn("Worktree missing from disk", out)


if __name__ == "__main__":
    import unittest
    unittest.main()
