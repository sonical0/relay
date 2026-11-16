"""hostload.py: limits derived from the host, hysteresis brakes, directory semaphore, orphans; wiring into
server.apply (worker cap, brakes), cloture.proposals (orphan processes) and sup.py (install, slot).

No real process is listed or stopped: PROCS and KILL are injected, the "dead holders" are impossible PIDs.
Slots are created under the temp root of tests/base.py.
"""
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.base import PROJECT, SUP, IsolatedTestCase

import cloture
import hostload
import server

DEAD_PID = 2**31 - 5  # no process has this PID


class Profil(unittest.TestCase):
    def test_poste_modeste(self):
        p = hostload.profile(4, 8)
        self.assertEqual((p["tier"], p["npm_ci"], p["lite_suggested"]), ("small", 1, True))
        self.assertEqual(p["workers_total"], 2)

    def test_poste_12_threads(self):  # 12 threads, 31.6 GB: a laptop that saturated
        p = hostload.profile(12, 31.6)
        self.assertEqual((p["tier"], p["npm_ci"], p["workers_total"], p["lite_suggested"]), ("normal", 2, 6, False))

    def test_peu_de_ram_suffit_pour_conseiller_le_mode_leger(self):
        self.assertTrue(hostload.profile(16, 8)["lite_suggested"])

    def test_la_config_prime(self):
        p = hostload.profile(12, 31.6, {"workers_total": 3, "npm_ci_max": 1})
        self.assertEqual((p["workers_total"], p["npm_ci"]), (3, 1))

    def test_host_limits_false_leve_les_plafonds(self):
        p = hostload.profile(4, 8, {"host_limits": False})
        self.assertEqual((p["workers_total"], p["npm_ci"]), (None, None))

    def test_ram_inconnue(self):
        self.assertEqual(hostload.profile(12, None)["tier"], "normal") if hostload.ram_gb() is None else None
        self.assertFalse(hostload.profile(12, 32)["lite_suggested"])


class Frein(unittest.TestCase):
    def test_serre_apres_la_fenetre_puis_desserre_apres_la_pause(self):
        b = hostload.Brake(90, 75, hold=300, release_hold=60)
        self.assertFalse(b.update(95, 0))
        self.assertFalse(b.update(99, 299))
        self.assertTrue(b.update(95, 300))
        self.assertTrue(b.on)
        self.assertFalse(b.update(80, 400))   # between the two thresholds: nothing changes
        self.assertFalse(b.update(70, 410))
        self.assertFalse(b.update(70, 469))
        self.assertTrue(b.update(70, 470))
        self.assertFalse(b.on)

    def test_un_pic_court_ne_serre_pas(self):
        b = hostload.Brake(90, 75, hold=300)
        b.update(100, 0)
        b.update(50, 100)      # fell back: the window restarts from zero
        self.assertFalse(b.update(100, 350))
        self.assertFalse(b.on)

    def test_un_pic_pendant_le_relachement_annule_la_pause(self):
        b = hostload.Brake(90, 75, hold=10, release_hold=60)
        b.update(95, 0)
        b.update(95, 10)
        self.assertTrue(b.on)
        b.update(50, 20)
        b.update(85, 50)       # back above the low threshold: the release countdown restarts
        self.assertFalse(b.update(50, 100))
        self.assertTrue(b.on)

    def test_mesure_manquante(self):
        b = hostload.Brake(90, 75, hold=0)
        self.assertFalse(b.update(None, 5))
        self.assertFalse(b.on)

    def test_seuils_de_la_config(self):
        br = hostload.brakes({"cpu_brake": 70, "cpu_release": 50, "cpu_window": 10, "mem_brake": 95})
        self.assertEqual((br["cpu"].high, br["cpu"].low, br["cpu"].hold, br["mem"].high), (70, 50, 10, 95))


class Places(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="sup-test-slots-")) / "slots"

    def tearDown(self):
        import shutil
        shutil.rmtree(self.root.parent, ignore_errors=True)

    def test_une_place_a_la_fois(self):
        s = hostload.Slots(self.root, "install", 1)
        a = s.try_acquire("A")
        self.assertIsNotNone(a)
        self.assertIsNone(s.try_acquire("B"))
        s.release(a)
        self.assertIsNotNone(s.try_acquire("B"))

    def test_deux_places(self):
        s = hostload.Slots(self.root, "install", 2)
        self.assertIsNotNone(s.try_acquire("A"))
        self.assertIsNotNone(s.try_acquire("B"))
        self.assertIsNone(s.try_acquire("C"))

    def test_place_d_un_processus_mort_reprise(self):
        s = hostload.Slots(self.root, "install", 1)
        s.try_acquire("A", pid=DEAD_PID)   # an orphan npm ci
        got = s.try_acquire("B")
        self.assertIsNotNone(got)
        self.assertEqual(json.loads((got / "owner").read_text(encoding="utf-8"))["who"], "B")

    def test_place_trop_vieille_reprise(self):
        s = hostload.Slots(self.root, "install", 1)
        s.try_acquire("A")  # live PID (ours), but the clock moves forward
        self.assertIsNone(s.try_acquire("B"))
        self.assertIsNotNone(s.try_acquire("B", now=__import__("time").time() + hostload.SLOT_STALE_SECONDS + 5))

    def test_acquire_attend_et_laisse_une_trace_visible(self):
        s = hostload.Slots(self.root, "install", 1)
        held = s.try_acquire("A")
        seen, said = [], []

        def sleep(_):
            seen.append(hostload.slot_state(self.root))
            s.release(held)   # the slot frees up while waiting

        got = s.acquire("B", poll=0, say=said.append, sleep=sleep)
        self.assertTrue(got.is_dir())
        self.assertEqual(seen[0]["held"][0]["who"], "A")
        self.assertEqual(seen[0]["waiting"][0]["who"], "B")
        self.assertEqual(hostload.slot_state(self.root)["waiting"], [])   # trace removed
        self.assertIn("waiting for my turn", said[0])

    def test_etat_ignore_les_morts(self):
        s = hostload.Slots(self.root, "install", 2)
        s.try_acquire("A", pid=DEAD_PID)
        s.try_acquire("B")
        st = hostload.slot_state(self.root)
        self.assertEqual([x["who"] for x in st["held"]], ["B"])

    def test_pid_alive(self):
        self.assertTrue(hostload.pid_alive(os.getpid()))
        self.assertFalse(hostload.pid_alive(DEAD_PID))
        self.assertFalse(hostload.pid_alive(None))


class Orphelins(unittest.TestCase):
    PROCS = [
        {"pid": 10, "name": "node.exe", "cmd": r'node "C:\wt\app-b1\node_modules\next\dist\bin\next" build'},
        {"pid": 11, "name": "node.exe", "cmd": r"node C:\wt\app-b2\node_modules\.bin\tsc"},
        {"pid": 12, "name": "node.exe", "cmd": r"node C:\wt\app-b10\node_modules\x"},   # prefix: not the same worktree
        {"pid": 13, "name": "node.exe", "cmd": "node server.js"},
    ]

    def test_seuls_les_chemins_disparus_comptent(self):
        out = hostload.orphans(self.PROCS, [r"C:\wt\app-b1"])
        self.assertEqual([o["pid"] for o in out], [10])

    def test_slash_et_casse(self):
        out = hostload.orphans(self.PROCS, ["c:/WT/app-b2/"])
        self.assertEqual([o["pid"] for o in out], [11])

    def test_son_propre_processus_jamais(self):
        self.assertEqual(hostload.orphans(self.PROCS, [r"C:\wt\app-b1"], own_pid=10), [])

    def test_rien_si_aucun_worktree_disparu(self):
        self.assertEqual(hostload.orphans(self.PROCS, []), [])


class Serveur(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        server.configure(PROJECT)
        self.s = server.empty_state()
        for m in "AB":
            server.apply(self.s, {"kind": "assign", "id": m, "scope": [f"src/{m.lower()}/*"]})
        self.saved = (server.WORKERS_TOTAL, dict(server.BRAKES), server.MAX_WORKERS)

    def tearDown(self):
        server.WORKERS_TOTAL, server.BRAKES, server.MAX_WORKERS = self.saved
        super().tearDown()

    def work(self, wid, task="t"):
        server.apply(self.s, {"kind": "worker", "id": wid, "status": "working", "task": task})

    def test_pas_de_plafond_en_test_sans_config(self):
        self.assertIsNone(server.WORKERS_TOTAL)

    def test_plafond_de_workers_au_total(self):
        server.WORKERS_TOTAL = 3
        self.work("A1")
        self.work("A2")
        self.work("B1")
        with self.assertRaisesRegex(ValueError, "3 active workers on the host"):
            self.work("B2")
        self.assertNotIn("B2", self.s["managers"]["B"]["workers"])

    def test_un_worker_deja_actif_n_est_pas_refuse(self):
        server.WORKERS_TOTAL = 1
        self.work("A1")
        self.work("A1", "other task")   # update of the existing one

    def test_frein_cpu_reduit_les_workers_par_manager(self):
        server.MAX_WORKERS = 4
        self.work("A1")
        self.work("A2")
        server.BRAKES["cpu"].on, server.BRAKES["cpu"].value = True, 97
        self.assertEqual(server.worker_limit(self.s), server.BRAKED_WORKERS)
        self.assertEqual(server.host_braked(), "CPU")
        with self.assertRaisesRegex(ValueError, "host CPU brake"):
            self.work("A3")
        codes = [a["code"] for a in server.alerts(self.s)]
        self.assertIn("host_cpu", codes)
        server.BRAKES["cpu"].on = False
        self.assertNotIn("host_cpu", [a["code"] for a in server.alerts(self.s)])
        self.work("A3")

    def test_frein_memoire(self):
        server.BRAKES["mem"].on, server.BRAKES["mem"].value = True, 93
        self.assertEqual(server.host_braked(), "memory")
        self.assertIn("host_mem", [a["code"] for a in server.alerts(self.s)])
        server.BRAKES["mem"].on = False

    def test_les_nouveaux_signaux_ont_un_remede(self):
        import signals
        self.assertIn("host_cpu", signals.CODES)
        self.assertIn("host_mem", signals.CODES)


class Cloture(IsolatedTestCase):
    @classmethod
    def tearDownClass(cls):
        for p in (cls.root / "depot").rglob("*") if (cls.root / "depot").exists() else ():  # read-only git objects (Windows)
            if p.is_file():
                os.chmod(p, stat.S_IWRITE | stat.S_IREAD)
        super().tearDownClass()

    def test_processus_orphelin_propose_puis_arrete_via_kill_injecte(self):
        server.configure(PROJECT)
        repo = Path(server.CFG["path"])
        run = lambda *a: subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(repo), *a],
                                        capture_output=True, text=True, check=True)
        run("init", "-q", "-b", "dev")
        (repo / "f.txt").write_text("x", encoding="utf-8")
        run("add", "f.txt")
        run("commit", "-q", "-m", "init")
        wt = self.root / "wt-mort"
        run("worktree", "add", "-q", str(wt), "-b", "feat/mort")
        import shutil
        shutil.rmtree(wt)   # worktree deleted by hand: git still lists it
        listed = cloture._worktrees(repo)[1]["path"]   # the path as git gives it: the one in command lines
        old = cloture.PROCS
        cloture.PROCS = lambda: [{"pid": 4242, "name": "node.exe", "cmd": f'node "{listed}\\node_modules\\x"'},
                                 {"pid": 4243, "name": "node.exe", "cmd": "node ailleurs.js"}]
        try:
            props = {p["id"]: p for p in cloture.proposals(server.CFG, server.CHECK_LOCK, server.PORT)}
        finally:
            cloture.PROCS = old
        self.assertIn("proc:4242", props)
        self.assertNotIn("proc:4243", props)
        self.assertEqual(props["proc:4242"]["kind"], "proc")
        self.assertTrue(props["proc:4242"]["run"])


class SupPy(IsolatedTestCase):
    def sup(self, *args):
        return subprocess.run([sys.executable, str(SUP / "sup.py"), "--project", PROJECT, *args], capture_output=True,
                              text=True, encoding="utf-8", timeout=60)

    def test_install_a_blanc(self):
        r = self.sup("install", "B", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        info = json.loads(r.stdout.splitlines()[0])
        self.assertEqual(info["slot"], "install")
        self.assertEqual(info["argv"][1:], ["ci", "--prefer-offline", "--no-audit", "--no-fund"])
        self.assertIn(info["places"], (1, 2))

    def test_slot_lance_la_commande_et_libere_la_place(self):
        r = self.sup("slot", "test", "B", "--places", "1", "--", sys.executable, "-c", "print('ok-slot')")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("ok-slot", r.stdout)
        self.assertEqual(hostload.slot_state(self.root / "run" / PROJECT / "slots"), {"held": [], "waiting": []})
        self.assertFalse(any((self.root / "run" / PROJECT / "slots").glob("test-*")))

    def test_slot_rend_le_code_de_la_commande_et_libere_aussi(self):
        r = self.sup("slot", "test", "B", "--places", "1", "--", sys.executable, "-c", "import sys; sys.exit(3)")
        self.assertEqual(r.returncode, 3)
        self.assertFalse(any((self.root / "run" / PROJECT / "slots").glob("test-*")))

    def test_slot_sans_commande(self):
        self.assertNotEqual(self.sup("slot", "test", "B", "--places", "1").returncode, 0)


if __name__ == "__main__":
    unittest.main()
