"""store.py: schema, WAL, append-only events, concurrency, state.json migration, /api/state, backup, worktree
removal. Everything under the test root (IsolatedTestCase); the migration uses a synthetic state.json fixture."""
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

from tests.base import PROJECT, REAL, SUP, IsolatedTestCase

import server
import store

TABLES = {"meta", "managers", "workers", "tasks", "merge_queue", "requests", "files", "sessions", "quota",
          "events", "waves", "days"}


def write_fixture_state(dst):
    """Synthetic state.json in the historical shape (what the server wrote before SQLite)."""
    state = store.empty_state()
    state.update({
        "project": "example-app", "updated": 1700000000,
        "managers": {"A": {"id": "A", "status": "working", "role": "", "branch": "feat/a", "task": "TASK-1 login",
                           "progress": 40, "updated": 1700000000, "files": ["src/a.js"], "real_files": [],
                           "workers": {"A1": {"status": "working", "model": "sonnet", "task": "tests",
                                              "verified": False, "updated": 1700000000}}}},
        "tasks": {"T1": {"id": "T1", "manager": "A", "status": "doing", "title": "TASK-1 login",
                         "updated": 1700000000}},
        "events": [{"t": 1700000100, "who": "Manager A", "msg": "worker A1 started", "level": "info"},
                   {"t": 1700000000, "who": "Manager A", "msg": "started", "level": "info"}],
        "merge_queue": [{"branch": "feat/a", "sha": "abc1234", "manager": "A", "status": "queued", "t": 1700000000}],
    })
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    return state


class Base(IsolatedTestCase):
    def open(self, name):
        st = store.Store.open(self.root / "run" / name, log=lambda m: None)
        self.addCleanup(st.close)
        return st

    @staticmethod
    def sample():
        s = store.empty_state()
        s["project"] = "essai"
        s["plan"] = "PLANS/vague-1.md"
        s["supervisor"] = {"surface": "surf-s", "session": "agents-1", "dashboard": "surf-d"}
        s["managers"]["A"] = {"id": "A", "status": "working", "role": "", "scope": ["src/a/*"], "files": ["src/a/x.js"],
                              "real_files": ["src/a/x.js", "src/a/y.js"], "worktrees": ["C:/wt/a"], "history": [[1, 2, 3]],
                              "updated": 1, "workers": {"A2": {"id": "A2", "status": "working", "model": "sonnet"},
                                                        "A1": {"id": "A1", "status": "done", "verified": True}}}
        s["managers"]["B"] = {"id": "B", "status": "idle", "files": [], "real_files": [], "workers": {}, "updated": 2}
        s["tasks"] = {"T2": {"id": "T2", "title": "deux", "manager": "B"}, "T1": {"id": "T1", "title": "un"}}
        s["requests"] = {"R1": {"id": "R1", "from": "A", "what": "x", "files": ["package.json"], "status": "open"}}
        s["merge_queue"] = [{"branch": "feat/b", "sha": "1", "status": "waiting"}, {"branch": "feat/a", "sha": "2"}]
        s["sessions"] = {"s1": {"who": "A", "ctx": 12.5, "cost": 1.2, "t": int(time.time())}}
        s["quota"] = {"five_hour": {"used_percentage": 40, "resets_at": 9999999999, "t": 1}}
        s["supervisor_notes"] = [{"t": 1, "text": "note"}]
        return s


class Schema(Base):
    def test_tables_et_wal(self):
        st = self.open("schema")
        names = {r[0] for r in st.query("SELECT name FROM sqlite_master WHERE type = 'table'")}
        self.assertTrue(TABLES <= names, TABLES - names)
        self.assertEqual(st.query("PRAGMA journal_mode")[0][0], "wal")
        self.assertEqual(st.path, self.root / "run" / "schema" / "supervision.db")

    def test_aller_retour(self):
        st = self.open("aller-retour")
        s = self.sample()
        st.save(json.loads(json.dumps(s)), actor="test")
        st.close()
        back = self.open("aller-retour").load()
        for k in s:
            if k != "updated":
                self.assertEqual(back[k], s[k], k)
        self.assertEqual(list(back["managers"]["A"]["workers"]), ["A2", "A1"])  # insertion order kept
        self.assertEqual(list(back["tasks"]), ["T2", "T1"])

    def test_ecriture_refusee_hors_racine(self):
        with self.assertRaises(PermissionError):
            store.Store(REAL["run/"] / "interdit" / "x.db")


class Events(Base):
    def test_ajout_seul(self):
        st = self.open("ajout-seul")
        st.append_event("test", "first")
        for sql in ("UPDATE events SET msg = 'x'", "DELETE FROM events"):
            with self.assertRaises(sqlite3.DatabaseError):
                st._conn.execute(sql)
        self.assertEqual(st.query("SELECT msg FROM events")[0][0], "first")

    def test_chaque_changement_une_ligne(self):
        server.configure(PROJECT)
        st = self.open("changements")
        s = st.load()
        server.manager(s, "A")
        st.save(s, actor="superviseur")
        s = st.load()
        server.apply(s, {"kind": "manager", "from": "Manager A", "id": "A", "status": "working", "progress": 40})
        st.save(s, actor="Manager A")
        rows = {(e["subject"], e["before"], e["after"], e["who"]) for e in st.events(kind="change")}
        self.assertIn(("manager:A.status", '"idle"', '"working"', "Manager A"), rows)
        self.assertIn(("manager:A.progress", "0", "40", "Manager A"), rows)
        self.assertIn("manager:A", {r[0] for r in rows})  # card created
        self.assertFalse(any(r[0].endswith((".updated", ".history")) for r in rows))
        n = len(st.events())
        st.save(st.load(), actor="rien")  # nothing changed: no row
        self.assertEqual(len(st.events()), n)

    def test_journal_jamais_tronque_et_remise_a_zero(self):
        server.configure(PROJECT)
        st = self.open("journal")
        s = st.load()
        for i in range(350):
            server.log(s, "test", f"message {i}")
        st.save(s)
        s = st.load()
        self.assertEqual(len(s["events"]), store.EVENTS_VIEW)
        self.assertEqual(s["events"][0]["msg"], "message 349")
        self.assertEqual(len(st.events(kind="log")), 350)
        server.apply(s, {"kind": "reset", "from": "test"})
        st.save(s)
        self.assertEqual([e["msg"] for e in st.load()["events"]], ["State reset"])
        self.assertEqual(len(st.events(kind="log")), 351)

    def test_vagues_et_jours(self):
        st = self.open("vagues")
        s = st.load()
        s["plan"] = "PLANS/v1.md"
        st.save(s)
        s = st.load()
        s["plan"] = "PLANS/v2.md"
        st.save(s)
        waves = st.query("SELECT name, end_t IS NULL FROM waves ORDER BY id")
        self.assertEqual(waves, [("v1", 0), ("v2", 1)])
        self.assertEqual(len(st.query("SELECT * FROM days")), 1)

    def test_concurrence_entre_threads(self):
        server.configure(PROJECT)
        st = self.open("concurrence")
        errors = []

        def writer(n):
            try:
                for i in range(25):
                    s = st.load()
                    server.manager(s, f"M{n}")["progress"] = i
                    server.log(s, f"t{n}", f"{n}-{i}")
                    st.save(s, actor=f"t{n}")
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        def reader():
            try:
                for _ in range(100):
                    json.dumps(st.load())
            except Exception as e:  # noqa: BLE001
                errors.append(e)
        threads = [threading.Thread(target=writer, args=(n,)) for n in range(8)] + [threading.Thread(target=reader) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
        self.assertEqual(errors, [])
        self.assertEqual(len(st.events(kind="log")), 200)
        self.assertEqual(st.query("PRAGMA integrity_check")[0][0], "ok")


class Migration(Base):
    def test_import_de_la_copie_du_vrai_state(self):
        d = self.root / "run" / "mig"
        orig = write_fixture_state(d / "state.json")
        st = self.open("mig")
        self.assertTrue((d / "supervision.db").is_file())
        self.assertTrue((d / "state.json.bak").is_file())
        self.assertFalse((d / "state.json").exists())
        back = st.load()
        for k in orig:
            if k != "updated":
                self.assertEqual(back[k], orig[k], k)
        self.assertEqual(len(st.events(kind="migration")), 1)
        self.assertEqual(st.events(kind="change"), [])  # no invented change
        n = len(st.events())
        st.close()
        st = self.open("mig")  # idempotent: second start without import
        self.assertEqual(len(st.events()), n)
        self.assertEqual(st.load()["managers"], orig["managers"])

    def test_state_json_ancien_restant(self):
        d = self.root / "run" / "mig-reste"
        (d).mkdir(parents=True)
        (d / "state.json").write_text(json.dumps(self.sample()), encoding="utf-8")
        self.open("mig-reste").close()
        (d / "state.json").write_text("{}", encoding="utf-8")  # stopped between import and rename: older file
        old = time.time() - 3600
        os.utime(d / "state.json", (old, old))
        st = self.open("mig-reste")
        self.assertFalse((d / "state.json").exists())
        self.assertEqual(len([p for p in d.iterdir() if p.name.startswith("state.json.bak")]), 2)
        self.assertEqual(st.load()["project"], "essai")

    def test_state_json_plus_recent_refuse(self):
        d = self.root / "run" / "mig-retour"
        d.mkdir(parents=True)
        (d / "state.json").write_text(json.dumps(self.sample()), encoding="utf-8")
        self.open("mig-retour").close()
        (d / "state.json").write_text("{}", encoding="utf-8")
        new = time.time() + 3600
        os.utime(d / "state.json", (new, new))
        with self.assertRaisesRegex(RuntimeError, "rollback"):
            store.Store.open(d, log=lambda m: None)
        self.assertTrue((d / "state.json").exists())  # nothing touched


class Worktrees(Base):
    def setUp(self):
        super().setUp()
        server.configure(PROJECT)
        self.s = server.empty_state()
        wt = self.root / "wt-a"
        wt.mkdir(exist_ok=True)
        self.wt = str(wt.resolve())
        server.apply(self.s, {"kind": "manager", "id": "A", "worktree": self.wt})
        server.apply(self.s, {"kind": "manager", "id": "A", "worktree": str(self.root / "wt-disparu")})

    def test_retrait_manuel(self):
        server.apply(self.s, {"kind": "manager", "from": "Manager A", "id": "A", "worktree_remove": self.wt.lower()})
        self.assertEqual(self.s["managers"]["A"]["worktrees"], [str((self.root / "wt-disparu").resolve())])
        self.assertIn("worktree removed", self.s["events"][0]["msg"])
        with self.assertRaisesRegex(ValueError, "card"):
            server.apply(self.s, {"kind": "manager", "id": "A", "worktree_remove": "C:/nulle/part"})

    def test_retrait_automatique_apres_deux_releves(self):
        missing = {}
        self.assertEqual(server.prune_worktrees(self.s, missing), [])  # a single reading: wait
        self.assertEqual(len(self.s["managers"]["A"]["worktrees"]), 2)
        gone = server.prune_worktrees(self.s, missing)
        self.assertEqual(gone, [("A", str((self.root / "wt-disparu").resolve()))])
        self.assertEqual(self.s["managers"]["A"]["worktrees"], [self.wt])
        self.assertIn("vanished worktree", self.s["events"][0]["msg"])

    def test_dossier_revenu_remet_le_compteur(self):
        missing = {}
        flip = iter([False, True, False])
        server.prune_worktrees(self.s, missing, exists=lambda p: p == self.wt or next(flip))
        server.prune_worktrees(self.s, missing, exists=lambda p: p == self.wt or next(flip))
        server.prune_worktrees(self.s, missing, exists=lambda p: p == self.wt or next(flip))
        self.assertEqual(len(self.s["managers"]["A"]["worktrees"]), 2)


class Sauvegarde(Base):
    def test_backup_api(self):
        st = self.open("sauve")
        s = self.sample()
        st.save(s)
        dest = store.backup_path("sauve", "2026-01-01")
        self.assertEqual(dest, self.root / "backups" / "2026-01-01" / "supervision-sauve.db")
        st.backup(dest)
        c = sqlite3.connect(str(dest))
        try:
            self.assertEqual(c.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(c.execute("SELECT COUNT(*) FROM managers").fetchone()[0], 2)
        finally:
            c.close()

    def test_backup_refuse_hors_racine(self):
        st = self.open("sauve-refus")
        with self.assertRaises(PermissionError):
            st.backup(REAL["BACKUPS"] / "interdit" / "x.db")


class ServeurSqlite(Base):
    """Test server on SQLite: sup.py worktree removal and backup."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        server.configure(PROJECT)
        cls.srv = server.make_server(0)
        cls.url = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        super().tearDownClass()

    def get_state(self):
        with urllib.request.urlopen(self.url + "/api/state", timeout=20) as r:
            return json.load(r)

    def post(self, body):
        req = urllib.request.Request(self.url + "/api/report", json.dumps(body).encode("utf-8"),
                                     {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.load(r)

    def sup(self, *args):
        env = dict(os.environ, SUP_URL=self.url)
        return subprocess.run([sys.executable, str(SUP / "sup.py"), "--project", PROJECT, *args], env=env,
                              capture_output=True, text=True, encoding="utf-8", timeout=60)

    def test_2_sup_py_worktree_remove(self):
        wt = self.root / "wt-http"
        wt.mkdir()
        self.post({"kind": "manager", "from": "Manager Z", "id": "Z", "worktree": str(wt)})
        r = self.sup("manager", "Z", "--worktree-remove", str(wt))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.get_state()["managers"]["Z"]["worktrees"], [])
        r = self.sup("manager", "Z", "--worktree-remove", str(wt))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("card", r.stderr)
        rows = server.STORE.events(kind="change")
        self.assertTrue(any(e["subject"] == "manager:Z.worktrees" and e["who"] == "Manager Z" for e in rows))

    def test_3_sup_py_backup_serveur_en_marche(self):
        r = self.sup("backup")
        self.assertEqual(r.returncode, 0, r.stderr)
        dest = store.backup_path(PROJECT)
        self.assertEqual(r.stdout.strip(), str(dest))
        c = sqlite3.connect(str(dest))
        try:
            self.assertEqual(c.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            n = c.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        finally:
            c.close()
        self.assertEqual(n, len(server.STORE.events()))

    def test_4_sup_py_backup_sans_base(self):
        env = dict(os.environ, SUP_URL=self.url, SUP_STATE_ROOT=str(self.root / "vide"))
        r = subprocess.run([sys.executable, str(SUP / "sup.py"), "--project", PROJECT, "backup"], env=env,
                           capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("database missing", r.stderr)


if __name__ == "__main__":
    import unittest
    unittest.main()
