"""improve.py (INBOX proposals), `submit` (structured end of task) and `browser` (tab without traps)."""
import os
import time
import unittest
from unittest import mock

from tests.base import PROJECT, IsolatedTestCase

import config
import improve
import metrics
import reprise
import server
import signals
import sup


def signaux(code, n, who="A"):
    for _ in range(n):
        server.STORE.append_event(who, f"{code} example", "warn", kind=signals.KIND, subject=code, after="open")


class Improve(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        if server.STORE is not None:  # fresh database per test: events is append-only, start from an empty file
            server.STORE.close()
            server.STORE = None
        for f in (self.root / "run" / PROJECT).glob("supervision.db*"):
            f.unlink()
        server.configure(PROJECT)
        self.inbox = self.root / "inbox" / self._testMethodName
        self.env = mock.patch.dict(os.environ, {"SUP_INBOX_DIR": str(self.inbox), "RELAY_HOST": "HOST-TEST"})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        super().tearDown()

    def test_motif_recurrent_donne_une_proposition_puis_plus(self):
        signaux("check_red", 3)
        res = improve.run(server.STORE, PROJECT)
        self.assertEqual(len(res["written"]), 1)
        f = self.inbox / "HOST-TEST"
        textes = [p.read_text(encoding="utf-8") for p in f.glob("*supervision-signal-check-red.md")]
        self.assertEqual(len(textes), 1)
        self.assertIn("type: rule", textes[0])
        self.assertIn("## Why", textes[0])
        self.assertIn("check_red example", textes[0])
        res = improve.run(server.STORE, PROJECT)  # second pass: already proposed
        self.assertEqual(res["written"], [])
        self.assertEqual(res["skipped"][0][0], "check_red")

    def test_sous_le_seuil_rien(self):
        signaux("collision", 2)
        self.assertEqual(improve.run(server.STORE, PROJECT)["written"], [])

    def test_plafond_hebdomadaire(self):
        signaux("scope_refused", 5)
        signaux("merge_conflict", 4)
        res = improve.run(server.STORE, PROJECT, max_new=1)
        self.assertEqual(len(res["written"]), 1)
        self.assertIn("scope-refused", res["written"][0])  # most frequent first
        self.assertIn("cap of", res["skipped"][0][1])

    def test_essai_n_ecrit_rien(self):
        signaux("stall", 3)
        res = improve.run(server.STORE, PROJECT, dry=True)
        self.assertEqual(len(res["written"]), 1)
        self.assertFalse(self.inbox.exists())

    def test_ecriture_refusee_sans_racine_de_test(self):
        signaux("lock_stale", 3)
        with mock.patch.dict(os.environ):
            os.environ.pop("SUP_INBOX_DIR")  # real INBOX: refused in test mode
            with self.assertRaises(PermissionError):
                improve.run(server.STORE, PROJECT)

    def test_consigne_de_diagnostic(self):
        signaux("check_red", 3, who="A")
        texte = improve.diagnosis_prompt(server.STORE, "check_red", PROJECT)
        self.assertIn("READ-ONLY", texte)
        self.assertIn("3 \"check_red\" signals", texte)
        self.assertIn("A: check_red example", texte)
        self.assertIn("SKILLS/supervision/manager.md", texte)
        self.assertIn("supervision-diagnostic-check-red.md", texte)

    def test_regles_jamais_declenchees(self):
        signaux("stall", 1)
        unused = metrics.unused_codes(server.STORE, 0, int(time.time()) + 10)
        self.assertNotIn("stall", unused)
        self.assertIn("check_red", unused)
        self.assertEqual(metrics.unused_codes(server.STORE, int(time.time()) + 100, int(time.time()) + 200), sorted(signals.CODES))


class Livraison(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        server.configure(PROJECT)
        self.s = server.empty_state()
        server.apply(self.s, {"kind": "assign", "id": "A", "scope": ["src/a/*"]})

    def test_refusee_sans_branche_ni_sha_ni_check_vert(self):
        with self.assertRaisesRegex(ValueError, "SHA"):
            server.apply(self.s, {"kind": "submit", "id": "A", "check": "green"})
        with self.assertRaisesRegex(ValueError, "no branch declared"):
            server.apply(self.s, {"kind": "submit", "id": "A", "sha": "abc1234", "check": "green"})
        server.apply(self.s, {"kind": "manager", "id": "A", "branch": "feat/a"})
        with self.assertRaisesRegex(ValueError, "check not green"):
            server.apply(self.s, {"kind": "submit", "id": "A", "sha": "abc1234", "check": "red"})
        self.assertNotEqual(self.s["managers"]["A"]["status"], "review")

    def test_preuve_du_check_vient_du_tableau(self):
        server.apply(self.s, {"kind": "manager", "id": "A", "branch": "feat/a"})
        ordre = {"kind": "submit", "id": "A", "sha": "abc1234", "check": "green"}
        with self.assertRaisesRegex(ValueError, "no green check recorded"):  # a declared "green" is not enough
            server.apply(self.s, ordre)
        server.apply(self.s, {"kind": "check", "id": "A", "rc": 1})
        with self.assertRaisesRegex(ValueError, "no green check recorded"):
            server.apply(self.s, ordre)
        server.apply(self.s, {"kind": "check", "id": "A", "rc": 0})
        self.s["managers"]["A"]["last_check"]["t"] -= server.CHECK_FRESH_SECONDS + 60  # too old
        with self.assertRaisesRegex(ValueError, "no green check recorded"):
            server.apply(self.s, ordre)
        server.apply(self.s, {"kind": "check", "id": "A", "rc": 0})
        server.apply(self.s, ordre)
        self.assertEqual(self.s["managers"]["A"]["status"], "review")

    def test_livraison_passe_en_review(self):
        server.apply(self.s, {"kind": "check", "id": "A", "rc": 0})
        server.apply(self.s, {"kind": "manager", "id": "A", "branch": "feat/a"})
        server.apply(self.s, {"kind": "submit", "id": "A", "sha": "abc1234def", "check": "green", "files": 3, "note": "aucun"})
        m = self.s["managers"]["A"]
        self.assertEqual((m["status"], m["progress"]), ("review", 100))
        self.assertEqual(m["submitted"]["sha"], "abc1234def")
        self.assertIn("Delivery abc1234def", self.s["events"][0]["msg"])


class SetupFiche(IsolatedTestCase):
    def test_setup_repris_dans_la_fiche(self):
        cfg = dict(config.load(PROJECT), setup="python -m venv .venv then pip install -e .")
        texte = reprise._manager({"managers": {}, "tasks": {}, "merge_queue": [], "requests": {}, "events": []},
                                 {"id": "A", "workers": {}}, {}, cfg)
        self.assertIn("Worktree setup: python -m venv .venv", texte)


class Navigateur(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        sup.setup(PROJECT)
        (self.root / "run" / PROJECT).mkdir(parents=True, exist_ok=True)
        self.idf = self.root / "run" / PROJECT / "browser-A.id"
        self.idf.unlink(missing_ok=True)

    def test_ouvrir_lire_fermer(self):
        with mock.patch.object(sup, "create_tab", return_value="surf-onglet-1") as tab, \
                mock.patch("wmuxcli.wmux", return_value="Titre") as w:
            sup.browser("A", "open", "http://localhost:3000/x")
            tab.assert_called_once_with("Check A")
            self.assertEqual(self.idf.read_text(encoding="utf-8"), "surf-onglet-1")
            args = w.call_args.args  # navigation through eval, always with --surface
            self.assertEqual(args[:2], ("browser", "eval"))
            self.assertIn("--surface", args)
            self.assertNotIn("open", args)
            with self.assertRaises(SystemExit):  # already open
                sup.browser("A", "open", "http://localhost:3000/y")
            sup.browser("A", "eval", "document.title")
            self.assertEqual(w.call_args.args, ("browser", "eval", "document.title", "--surface", "surf-onglet-1"))
            sup.browser("A", "close", "")
            self.assertEqual(w.call_args.args[:2], ("close-surface", "surf-onglet-1"))
            self.assertFalse(self.idf.exists())

    def test_sans_onglet_ni_url_valide_rien_ne_part(self):
        with mock.patch("wmuxcli.wmux") as w:
            for op, arg in (("eval", "1"), ("close", "")):
                with self.assertRaises(SystemExit):
                    sup.browser("A", op, arg)
            with self.assertRaises(SystemExit):
                sup.browser("A", "open", "javascript:alert(1)")
            w.assert_not_called()


if __name__ == "__main__":
    unittest.main()
