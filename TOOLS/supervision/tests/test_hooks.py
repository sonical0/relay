"""Agent state from their Claude Code hooks (sup-hook.js + POST /api/hook)."""
import json
import os
import subprocess
import threading
import time
import urllib.request

from tests.base import PROJECT, SUP, IsolatedTestCase

import backend
import server

HOOK = SUP / "sup-hook.js"


def ev(agent, event, ts, **kw):
    return {"agent": f"{PROJECT}/{agent}", "event": event, "ts": ts, **kw}


class Correspondance(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        server.configure(PROJECT)
        server.HOOKS.clear()
        server.RUN_ID = ""

    def tearDown(self):
        server.HOOKS.clear()
        super().tearDown()

    def test_evenements_vers_etats(self):
        cas = [("UserPromptSubmit", "", "working"), ("PreToolUse", "Bash", "working"),
               ("PermissionRequest", "Bash", "waiting"), ("PostToolUse", "Bash", "working"),
               ("PreToolUse", "AskUserQuestion", "waiting"), ("PostToolUse", "AskUserQuestion", "working"),
               ("Stop", "", "done"), ("UserPromptSubmit", "", "working"), ("StopFailure", "", "blocked"),
               ("SessionStart", "", "idle"), ("SessionEnd", "", "ended")]
        for i, (e, tool, attendu) in enumerate(cas, 1):
            server.hook_event(ev("A", e, 1000 + i, tool=tool), t=1000 + i)
            self.assertEqual(server.HOOKS["A"]["state"], attendu, e)

    def test_notification_permission_et_repos(self):
        server.hook_event(ev("A", "PreToolUse", 10, tool="Bash"), t=10)
        server.hook_event(ev("A", "Notification", 11, kind="permission_prompt"), t=11)
        self.assertEqual(server.HOOKS["A"]["state"], "waiting")
        server.hook_event(ev("A", "Notification", 12, kind="idle_prompt"), t=12)
        self.assertEqual(server.HOOKS["A"]["state"], "done")

    def test_stop_avec_sous_agent_actif(self):
        server.hook_event(ev("A", "SubagentStart", 10, sub="s1"), t=10)
        server.hook_event(ev("A", "Stop", 11), t=11)
        self.assertEqual(server.HOOKS["A"]["state"], "working")  # the subagent is still working
        server.hook_event(ev("A", "SubagentStop", 20, sub="s1"), t=20)
        server.hook_event(ev("A", "Stop", 21), t=21)
        self.assertEqual(server.HOOKS["A"]["state"], "done")

    def test_desordre_des_hooks_asynchrones(self):
        server.hook_event(ev("A", "PreToolUse", 10, tool="Bash"), t=10)
        server.hook_event(ev("A", "Stop", 12), t=12)
        # PostToolUse of the last tool, dated just before or just after Stop: it belongs to the finished turn.
        self.assertIsNone(server.hook_event(ev("A", "PostToolUse", 11.9, tool="Bash"), t=12.1))
        self.assertIsNone(server.hook_event(ev("A", "PostToolUse", 12.3, tool="Bash"), t=12.4))
        self.assertEqual(server.HOOKS["A"]["state"], "done")
        # a real new turn
        self.assertEqual(server.hook_event(ev("A", "UserPromptSubmit", 30), t=30), ("A", "done", "working"))

    def test_permission_ancienne_ignoree(self):
        server.hook_event(ev("A", "PostToolUse", 20, tool="Bash"), t=20)
        self.assertIsNone(server.hook_event(ev("A", "PermissionRequest", 15, tool="Bash"), t=21))
        self.assertEqual(server.HOOKS["A"]["state"], "working")

    def test_autre_projet_refuse(self):
        with self.assertRaisesRegex(ValueError, "not in this project"):
            server.hook_event({"agent": "autre/A", "event": "Stop", "ts": 1})

    def test_jeton_d_un_autre_lancement_ignore(self):
        server.RUN_ID = "run-actuel"
        self.assertIsNone(server.hook_event(ev("A", "Stop", 5, run="run-ancien"), t=5))
        self.assertNotIn("A", server.HOOKS)
        self.assertEqual(server.hook_event(ev("A", "Stop", 6, run="run-actuel"), t=6), ("A", "", "done"))
        self.assertEqual(server.hook_event(ev("B", "Stop", 6), t=6), ("B", "", "done"))  # older manager without a token

    def test_repli_sur_le_releve_apres_deux_minutes(self):
        server.hook_event(ev("A", "PreToolUse", 100, tool="Bash"), t=100)
        self.assertIsNotNone(server.hook_live("A", t=100 + server.HOOK_FRESH_SECONDS - 1))
        self.assertIsNone(server.hook_live("A", t=100 + server.HOOK_FRESH_SECONDS + 1))  # backend wmux (tests)

    def test_file_relue_au_demarrage(self):
        f = server.RUN_DIR / "hook-spool.jsonl"
        lignes = [ev("B", "PermissionRequest", 50, tool="Bash"), ev("B", "PreToolUse", 40, tool="Bash")]
        f.write_text("\n".join(json.dumps(x) for x in lignes) + "\nillisible\n", encoding="utf-8")
        self.assertEqual(server.replay_spool(), 2)
        self.assertEqual(server.HOOKS["B"]["state"], "waiting")  # replayed in timestamp order
        self.assertFalse(f.exists())


class VueEtAlertes(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        server.configure(PROJECT)
        server.HOOKS.clear()
        with server.AGENTS_LOCK:
            server.AGENTS.clear()
        self.s = server.empty_state()
        server.apply(self.s, {"kind": "assign", "id": "A", "scope": ["src/a/*"], "surface": "surf-A"})
        server.apply(self.s, {"kind": "manager", "id": "A", "status": "working"})
        self.s["managers"]["A"]["updated"] = int(time.time()) - server.STALE_SECONDS - 60

    def tearDown(self):
        server.HOOKS.clear()
        super().tearDown()

    def test_invite_visible_sans_attendre_le_releve(self):
        server.hook_event(ev("A", "PermissionRequest", time.time(), tool="Bash"))
        a = server.agents_view()["A"]
        self.assertTrue(a["waiting"])
        self.assertEqual(a["via"], "hook")
        self.assertIn("Bash", a["reason"])

    def test_manager_qui_travaille_n_est_pas_silencieux(self):
        # no hook and no screen reading (no-wmux backend): "silent" alert although it is working
        self.assertIn("stall", {x["code"] for x in server.alerts(self.s)})
        server.hook_event(ev("A", "PreToolUse", time.time(), tool="Bash"))
        self.assertNotIn("stall", {x["code"] for x in server.alerts(self.s)})

    def test_manager_au_repos_depuis_longtemps_reste_silencieux(self):
        t = time.time()
        server.hook_event(ev("A", "Stop", t - 30), t=t - 30)
        self.assertIn("stall", {x["code"] for x in server.alerts(self.s)})

    def test_question_declaree_gardee_malgre_le_hook(self):
        with server.AGENTS_LOCK:
            server.AGENTS["A"] = {"state": "blocked", "reason": "Who takes x.js?", "choices": [], "since": 1,
                                  "waiting": True, "declared": True, "screen": "", "surface": "surf-A"}
        server.hook_event(ev("A", "Stop", time.time()))
        self.assertTrue(server.agents_view()["A"]["waiting"])

    def test_sans_wmux_l_etat_par_hook_ne_vieillit_pas(self):
        os.environ["SUP_BACKEND"] = "none"
        backend.reset()
        try:
            server.hook_event(ev("A", "PermissionRequest", 100, tool="Bash"), t=100)
            self.assertIsNotNone(server.hook_live("A", t=100 + 3600))
        finally:
            os.environ["SUP_BACKEND"] = "wmux"
            backend.reset()


class HookDeBoutEnBout(IsolatedTestCase):
    """Real sup-hook.js, test HTTP server: an event reaches the board; without a board, it waits in the spool."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        server.configure(PROJECT)
        server.save(server.empty_state())
        server.HOOKS.clear()
        cls.srv = server.make_server(0)
        cls.url = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        server.HOOKS.clear()
        super().tearDownClass()

    def hook(self, payload, **env):
        e = {k: v for k, v in os.environ.items() if k != "SUP_AGENT"}
        e.update(env)
        return subprocess.run(["node", str(HOOK)], input=json.dumps(payload), env=e, capture_output=True,
                              text=True, encoding="utf-8", timeout=20)

    def test_sans_sup_agent_rien(self):
        r = self.hook({"hook_event_name": "Stop"}, SUP_URL=self.url)
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))
        self.assertNotIn("A", server.HOOKS)
        self.assertFalse((self.root / "run" / PROJECT / "hook-spool.jsonl").exists())

    def test_evenement_au_tableau(self):
        r = self.hook({"hook_event_name": "PermissionRequest", "tool_name": "Bash", "session_id": "s",
                       "tool_input": {"command": "secret"}}, SUP_URL=self.url, SUP_AGENT=f"{PROJECT}/C")
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        etat = json.load(urllib.request.urlopen(self.url + "/api/state", timeout=10))
        self.assertTrue(etat["agents"]["C"]["waiting"])
        self.assertIn("waiting for your answer", etat["events"][0]["msg"])
        self.assertNotIn("secret", json.dumps(server.HOOKS["C"]))  # neither tool content nor prompt

    def test_tableau_injoignable_file_sur_disque(self):
        r = self.hook({"hook_event_name": "Stop"}, SUP_URL="http://127.0.0.1:9", SUP_AGENT=f"{PROJECT}/D")
        self.assertEqual(r.returncode, 0)
        f = self.root / "run" / PROJECT / "hook-spool.jsonl"
        ligne = json.loads(f.read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual((ligne["agent"], ligne["event"]), (f"{PROJECT}/D", "Stop"))
        f.unlink()


class Pointeur(IsolatedTestCase):
    """Mailbox + pointer typed when idle (agent with hooks); direct typing otherwise."""

    def setUp(self):
        super().setUp()
        server.configure(PROJECT)
        server.HOOKS.clear()
        log = self.root / "wmux-calls.log"
        if log.exists():
            log.unlink()
        self.s = server.empty_state()
        server.apply(self.s, {"kind": "assign", "id": "A", "scope": ["src/a/*"], "surface": "surf-A"})

    def tearDown(self):
        server.HOOKS.clear()
        super().tearDown()

    def tapes(self):
        return [c[-1] for c in self.wmux_calls() if c[0] == "send" and "surf-A" in c]

    def test_sans_hook_saisie_directe_comme_avant(self):
        self.assertEqual(server.send_to(self.s, "A", "bonjour"), "pane")
        self.assertEqual(self.tapes(), ["bonjour"])

    def test_pendant_un_tour_rien_n_est_tape(self):
        server.hook_event(ev("A", "UserPromptSubmit", time.time()))
        for i in range(10):
            self.assertEqual(server.send_to(self.s, "A", f"m{i}"), "pointer")
        self.assertEqual(self.tapes(), [])
        self.assertEqual(len(self.s["mailbox"]["A"]), 10)
        res = server.hook_event(ev("A", "Stop", time.time() + 1))
        server.on_hook(self.s, *res)
        self.assertEqual(len(self.tapes()), 1)
        self.assertIn("10 message(s)", self.tapes()[0])
        self.assertIn("$S inbox A", self.tapes()[0])

    def test_au_repos_pointeur_tout_de_suite_et_une_fois_par_lot(self):
        server.hook_event(ev("A", "Stop", time.time()))
        server.send_to(self.s, "A", "m1")
        server.deliver_pointer(self.s, "A")  # same batch: no second pointer
        self.assertEqual(len(self.tapes()), 1)
        server.send_to(self.s, "A", "m2")  # batch grew: new pointer
        self.assertEqual(len(self.tapes()), 2)

    def test_jamais_sur_une_invite_ouverte(self):
        server.hook_event(ev("A", "PermissionRequest", time.time(), tool="Bash"))
        server.send_to(self.s, "A", "m1")
        self.assertEqual(self.tapes(), [])

    def test_urgent_tape_tout_de_suite(self):
        server.hook_event(ev("A", "UserPromptSubmit", time.time()))
        server.command(self.s, "stop", "A")
        self.assertTrue(any("STOP" in t for t in self.tapes()))

    def test_lecture_efface_le_pointeur(self):
        server.hook_event(ev("A", "Stop", time.time()))
        server.send_to(self.s, "A", "m1")
        self.assertIn("A", self.s["pointers"])
        self.assertEqual([m["text"] for m in server.mailbox_pop(self.s, "A")], ["m1"])
        self.assertNotIn("A", self.s["pointers"])

    def test_rappel_puis_signal(self):
        server.apply(self.s, {"kind": "supervisor", "surface": "surf-S"})
        server.hook_event(ev("A", "Stop", time.time()))
        server.send_to(self.s, "A", "m1")
        t0 = self.s["pointers"]["A"]["t"]
        self.assertFalse(server.pointer_followup(self.s, t=t0 + 60))
        self.assertTrue(server.pointer_followup(self.s, t=t0 + server.POINTER_REMIND_SECONDS))  # reminder
        self.assertEqual(len(self.tapes()), 2)
        self.s["pointers"]["A"]["t"] = t0
        self.assertTrue(server.pointer_followup(self.s, t=t0 + server.POINTER_REMIND_SECONDS))  # reported to S
        self.assertIn("mail_unread", {x["code"] for x in server.alerts(self.s)})
        self.assertEqual(len(self.tapes()), 2)  # no third pointer

    def test_pointeur_en_retard_au_veilleur(self):
        server.hook_event(ev("A", "UserPromptSubmit", time.time()))
        server.send_to(self.s, "A", "m1")
        server.HOOKS["A"]["state"] = "done"  # went idle before anything could be typed (Stop arrived before the message)
        self.assertTrue(server.pointer_followup(self.s))
        self.assertEqual(len(self.tapes()), 1)
