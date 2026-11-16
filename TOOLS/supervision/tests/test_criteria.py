"""Task brief and done criteria: `submit` refuses while the criteria of a framed task are not ticked."""
from tests.base import PROJECT, IsolatedTestCase

import config
import reprise
import server


class Criteres(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        server.configure(PROJECT)
        self.s = server.empty_state()
        server.apply(self.s, {"kind": "assign", "id": "A", "scope": ["src/a/*"]})
        server.apply(self.s, {"kind": "check", "id": "A", "rc": 0})  # proof of a green check recorded by the board
        server.apply(self.s, {"kind": "manager", "id": "A", "branch": "feat/a", "task": "T7 export CSV"})
        server.apply(self.s, {"kind": "task", "id": "T7", "title": "export CSV", "manager": "A", "status": "doing",
                              "brief": "Goal: export the list.\nOut of scope: PDF.",
                              "criteria_add": ["export downloadable", "header test"]})

    def test_perimetre_vide(self):
        """`--scope ""` empties the scope instead of storing an empty path that overlaps everything."""
        server.apply(self.s, {"kind": "assign", "id": "A", "scope": [""]})
        self.assertEqual(self.s["managers"]["A"]["scope"], [])

    def livrer(self):
        server.apply(self.s, {"kind": "submit", "id": "A", "sha": "abc1234", "check": "green"})

    def test_livraison_refusee_tant_que_les_criteres_sont_ouverts(self):
        with self.assertRaisesRegex(ValueError, r"T7 #1 export downloadable.*T7 #2"):
            self.livrer()
        server.apply(self.s, {"kind": "task", "id": "T7", "criteria_pass": [1]})
        with self.assertRaisesRegex(ValueError, r"T7 #2"):
            self.livrer()
        server.apply(self.s, {"kind": "task", "id": "T7", "criteria_pass": [2]})
        self.livrer()
        self.assertEqual(self.s["managers"]["A"]["status"], "review")

    def test_revoir_un_critere_rouvre_la_livraison_et_hors_borne_refuse(self):
        server.apply(self.s, {"kind": "task", "id": "T7", "criteria_pass": [1, 2]})
        server.apply(self.s, {"kind": "task", "id": "T7", "criteria_fail": [2]})
        with self.assertRaisesRegex(ValueError, "T7 #2"):
            self.livrer()
        with self.assertRaisesRegex(ValueError, "criterion 5 does not exist"):
            server.apply(self.s, {"kind": "task", "id": "T7", "criteria_pass": [5]})

    def test_tache_sans_critere_ou_d_un_autre_manager_ne_bloque_pas(self):
        server.apply(self.s, {"kind": "task", "id": "T7", "criteria_clear": True})
        self.livrer()  # no criterion: previous usage
        server.apply(self.s, {"kind": "task", "id": "T8", "title": "autre", "manager": "B", "criteria_add": ["x"]})
        server.apply(self.s, {"kind": "manager", "id": "A", "task": "T8 autre"})
        self.livrer()  # T8 belongs to B

    def test_fiche_montre_cadrage_et_cases(self):
        server.apply(self.s, {"kind": "task", "id": "T7", "criteria_pass": [1]})
        texte = reprise._manager(self.s, self.s["managers"]["A"], {}, config.load(PROJECT))
        self.assertIn("Goal: export the list.", texte)
        self.assertIn("- [x] 1. export downloadable", texte)
        self.assertIn("- [ ] 2. header test", texte)


if __name__ == "__main__":
    import unittest
    unittest.main()


class LivraisonPrevientLeSuperviseur(Criteres):
    """A delivery is sent to the supervisor, not only shown on the card."""

    def test_message_au_superviseur(self):
        from unittest import mock
        server.apply(self.s, {"kind": "task", "id": "T7", "criteria_pass": [1, 2]})
        with mock.patch.object(server.backend, "enabled", return_value=False):
            self.livrer()
        box = self.s.get("mailbox", {}).get("S") or []
        self.assertEqual(len(box), 1)
        self.assertIn("Delivery from Manager A to review: branch feat/a @ abc1234", box[0]["text"])


class CriteresHerites(Criteres):
    """A reused task id does not add its criteria to the ticked ones of the previous task."""

    def test_ajout_refuse_si_un_critere_est_coche(self):
        server.apply(self.s, {"kind": "task", "id": "T7", "criteria_pass": [1]})
        with self.assertRaisesRegex(ValueError, r"^T7 has 2 criterion/criteria from a previous task \(1 ticked\): "
                                                r"run again with --clear-criteria$"):
            server.apply(self.s, {"kind": "task", "id": "T7", "criteria_add": ["nouveau"]})
        self.assertEqual(len(self.s["tasks"]["T7"]["criteria"]), 2)

    def test_clear_criteria_repart_d_un_seul_critere(self):
        server.apply(self.s, {"kind": "task", "id": "T7", "criteria_pass": [1]})
        server.apply(self.s, {"kind": "task", "id": "T7", "criteria_add": ["nouveau"], "criteria_clear": True})
        self.assertEqual(self.s["tasks"]["T7"]["criteria"], [{"text": "nouveau", "passes": False}])

    def test_ajout_permis_sans_critere_coche(self):
        server.apply(self.s, {"kind": "task", "id": "T7", "criteria_add": ["third"]})
        self.assertEqual(len(self.s["tasks"]["T7"]["criteria"]), 3)
