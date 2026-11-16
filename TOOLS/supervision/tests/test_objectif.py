"""Current goal (objectif.py): task weights, capped median duration, estimate, CLI `sup.py objectif`."""
import json
import os
import subprocess
import sys
import threading
import unittest
import urllib.request

from tests.base import PROJECT, SUP, IsolatedTestCase

import config
import objectif
import reprise
import server

H = 3600


def tache(status, crit=()):
    return {"status": status, "criteria": [{"text": str(i), "passes": p} for i, p in enumerate(crit)]}


class Poids(unittest.TestCase):
    def test_done_todo_et_inconnue(self):
        self.assertEqual(objectif.weight(tache("done")), 1)
        self.assertEqual(objectif.weight(tache("todo", [True, True])), 0)
        self.assertEqual(objectif.weight(None), 0)

    def test_doing_fraction_des_criteres_plafonnee(self):
        self.assertEqual(objectif.weight(tache("doing")), 0)  # without criteria
        self.assertAlmostEqual(objectif.weight(tache("doing", [True, False, False, True])), 0.5)
        self.assertEqual(objectif.weight(tache("doing", [True, True])), 0.9)  # finished only when done
        self.assertEqual(objectif.weight(tache("review")), 0.9)  # reviewed, not delivered yet: even without criteria
        self.assertAlmostEqual(objectif.weight(tache("blocked", [True, False])), 0.5)  # like doing
        self.assertEqual(objectif.weight(tache("blocked")), 0)


class Calcul(unittest.TestCase):
    NOW = 1_800_000_000

    def obj(self, *ids):
        return {"title": "Split", "tasks": list(ids)}

    def test_pourcentage_et_taches(self):
        tasks = {"T1": tache("done"), "T2": tache("doing", [True, False]), "T3": tache("todo")}
        o = objectif.compute(self.obj("T1", "T2", "T3", "T9"), tasks, {}, self.NOW)
        self.assertEqual((o["n"], o["done"], o["percent"]), (4, 1, 38))  # (1 + 0.5) / 4
        self.assertEqual(o["remaining_work"], 2.5)
        self.assertEqual([x["id"] for x in o["left"]], ["T2", "T3", "T9"])
        self.assertEqual(o["left"][2]["status"], "unknown")

    def test_moins_de_deux_taches_finies_dans_la_fenetre_pas_d_estimation(self):
        tasks = {"T1": tache("done"), "T2": tache("done"), "T3": tache("done"), "T4": tache("todo")}
        hist = {"T1": {"done": self.NOW - 5 * H}, "T2": {"done": self.NOW - H}, "T3": {}}  # T3: imported from state.json
        o = objectif.compute(self.obj("T1", "T2", "T3", "T4"), tasks, hist, self.NOW)
        self.assertEqual(o["measured"], 0)  # T1 outside the 3 h window, T2 alone inside
        self.assertIsNone(o["eta"])
        self.assertIn("time left —", objectif.line(o))

    def test_debit_depuis_la_premiere_tache_finie(self):
        # Less than 3 h of history: window = first finished task -> now; the first one only bounds it.
        tasks = {"T1": tache("done"), "T2": tache("done"), "T3": tache("review"), "T4": tache("todo"), "T5": tache("todo")}
        hist = {"T1": {"done": self.NOW - 2 * H}, "T2": {"done": self.NOW - H}, "T3": {"review": self.NOW - H // 2}}
        o = objectif.compute(self.obj(*tasks), tasks, hist, self.NOW)
        self.assertEqual((o["measured"], o["window_s"], o["rate_per_h"]), (3, 2 * H, 0.95))  # (1 + 0.9) / 2 h
        self.assertAlmostEqual(o["remaining_s"], 2.1 / 0.95 * H, delta=2)  # 0.1 + 1 + 1 left
        self.assertEqual(o["end_t"], self.NOW + o["remaining_s"])
        self.assertTrue(o["eta"].startswith("≈ 2 h 13 left, done around "), o["eta"])
        self.assertIn("(estimate)", o["eta"])
        self.assertRegex(o["rate_text"], r"^observed throughput: \d\.\d tasks/h over the last 2\.0 hours$")
        self.assertIn("58 %, 2/5 tasks, ≈ 2 h 13", objectif.line(o))  # (1 + 1 + 0.9) / 5

    def test_fenetre_glissante_de_trois_heures(self):
        tasks = {f"T{i}": tache("done") for i in range(1, 5)} | {"T5": tache("todo"), "T6": tache("todo")}
        hist = {"T1": {"done": self.NOW - 5 * H}, "T2": {"done": self.NOW - 2 * H - H // 2},
                "T3": {"done": self.NOW - 2 * H}, "T4": {"done": self.NOW - H}}
        o = objectif.compute(self.obj(*tasks), tasks, hist, self.NOW)
        self.assertEqual((o["measured"], o["window_s"], o["rate_per_h"]), (3, 3 * H, 1.0))  # T1 outside the window
        self.assertTrue(o["eta"].startswith("≈ 2 h 00 left"), o["eta"])

    def test_pause_non_comptee(self):
        # Nothing finished for 10 h (night): the window ends at the last finished task, not now.
        last = self.NOW - 10 * H
        tasks = {"T1": tache("done"), "T2": tache("done"), "T3": tache("todo")}
        hist = {"T1": {"done": last - H}, "T2": {"done": last}}
        o = objectif.compute(self.obj(*tasks), tasks, hist, self.NOW)
        self.assertEqual((o["window_s"], o["rate_per_h"], o["remaining_s"]), (H, 1.0, H))

    def test_tache_suspendue_puis_reprise(self):
        # Moved to doing early, suspended (todo), resumed 10 h later: only the resumption counts.
        tasks = {"T1": tache("done"), "T2": tache("done"), "T3": tache("todo")}
        hist = {"T1": {"created": self.NOW - 20 * H, "doing": self.NOW - 3 * H, "done": self.NOW - 2 * H},
                "T2": {"created": self.NOW - 20 * H, "doing": self.NOW - H - H // 2, "done": self.NOW - H}}
        self.assertEqual(objectif.durations(["T1", "T2"], tasks, hist), [H, H // 2])  # far from the 3 h cap
        o = objectif.compute(self.obj(*tasks), tasks, hist, self.NOW)
        self.assertEqual(o["median_s"], 45 * 60)
        self.assertEqual((o["rate_per_h"], o["remaining_s"]), (0.5, 2 * H))  # 1 task in 2 h since T1; 1 left

    def test_duree_par_tache_plafonnee_et_repli_sur_la_creation(self):
        tasks = {"T1": tache("done"), "T2": tache("done"), "T3": tache("doing"), "T8": tache("done")}
        hist = {"T1": {"created": 0, "done": 2 * H}, "T2": {"doing": 0, "done": 14 * H},
                "T3": {"doing": 0, "done": H}, "T8": {"doing": 0, "done": H}}
        self.assertEqual(objectif.durations(["T1", "T2", "T3"], tasks, hist), [2 * H, 3 * H])  # T3 reopened, T8 outside the goal

    def test_tout_fini_et_sans_objectif(self):
        o = objectif.compute(self.obj("T1"), {"T1": tache("done")}, {}, self.NOW)
        self.assertEqual((o["percent"], o["eta"]), (100, "finished"))
        self.assertIsNone(objectif.compute(None, {}, {}))
        self.assertEqual(objectif.line(None), "")

    def test_format(self):
        self.assertEqual(objectif.fmt_rate(1.5, 2.5 * H), "observed throughput: 1.5 tasks/h over the last 2.5 hours")
        self.assertEqual(objectif.fmt_rate(2, 40 * 60), "observed throughput: 2.0 tasks/h over the last 40 min")
        self.assertEqual(objectif.fmt_duration(130 * 60), "2 h 10")
        self.assertEqual(objectif.fmt_duration(45 * 60), "45 min")


class Definition(unittest.TestCase):
    def test_definir_ajouter_retirer_effacer(self):
        s = {}
        objectif.update(s, {"title": "Split", "tasks": ["T16", "T17,T19c", "T16"]})
        self.assertEqual(s["objective"]["tasks"], ["T16", "T17", "T19c"])  # no duplicates, case kept
        objectif.update(s, {"add": ["T34", "T17"], "remove": ["T16"]})
        self.assertEqual(s["objective"]["tasks"], ["T17", "T19c", "T34"])
        objectif.update(s, {"title": "New title"})
        self.assertEqual((s["objective"]["title"], len(s["objective"]["tasks"])), ("New title", 3))
        objectif.update(s, {"tasks": ["T1"]})  # replaces the list, keeps the title
        self.assertEqual(s["objective"]["tasks"], ["T1"])
        objectif.update(s, {"clear": True})
        self.assertNotIn("objective", s)

    def test_refus(self):
        with self.assertRaisesRegex(ValueError, "title required"):
            objectif.update({}, {"tasks": ["T1"]})
        with self.assertRaisesRegex(ValueError, "no goal"):
            objectif.update({}, {"add": ["T1"]})


class Historique(IsolatedTestCase):
    """Durations read from the `change` rows of events, written by store.py as statuses are declared."""

    def setUp(self):
        super().setUp()
        server.configure(PROJECT)
        self.s = server.empty_state()

    def test_historique_depuis_la_base(self):
        st = server.STORE
        st.append_event("test", None, kind="change", subject="task:H1", after={"id": "H1", "status": "todo"}, t=1000)
        st.append_event("test", None, kind="change", subject="task:H1.status", before="todo", after="doing", t=1600)
        st.append_event("test", None, kind="change", subject="task:H1.status", before="doing", after="review", t=4000)
        st.append_event("test", None, kind="change", subject="task:H1.status", before="review", after="doing", t=5000)
        st.append_event("test", None, kind="change", subject="task:H1.status", before="doing", after="done", t=8800)
        st.append_event("test", None, kind="change", subject="task:H2", after={"id": "H2", "status": "doing"}, t=2000)
        st.append_event("test", None, kind="change", subject="task:H22.status", before="todo", after="done", t=9000)
        h = objectif.history(st, ["H1", "H2"])
        self.assertEqual(h["H1"], {"created": 1000, "doing": 5000, "review": 4000, "done": 8800})  # last transitions
        self.assertEqual(h["H2"], {"created": 2000, "doing": 2000})
        self.assertNotIn("H22", h)  # neighbouring subject (H2...): not confused

    def test_apply_et_fiche_du_superviseur(self):
        server.apply(self.s, {"kind": "task", "id": "T1", "title": "a", "status": "done"})
        server.apply(self.s, {"kind": "task", "id": "T2", "title": "b", "status": "doing", "criteria_add": ["x", "y"],
                              "criteria_pass": [1]})
        out = server.apply(self.s, {"kind": "objective", "from": "superviseur", "title": "Split", "tasks": ["T1", "T2"]})
        self.assertIn("Goal: Split · 75 %, 1/2 tasks", out)
        server.save(self.s, "test")
        self.assertEqual(server.load()["objective"]["tasks"], ["T1", "T2"])  # kept in the database (meta)
        o = server.objective_status(server.load())
        self.assertEqual(o["percent"], 75)
        fiche = reprise._supervisor(self.s, {}, config.load(PROJECT), o)
        self.assertIn("Goal: Split · 75 %, 1/2 tasks", fiche)
        self.assertNotIn("Goal: Split ·", reprise._supervisor(self.s, {}, config.load(PROJECT)))


class Cli(IsolatedTestCase):
    """`sup.py objectif` against a test server (free port, never 8765)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        server.configure(PROJECT)
        server.save(server.empty_state())
        cls.srv = server.make_server(0)
        cls.url = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        super().tearDownClass()

    def sup(self, *args):
        env = dict(os.environ, SUP_URL=self.url)
        return subprocess.run([sys.executable, str(SUP / "sup.py"), "--project", PROJECT, *args], env=env,
                              capture_output=True, text=True, encoding="utf-8", timeout=60)

    def state(self):
        with urllib.request.urlopen(self.url + "/api/state", timeout=10) as r:
            return json.load(r)

    def test_cycle_complet(self):
        r = self.sup("objectif")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("no goal", r.stdout)
        self.assertIsNone(self.state()["objective_status"])
        self.assertEqual(self.sup("task", "T16", "--title", "api", "--status", "done").returncode, 0)
        self.assertEqual(self.sup("task", "T17", "--title", "ui", "--status", "doing").returncode, 0)
        r = self.sup("objectif", "--title", "Split the API from the UI", "--tasks", "T16", "T17", "T18")
        self.assertEqual(r.returncode, 0, r.stderr)
        o = self.state()["objective_status"]
        self.assertEqual((o["title"], o["n"], o["done"], o["percent"]), ("Split the API from the UI", 3, 1, 33))
        self.assertEqual(self.sup("objectif", "--add", "T34", "--remove", "T18").returncode, 0)
        self.assertEqual([x["id"] for x in self.state()["objective_status"]["tasks"]], ["T16", "T17", "T34"])
        r = self.sup("objectif")
        self.assertIn("Goal: Split the API from the UI · 33 %, 1/3 tasks", r.stdout)
        self.assertIn("T17", r.stdout)
        self.assertIn("time left: —", r.stdout)
        self.assertEqual(self.sup("objectif", "--clear").returncode, 0)
        self.assertIsNone(self.state()["objective_status"])

    def test_refus_sans_titre(self):
        self.sup("objectif", "--clear")
        r = self.sup("objectif", "--tasks", "T1")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("title required", r.stderr)


if __name__ == "__main__":
    unittest.main()
