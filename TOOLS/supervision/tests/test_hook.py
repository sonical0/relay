"""TOOLS/supervision/session-sheet.js: injects the resume sheet of the right pane, and nothing outside a supervision."""
import json
import os
import subprocess
import unittest

from tests.base import PROJECT, RELAY_ROOT, IsolatedTestCase

HOOK = RELAY_ROOT / "TOOLS/supervision/session-sheet.js"
TAG = "[RELAY · supervision]"


class Hook(IsolatedTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        out = cls.root / "relay-supervision"
        (out / PROJECT).mkdir(parents=True)
        for x in ("A", "B"):
            (out / PROJECT / f"{x}.md").write_text(f"# Resume — Manager {x}\nMARQUEUR-FICHE-{x}\n", encoding="utf-8")
        (cls.root / "evil.md").write_text("MARQUEUR-HORS-SUPERVISION\n", encoding="utf-8")
        (out / "index.json").write_text(json.dumps({"surf-test-A": f"{PROJECT}/A.md", "surf-test-B": f"{PROJECT}/B.md",
                                                    "surf-evil": "../evil.md"}), encoding="utf-8")

    def run_hook(self, surface=None, **env_over):
        env = dict(os.environ, **env_over)
        if surface:
            env["WMUX_SURFACE_ID"] = surface
        r = subprocess.run(["node", str(HOOK)], input=json.dumps({"cwd": str(self.root), "source": "clear"}),
                           env=env, capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]

    def test_fiche_du_bon_panneau(self):
        ctx = self.run_hook("surf-test-B")
        self.assertIn(TAG, ctx)
        self.assertIn("MARQUEUR-FICHE-B", ctx)
        self.assertNotIn("MARQUEUR-FICHE-A", ctx)

    def test_panneau_hors_supervision(self):
        for surface in (None, "surf-inconnu"):
            with self.subTest(surface=surface):
                ctx = self.run_hook(surface)
                self.assertNotIn(TAG, ctx)
                self.assertNotIn("MARQUEUR-", ctx)

    def test_chemin_hors_supervision_refuse(self):
        ctx = self.run_hook("surf-evil")
        self.assertNotIn(TAG, ctx)
        self.assertNotIn("MARQUEUR-HORS-SUPERVISION", ctx)

    def test_alias_sup_reprise_dir(self):
        env = {"SUP_REPRISE_DIR": os.environ["SUP_RELAY_STATE"]}
        saved = os.environ.pop("SUP_RELAY_STATE")
        try:
            ctx = self.run_hook("surf-test-A", **env)
        finally:
            os.environ["SUP_RELAY_STATE"] = saved
        self.assertIn("MARQUEUR-FICHE-A", ctx)


    def test_tableau_arrete_fiche_ignoree(self):
        import socket
        guard = self.root / "relay-supervision" / f"{PROJECT}.guard.json"
        libre = socket.socket(); libre.bind(("127.0.0.1", 0)); port = libre.getsockname()[1]; libre.close()
        guard.write_text(json.dumps({"base": "dev", "port": port, "managers": {}}), encoding="utf-8")
        try:
            ctx = self.run_hook("surf-test-A")
        finally:
            guard.unlink()
        self.assertNotIn("MARQUEUR-FICHE-A", ctx)
        self.assertIn("its board does not answer", ctx)
        self.assertIn("MARQUEUR-FICHE-A", self.run_hook("surf-test-A"))  # no known port: as before


if __name__ == "__main__":
    unittest.main()
