"""server.py: apply() (scopes, common files) without HTTP, then a test server on a free port."""
import json
import os
import subprocess
import sys
import threading
import unittest
import urllib.error
import urllib.request

from tests.base import PROJECT, SUP, IsolatedTestCase

import server


class Apply(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        server.configure(PROJECT)
        self.s = server.empty_state()
        server.apply(self.s, {"kind": "assign", "id": "B", "scope": ["src/b/*"]})
        server.apply(self.s, {"kind": "assign", "id": "D", "scope": ["src/d/*"], "role": "integrator"})

    def test_racines_du_serveur(self):
        self.assertEqual(server.RUN_DIR, self.root / "run" / PROJECT)
        self.assertEqual(server.CHECK_LOCK, self.root / "depot" / ".claude" / "check.lock")
        self.assertTrue((server.RUN_DIR / ".token").is_file())

    def test_perimetre(self):
        server.apply(self.s, {"kind": "files", "id": "B", "add": ["src/b/x.js"]})
        self.assertEqual(self.s["managers"]["B"]["files"], ["src/b/x.js"])
        with self.assertRaisesRegex(ValueError, "out of scope"):
            server.apply(self.s, {"kind": "files", "id": "B", "add": ["src/d/y.js"]})
        self.assertEqual(self.s["managers"]["B"]["files"], ["src/b/x.js"])

    def test_fichier_commun(self):
        with self.assertRaisesRegex(ValueError, "common file"):
            server.apply(self.s, {"kind": "files", "id": "B", "add": ["package.json"]})
        server.apply(self.s, {"kind": "files", "id": "D", "add": ["package.json"]})  # the integrator can
        self.assertIn("package.json", self.s["managers"]["D"]["files"])

    def test_sans_perimetre(self):
        with self.assertRaisesRegex(ValueError, "no scope"):
            server.apply(self.s, {"kind": "files", "id": "C", "add": ["src/c/z.js"]})

    def test_refus_journalise_sous_la_racine(self):
        with self.assertRaises(ValueError):
            server.apply(self.s, {"kind": "files", "id": "B", "add": ["src/d/y.js"]})
        self.assertTrue((self.root / "run" / PROJECT / "supervision.db").is_file())
        self.assertIn("Declaration refused", server.load()["events"][0]["msg"])


class ServeurHttp(IsolatedTestCase):
    """Test server on a free port (never 8765), queried over HTTP and by sup.py."""

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

    def post(self, body):
        req = urllib.request.Request(self.url + "/api/report", json.dumps(body).encode("utf-8"),
                                     {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.load(r)

    def sup(self, *args):
        env = dict(os.environ, SUP_URL=self.url)
        return subprocess.run([sys.executable, str(SUP / "sup.py"), "--project", PROJECT, *args], env=env,
                              capture_output=True, text=True, encoding="utf-8", timeout=60)

    def test_api_state_meme_forme(self):
        self.post({"kind": "assign", "from": "test", "id": "A", "scope": ["src/a/*"]})
        with urllib.request.urlopen(self.url + "/api/state", timeout=10) as r:
            s = json.load(r)
        for k in ("managers", "tasks", "requests", "events", "merge_queue", "alerts", "common", "check_lock",
                  "worker_limit", "quota_brake", "fresh", "agents"):
            self.assertIn(k, s)
        self.assertEqual(s["managers"]["A"]["scope"], ["src/a/*"])

    def test_refus_http(self):
        self.post({"kind": "assign", "from": "test", "id": "B", "scope": ["src/b/*"]})
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.post({"kind": "files", "id": "B", "add": ["config/x.json"]})
        self.assertEqual(e.exception.code, 400)
        e.exception.close()

    def test_sup_py_contre_le_serveur_de_test(self):
        self.assertEqual(self.sup("assign", "C", "--scope", "src/c/*").returncode, 0)
        r = self.sup("files", "C", "--add", "src/b/x.js")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("out of scope", r.stderr)

    def test_sup_py_verrou_sous_la_racine(self):
        lock = self.root / "depot" / ".claude" / "check.lock"
        r = self.sup("lock", "take", "B")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((lock / "owner").is_file())
        self.assertEqual(self.sup("lock", "release", "B").returncode, 0)
        self.assertFalse(lock.exists())

    def test_sup_py_refuse_8765(self):
        env = dict(os.environ, SUP_URL="http://127.0.0.1:8765")
        r = subprocess.run([sys.executable, str(SUP / "sup.py"), "--project", PROJECT, "event", "must not be sent"],
                           env=env, capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("8765", r.stderr)

    def test_aucun_appel_wmux_reel(self):
        # The test server does not start the watchers: nothing was sent, not even to the fake wmux.
        self.assertEqual(self.wmux_calls(), [])


if __name__ == "__main__":
    unittest.main()

class QuestionPersistante(unittest.TestCase):
    """wmux clears the declared question at the end of the turn: the board keeps it until the answer or the resumption."""
    def test_gardee_apres_fin_de_tour_puis_levee(self):
        sticky = {}
        bloque = {"state": "blocked", "blockedReason": "Push?", "choices": [{"id": "y"}], "blockedSince": 5000}
        self.assertEqual(server.sticky_ask(sticky, "D", bloque, True, False), (True, "Push?", [{"id": "y"}], 5))
        efface = {"state": "idle", "blockedReason": None}  # end of turn
        self.assertEqual(server.sticky_ask(sticky, "D", efface, False, False)[:2], (True, "Push?"))
        self.assertEqual(server.sticky_ask(sticky, "D", {"state": "working"}, False, False)[0], False)
        self.assertNotIn("D", sticky)

    def test_permission_sans_invite_levee(self):
        """Claude Code permission still flagged while no prompt is on screen: dropped on the 3rd reading."""
        seen, perm = {}, "Claude needs your permission"
        self.assertEqual([server.stale_permission(seen, "B", perm, False) for _ in range(3)], [False, False, True])
        # visible prompt: never dropped, and the count restarts at zero
        self.assertFalse(server.stale_permission(seen, "B", perm, True))
        self.assertNotIn("B", seen)
        # question declared as text (report-agent): never dropped by this path
        self.assertFalse(any(server.stale_permission(seen, "D", "Push fr-en?", False) for _ in range(5)))

    def test_levee_par_reponse(self):
        sticky = {"D": {"reason": "Push?", "choices": [], "since": 5}}
        self.assertFalse(server.sticky_ask(sticky, "D", {"state": "idle"}, False, 6000)[0])
        self.assertEqual(sticky, {})


class QuestionRepondue(unittest.TestCase):
    """Answered from the board: it does not come back while the agent applies the answer (still "blocked")."""
    def test_meme_question_ne_revient_pas(self):
        self.assertTrue(server.answered_here({"D": 5}, "D", 5))

    def test_nouvelle_question_revient(self):
        self.assertFalse(server.answered_here({"D": 5}, "D", 9))
        self.assertFalse(server.answered_here({"D": 5}, "A", 5))

    def test_sans_horodatage_rien_n_est_masque(self):
        self.assertFalse(server.answered_here({"D": 0}, "D", 0))

    def test_invite_suivante_sans_choix_perimes(self):
        """"Yes, push" answered, then the push permission on screen: keys, not the old choice."""
        reason, choices = server.shown_ask(False, 5000, True, "Push?", [{"id": "yes"}])
        self.assertEqual(choices, [])
        self.assertIn("Prompt on screen", reason)

    def test_question_ouverte_inchangee(self):
        self.assertEqual(server.shown_ask(True, False, True, "Push?", [{"id": "yes"}]), ("Push?", [{"id": "yes"}]))


class ChoixGarde(IsolatedTestCase):
    """Kept question (STICKY_ASKS): wmux cleared it at the end of the turn and refuses answer-agent;
    the choice is then typed into the pane."""

    def test_repli_tape_le_texte_du_choix(self):
        from unittest import mock
        calls = []
        def fake(*args, **kw):
            calls.append(args)
            return args[0] != "answer-agent"
        server.STICKY_ASKS["S"] = {"reason": "?", "since": 1,
                                   "choices": [{"id": "yes", "label": "Yes", "text": "yes, launch them\r"}]}
        st = {"supervisor": {"surface": "surf-s"}, "managers": {}, "events": []}
        with mock.patch.object(server, "wmux", side_effect=fake), mock.patch.object(server, "supervisor", return_value={"surface": "surf-s"}), \
                mock.patch.object(server, "requires_wmux"), mock.patch.object(server, "log"):
            self.assertTrue(server.answer(st, "S", {"choice": "yes", "label": "Yes"}))
        self.assertIn(("send", "--surface", "surf-s", "yes, launch them"), calls)
        self.assertIn(("send-key", "enter", "--surface", "surf-s"), calls)
        self.assertNotIn("S", server.STICKY_ASKS)

    def test_choix_inconnu_reste_un_echec(self):
        from unittest import mock
        server.STICKY_ASKS.pop("S", None)
        with mock.patch.object(server, "wmux", return_value=False), mock.patch.object(server, "supervisor", return_value={"surface": "surf-s"}), \
                mock.patch.object(server, "requires_wmux"), mock.patch.object(server, "log"):
            self.assertFalse(server.answer({}, "S", {"choice": "zz"}))
