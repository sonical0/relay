"""prereview.py (stage 1) on git repositories created in the test root, then the server wiring.

A bare "origin" repository + a working clone per class, under the temporary root (never a real repository);
each test works on its own branches (git is slow on Windows: one repository per test cost 8 s).
"""
import itertools
import os
import stat
import subprocess
import time

from tests.base import PROJECT, IsolatedTestCase

import config
import prereview
import server

_n = itertools.count()
OK_LOG = "✔ one test\nℹ fail 0\n"


def git(cwd, *a):
    r = subprocess.run(["git", "-c", "user.name=test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
                        "-c", "init.defaultBranch=dev", "-c", "core.hooksPath=", *a], cwd=str(cwd), capture_output=True,
                       text=True, encoding="utf-8", creationflags=config.NO_WINDOW)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(a)} : {r.stderr}")
    return r.stdout.strip()


def writable(root):
    """git makes its objects read-only: on Windows, deleting the test root would fail."""
    for dirpath, _, files in os.walk(root):
        for f in files:
            os.chmod(os.path.join(dirpath, f), stat.S_IREAD | stat.S_IWRITE)


class Depot:
    """bare origin + working clone; base branch dev with a first pushed commit."""

    def __init__(self, root):
        base = root / f"git{next(_n)}"
        self.origin, self.work = base / "origin.git", base / "work"
        self.origin.mkdir(parents=True)
        git(self.origin, "init", "--bare", "-q")
        git(base, "clone", "-q", str(self.origin), str(self.work))
        self.commit("README.md")
        git(self.work, "push", "-q", "origin", "HEAD:dev")
        git(self.work, "fetch", "-q", "origin")

    def commit(self, path):
        f = self.work / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(f"{path} {time.time()} {next(_n)}\n", encoding="utf-8")
        git(self.work, "add", path)
        git(self.work, "commit", "-q", "-m", f"modifie {path}")
        return git(self.work, "rev-parse", "HEAD")

    def branch(self, name, start):
        git(self.work, "checkout", "-q", "-B", name, start)

    def push(self, name):
        git(self.work, "push", "-q", "-f", "origin", f"{name}:{name}")  # also updates origin/<name>, without fetch


class GitTestCase(IsolatedTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.d = Depot(cls.root)

    @classmethod
    def tearDownClass(cls):
        if cls.root:
            writable(cls.root)
        super().tearDownClass()

    def feature(self, base="origin/dev", path="src/a/x.js"):
        name = f"feat/a{next(_n)}"
        self.d.branch(name, base)
        self.d.commit(path)
        self.d.push(name)
        return name

    def fresh_check(self, path, text=OK_LOG, later=True):
        path.write_text(text, encoding="utf-8")
        t = time.time() + (5 if later else -3600)  # later (or earlier) than the last commit
        os.utime(path, (t, t))


def scope_a(path):
    return None if path.startswith("src/a/") else "out of scope"


class PreRevue(GitTestCase):
    def setUp(self):
        super().setUp()
        self.br = self.feature()
        self.log = self.root / f"check-{next(_n)}.log"
        self.fresh_check(self.log)

    def run_pre(self, branch=None, **kw):
        args = dict(base="dev", others=[], allowed=scope_a, declared=["src/a/x.js"], manager_id="A", check_log=self.log)
        args.update(kw)
        return prereview.run(self.d.work, branch or self.br, **args)

    def test_tout_va_bien(self):
        r = self.run_pre()
        self.assertTrue(r["ok"], r["reasons"])
        self.assertEqual(r["sign"], "✓")
        self.assertEqual(r["files"], ["src/a/x.js"])
        self.assertTrue(r["sha"])

    def test_branche_absente(self):
        r = self.run_pre("feat/rien")
        self.assertFalse(r["ok"])
        self.assertIn("not found", " ".join(r["reasons"]))

    def test_sha_local_different(self):
        self.d.branch(self.br, self.br)
        self.d.commit("src/a/x.js")  # local commit not pushed
        r = self.run_pre()
        self.assertTrue(any("local SHA" in x for x in r["reasons"]), r["reasons"])

    def test_sha_declare_different(self):
        r = self.run_pre(expected_sha="0000000")
        self.assertTrue(any("declared SHA" in x for x in r["reasons"]), r["reasons"])

    def test_base_en_retard(self):
        base = f"base{next(_n)}"
        self.d.branch(base, "origin/dev")
        self.d.push(base)
        br = self.feature(f"origin/{base}")
        self.d.branch(base, base)
        self.d.commit("src/z.js")
        self.d.push(base)
        self.fresh_check(self.log)
        r = self.run_pre(br, base=base)
        self.assertTrue(any("base behind: 1 commit" in x for x in r["reasons"]), r["reasons"])

    def test_hors_perimetre_et_non_declare(self):
        self.d.branch(self.br, self.br)
        self.d.commit("src/b/y.js")
        self.d.push(self.br)
        self.fresh_check(self.log)
        joined = " ".join(self.run_pre()["reasons"])
        self.assertIn("out of scope: src/b/y.js", joined)
        self.assertIn("not declared: src/b/y.js", joined)

    def test_declare_absent_est_une_info(self):
        r = self.run_pre(declared=["src/a/x.js", "src/a/plus.js"])
        self.assertTrue(r["ok"], r["reasons"])
        self.assertTrue(any("src/a/plus.js" in x for x in r["infos"]))

    def test_commits_etrangers(self):
        b = self.feature(path="src/b/y.js")
        a2 = f"feat/a{next(_n)}"
        self.d.branch(a2, b)  # reprend le commit de B
        self.d.commit("src/a/w.js")
        self.d.push(a2)
        self.fresh_check(self.log)
        r = self.run_pre(a2, others=[b], declared=["src/a/w.js"])
        self.assertTrue(any("1 foreign commit(s)" in x for x in r["reasons"]), r["reasons"])
        self.assertEqual(r["files"], ["src/a/w.js"])  # B's files are not charged to A
        integ = self.run_pre(a2, others=[b], declared=["src/a/w.js"], integrator=True)
        self.assertTrue(any("foreign" in x for x in integ["infos"]))
        self.assertFalse(any("foreign" in x for x in integ["reasons"]))

    def test_verrou_non_rendu(self):
        r = self.run_pre(lock_owner="Manager A · 12:00")
        self.assertIn("check lock not released (Manager A · 12:00)", r["reasons"])
        self.assertTrue(self.run_pre(lock_owner="Manager B · 12:00")["ok"])

    def test_check_absent_en_echec_ou_ancien(self):
        self.assertTrue(any("no check found" in x for x in self.run_pre(check_log=self.root / "absent.log")["reasons"]))
        self.fresh_check(self.log, "✔ a\n✖ b\n")
        self.assertTrue(any("failed (1 test(s) ✖)" in x for x in self.run_pre()["reasons"]))
        self.fresh_check(self.log, "✔ a\n✖ 1 problem (0 errors, 1 warning)\n")  # bilan ESLint, pas un test
        self.assertFalse(any("failed" in x for x in self.run_pre()["reasons"]))
        self.fresh_check(self.log, later=False)
        self.assertTrue(any("older than the last commit" in x for x in self.run_pre()["reasons"]))

    def test_depot_introuvable(self):
        r = prereview.run(self.root / "nulle-part", "feat/a")
        self.assertFalse(r["ok"])
        self.assertIn("repository not found", r["reasons"][0])

    def test_aucune_ecriture_git(self):
        before = git(self.d.work, "for-each-ref")
        self.run_pre()
        self.assertEqual(git(self.d.work, "for-each-ref"), before)


class PreRevueServeur(GitTestCase):
    """compute()/store_results() via le module serveur (sans HTTP) et le bouton « Relire »."""

    def setUp(self):
        super().setUp()
        server.configure(PROJECT)
        server.BASE_BRANCH = "dev"
        self.br = self.feature()
        log = prereview.check_log_path(server.RUN_DIR, PROJECT, "A", config.test_mode())
        self.assertTrue(str(log).startswith(str(self.root)))  # mode test : journal du check sous la racine
        self.fresh_check(log)
        s = server.empty_state()
        server.apply(s, {"kind": "supervisor", "surface": "surf-S"})
        server.apply(s, {"kind": "assign", "id": "A", "scope": ["src/a/*"], "surface": "surf-A"})
        server.apply(s, {"kind": "files", "id": "A", "add": ["src/a/x.js"]})
        server.apply(s, {"kind": "manager", "id": "A", "branch": self.br, "worktree": str(self.d.work), "status": "review"})
        server.apply(s, {"kind": "merge", "branch": self.br, "manager": "A", "status": "waiting"})
        server.save(s, "test")

    def test_passage_en_review(self):
        r = server.prereview_now(self.br)
        self.assertTrue(r and r["ok"], r)
        s = server.load()
        self.assertEqual(s["managers"]["A"]["prereview"]["sign"], "✓")
        self.assertEqual(s["merge_queue"][0]["prereview"]["sign"], "✓")
        for k in ("branch", "sha", "manager", "note", "status", "t"):  # additions only: existing keys untouched
            self.assertIn(k, s["merge_queue"][0])

    def test_resultat_identique_non_reecrit(self):
        server.prereview_now(self.br)
        s = server.load()
        self.assertEqual(prereview.store_results(s, prereview.compute(s, server)), 0)
        self.d.branch(self.br, self.br)
        self.d.commit("src/b/y.js")
        self.d.push(self.br)
        msgs = []
        self.assertEqual(prereview.store_results(s, prereview.compute(s, server), lambda *a: msgs.append(a)), 2)
        self.assertEqual(s["managers"]["A"]["prereview"]["sign"], "⚠")
        self.assertEqual(msgs[0][2], "warn")

    def test_hors_review_rien_a_calculer(self):
        s = server.load()
        server.apply(s, {"kind": "manager", "id": "A", "status": "working"})
        server.apply(s, {"kind": "merge", "branch": self.br, "op": "rm"})
        self.assertEqual(prereview.compute(s, server), {})

    def test_bouton_relire(self):
        server.prereview_now(self.br)
        s = server.load()
        self.assertTrue(server.review_request(s, "", self.br))
        sent = " ".join(" ".join(c) for c in self.wmux_calls() if "surf-S" in c)
        self.assertIn(f"REVIEW branch {self.br} of Manager A", sent)
        self.assertIn("pre-review ✓", sent)
        self.assertIn("Review requested", s["events"][0]["msg"])
        with self.assertRaises(ValueError):
            server.review_request(s, "Z", "inconnue")
