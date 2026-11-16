"""Shared directories (a), launch token (b), idempotent posts (c), de-duplicated alerts (d)."""
import json
import os
import shutil
import socket
import stat
import sys
import subprocess
import unittest

from tests.base import PROJECT, SUP, IsolatedTestCase

import server
import shared
import signals

GUARD = SUP / "supervision-guard.js"


def git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, timeout=30)


class DossiersPartages(IsolatedTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        main = cls.root / "principal"
        main.mkdir()
        git(main, "init", "-q")
        (main / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
        (main / "suivi").mkdir()
        (main / "suivi" / "f.txt").write_text("x", encoding="utf-8")
        (main / "node_modules" / "lodash").mkdir(parents=True)
        (main / "node_modules" / "lodash" / "package.json").write_text("{}", encoding="utf-8")
        git(main, "add", "-A")
        git(main, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")
        cls.main = main

    @classmethod
    def tearDownClass(cls):
        for wt in cls.root.glob("wt*"):  # junctions first: cleanup must not follow into the main checkout
            if shared.is_link(wt / "node_modules"):
                os.rmdir(wt / "node_modules")
        # Windows: read-only git objects
        retire = {"onexc" if sys.version_info >= (3, 12) else "onerror": lambda f, p, e: (os.chmod(p, stat.S_IWRITE), f(p))}
        shutil.rmtree(cls.main, **retire)
        super().tearDownClass()

    def worktree(self, name):
        wt = self.root / name
        wt.mkdir()
        return wt

    def test_jonction_seulement_pour_un_dossier_ignore_et_present(self):
        wt = self.worktree("wt1")
        res = dict(shared.link_shared(self.main, wt, ["node_modules", "suivi", ".next", "../hors"]))
        self.assertEqual(res, {"node_modules": "linked", "suivi": "tracked by git (not linked)", ".next": "missing from main"})
        self.assertTrue(shared.is_link(wt / "node_modules"))
        self.assertTrue((wt / "node_modules" / "lodash" / "package.json").is_file())
        self.assertEqual(dict(shared.link_shared(self.main, wt, ["node_modules"])), {"node_modules": "already shared"})
        os.rmdir(wt / "node_modules")  # removing the junction does not touch the main checkout's directory
        self.assertTrue((self.main / "node_modules" / "lodash" / "package.json").is_file())

    def test_dossier_deja_present_garde(self):
        wt = self.worktree("wt2")
        (wt / "node_modules").mkdir()
        self.assertEqual(dict(shared.link_shared(self.main, wt, ["node_modules"])), {"node_modules": "present (not linked)"})
        self.assertFalse(shared.is_link(wt / "node_modules"))

    def test_commandes_npm_qui_reecrivent(self):
        for argv, attendu in ((["npm", "ci"], True), (["npm.cmd", "install", "x"], True), (["npm", "run", "test"], False),
                              (["npm", "test"], False), (["yarn", "add", "x"], True), (["node", "x.js"], False)):
            self.assertEqual(shared.npm_mutates(argv), attendu, argv)

    def test_garde_refuse_npm_ci_dans_un_worktree_partage(self):
        wt = self.worktree("wt3")
        shared.link_shared(self.main, wt, ["node_modules"])
        js = "const g=require(process.argv[1]);console.log(JSON.stringify([g.sharedNpm(process.argv[2],process.argv[3])]))"
        for cmd, refuse in (("npm ci", True), ("npm install lodash", True), ("npm test", False), ("npm run build", False)):
            r = subprocess.run(["node", "-e", js, str(GUARD), cmd, str(wt)], capture_output=True, text=True,
                               encoding="utf-8", timeout=30)
            self.assertEqual(bool(json.loads(r.stdout)[0]), refuse, cmd)
        r = subprocess.run(["node", "-e", js, str(GUARD), "npm ci", str(self.main)], capture_output=True, text=True,
                           encoding="utf-8", timeout=30)
        self.assertIsNone(json.loads(r.stdout)[0])  # main checkout: its own node_modules


class JetonDeLancement(IsolatedTestCase):
    """The guard only applies its refusals to a session of the current launch (SUP_RUN); without a token, as before."""

    def setUp(self):
        super().setUp()
        self.dir = self.root / "etat-garde"
        self.dir.mkdir(exist_ok=True)
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen()

    def tearDown(self):
        self.sock.close()
        super().tearDown()

    def regles(self, run):
        g = {"project": "p", "base": "dev", "integrator": "D", "port": self.sock.getsockname()[1],
             "managers": {"A": {"role": "", "branch": "feat/a"}}}
        if run:
            g["run"] = run
        (self.dir / "p.guard.json").write_text(json.dumps(g), encoding="utf-8")

    def pousser(self, **env):
        e = {k: v for k, v in os.environ.items() if k not in ("SUP_AGENT", "SUP_RUN", "WMUX_SURFACE_ID")}
        e.update(env, SUP_RELAY_STATE=str(self.dir))
        r = subprocess.run(["node", str(GUARD)], input=json.dumps({"tool_input": {"command": "git push origin dev"},
                                                                    "cwd": str(self.dir)}),
                           env=e, capture_output=True, text=True, encoding="utf-8", timeout=30)
        return r.returncode

    def test_jeton(self):
        self.regles("run-2")
        self.assertEqual(self.pousser(SUP_AGENT="p/A", SUP_RUN="run-1"), 0)  # other launch: let through
        self.assertEqual(self.pousser(SUP_AGENT="p/A", SUP_RUN="run-2"), 2)  # current launch: refused
        self.assertEqual(self.pousser(SUP_AGENT="p/A"), 2)                   # old manager without token: as before
        self.regles(None)
        self.assertEqual(self.pousser(SUP_AGENT="p/A", SUP_RUN="run-1"), 2)  # old server without token: as before

    def test_jeton_stable_d_un_redemarrage_a_l_autre(self):
        server.configure(PROJECT)
        a = server.new_run_id()
        self.assertEqual(server.new_run_id(), a)  # --restart: same managers, same token
        f = server.RUN_DIR / "run.id"
        old = f.stat().st_mtime - server.RUN_ID_MAX_AGE - 10
        os.utime(f, (old, old))
        self.assertNotEqual(server.new_run_id(), a)  # a day old: new supervision


class EnvoisIdempotents(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        server.configure(PROJECT)
        self.s = server.empty_state()
        server.apply(self.s, {"kind": "assign", "id": "A", "scope": ["src/a/*"]})
        server.apply(self.s, {"kind": "manager", "id": "A", "branch": "feat/a"})
        server.apply(self.s, {"kind": "check", "id": "A", "rc": 0})

    def test_submit_renvoye_un_seul_effet(self):
        sub = {"kind": "submit", "id": "A", "sha": "abc1234", "check": "green", "files": 2, "request_id": "r1"}
        server.apply(self.s, dict(sub))
        server.apply(self.s, dict(sub))
        self.assertEqual(sum("Delivery" in e["msg"] for e in self.s["events"]), 1)
        self.assertEqual(sum("Delivery from Manager A" in m["text"] for m in self.s["mailbox"]["S"]), 1)

    def test_request_renvoyee_rend_le_meme_numero(self):
        req = {"kind": "request", "id": "A", "what": "x", "request_id": "r2"}
        self.assertEqual([server.apply(self.s, dict(req)) for _ in range(3)], ["R1", "R1", "R1"])
        self.assertEqual(len(self.s["requests"]), 1)
        self.assertEqual(server.apply(self.s, {"kind": "request", "id": "A", "what": "y"}), "R2")  # without identifier

    def test_refus_non_enregistre(self):
        sub = {"kind": "submit", "id": "A", "sha": "abc", "check": "red", "request_id": "r3"}
        with self.assertRaises(ValueError):
            server.apply(self.s, dict(sub))
        self.assertNotIn("r3", self.s.get("receipts", {}))


class AlertesDedoublonnees(unittest.TestCase):
    def a(self, code, who, msg):
        return {"level": "error", "code": code, "who": who, "msg": msg}

    def test_message_qui_change_ne_renotifie_pas(self):
        mem = {}
        self.assertEqual(len(server.notify_plan(mem, [self.a("lock_stale", "B", "depuis 16 min")], 0)), 1)
        self.assertEqual(server.notify_plan(mem, [self.a("lock_stale", "B", "depuis 17 min")], 60), [])

    def test_rafale_absorbee_vraie_reouverture_notifiee(self):
        mem = {}
        server.notify_plan(mem, [self.a("stray_browser", "?", "x")], 0)
        server.notify_plan(mem, [], 5)
        self.assertEqual(server.notify_plan(mem, [self.a("stray_browser", "?", "x")], 7), [])   # back after 2 s
        server.notify_plan(mem, [], 10)
        self.assertEqual(len(server.notify_plan(mem, [self.a("stray_browser", "?", "x")], 60)), 1)  # 50 s later

    def test_nouvelle_cle_toujours_notifiee(self):
        mem = {}
        server.notify_plan(mem, [self.a("collision", "A,B", "x")], 0)
        self.assertEqual(len(server.notify_plan(mem, [self.a("collision", "A,B", "x"), self.a("collision", "B,C", "y")], 1)), 1)

    def test_attend_distinct_de_fini(self):
        self.assertTrue(signals.AGENT_NOTIFY["waiting"])
        self.assertFalse(signals.AGENT_NOTIFY["done"])
        c = signals.Cooldown(5, clock=lambda: 0)
        self.assertTrue(c.allow(("A", "waiting"), t=0))
        self.assertTrue(c.allow(("A", "done"), t=1))  # distinct key
        self.assertFalse(c.allow(("A", "waiting"), t=3))
        self.assertTrue(c.allow(("A", "waiting"), t=6))
