"""signals.py and the failure patterns of server.alerts(): one signal per fault, deduplicated after a restart."""
import threading
import time
import unittest
from unittest import mock

from tests.base import PROJECT, IsolatedTestCase

import config
import lifecycle
import metrics
import reprise
import server
import signals


def alerte(code, who, msg="m", level="error"):
    return {"code": code, "who": who, "msg": msg, "level": level}


class Rejeu(unittest.TestCase):
    def test_replay_garde_les_ouverts(self):
        rows = [{"subject": "stall", "who": "A", "after": "open", "msg": "a", "level": "warn"},
                {"subject": "stall", "who": "A", "after": "resolved", "msg": "a", "level": "info"},
                {"subject": "collision", "who": "A,B", "after": "open", "msg": "c", "level": "error"}]
        self.assertEqual(signals.replay(rows), {("collision", "A,B"): ("c", "error")})

    def test_diff_ouvre_une_fois_ferme_quand_la_condition_cesse(self):
        open_ = {}
        to_open, to_close = signals.diff(open_, [alerte("stall", "A"), {"level": "warn", "msg": "no code"}])
        self.assertEqual([a["code"] for a in to_open], ["stall"])
        self.assertEqual(to_close, [])
        to_open, to_close = signals.diff(open_, [alerte("stall", "A", "changed message")])
        self.assertEqual((to_open, to_close), ([], []))  # same fault, other wording: not a new signal
        self.assertEqual(open_[("stall", "A")][0], "changed message")
        to_open, to_close = signals.diff(open_, [])
        self.assertEqual([k for k, _ in to_close], [("stall", "A")])
        self.assertEqual(open_, {})


class NavigateurHorsPanneau(unittest.TestCase):
    def test_confirme_a_deux_releves_de_suite(self):
        self.assertEqual(server.confirm_stray(set(), ["surf-tableau"]), [])  # first scan: not an alert yet
        self.assertEqual(server.confirm_stray({"surf-tableau"}, ["surf-tableau"]), ["surf-tableau"])
        self.assertEqual(server.confirm_stray({"surf-tableau"}, []), [])  # registered meanwhile: no more alert


class Motifs(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        server.configure(PROJECT)
        self.s = server.empty_state()
        server.apply(self.s, {"kind": "assign", "id": "A", "scope": ["src/a/*"]})
        server.apply(self.s, {"kind": "assign", "id": "D", "scope": ["src/d/*"], "role": "integrator"})

    def codes(self):
        return {(a["code"], a["who"]) for a in server.alerts(self.s) if a.get("code")}

    def test_trois_checks_rouges_de_suite(self):
        for _ in range(2):
            server.apply(self.s, {"kind": "check", "id": "A", "rc": 1})
        self.assertNotIn(("check_red", "A"), self.codes())
        server.apply(self.s, {"kind": "check", "id": "A", "rc": 1})
        self.assertIn(("check_red", "A"), self.codes())
        server.apply(self.s, {"kind": "check", "id": "A", "rc": 0})  # a green check resets
        self.assertNotIn(("check_red", "A"), self.codes())

    def test_autre_tache_remet_les_rouges_a_zero(self):
        for _ in range(3):
            server.apply(self.s, {"kind": "check", "id": "A", "rc": 1})
        server.apply(self.s, {"kind": "manager", "id": "A", "task": "T9 autre"})
        self.assertNotIn(("check_red", "A"), self.codes())

    def test_conflit_de_fusion_compte_puis_monte_en_rouge(self):
        server.apply(self.s, {"kind": "merge", "branch": "feat/a", "manager": "A", "status": "conflict"})
        niveaux = {a["code"]: a["level"] for a in server.alerts(self.s) if a.get("code")}
        self.assertEqual(niveaux["merge_conflict"], "warn")
        server.apply(self.s, {"kind": "merge", "branch": "feat/a", "status": "conflict"})
        niveaux = {a["code"]: a["level"] for a in server.alerts(self.s) if a.get("code")}
        self.assertEqual(niveaux["merge_conflict"], "error")
        server.apply(self.s, {"kind": "merge", "branch": "feat/a", "status": "merged"})
        self.assertNotIn(("merge_conflict", "A"), self.codes())

    def test_worker_sans_nouvelle(self):
        server.apply(self.s, {"kind": "worker", "id": "A1", "status": "working", "task": "x"})
        self.assertNotIn(("worker_stall", "A1"), self.codes())
        self.s["managers"]["A"]["workers"]["A1"]["updated"] = int(time.time()) - 3 * server.STALE_SECONDS
        self.assertIn(("worker_stall", "A1"), self.codes())

    def test_relances_a_neuf_repetees_sur_la_meme_tache(self):
        m = self.s["managers"]["A"]
        m["task"] = "T7"
        for _ in range(2):  # same effect as server.fresh() without touching wmux
            m["fresh_n"] = m.get("fresh_n", 0) + 1 if m.get("fresh_task") == m["task"] else 1
            m["fresh_task"] = m["task"]
        self.assertIn(("resume_loop", "A"), self.codes())
        m["task"] = "T8"  # other task: the framing is no longer at fault
        self.assertNotIn(("resume_loop", "A"), self.codes())

    def test_une_faute_une_ligne_meme_apres_redemarrage(self):
        server.apply(self.s, {"kind": "worker", "id": "A9", "status": "working", "task": "x"})  # badly named worker
        open_ = signals.replay(server.STORE.events(kind=signals.KIND))
        to_open, _ = signals.diff(open_, server.alerts(self.s))
        for a in to_open:
            server.STORE.append_event(a["who"], a["msg"], a["level"], kind=signals.KIND, subject=a["code"], after="open")
        self.assertIn(("worker_name", "A9"), {(a["code"], a["who"]) for a in to_open})
        # "restart": the table is replayed, the same fault reopens nothing
        open_ = signals.replay(server.STORE.events(kind=signals.KIND))
        self.assertEqual(signals.diff(open_, server.alerts(self.s))[0], [])
        # the fault disappears: one 'resolved' row, and the signal is no longer open
        server.apply(self.s, {"kind": "remove-worker", "id": "A9"})
        _, to_close = signals.diff(open_, server.alerts(self.s))
        for (code, who), (msg, _l) in to_close:
            server.STORE.append_event(who, msg, "info", kind=signals.KIND, subject=code, after="resolved")
        self.assertNotIn(("worker_name", "A9"), signals.replay(server.STORE.events(kind=signals.KIND)))

    def test_veilleur_ecrit_un_signal_et_ne_le_repete_pas_apres_redemarrage(self):
        server.apply(self.s, {"kind": "worker", "id": "A7", "status": "working", "task": "x"})  # badly named worker
        server.save(self.s)

        def tourner():
            """A "start" of the watcher: a few rounds, then stop."""
            lifecycle.STOP.clear()
            with mock.patch.object(server, "WATCH_SECONDS", 0.05), mock.patch.object(server, "NOTIFY", False):
                t = threading.Thread(target=server.watcher, daemon=True)
                t.start()
                time.sleep(0.6)
                lifecycle.STOP.set()
                t.join(5)
            lifecycle.STOP.clear()

        def lignes():
            return [(r["subject"], r["who"], r["after"]) for r in server.STORE.events(kind=signals.KIND)]

        tourner()
        self.assertEqual(lignes().count(("worker_name", "A7", '"open"')), 1)
        n_log = sum("Badly named worker" in e["msg"] for e in server.load()["events"])
        tourner()  # "restart": the same fault is written neither as a signal nor in the log
        self.assertEqual(lignes().count(("worker_name", "A7", '"open"')), 1)
        self.assertEqual(sum("Badly named worker" in e["msg"] for e in server.load()["events"]), n_log)

    def test_note_hebdo_compte_les_signaux_par_motif(self):
        for code in ("stall", "stall", "collision"):
            server.STORE.append_event("A", "m", "warn", kind=signals.KIND, subject=code, after="open")
        texte = metrics.weekly_note(server.STORE, PROJECT)
        self.assertIn("## Error signals per pattern", texte)
        self.assertRegex(texte, r"\| stall \| 2 \|")
        self.assertRegex(texte, r"\| collision \| 1 \|")


class ProtocoleParRole(IsolatedTestCase):
    def test_fichiers_cites_par_role(self):
        self.assertIn("supervisor.md", reprise.protocole("S"))
        self.assertNotIn("integrator.md", reprise.protocole("A"))
        self.assertIn("manager.md", reprise.protocole("A"))
        self.assertIn("integrator.md", reprise.protocole("D", integrator=True))

    def test_les_fichiers_cites_existent(self):
        base = config.RELAY_ROOT / "SKILLS" / "supervision"
        for nom in ("SKILL.md", "common.md", "supervisor.md", "manager.md", "integrator.md", "no-wmux.md", "MAINTENANCE.md"):
            self.assertTrue((base / nom).is_file(), nom)

    def test_le_tronc_commun_reste_court(self):
        # Re-injection cap after compaction: 5,000 tokens per skill.
        # The trunk reread at every start fits in 2,500 bytes; the rest is served by `$S guide`.
        taille = (config.RELAY_ROOT / "SKILLS" / "supervision" / "SKILL.md").stat().st_size
        self.assertLessEqual(taille, 2500)

    def test_preambules_courts(self):
        for mid, integ in (("A", False), ("D", True), ("S", False)):
            self.assertLessEqual(len(reprise.preamble(mid, integ).encode("utf-8")), reprise.PREAMBLE_MAX, mid)


if __name__ == "__main__":
    unittest.main()
