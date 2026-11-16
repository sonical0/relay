"""Check steps: npm scripts (strings) or explicit commands (lists), for projects without npm."""
import sys
from pathlib import Path

from tests.base import PROJECT, IsolatedTestCase

import config
import reprise
import server
import sup


class Etapes(IsolatedTestCase):
    def test_script_npm_inchange(self):
        argv, label = sup.step_command("test:ci", self.root)
        self.assertEqual(argv[1:], ["run", "test:ci"])
        self.assertTrue(argv[0].startswith("npm"))
        self.assertEqual(label, "test:ci")

    def test_commande_explicite_resolue_depuis_le_worktree(self):
        argv, label = sup.step_command([".venv/Scripts/python.exe", "-m", "pytest", "-q"], self.root / "wt")
        self.assertEqual(Path(argv[0]), (self.root / "wt" / ".venv/Scripts/python.exe").resolve())
        self.assertEqual(argv[1:], ["-m", "pytest", "-q"])
        self.assertEqual(label, ".venv/Scripts/python.exe -m pytest -q")

    def test_commande_du_path_non_resolue(self):
        argv, _ = sup.step_command(["python", "-V"], self.root)
        self.assertEqual(argv, ["python", "-V"])

    def test_bilans_pytest_ruff_et_node(self):
        log = "\n".join(["> zz-test pytest", "9 passed in 3.83s", "bruit", "All checks passed!",
                         "Found 2 errors.", "ℹ tests 12", "  1 failed, 3 passed"])
        self.assertEqual(sup.summary_lines(log), ["9 passed in 3.83s", "All checks passed!", "Found 2 errors.",
                                                  "ℹ tests 12", "1 failed, 3 passed"])

    def test_fiche_de_reprise(self):
        self.assertEqual(reprise._steps(["test:ci"]), "npm run test:ci")
        self.assertEqual(reprise._steps("check"), "npm run check")
        self.assertEqual(reprise._steps([["py", "-m", "pytest"], "build"]), "py -m pytest, npm run build")


class Avancement(IsolatedTestCase):
    def test_etape_lue_dans_le_marqueur_de_sup(self):
        old = server.CHECK_STAGES
        try:
            server.CHECK_STAGES = [config.step_label(["py", "-m", "pytest"]), "build"]
            log = self.root / "check.log"
            log.write_text("\n> zz-test py -m pytest\n", encoding="utf-8")
            self.assertEqual(server.check_progress(log)["stage"], "py -m pytest")
            log.write_text("\n> example-app@1.0.0 build\n", encoding="utf-8")  # form written by npm
            self.assertEqual(server.check_progress(log)["stage"], "build")
        finally:
            server.CHECK_STAGES = old


class CheckComplet(IsolatedTestCase):
    def test_commande_explicite_lancee_sous_verrou(self):
        old = (sup.CFG, sup.CHECK_LOCK)
        try:
            sup.CFG = dict(config.load(PROJECT), check={
                "quick": [[sys.executable, "-c", "print('4 passed in 0.1s')"]], "full": []})
            sup.CHECK_LOCK = self.root / "depot" / ".claude" / "check.lock"
            sup.CHECK_LOCK.parent.mkdir(parents=True, exist_ok=True)
            run_dir = Path(sup.CFG["run_dir"])  # in test mode, the check log goes under run_dir (created by the server)
            run_dir.mkdir(parents=True, exist_ok=True)
            with self.assertRaises(SystemExit) as end:
                sup.check("a", str(self.root), full=False)
            self.assertEqual(end.exception.code, 0)
            self.assertFalse(sup.CHECK_LOCK.exists())  # lock released
            log = (run_dir / f"{PROJECT}-check-A.log").read_text(encoding="utf-8")
            self.assertIn(f"> {PROJECT} {sys.executable} -c", log)
            self.assertIn("4 passed", log)
        finally:
            sup.CFG, sup.CHECK_LOCK = old


class VerrouSansDossier(IsolatedTestCase):
    """`check` crashed when the lock's parent directory was missing (mkdir without parents)."""

    def test_verrou_pris_dossier_parent_absent(self):
        saved = sup.CHECK_LOCK
        sup.CHECK_LOCK = self.root / "run" / "absent" / "check.lock"
        try:
            sup.lock("take", "a")
            self.assertTrue((sup.CHECK_LOCK / "owner").is_file())
            with self.assertRaises(SystemExit):  # still atomic: a single winner
                sup.lock("take", "b")
            sup.lock("release", "a")
            self.assertFalse(sup.CHECK_LOCK.exists())
        finally:
            sup.CHECK_LOCK = saved
