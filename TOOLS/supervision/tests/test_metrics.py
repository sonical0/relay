"""metrics.py: task phases, trains, quota, defects, /api/metrics route, weekly note.
Everything under the test root (IsolatedTestCase)."""
import datetime
import json
import os
import subprocess
import sys
import threading
import urllib.error
import unittest
import urllib.request

from tests.base import PROJECT, REAL, SUP, IsolatedTestCase

import metrics
import server
import store

DAY0 = int(datetime.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
T0 = DAY0 + 3600  # 1 a.m., today
NOW = T0 + 4000


def seed(st):
    """Story of a task: framing, review, one round trip, ready through the queue, merged by a train."""
    ch = lambda dt, subj, before, after, who="superviseur": st.append_event(who, None, kind="change", subject=subj,
                                                                           before=before, after=after, t=T0 + dt)
    ch(0, "task:T1", None, {"id": "T1", "status": "todo", "manager": "B", "title": "one"})
    ch(50, "task:T1.status", "todo", "doing")
    ch(100, "manager:B.scope", ["src/b/*"], ["src/b/*", "src/c/*"])
    st.append_event("Manager B", "Declaration refused: src/d/x.js (out of scope)", "error", t=T0 + 200)
    st.append_event("alerte", "Collision: src/x.js declared by A, B", "error", t=T0 + 250)
    st.append_event("Manager C", "test broken on T1", "warn", kind="defaut", subject="C", t=T0 + 400)
    ch(600, "task:T1.status", "doing", "review")
    ch(900, "task:T1.status", "review", "doing")
    ch(1200, "task:T1.status", "doing", "review")
    ch(1300, "merge:feat/b-x", None, {"branch": "feat/b-x", "status": "waiting", "note": "T1: one"})
    ch(1500, "merge:feat/b-x.status", "waiting", "ready")
    st.append_event("Manager D", "Full check: rc=0, 10 min", t=T0 + 2900)
    ch(3000, "merge:feat/b-x.status", "ready", "merged")
    s = st.load()
    for pct, dt, reset in ((10, 10, 9e9), (25, 20, 9e9), (5, 30, 9e9 + 1)):
        s["quota"] = {"five_hour": {"used_percentage": pct, "resets_at": reset, "t": T0 + dt}}
        st.save(s, actor="test")
    s["sessions"] = {"s1": {"who": "B", "cost": 2.5, "t": T0 + 100}, "s2": {"who": "C", "cost": 1.25, "t": T0 + 200}}
    st.save(s, actor="test")


class Calculs(IsolatedTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.st = store.Store.open(cls.root / "run" / "calculs", log=lambda m: None)
        seed(cls.st)

    @classmethod
    def tearDownClass(cls):
        cls.st.close()
        super().tearDownClass()

    def test_phases_de_la_tache(self):
        t = {x["id"]: x for x in metrics.compute(self.st, "day", NOW)["tasks"]}["T1"]
        self.assertEqual((t["d_cadrage"], t["d_review"], t["d_merge"], t["d_total"]), (600, 900, 1500, 3000))
        self.assertEqual(t["review_rounds"], 1)
        self.assertEqual((t["widenings"], t["refusals"], t["collisions"]), (1, 1, 1))

    def test_journee(self):
        m = metrics.compute(self.st, "day", NOW)
        self.assertFalse(m["empty"])
        self.assertEqual(len(m["periods"]), metrics.PERIODS["day"])
        p = m["periods"][-1]
        self.assertEqual((p["delivered"], p["trains"], p["full_checks"], p["full_checks_ok"]), (1, 1, 1, 1))
        self.assertEqual(p["train_min"], round((3000 - 2300) / 60))  # since the start of the full check
        self.assertEqual(p["quota"]["five_hour"], 20)  # +15 in the window, then 5 in the next one
        self.assertEqual((p["cost"], p["sessions"]), (3.75, 2))
        self.assertEqual((p["defects"], p["refusals"], p["collisions"], p["widenings"]), (1, 1, 1, 1))
        self.assertEqual(m["defects"][0]["msg"], "test broken on T1")
        json.dumps(m)  # serializable as is by /api/metrics

    def test_semaine_et_vague(self):
        self.assertEqual(len(metrics.compute(self.st, "week", NOW)["periods"]), metrics.PERIODS["week"])
        self.assertEqual(metrics.compute(self.st, "wave", NOW)["periods"], [])  # no plan declared here
        with self.assertRaises(ValueError):
            metrics.compute(self.st, "mois")

    def test_note_hebdomadaire(self):
        text = metrics.weekly_note(self.st, PROJECT, NOW)
        self.assertTrue(text.startswith("---\ntags: [relay, supervision"))
        self.assertIn("| T1 one | B | 10 min | 15 min | 25 min | 50 min | 1 |", text)
        self.assertIn("test broken on T1", text)


class SourceFactice:
    """Minimal database for history(): the events and the tasks table given as is."""
    def __init__(self, events, tasks=()):
        self.events, self.tasks = events, list(tasks)

    def query(self, sql, args=()):
        if "FROM events" in sql:
            rows = [(i, t, who, kind, subj, msg, json.dumps(b) if b is not None else None,
                     json.dumps(a) if a is not None else None)
                    for i, (t, who, kind, subj, msg, b, a) in enumerate(self.events)]
            return sorted(rows, key=lambda r: (r[1], r[0])) if "ORDER BY t" in sql else rows
        return self.tasks


class IdentifiantReutilise(unittest.TestCase):
    """Yesterday's T1, closed in the morning then deleted, and today's T1: two tasks, each at its own date."""
    def test_deux_generations(self):
        J1, J2 = 1_000_000, 1_086_400  # two days
        ev = [
            (J1, "superviseur", "change", "task:T1", "", None, {"id": "T1", "title": "yesterday", "status": "doing"}),
            (J1 + 3600, "superviseur", "change", "merge:feat/a", "", None, {"status": "merged", "note": "T1 reviewed"}),
            (J2 + 600, "superviseur", "change", "task:T1.status", "", "doing", "done"),   # late close
            (J2 + 1200, "superviseur", "change", "task:T1", "", {"id": "T1"}, None),         # deleted
            (J2 + 3600, "superviseur", "change", "task:T1", "", None, {"id": "T1", "title": "today", "status": "doing"}),
            (J2 + 7200, "superviseur", "change", "merge:feat/b", "", None, {"status": "merged", "note": "T1 reviewed"}),
        ]
        h = metrics.history(SourceFactice(ev, [("T1", "A", "doing", "today")]))
        par_titre = {x["title"]: x for x in h["tasks"].values()}
        self.assertEqual(set(par_titre), {"yesterday", "today"})
        self.assertEqual(par_titre["yesterday"]["merged"], J1 + 3600)   # the queue merge, not the close
        self.assertEqual(par_titre["today"]["merged"], J2 + 7200)

    def test_sans_suppression_et_dans_le_desordre(self):
        """Re-imported backup: T1 of 15/10 added AFTER (larger id) the T1 of 26/10, without a deletion in between."""
        J1, J2 = 1_000_000, 1_900_000
        ev = [
            (J2, "superviseur", "change", "task:T1", "", None, {"id": "T1", "title": "26/10", "status": "doing"}),
            (J2 + 600, "superviseur", "change", "task:T1.status", "", "doing", "done"),
            (J1, "reprise", "change", "task:T1", "", None, {"id": "T1", "title": "15/10", "status": "doing"}),
            (J1 + 600, "reprise", "change", "task:T1.status", "", "doing", "done"),
        ]
        h = metrics.history(SourceFactice(ev, [("T1", "A", "done", "26/10")]))
        par_titre = {x["title"]: x["merged"] for x in h["tasks"].values()}
        self.assertEqual(par_titre, {"15/10": J1 + 600, "26/10": J2 + 600})


class EtatsCourts(IsolatedTestCase):
    def test_base_vide(self):
        st = store.Store.open(self.root / "run" / "vide", log=lambda m: None)
        self.addCleanup(st.close)
        m = metrics.compute(st, "day")
        self.assertTrue(m["empty"])
        self.assertEqual(m["tasks"], [])
        self.assertIn("No data yet", metrics.weekly_note(st, PROJECT))

    def test_ancien_journal_en_francais(self):
        """Databases written before the port keep French log prefixes: still counted."""
        ev = [(10, "Manager B", "log", "", "Déclaration refusée : src/d/x.js", None, None),
              (20, "alerte", "log", "", "Collision : src/x.js déclaré par A, B", None, None),
              (30, "Manager D", "log", "", "Check complet : rc=0, 10 min", None, None)]
        h = metrics.history(SourceFactice(ev))
        self.assertEqual((len(h["refus"]), h["coll"][0]["managers"], len(h["checks"])), (1, ["A", "B"], 1))

    def test_jamais_le_vrai_vault(self):
        with self.assertRaises(PermissionError):
            metrics.write_note("x", REAL["vault"] / "supervision" / metrics.NOTE_NAME)
        self.assertEqual(metrics.vault_note_path(), self.root / "vault" / "supervision" / metrics.NOTE_NAME)


class HttpEtCli(IsolatedTestCase):
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

    def get(self, path):
        with urllib.request.urlopen(self.url + path, timeout=10) as r:
            return json.load(r)

    def test_defaut_par_sup_py_puis_api(self):
        r = self.sup("note", "C", "--defaut", "sort regression")
        self.assertEqual(r.returncode, 0, r.stderr)
        rows = server.STORE.events(kind="defaut")
        self.assertEqual([(e["subject"], e["msg"]) for e in rows][-1], ("C", "sort regression"))
        self.assertIn("Defect found", server.load()["events"][0]["msg"])
        self.assertEqual(server.load()["managers"]["C"].get("notes", []), [])  # without text: no resume note
        for scope in metrics.SCOPES:
            self.assertEqual(self.get(f"/api/metrics?scope={scope}")["scope"], scope)
        self.assertGreaterEqual(self.get("/api/metrics")["periods"][-1]["defects"], 1)
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.get("/api/metrics?scope=mois")
        self.assertEqual(e.exception.code, 400)
        e.exception.close()

    def test_sup_py_metrics_hebdo_vers_le_vault_de_test(self):
        r = self.sup("metrics", "--weekly", "--vault")
        self.assertEqual(r.returncode, 0, r.stderr)
        note = self.root / "vault" / "supervision" / metrics.NOTE_NAME
        raw = note.read_bytes()
        self.assertTrue(raw.startswith(b"---\ntags:"))  # neither BOM nor CRLF: plain frontmatter
        self.assertNotIn(b"\r\n", raw)
        r = self.sup("metrics", "--scope", "week")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["scope"], "week")
