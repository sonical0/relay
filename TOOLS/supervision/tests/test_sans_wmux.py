"""Supervision without wmux: backend detection, mailbox, identity through SUP_AGENT, launch."""
import argparse
import io
import json
import os
import subprocess
import sys
import threading
import time
import unittest
import urllib.request
from contextlib import redirect_stdout
from unittest import mock

from tests.base import PROJECT, SUP, RELAY_ROOT, IsolatedTestCase

import backend
import config
import launch
import reprise
import server


def sans_wmux():
    os.environ["SUP_BACKEND"] = "none"
    backend.reset()


class Detection(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        self.saved = os.environ.get("SUP_BACKEND")
        self.cli = os.environ["WMUX_CLI"]

    def tearDown(self):
        os.environ["WMUX_CLI"] = self.cli
        if self.saved is None:
            os.environ.pop("SUP_BACKEND", None)
        else:
            os.environ["SUP_BACKEND"] = self.saved
        backend.reset()
        super().tearDown()

    def test_force_et_valeur_inconnue(self):
        os.environ["SUP_BACKEND"] = "none"
        self.assertEqual(backend.detect(), "none")
        os.environ["SUP_BACKEND"] = "wmux"
        self.assertEqual(backend.detect(), "wmux")
        os.environ["SUP_BACKEND"] = "tmux"
        with self.assertRaisesRegex(ValueError, "unknown SUP_BACKEND"):
            backend.detect()

    def test_auto_wmux_si_pong_sinon_none(self):
        os.environ["SUP_BACKEND"] = "auto"
        self.assertEqual(backend.detect(), "none")  # the fake client of base.py answers {} and not pong
        pong = self.root / "pong-wmux.js"
        pong.write_text("process.stdout.write('pong')", encoding="utf-8")
        os.environ["WMUX_CLI"] = str(pong)
        self.assertEqual(backend.detect(), "wmux")
        os.environ["WMUX_CLI"] = str(self.root / "absent.js")  # client not found: no wmux
        self.assertEqual(backend.detect(), "none")

    def test_sans_wmux_rien_n_est_appele(self):
        sans_wmux()
        with mock.patch("wmuxcli.wmux") as w:
            self.assertFalse(backend.wmux("send", "--surface", "x", "text"))
            self.assertEqual(backend.wmux("list-panes", out=True), "")
            self.assertFalse(backend.notify("hello"))
            w.assert_not_called()

    def test_notification_par_commande(self):
        sans_wmux()
        sortie = self.root / "notif.txt"
        cmd = f'"{sys.executable}" -c "import os; open(r\'{sortie}\', \'w\', encoding=\'utf-8\').write(os.environ[\'SUP_MSG\'])"'
        with mock.patch.dict(os.environ, {"SUP_NOTIFY_CMD": cmd}):
            self.assertTrue(backend.notify("Supervision: trial"))
            for _ in range(50):
                if sortie.exists() and sortie.read_text(encoding="utf-8"):
                    break
                time.sleep(0.1)
        self.assertEqual(sortie.read_text(encoding="utf-8"), "Supervision: trial")


class BoiteAuxLettres(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        self.saved = os.environ.get("SUP_BACKEND")
        sans_wmux()
        server.configure(PROJECT)
        self.s = server.empty_state()
        server.apply(self.s, {"kind": "assign", "id": "A", "scope": ["src/a/*"]})

    def tearDown(self):
        if self.saved is None:
            os.environ.pop("SUP_BACKEND", None)
        else:
            os.environ["SUP_BACKEND"] = self.saved
        backend.reset()
        super().tearDown()

    def test_commandes_du_tableau_vont_dans_la_boite(self):
        self.assertTrue(server.command(self.s, "greenlight", "A"))
        textes = [m["text"] for m in self.s["mailbox"]["A"]]
        self.assertTrue(any("PUSH GO-AHEAD" in t for t in textes))
        self.assertTrue(any("greenlight" in t.lower() or "Push go-ahead" in t for t in [m["text"] for m in self.s["mailbox"]["S"]]))
        self.assertTrue(self.s["managers"]["A"]["push_ok"])
        self.assertIn("mailbox", self.s["events"][0]["msg"])

    def test_feu_vert_fusion_et_message_a_s(self):
        server.apply(self.s, {"kind": "merge", "branch": "feat/a", "manager": "A", "sha": "abc"})
        server.merge_go(self.s, "feat/a")
        self.assertIn("MERGE GO-AHEAD", self.s["mailbox"]["S"][0]["text"])
        server.answer(self.s, "S", {"text": "[Tableau de bord — message de l'utilisateur] bonjour"})
        self.assertIn("bonjour", self.s["mailbox"]["S"][-1]["text"])

    def test_ce_qui_exige_wmux_est_refuse_clairement(self):
        with self.assertRaisesRegex(ValueError, "unavailable without wmux"):
            server.answer(self.s, "A", {"key": "1"})
        with self.assertRaisesRegex(ValueError, "unavailable without wmux"):
            server.answer(self.s, "A", {"choice": "yes"})
        with self.assertRaisesRegex(ValueError, "unavailable without wmux"):
            server.fresh(self.s, "A")

    def test_boite_bornee_et_vidage(self):
        for i in range(server.MAILBOX_MAX + 5):
            server.mailbox_push(self.s, "A", f"m{i}")
        pris = server.mailbox_pop(self.s, "A")
        self.assertEqual(len(pris), server.MAILBOX_MAX)
        self.assertEqual(pris[-1]["text"], f"m{server.MAILBOX_MAX + 4}")
        self.assertEqual(server.mailbox_pop(self.s, "A"), [])

    def test_identite_par_sup_agent(self):
        self.assertEqual(server.agent_id(f"{PROJECT}/a"), "A")
        self.assertEqual(server.agent_id("autre/A"), "")
        self.assertEqual(server.agent_id(""), "")
        server.statusline(self.s, {"session_id": "s1", "_agent": f"{PROJECT}/B", "model": {"display_name": "x"}})
        self.assertEqual(self.s["sessions"]["s1"]["who"], "B")

    def test_fiche_annonce_la_boite(self):
        etat = {"project": "", "plan": "", "managers": {"A": {"id": "A", "surface": "", "workers": {}}}, "tasks": {},
                "merge_queue": [], "requests": {}, "events": [], "supervisor_notes": []}
        texte = reprise._manager(etat, etat["managers"]["A"], {}, config.load(PROJECT))
        self.assertIn("Without wmux", texte)
        self.assertIn("$S inbox", texte)


class ServeurSansWmux(IsolatedTestCase):
    """End to end: test HTTP server without wmux, queried the way an agent does (HTTP and sup.py)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.saved = os.environ.get("SUP_BACKEND")
        sans_wmux()
        server.configure(PROJECT)
        server.save(server.empty_state())
        cls.srv = server.make_server(0)
        cls.url = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        if cls.saved is None:
            os.environ.pop("SUP_BACKEND", None)
        else:
            os.environ["SUP_BACKEND"] = cls.saved
        backend.reset()
        super().tearDownClass()

    def post(self, path, body):
        req = urllib.request.Request(self.url + path, json.dumps(body).encode("utf-8"), {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.load(r)

    def sup(self, *args):
        env = dict(os.environ, SUP_URL=self.url)
        return subprocess.run([sys.executable, str(SUP / "sup.py"), "--project", PROJECT, *args], env=env,
                              capture_output=True, text=True, encoding="utf-8", timeout=60)

    def test_message_du_tableau_arrive_a_l_appel_suivant_de_l_agent(self):
        self.post("/api/report", {"kind": "assign", "id": "A", "from": "superviseur"})
        token = (self.root / "run" / PROJECT / ".token").read_text(encoding="utf-8").strip()
        req = urllib.request.Request(self.url + "/api/command", json.dumps({"action": "stop", "id": "A"}).encode("utf-8"),
                                     {"Content-Type": "application/json", "X-Sup-Token": token})
        urllib.request.urlopen(req, timeout=10).close()
        etat = json.load(urllib.request.urlopen(self.url + "/api/state", timeout=10))
        self.assertEqual(etat["backend"], "none")
        self.assertEqual(etat["mailbox_pending"].get("A"), 1)
        r = self.sup("manager", "A", "--status", "blocked")  # an ordinary call from the agent
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Messages from the board", r.stdout)
        self.assertIn("STOP", r.stdout)
        r = self.sup("manager", "A", "--progress", "10")  # read once: nothing afterwards
        self.assertNotIn("Messages from the board", r.stdout)

    def test_fiche_ecrite_des_l_inscription(self):
        self.post("/api/report", {"kind": "assign", "id": "C", "from": "superviseur"})
        fiche = self.root / "relay-supervision" / PROJECT / "C.md"
        self.assertTrue(fiche.is_file())  # without waiting for the watcher's next round
        self.assertIn("Resume — Manager C", fiche.read_text(encoding="utf-8"))

    def test_inbox_explicite_sans_doublon(self):
        self.post("/api/report", {"kind": "assign", "id": "B", "from": "superviseur"})
        token = (self.root / "run" / PROJECT / ".token").read_text(encoding="utf-8").strip()
        req = urllib.request.Request(self.url + "/api/command", json.dumps({"action": "revoke", "id": "B"}).encode("utf-8"),
                                     {"Content-Type": "application/json", "X-Sup-Token": token})
        urllib.request.urlopen(req, timeout=10).close()
        r = self.sup("inbox", "B")
        self.assertEqual(r.stdout.count("Push go-ahead REVOKED"), 1, r.stdout + "|" + r.stderr)
        self.assertNotIn("=== Messages", r.stdout)
        self.assertIn("[]", self.sup("inbox", "B").stdout)

    def test_navigateur_sup_py_indisponible_sans_wmux(self):
        r = self.sup("browser", "A", "open", "http://localhost:3000/")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("without wmux", r.stderr)


class LancementSansWmux(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        self.saved = os.environ.get("SUP_BACKEND")
        sans_wmux()

    def tearDown(self):
        if self.saved is None:
            os.environ.pop("SUP_BACKEND", None)
        else:
            os.environ["SUP_BACKEND"] = self.saved
        backend.reset()
        super().tearDown()

    def test_inscription_sans_panneau_et_identites(self):
        cfg = dict(config.load(PROJECT), managers=["A", "D"], integrator="D", lite=False)
        a = argparse.Namespace(no_browser=True, dry_run=True, no_panes=False)
        out = io.StringIO()
        with redirect_stdout(out):
            launch.plain_steps(cfg, a, PROJECT, "http://127.0.0.1:1", {"managers": {}}, {"A": "agents-1"})
        texte = out.getvalue()
        self.assertIn("assign A --session agents-1", texte)
        self.assertIn("assign D --role integrator", texte)
        for x in ("S", "A", "D"):
            self.assertIn(f"SUP_AGENT={PROJECT}/{x}", texte)
        self.assertIn("SUP_NOTIFY_CMD", texte)

    def test_mode_leger_seul_s_a_une_identite(self):
        cfg = dict(config.load(PROJECT), managers=["A"], integrator="A", lite=True)
        a = argparse.Namespace(no_browser=True, dry_run=True, no_panes=False)
        out = io.StringIO()
        with redirect_stdout(out):
            launch.plain_steps(cfg, a, PROJECT, "http://127.0.0.1:1", {"managers": {}}, {})
        texte = out.getvalue()
        self.assertIn(f"SUP_AGENT={PROJECT}/S", texte)
        self.assertNotIn(f"SUP_AGENT={PROJECT}/A", texte)


class HooksSansWmux(IsolatedTestCase):
    """Resume sheet injected and push guard through SUP_AGENT, without WMUX_SURFACE_ID."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        out = cls.root / "relay-supervision"
        (out / PROJECT).mkdir(parents=True)
        (out / PROJECT / "A.md").write_text("# Resume — Manager A\nMARQUEUR-SANS-WMUX\n", encoding="utf-8")
        (out / "index.json").write_text("{}", encoding="utf-8")
        (out / f"{PROJECT}.guard.json").write_text(json.dumps({"base": "dev", "managers": {"A": {"role": "", "branch": "feat/a"}}}),
                                                  encoding="utf-8")

    def lancer(self, script, stdin, agent, surface=None):
        env = dict(os.environ)
        env.pop("WMUX_SURFACE_ID", None)
        if surface:
            env["WMUX_SURFACE_ID"] = surface
        if agent:
            env["SUP_AGENT"] = agent
        return subprocess.run(["node", str(script)], input=stdin, capture_output=True, text=True, encoding="utf-8", env=env,
                              timeout=30)

    def test_fiche_injectee_par_sup_agent(self):
        hook = RELAY_ROOT / "TOOLS/supervision/session-sheet.js"
        stdin = json.dumps({"cwd": str(self.root), "source": "clear"})
        ctx = json.loads(self.lancer(hook, stdin, f"{PROJECT}/A").stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("MARQUEUR-SANS-WMUX", ctx)
        ctx = json.loads(self.lancer(hook, stdin, f"{PROJECT}/A", surface="surf-inconnu").stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("MARQUEUR-SANS-WMUX", ctx)  # unregistered pane: the identity is the fallback
        for mauvais in (None, "inconnu/Z", "../evil/A", f"{PROJECT}/A/../.."):
            ctx = json.loads(self.lancer(hook, stdin, mauvais).stdout)["hookSpecificOutput"]["additionalContext"]
            self.assertNotIn("MARQUEUR-SANS-WMUX", ctx, mauvais)

    def test_garde_de_push_par_sup_agent(self):
        garde = SUP / "supervision-guard.js"

        def push(cmd, agent):
            return self.lancer(garde, json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": str(self.root)}), agent)

        self.assertEqual(push("git push origin HEAD:dev", f"{PROJECT}/A").returncode, 2)
        self.assertEqual(push("git push origin feat/a", f"{PROJECT}/A").returncode, 0)
        self.assertEqual(push("git push origin HEAD:dev", None).returncode, 0)  # no identity: outside supervision


if __name__ == "__main__":
    unittest.main()
