"""Launch overrides (--base, --managers, --integrator, --lite, --default): remembered, read by the config,
and lite mode raises neither "silent" nor "no integrator" for its virtual manager."""
import argparse
import time

from tests.base import PROJECT, IsolatedTestCase

import config
import launch
import server


def args(**kw):
    base = dict(default=False, lite=False, base=None, managers=None, integrator=None, dry_run=False)
    base.update(kw)
    return argparse.Namespace(**base)


class Surcharges(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        config.write_overrides(PROJECT, {})

    def tearDown(self):
        config.write_overrides(PROJECT, {})
        super().tearDown()

    def test_base_memorisee_puis_effacee(self):
        self.assertEqual(launch.apply_overrides(PROJECT, args(base="feat/x")), {"base_branch": "feat/x"})
        self.assertEqual(config.load(PROJECT)["base_branch"], "feat/x")
        self.assertEqual(config.load(PROJECT)["overrides"], {"base_branch": "feat/x"})
        self.assertIsNone(launch.apply_overrides(PROJECT, args(base="feat/x")))  # nothing changed
        self.assertEqual(launch.apply_overrides(PROJECT, args(default=True)), {})
        self.assertEqual(config.load(PROJECT)["overrides"], {})
        self.assertFalse(config.overrides_path(PROJECT).exists())

    def test_managers_et_integrateur(self):
        launch.apply_overrides(PROJECT, args(managers="a, d", integrator="d"))
        cfg = config.load(PROJECT)
        self.assertEqual((cfg["managers"], cfg["integrator"]), (["A", "D"], "D"))
        with self.assertRaises(SystemExit):
            launch.apply_overrides(PROJECT, args(integrator="Z"))

    def test_essai_n_ecrit_rien(self):
        self.assertIsNotNone(launch.apply_overrides(PROJECT, args(lite=True, dry_run=True)))
        self.assertFalse(config.overrides_path(PROJECT).exists())

    def test_mode_leger(self):
        launch.apply_overrides(PROJECT, args(lite=True))
        cfg = config.load(PROJECT)
        self.assertEqual((cfg["managers"], cfg["integrator"], cfg["max_workers"], cfg["lite"]), (["A"], "A", 3, True))

    def test_mode_leger_sans_fausses_alertes(self):
        launch.apply_overrides(PROJECT, args(lite=True))
        server.configure(PROJECT)
        s = server.empty_state()
        server.apply(s, {"kind": "assign", "id": "A", "scope": ["src/*"]})  # no pane: virtual manager
        server.apply(s, {"kind": "manager", "id": "A", "status": "working", "task": "T1"})
        s["managers"]["A"]["updated"] = int(time.time()) - 3 * server.STALE_SECONDS
        codes = {a.get("code") for a in server.alerts(s)}
        self.assertNotIn("stall", codes)
        self.assertNotIn("no_integrator", codes)
        # outside lite mode, the same data does raise both alerts
        config.write_overrides(PROJECT, {})
        server.configure(PROJECT)
        codes = {a.get("code") for a in server.alerts(s)}
        self.assertIn("stall", codes)
        self.assertIn("no_integrator", codes)


class FicheModeLeger(IsolatedTestCase):
    def test_fiche_du_superviseur_et_pas_de_fiche_pour_le_manager_virtuel(self):
        import reprise
        cfg = dict(config.load(PROJECT), lite=True, managers=["A"], integrator="A", max_workers=3)
        etat = {"project": "", "plan": "", "managers": {"A": {"id": "A", "surface": "", "role": "integrator", "workers": {}}},
                "tasks": {}, "merge_queue": [], "requests": {}, "events": [], "supervisor_notes": []}
        reprise.write_all(etat, {"surface": "surf-test-S", "session": "essai-s"}, cfg)
        out = self.root / "relay-supervision" / PROJECT
        self.assertEqual(sorted(p.name for p in out.iterdir()), ["S.md"])
        texte = (out / "S.md").read_text(encoding="utf-8")
        self.assertIn("Lite mode", texte)
        self.assertNotIn("SendMessage to", texte)


if __name__ == "__main__":
    import unittest
    unittest.main()


class TableauHorsPanneaux(IsolatedTestCase):
    """Board tab moved to the side panel (Ctrl+Shift+I), which list-panes does not show:
    le lancement ne doit pas en rouvrir un second (2026-10-27)."""

    def run_launch(self, *extra):
        import contextlib, io, json, sys
        from unittest import mock
        panes = self.root / "panes.json"
        panes.write_text(json.dumps({"panes": [{"paneId": "p1", "surfaces": [{"id": "surf-s", "type": "terminal"}]}]}),
                         encoding="utf-8")
        st = self.root / "state.json"
        st.write_text(json.dumps({"supervisor": {"dashboard": "surf-lateral"}, "managers": {}}), encoding="utf-8")
        argv = ["launch.py", PROJECT, "--dry-run", "--caller", "surf-s", "--panes-json", str(panes),
                "--state-json", str(st), "--skip-checks", "--port", "55999", *extra]
        out = io.StringIO()
        with mock.patch.object(sys, "argv", argv), contextlib.redirect_stdout(out):
            launch.main()
        return out.getvalue()

    def test_onglet_inscrit_hors_panneaux_garde(self):
        out = self.run_launch()
        self.assertIn("outside the panes", out)
        self.assertNotIn("wmux browser open", out)

    def test_nouveau_tableau_force(self):
        self.assertIn("wmux browser open", self.run_launch("--new-dashboard"))
