"""server.quota_windows: tracking of quota windows exhausted then reset (quota_resume option)."""
import unittest

from tests.base import IsolatedTestCase  # noqa: F401 — isolates writes like the other tests

import config
import server

T = 1_800_000_000


def state(used, resets_at, kind="five_hour"):
    return {"quota": {kind: {"used_percentage": used, "resets_at": resets_at, "t": T}}}


class FenetresQuota(unittest.TestCase):
    def test_sous_le_plafond_rien(self):
        s = state(99, T + 600)
        self.assertEqual(server.quota_windows(s, T), ([], []))
        self.assertEqual(s["quota_out"], {})

    def test_epuise_puis_reinitialise(self):
        s = state(100, T + 600)
        self.assertEqual(server.quota_windows(s, T), (["five_hour"], []))
        self.assertEqual(server.quota_windows(s, T + 300), ([], []))  # counted once
        self.assertEqual(server.quota_windows(s, T + 600), ([], ["five_hour"]))
        self.assertEqual(s["quota_out"], {})

    def test_vieux_100_apres_reset_pas_recompte(self):
        # the statusline has not rendered yet: the old 100 % stays, with a past reset time
        s = state(100, T - 1)
        self.assertEqual(server.quota_windows(s, T), ([], []))

    def test_sans_heure_de_reset_ignore(self):
        self.assertEqual(server.quota_windows(state(100, None), T), ([], []))

    def test_fenetre_7_jours(self):
        s = state(100, T + 3600, "seven_day")
        self.assertEqual(server.quota_windows(s, T), (["seven_day"], []))
        self.assertEqual(server.quota_windows(s, T + 3600), ([], ["seven_day"]))


class Config(unittest.TestCase):
    def test_desactive_par_defaut(self):
        self.assertIs(config.DEFAULTS["quota_resume"], False)


if __name__ == "__main__":
    unittest.main()
