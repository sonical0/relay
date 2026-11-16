"""Guide served by `$S guide`, role preamble and base drift in the resume sheet."""
import json
import os
import shutil
import stat
import subprocess
import sys

from tests.base import PROJECT, SUP, IsolatedTestCase

import config
import reprise
import server


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), "-c", "user.email=t@t", "-c", "user.name=t", *args], check=True,
                          capture_output=True, text=True, timeout=30).stdout


class Guide(IsolatedTestCase):
    def sup(self, *args):
        return subprocess.run([sys.executable, str(SUP / "sup.py"), "--project", PROJECT, *args], env=dict(os.environ),
                              capture_output=True, text=True, encoding="utf-8", timeout=60)

    def test_guides_par_role(self):
        m = self.sup("guide", "manager").stdout
        self.assertIn("## Dashboard", m)      # common.md
        self.assertIn("## End of task", m)          # manager.md
        self.assertNotIn("A single user approval and a single build", m)  # integrator.md: not for A
        self.assertIn("## Lite mode", self.sup("guide", "S").stdout)
        self.assertIn("integrator", self.sup("guide", "integrator").stdout)
        self.assertIn("Merge train", self.sup("guide", "integrateur").stdout)  # French alias kept

    def test_worker_ne_recoit_que_ses_sections(self):
        w = self.sup("guide", "A1").stdout
        self.assertIn("## Worktrees", w)
        self.assertIn("## Browser", w)
        self.assertNotIn("## End of task", w)
        self.assertNotIn("## Dashboard", w)

    def test_section_et_appels_comptes(self):
        r = self.sup("guide", "manager", "--section", "check")
        self.assertIn("## Checks", r.stdout)
        self.assertNotIn("## Browser", r.stdout)
        lignes = (self.root / "run" / PROJECT / "guide-calls.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(json.loads(lignes[-1])["section"], "check")

    def test_role_inconnu(self):
        r = self.sup("guide", "plombier")
        self.assertNotEqual(r.returncode, 0)


class FicheEtDerive(IsolatedTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        origin, wt = cls.root / "origin.git", cls.root / "wt"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "dev", str(origin)], check=True, capture_output=True)
        subprocess.run(["git", "clone", "-q", str(origin), str(wt)], check=True, capture_output=True)
        git(wt, "checkout", "-q", "-b", "dev")
        git(wt, "commit", "-q", "--allow-empty", "-m", "base")
        git(wt, "push", "-q", "origin", "dev")
        git(wt, "checkout", "-q", "-b", "feat/a")
        git(wt, "checkout", "-q", "dev")
        for i in range(7):
            git(wt, "commit", "-q", "--allow-empty", "-m", f"base {i}")
        git(wt, "push", "-q", "origin", "dev")
        git(wt, "checkout", "-q", "feat/a")
        cls.wt = wt

    @classmethod
    def tearDownClass(cls):
        retire = {"onexc" if sys.version_info >= (3, 12) else "onerror": lambda f, p, e: (os.chmod(p, stat.S_IWRITE), f(p))}
        for d in ("wt", "origin.git"):
            shutil.rmtree(cls.root / d, **retire)
        super().tearDownClass()

    def test_derive_de_base(self):
        d = server.base_drift(self.wt, "dev")
        self.assertEqual(d["n"], 7)
        self.assertEqual(d["subjects"][0], "base 6")
        self.assertEqual(len(d["subjects"]), server.DRIFT_SHOWN)
        self.assertIsNone(server.base_drift(self.root / "nulle-part", "dev"))

    def test_fiche_preambule_derive_courrier(self):
        server.configure(PROJECT)
        s = server.empty_state()
        server.apply(s, {"kind": "assign", "id": "A", "scope": ["src/a/*"]})
        m = s["managers"]["A"]
        m["drift"] = server.base_drift(self.wt, "dev")
        server.mailbox_push(s, "A", "m1")
        f = reprise._manager(s, m, {}, config.load(PROJECT))
        self.assertIn("Manager preamble", f)
        self.assertIn("$S submit A", f)
        self.assertIn("Base drift: 7 commit(s)", f)
        self.assertIn("1 unread board message(s)", f)
        self.assertIn("$S guide manager", f)
        self.assertNotIn("SKILL.md", f)  # no rereading of the whole skill on resume
