"""`GET /api/island`: versioned contract for an external app — stable shape, nothing sensitive."""
import json
import threading
import unittest
import urllib.error
import urllib.request

from tests.base import PROJECT, IsolatedTestCase

import island
import server

CHAMPS = {"schema", "project", "now", "url", "managers", "alerts", "objective", "waiting"}
CHAMPS_MANAGER = {"id", "role", "status", "task", "progress", "waiting", "question"}


class Construction(unittest.TestCase):
    def etat(self):
        return {"project": "example-app", "now": 100, "managers": {
            "B": {"id": "B", "role": "", "status": "working", "task": "T2 forecast", "progress": 40,
                  "worktrees": ["C:/secret/wt"], "scope": ["x/*"]},
            "D": {"id": "D", "role": "integrator", "status": "idle", "task": "", "progress": 0}}}

    def test_forme_du_schema_1(self):
        out = island.build(self.etat(), [], None, {}, "http://127.0.0.1:1/")
        self.assertEqual(set(out), CHAMPS)
        self.assertEqual(out["schema"], 1)
        self.assertEqual([m["id"] for m in out["managers"]], ["B", "D"])
        for m in out["managers"]:
            self.assertEqual(set(m), CHAMPS_MANAGER)
        self.assertEqual(out["managers"][1]["role"], "integrator")
        self.assertEqual(out["managers"][0]["role"], "manager")
        self.assertIsNone(out["objective"])

    def test_question_seulement_si_en_attente_et_sans_ecran(self):
        agents = {"B": {"waiting": True, "reason": "Push?", "screen": "token=abc",
                        "choices": [{"id": "1", "label": "Yes"}, "No", {"id": "3"}]},
                  "D": {"waiting": False, "reason": "old", "choices": ["x"]}}
        out = island.build(self.etat(), [], None, agents)
        b, d = out["managers"]
        self.assertEqual(b["question"], {"text": "Push?", "choices": ["Yes", "No", "3"], "ids": ["1", "", "3"],
                                         "since": None, "kind": "question", "generic": False})
        self.assertIsNone(d["question"])
        self.assertEqual(out["waiting"], 1)
        texte = json.dumps(out)
        for interdit in ("token=abc", "C:/secret/wt", "screen", "scope"):
            self.assertNotIn(interdit, texte)

    def test_genre_et_libelle_generique(self):
        cas = {"Claude needs your permission": ("permission", True), "Claude needs your permission to use Bash": ("permission", False),
               "needs your attention.": ("question", True), "Claude is waiting for your input": ("question", True),
               "a prompt is waiting for your answer in its pane": ("question", True), "": ("question", True),
               "Push the train?": ("question", False)}
        for texte, attendu in cas.items():
            q = island.build(self.etat(), [], None, {"B": {"waiting": True, "reason": texte}})["managers"][0]["question"]
            self.assertEqual((q["kind"], q["generic"]), attendu, texte)

    def test_objectif_et_alertes_reduits(self):
        obj = {"title": "O2", "done": 1, "n": 2, "percent": 50, "eta": "~40 min", "tasks": [{"id": "T1"}]}
        al = [{"level": "warn", "code": "stall", "who": "B", "msg": "B idle", "extra": 1}]
        out = island.build(self.etat(), al, obj, {})
        self.assertEqual(out["objective"], {"title": "O2", "done": 1, "n": 2, "percent": 50, "eta": "~40 min"})
        self.assertEqual(out["alerts"], [{"level": "warn", "code": "stall", "who": "B", "msg": "B idle"}])


class Superviseur(unittest.TestCase):
    def test_superviseur_en_tete_quand_inscrit(self):
        s = {"project": "example-app", "supervisor": {"surface": "surf-s"}, "supervisor_notes": [{"t": 1, "text": "wave 4"}],
             "managers": {"A": {"id": "A", "role": "", "status": "working", "task": "T40", "progress": 50}}}
        out = island.build(s, [], None, {"S": {"state": "working", "waiting": True, "reason": "Push?", "since": 9,
                                                 "choices": [{"id": "1", "label": "Yes"}]}})
        self.assertEqual([m["id"] for m in out["managers"]], ["S", "A"])
        sv = out["managers"][0]
        self.assertEqual((sv["role"], sv["status"], sv["task"]), ("supervisor", "working", "wave 4"))
        self.assertEqual(sv["question"]["ids"], ["1"])
        self.assertEqual(set(sv), CHAMPS_MANAGER)

    def test_superviseur_une_seule_fois(self):
        s = {"supervisor": {"surface": "surf-s"}, "managers": {"A": {"id": "A"}, "S": {"id": "S", "scope": []}}}
        self.assertEqual([m["id"] for m in island.build(s, [], None, {})["managers"]], ["S", "A"])

    def test_pas_de_superviseur_sans_panneau(self):
        out = island.build({"managers": {}}, [], None, {})
        self.assertEqual(out["managers"], [])


class ControleReponse(unittest.TestCase):
    """POST /api/island/answer: only a choice of the question still displayed (no double answer)."""
    AG = {"B": {"waiting": True, "since": 1700, "choices": [{"id": "1", "label": "Yes"}, {"id": "2", "label": "No"}]}}

    def test_choix_valide(self):
        self.assertEqual(island.check_answer(self.AG, "B", {"choice": "2", "since": 1700}), (200, ""))

    def test_question_remplacee_ou_repondue(self):
        self.assertEqual(island.check_answer(self.AG, "B", {"choice": "1", "since": 1600})[0], 409)
        self.assertEqual(island.check_answer({"B": {**self.AG["B"], "waiting": False}}, "B",
                                             {"choice": "1", "since": 1700})[0], 409)
        self.assertEqual(island.check_answer(self.AG, "Z", {"choice": "1", "since": 1700})[0], 409)

    def test_choix_inconnu_ou_texte_libre(self):
        self.assertEqual(island.check_answer(self.AG, "B", {"choice": "9", "since": 1700})[0], 400)
        self.assertEqual(island.check_answer(self.AG, "B", {"text": "rm -rf", "since": 1700})[0], 400)


class Http(IsolatedTestCase):
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

    def test_api_island(self):
        req = urllib.request.Request(self.url + "/api/report",
                                     json.dumps({"kind": "assign", "from": "test", "id": "A", "scope": ["a/*"]}).encode(),
                                     {"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10).read()
        with urllib.request.urlopen(self.url + "/api/island", timeout=10) as r:
            out = json.load(r)
        self.assertEqual(set(out), CHAMPS)
        self.assertEqual(out["schema"], 1)
        self.assertEqual(out["url"], self.url + "/")
        self.assertEqual(out["project"], PROJECT)
        self.assertEqual(out["managers"][0]["id"], "A")

    def repondre(self, token, body):
        req = urllib.request.Request(self.url + "/api/island/answer", json.dumps(body).encode(),
                                     {"Content-Type": "application/json", "X-Island-Token": token})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code

    def test_reponse_exige_le_jeton_de_l_ile(self):
        jeton = (server.RUN_DIR / "island.token").read_text(encoding="utf-8").strip()
        self.assertNotEqual(jeton, server.TOKEN)  # token distinct from the board buttons' one
        self.assertEqual(self.repondre("faux", {"id": "A", "choice": "1", "since": 1}), 403)
        self.assertEqual(self.repondre(server.TOKEN, {"id": "A", "choice": "1", "since": 1}), 403)
        self.assertEqual(self.repondre(jeton, {"id": "A", "choice": "1", "since": 1}), 409)  # no question


if __name__ == "__main__":
    unittest.main()
