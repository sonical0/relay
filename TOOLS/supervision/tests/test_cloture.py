"""cloture.py: drafts, atomic write at the top, cleanup on a test git repository, evening reminder.

Everything happens under the temporary root of tests/base.py: repository created by git init in `depot/`, log under
SUP_LOG_DIR, note under SUP_VAULT_DIR, backup under SUP_BACKUP_DIR. No taskkill (KILL injected), no netstat
(LISTENERS injected), no real wmux (fake client from base.py).
"""
import datetime
import json
import os
import stat
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from tests.base import PROJECT, IsolatedTestCase

import cloture
import config
import server

NOW = datetime.datetime(2026, 10, 15, 18, 30)


def git(cwd, *args):
    r = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(cwd), *args],
                       capture_output=True, text=True, encoding="utf-8", creationflags=config.NO_WINDOW)
    if r.returncode:
        raise RuntimeError(f"git {args} : {r.stderr}")
    return r.stdout.strip()


class Base(IsolatedTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        server.configure(PROJECT)
        cls.kills = []
        cls._saved_fns = (cloture.LISTENERS, cloture.KILL)
        cloture.LISTENERS = lambda: {3000: 4242, 8765: 1, 9999: 7}
        cloture.KILL = lambda pid: (cls.kills.append(pid), (True, "killed (simulated)"))[1]

    @classmethod
    def tearDownClass(cls):
        cloture.LISTENERS, cloture.KILL = cls._saved_fns
        for d in (cls.root / "depot", cls.root / "origin.git"):  # objets git en lecture seule (Windows)
            for p in d.rglob("*") if d.exists() else ():
                if p.is_file():
                    os.chmod(p, stat.S_IWRITE | stat.S_IREAD)
        super().tearDownClass()


class Brouillons(Base):
    def test_entree_de_journal_au_format(self):
        s = server.empty_state()
        s["merge_queue"] = [{"branch": "feat/x", "status": "merged", "t": NOW.timestamp()}]  # today's (only the wave)
        text = cloture.log_draft(s, server.CFG, [{"who": "alert"}, {"who": "x"}], NOW)
        lines = cloture.check_log(text)
        self.assertEqual(lines[0], f"## 2026-10-15 18:30 — Claude ({PROJECT}: supervision close)")
        self.assertLessEqual(len(lines) - 1, 5)
        self.assertIn("1/1 branches merged", text)

    def test_format_refuse(self):
        ok = "## 2026-10-15 18:30 — Claude (p: s)\n"
        for bad in ("no header\n- [p] x", ok, ok + "- [p] " + "word " * 16, ok + "free text",
                    ok + "\n".join(f"- [p] bullet {i}" for i in range(6))):
            with self.assertRaises(ValueError):
                cloture.check_log(bad)

    def test_ecriture_en_tete_heure_relue(self):
        d = Path(os.environ["SUP_LOG_DIR"])
        d.mkdir(parents=True, exist_ok=True)
        f = d / f"{cloture.host()}.md"
        f.write_text("# Relay log — X\n\n> header\n\n## 2026-10-14 10:00 — Claude (p: old)\n- [p] stale\n",
                     encoding="utf-8")
        out = cloture.write_log("## 2020-01-01 00:00 — Claude (p: new)\n- [p] fresh → 1\n", NOW)
        self.assertEqual(out, f)
        t = f.read_text(encoding="utf-8")
        self.assertTrue(t.startswith("# Relay log — X\n\n> header\n\n## 2026-10-15 18:30 — Claude (p: new)\n"))
        self.assertLess(t.index("new"), t.index("old"))
        self.assertFalse(f.with_name(f.name + ".tmp").exists())
        self.assertFalse(t.startswith("﻿"))

    def test_note_creee_puis_completee_en_tete(self):
        p = cloture.write_note("body 1", server.CFG, NOW)
        self.assertEqual(p, Path(os.environ["SUP_VAULT_DIR"]) / "Supervision-2026-10-15.md")
        cloture.write_note("body 2", server.CFG, NOW + datetime.timedelta(minutes=5))
        t = p.read_text(encoding="utf-8")
        self.assertTrue(t.startswith("---\n"))
        self.assertLess(t.index("body 2"), t.index("body 1"))

    def test_brouillon_de_note_reprend_sup_report(self):
        s = server.load()
        body = cloture.note_draft(s, server.CFG, NOW)
        self.assertIn("## Tasks", body)
        self.assertIn("## Today's log", body)

    def test_journal_reel_refuse(self):
        with self.assertRaises(PermissionError):
            config.guard_write(config.LOG_ROOT / "X.md")


class Menage(Base):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        repo, origin = cls.root / "depot", cls.root / "origin.git"
        git(cls.root, "init", "-q", "-b", "dev", str(repo))
        (repo / "a.txt").write_text("a\n", encoding="utf-8")
        git(repo, "add", "a.txt")
        git(repo, "commit", "-q", "-m", "init")
        git(repo, "branch", "feat/zz-12-fait")
        git(repo, "worktree", "add", "-q", str(cls.root / "wt-fait"), "feat/zz-12-fait")
        git(repo, "checkout", "-q", "-b", "feat/en-cours")
        (repo / "b.txt").write_text("b\n", encoding="utf-8")
        git(repo, "add", "b.txt")
        git(repo, "commit", "-q", "-m", "en cours")
        git(repo, "checkout", "-q", "dev")
        git(cls.root, "init", "-q", "--bare", str(origin))
        git(repo, "remote", "add", "origin", str(origin))
        git(repo, "push", "-q", "origin", "dev", "feat/zz-12-fait")
        # Merged on origin/dev only (pushed by the integrator from its worktree): local dev behind.
        git(repo, "checkout", "-q", "-b", "feat/zz-13-distant")
        (repo / "c.txt").write_text("c\n", encoding="utf-8")
        git(repo, "add", "c.txt")
        git(repo, "commit", "-q", "-m", "distant")
        git(repo, "push", "-q", "-u", "origin", "feat/zz-13-distant", "feat/zz-13-distant:dev")
        git(repo, "checkout", "-q", "dev")
        git(repo, "worktree", "add", "-q", str(cls.root / "wt-distant"), "feat/zz-13-distant")
        git(repo, "fetch", "-q", "origin")
        lock = Path(server.CHECK_LOCK)
        lock.mkdir(parents=True, exist_ok=True)
        (lock / "owner").write_text("Manager B", encoding="utf-8")
        old = min(time.time(), NOW.timestamp()) - 3600  # old for the real clock AND for NOW (otherwise fails after 19:30)
        os.utime(lock / "owner", (old, old))

    def test_1_propositions(self):
        props = {p["id"]: p for p in cloture.proposals(server.CFG, server.CHECK_LOCK, server.PORT)}
        self.assertIn("wt:feat/zz-12-fait", props)
        self.assertIn("br:feat/zz-12-fait", props)
        self.assertNotIn("br:feat/en-cours", props)
        self.assertNotIn("br:dev", props)
        self.assertIn("wt:feat/zz-13-distant", props)  # merged into origin/dev, not into local dev
        self.assertIn("br:feat/zz-13-distant", props)
        self.assertEqual(props["ff"]["ref"], "origin/dev")
        self.assertIn("merge --ff-only origin/dev", props["ff"]["cmd"])
        self.assertFalse(props["rbr:feat/zz-12-fait"]["run"])
        self.assertIn("push origin --delete", props["rbr:feat/zz-12-fait"]["cmd"])
        self.assertIn("port:3000", props)
        self.assertNotIn("port:8765", props)
        self.assertNotIn("port:9999", props)  # pas un port de dev
        self.assertIn("lock", props)
        self.assertEqual(cloture.jira(server.load(), server.CFG), [{"ticket": "ZZ-12", "branch": "feat/zz-12-fait"}])

    def test_2_execution_validee(self):
        s = server.load()
        res = cloture.execute(server, s, {"actions": [
            "br:feat/zz-12-fait", "wt:feat/zz-12-fait", "port:3000", "lock", "rbr:feat/zz-12-fait", "backup",
            "br:feat/zz-13-distant", "wt:feat/zz-13-distant", "ff", "made-up", "log", "note"],
            "log_text": "## 2026-10-15 18:30 — Claude (p: close)\n- [p] end → ok\n", "note_text": "note"}, NOW)
        server.save(s, "User")
        by = {r["id"]: r for r in res}
        for k in ("wt:feat/zz-12-fait", "br:feat/zz-12-fait", "port:3000", "lock", "backup", "log", "note",
                  "ff", "wt:feat/zz-13-distant", "br:feat/zz-13-distant"):
            self.assertTrue(by[k]["ok"], (k, by[k]))
        self.assertFalse(by["rbr:feat/zz-12-fait"]["ok"])
        self.assertFalse(by["made-up"]["ok"])
        ids = [r["id"] for r in res]
        self.assertEqual(ids[3], "ff")  # clone fast-forwarded before `branch -d`, which compares to HEAD
        self.assertLess(ids.index("wt:feat/zz-12-fait"), ids.index("br:feat/zz-12-fait"))  # worktree d'abord
        self.assertEqual(git(self.root / "depot", "rev-parse", "dev"), git(self.root / "depot", "rev-parse", "origin/dev"))
        self.assertEqual(self.kills, [4242])
        self.assertFalse((self.root / "wt-fait").exists())
        self.assertNotIn("feat/zz-12-fait", git(self.root / "depot", "branch", "--list"))
        self.assertIn("feat/zz-12-fait", git(self.root / "depot", "branch", "-r"))  # remote: never touched
        self.assertFalse(Path(server.CHECK_LOCK).exists())
        self.assertTrue((Path(os.environ["SUP_BACKUP_DIR"]) / "2026-10-15" / f"supervision-{PROJECT}.db").is_file())
        st = server.load()
        self.assertEqual(st["cloture"]["done"], "2026-10-15")
        self.assertIn("End-of-day close", st["events"][0]["msg"])
        bilan = (s.get("mailbox") or {}).get("S") or []  # without wmux: the supervisor's mailbox
        self.assertTrue(bilan, "the supervisor must receive the close summary")
        self.assertIn("End-of-day close confirmed", bilan[-1]["text"])
        self.assertIn("push origin --delete feat/zz-12-fait", bilan[-1]["text"])  # what is left to do by hand

    def test_3_rien_de_coche(self):
        with self.assertRaises(ValueError):
            cloture.execute(server, server.load(), {"actions": []}, NOW)


class Rappel(Base):
    def test_rappel_une_fois_par_jour(self):
        server.CFG["cloture_reminder"] = "18:00"
        saved = server.NOTIFY
        server.NOTIFY = True  # to the fake wmux of base.py
        try:
            self.assertFalse(cloture.tick(server, datetime.datetime(2026, 10, 16, 17, 59)))
            self.assertTrue(cloture.tick(server, datetime.datetime(2026, 10, 16, 18, 1)))
            self.assertFalse(cloture.tick(server, datetime.datetime(2026, 10, 16, 19, 0)))
        finally:
            server.NOTIFY = saved
        self.assertTrue(any(c[0] == "notify" and "close the day" in c[1] for c in self.wmux_calls()))
        st = cloture.status(server.load(), server.CFG, datetime.datetime(2026, 10, 16, 19, 0))
        self.assertEqual(st, {"at": "18:00", "reminded": True, "done": False, "banner": True})
        self.assertFalse(cloture.status(server.load(), server.CFG, datetime.datetime(2026, 10, 17, 9, 0))["banner"])

    def test_heure_configurable(self):
        self.assertEqual(cloture.reminder_at({"cloture_reminder": "17:15"}), datetime.time(17, 15))
        self.assertEqual(cloture.reminder_at({}), datetime.time(18, 0))


class Http(Base):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.srv = server.make_server(0)
        cls.url = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        super().tearDownClass()

    def post(self, body, token):
        req = urllib.request.Request(self.url + "/api/cloture", json.dumps(body).encode("utf-8"),
                                     {"Content-Type": "application/json", "X-Sup-Token": token})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)

    def test_page_et_validation(self):
        with urllib.request.urlopen(self.url + "/api/cloture", timeout=30) as r:
            d = json.load(r)
        for k in ("log", "note", "backup", "cleanup", "jira", "state"):
            self.assertIn(k, d)
        cloture.check_log(d["log"]["text"])
        with urllib.request.urlopen(self.url + "/api/state", timeout=10) as r:
            self.assertIn("banner", json.load(r)["cloture"])
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.post({"actions": ["backup"]}, "faux")
        self.assertEqual(e.exception.code, 403)
        e.exception.close()
        out = self.post({"actions": ["backup"]}, server.TOKEN)
        self.assertTrue(out["result"][0]["ok"], out)
        self.assertIn("End-of-day close", server.load()["events"][0]["msg"])


class Vidage(Base):
    """The close clears the board for the next wave."""

    def setUp(self):
        super().setUp()
        s = server.load()
        server.apply(s, {"kind": "reset", "from": "test"})
        for mid in ("A", "B"):
            server.apply(s, {"kind": "assign", "id": mid, "session": f"sess-{mid}", "surface": f"surf-{mid}",
                             "scope": [f"src/{mid.lower()}/*"], "domain": "d"})
        server.apply(s, {"kind": "manager", "id": "A", "task": "T1 x", "branch": "feat/a", "status": "idle"})
        server.apply(s, {"kind": "worker", "id": "A1", "task": "t", "status": "done", "verified": True})
        server.apply(s, {"kind": "task", "id": "T1", "title": "x", "manager": "A", "status": "done",
                         "criteria_add": ["c1"], "criteria_pass": [1]})
        server.apply(s, {"kind": "merge", "branch": "feat/a", "status": "merged"})
        server.apply(s, {"kind": "request", "id": "B", "what": "w"})
        server.apply(s, {"kind": "note", "id": "A", "text": "reprise d'hier"})
        server.apply(s, {"kind": "objective", "title": "vague", "tasks": ["T1"]})
        server.save(s, "test")
        self.s = server.load()

    def clore(self, actions, **body):
        res = cloture.execute(server, self.s, {"actions": actions, **body}, NOW)
        server.save(self.s, "User")
        return {r["id"]: r for r in res}

    def test_sauvegarde_puis_vidage(self):
        by = self.clore(["clear", "backup"])
        self.assertTrue(by["backup"]["ok"], by)
        self.assertTrue(by["clear"]["ok"], by)
        st = server.load()
        self.assertEqual((st["tasks"], st["merge_queue"], st["requests"]), ({}, [], {}))
        self.assertNotIn("objective", st)
        for mid in ("A", "B"):
            m = st["managers"][mid]
            self.assertEqual((m["session"], m["surface"]), (f"sess-{mid}", f"surf-{mid}"))
            self.assertEqual((m["status"], m["task"], m["workers"], m["scope"]), ("idle", "", {}, []))
            self.assertNotIn("notes", m)
        arch = server.STORE.events(kind="task_archive")
        self.assertEqual(len(arch), 1)
        self.assertIn("T1", json.loads(arch[0]["after"])["tasks"])
        self.assertTrue(any("Board cleared (close)" in e["msg"] for e in st["events"]))
        # Next wave: a reused id starts again with a single criterion, and the report only counts this wave.
        server.apply(st, {"kind": "task", "id": "T1", "title": "new", "criteria_add": ["x"]})
        self.assertEqual(st["tasks"]["T1"]["criteria"], [{"text": "x", "passes": False}])

    def test_sans_sauvegarde_refuse(self):
        before = json.dumps({k: self.s[k] for k in ("tasks", "managers", "merge_queue", "requests")}, sort_keys=True)
        by = self.clore(["clear"])
        self.assertFalse(by["clear"]["ok"])
        self.assertIn("backup", by["clear"]["msg"])
        st = server.load()
        self.assertEqual(json.dumps({k: st[k] for k in ("tasks", "managers", "merge_queue", "requests")}, sort_keys=True), before)
        self.assertFalse(server.STORE.events(kind="task_archive"))

    def test_sauvegarde_ratee_refuse(self):
        saved = server.STORE.backup
        server.STORE.backup = lambda dest: (_ for _ in ()).throw(OSError("disk full"))
        try:
            by = self.clore(["backup", "clear"])
        finally:
            server.STORE.backup = saved
        self.assertFalse(by["backup"]["ok"])
        self.assertFalse(by["clear"]["ok"])
        self.assertIn("T1", server.load()["tasks"])

    def test_worker_actif_refuse_sans_forcer(self):
        server.apply(self.s, {"kind": "worker", "id": "B1", "task": "t", "status": "working"})
        by = self.clore(["backup", "clear"])
        self.assertFalse(by["clear"]["ok"])
        self.assertIn("worker B1", by["clear"]["msg"])
        self.assertIn("T1", server.load()["tasks"])
        self.s = server.load()
        by = self.clore(["backup", "clear"], force_clear=True)
        self.assertTrue(by["clear"]["ok"], by)
        self.assertIn("forced", by["clear"]["msg"])
        self.assertEqual(server.load()["tasks"], {})

    def test_verrou_tenu_refuse_sans_forcer(self):
        lock = Path(server.CHECK_LOCK)
        lock.mkdir(parents=True)
        try:
            by = self.clore(["backup", "clear"])
        finally:
            lock.rmdir()
        self.assertFalse(by["clear"]["ok"])
        self.assertIn("lock", by["clear"]["msg"])
        self.assertIn("T1", server.load()["tasks"])

    def test_page_de_cloture_annonce_le_vidage(self):
        c = cloture.clear_preview(self.s, server.CHECK_LOCK)
        self.assertEqual((c["tasks"], c["queue"], c["requests"], c["managers"], c["blockers"]), (1, 1, 1, 2, []))


class CompteRenduDeLaVague(Base):
    """A task from the day before (or from before the last clearing) does not count."""

    def test_tache_de_la_veille_absente(self):
        import sup
        s = server.empty_state()
        hier, auj = NOW.timestamp() - 86400, NOW.timestamp() - 3600
        s["tasks"] = {"T1": {"id": "T1", "title": "stale", "manager": "C", "status": "done", "note": "", "updated": hier},
                      "T2": {"id": "T2", "title": "fresh", "manager": "A", "status": "doing", "note": "", "updated": auj}}
        s["merge_queue"] = [{"branch": "feat/stale", "sha": "", "manager": "C", "note": "", "status": "merged", "t": hier}]
        body = cloture.note_draft(s, server.CFG, NOW)
        self.assertIn("0/1 tasks done", body)
        self.assertIn("0/0 branches merged", body)
        self.assertNotIn("stale", body)
        self.assertIn("fresh", body)
        log = cloture.log_draft(s, server.CFG, [], NOW)
        self.assertIn("0/1 tasks done", log)
        s["cleared_t"] = auj + 1  # cleared after T2: nothing left of the previous wave
        self.assertEqual(sup.wave(s, cloture._day_start(NOW)), ([], [], []))
