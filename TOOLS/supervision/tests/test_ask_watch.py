"""Manager questions (`sup.py ask`), supervisor warned of a block, `sup.py watch` monitor."""
import io
import json
import os
import subprocess
import sys
import threading

from tests.base import PROJECT, SUP, IsolatedTestCase

import backend
import config
import reprise
import server
import watch


def backend_force(mode):
    os.environ["SUP_BACKEND"] = mode
    backend.reset()


class Question(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        backend_force("none")
        server.configure(PROJECT)
        self.s = server.empty_state()
        server.apply(self.s, {"kind": "assign", "id": "A", "scope": ["src/a/*"]})

    def tearDown(self):
        backend_force("wmux")
        super().tearDown()

    def boite_s(self):
        return [m["text"] for m in self.s.get("mailbox", {}).get("S", [])]

    def test_question_bloque_et_previent_s(self):
        server.apply(self.s, {"kind": "manager", "id": "A", "status": "working"})
        via = server.apply(self.s, {"kind": "ask", "id": "A", "question": "Who takes extras-ui.js?",
                                    "choices": [{"id": "a", "label": "A", "key": "1"}]})
        self.assertEqual(via, "mailbox")
        m = self.s["managers"]["A"]
        self.assertEqual(m["status"], "blocked")
        self.assertEqual(m["question"]["choices"], ["A"])
        self.assertTrue(any("Question from Manager A" in t and "extras-ui.js" in t for t in self.boite_s()))
        server.apply(self.s, {"kind": "ask", "id": "A", "done": True})
        self.assertEqual(m["status"], "working")  # status from before the question
        self.assertNotIn("question", m)

    def test_question_vide_refusee(self):
        with self.assertRaisesRegex(ValueError, "empty question"):
            server.apply(self.s, {"kind": "ask", "id": "A", "question": "  "})

    def test_passage_en_blocked_previent_s_une_fois(self):
        server.apply(self.s, {"kind": "manager", "id": "A", "status": "blocked", "msg": "scope too narrow"})
        self.assertEqual(sum("Manager A blocked" in t for t in self.boite_s()), 1)
        self.assertTrue(any("scope too narrow" in t for t in self.boite_s()))
        server.apply(self.s, {"kind": "manager", "id": "A", "status": "blocked", "progress": 20})
        self.assertEqual(sum("Manager A blocked" in t for t in self.boite_s()), 1)  # already blocked: no duplicate

    def test_fiches_citent_ask_et_watch(self):
        etat = {"project": "", "plan": "", "managers": {"A": {"id": "A", "surface": "", "workers": {},
                "question": {"text": "Push now?", "t": 0}}}, "tasks": {}, "merge_queue": [], "requests": {},
                "events": [], "supervisor_notes": []}
        cfg = config.load(PROJECT)
        fiche = reprise._manager(etat, etat["managers"]["A"], {}, cfg)
        self.assertIn('$S ask A "<question>"', fiche)
        self.assertIn("Pending question", fiche)
        self.assertIn("$S watch", reprise._supervisor(etat, {}, cfg))

    def test_fiche_avec_wmux_cite_le_chemin_absolu(self):
        backend_force("wmux")
        import wmuxcli
        etat = {"managers": {}, "tasks": {}, "merge_queue": [], "requests": {}, "events": []}
        fiche = reprise._manager(etat, {"id": "B", "workers": {}}, {}, config.load(PROJECT))
        self.assertIn(os.path.basename(wmuxcli.WMUX), fiche)
        self.assertIn("wmux on this host", fiche)


class AskBoutEnBout(IsolatedTestCase):
    """`sup.py ask` against a test HTTP server: board, then report-agent through the fake wmux client of the root."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        server.configure(PROJECT)
        server.save(server.empty_state())
        cls.srv = server.make_server(0)
        cls.url = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        st = server.load()
        server.apply(st, {"kind": "assign", "id": "A", "scope": ["src/a/*"], "surface": "surf-test-A"})
        server.save(st, "test")

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        super().tearDownClass()

    def sup(self, *args, backend_mode="wmux"):
        env = dict(os.environ, SUP_URL=self.url, SUP_BACKEND=backend_mode)
        return subprocess.run([sys.executable, str(SUP / "sup.py"), "--project", PROJECT, *args], env=env,
                              capture_output=True, text=True, encoding="utf-8", timeout=60)

    def calls(self):
        return [c for c in self.wmux_calls() if c and c[0] == "report-agent"]

    def test_ask_puis_done(self):
        choix = '[{"id":"oui","label":"Yes","key":"1"}]'
        r = self.sup("ask", "A", "Push now?", "--choices", choix)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("question shown", r.stdout)
        c = self.calls()[-1]
        self.assertEqual(c[:3], ["report-agent", "--blocked", "Push now?"])
        self.assertEqual(json.loads(c[c.index("--choices") + 1])[0]["id"], "oui")
        self.assertEqual(c[c.index("--surface") + 1], "surf-test-A")  # no WMUX_SURFACE_ID: registered surface
        self.assertEqual(server.load()["managers"]["A"]["status"], "blocked")
        r = self.sup("ask", "A", "--done")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.calls()[-1][:2], ["report-agent", "--unblocked"])
        self.assertNotIn("question", server.load()["managers"]["A"])

    def test_choix_invalides_refuses(self):
        r = self.sup("ask", "A", "x ?", "--choices", "{not json")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("JSON", r.stderr)

    def test_sans_wmux_question_deposee(self):
        n = len(self.calls())
        r = self.sup("ask", "A", "Without wmux?", backend_mode="none")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("without wmux", r.stdout)
        self.assertEqual(len(self.calls()), n)  # no wmux call
        self.sup("ask", "A", "--done", backend_mode="none")

    def test_watch_once(self):
        r = self.sup("watch", "--once", "--no-screens")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("[board] A ", r.stdout)


def etat(**m):
    base = {"id": "A", "status": "working", "progress": 10, "task": "T1"}
    return {"managers": {"A": {**base, **m}}, "requests": {}, "alerts": [], "events": []}


class Moniteur(IsolatedTestCase):
    def test_rien_de_repete_tant_que_rien_ne_change(self):
        w = watch.Watch()
        self.assertEqual(len(w.lines(etat())), 1)
        self.assertEqual(w.lines(etat()), [])
        out = w.lines(etat(progress=40))
        self.assertEqual(len(out), 1)
        self.assertIn("40%", out[0])

    def test_dernier_message_du_manager(self):
        w = watch.Watch()
        s = etat()
        s["events"] = [{"who": "Manager A", "msg": "worker A1 started"}, {"who": "Manager A", "msg": "older"}]
        self.assertIn("worker A1 started", w.lines(s)[0])

    def test_alerte_nouvelle_seulement(self):
        w = watch.Watch()
        s = etat()
        w.lines(s)
        s["alerts"] = [{"code": "stall", "who": "A", "level": "warn", "msg": "Manager A silent for 12 min"}]
        self.assertEqual(len(w.lines(s)), 1)
        s["alerts"][0]["msg"] = "Manager A silent for 13 min"
        self.assertEqual(w.lines(s), [])  # same alert, aging text: no line
        s["alerts"] = []
        w.lines(s)
        s["alerts"] = [{"code": "stall", "who": "A", "level": "warn", "msg": "Manager A silent for 30 min"}]
        self.assertEqual(len(w.lines(s)), 1)  # gone then back: reported again

    def test_question_demande_et_boite(self):
        w = watch.Watch()
        s = etat(status="blocked", question={"text": "Push now?", "choices": ["Yes", "No"]})
        s["requests"] = {"R1": {"id": "R1", "from": "B", "what": "add x", "status": "open"}}
        s["mailbox_pending"] = {"S": 2}
        out = "\n".join(w.lines(s))
        self.assertIn("[question] A is waiting for an answer: Push now? [Yes / No]", out)
        self.assertIn("[request] R1 from B", out)
        self.assertIn("[mailbox] 2 message(s)", out)

    def test_ecran_en_attente(self):
        w = watch.Watch()
        w.lines(etat())
        out = w.lines(etat(), {"A": "Can you widen my scope?"})
        self.assertEqual(out, ["[screen] A idle, waiting for an answer: Can you widen my scope?"])
        self.assertEqual(w.lines(etat(), {"A": "Can you widen my scope?"}), [])

    def test_lecture_d_ecran(self):
        ecran = "● Part 1 is done.\nWho takes extras-ui.js?\n────────\n> \n⏵⏵ accept edits on"
        self.assertEqual(watch.screen_question(ecran), "Who takes extras-ui.js?")
        self.assertEqual(watch.screen_question(ecran, busy=True), "")
        self.assertEqual(watch.screen_question("✻ Thinking… (12s · esc to interrupt)\nWho?\n> "), "")
        self.assertEqual(watch.screen_question("All done.\n> "), "")
        self.assertEqual(watch.screen_question(""), "")

    def test_sans_wmux_pas_de_lecture_d_ecran(self):
        backend_force("none")
        try:
            self.assertEqual(watch.read_screens({"managers": {"A": {"surface": "surf-x"}}}), {})
        finally:
            backend_force("wmux")

    def test_tableau_injoignable_une_ligne(self):
        out = io.StringIO()
        watch.run(os.environ["SUP_URL"], once=True, screens=False, out=out)
        self.assertTrue(out.getvalue().startswith("[board unreachable]"))
        w = watch.Watch()
        self.assertEqual(len(w.error("refused")), 1)
        self.assertEqual(w.error("refused"), [])


if __name__ == "__main__":
    import unittest
    unittest.main()
