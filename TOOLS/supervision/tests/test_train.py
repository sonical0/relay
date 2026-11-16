"""Integrator's merge train: no scope alerts for the branches of the merge queue.

Temporary git repository: base dev, branches of A and B (local, their managers have moved on) registered in the merge
queue; D, the integrator, merges them into integ/train. No scope alert; a real change by D outside its scope still
alerts, even pushed to origin/integ/*.
"""
from tests.base import PROJECT
from tests.test_prereview import GitTestCase, git

import server

PERIMETRE = ("undeclared", "scope_refused", "collision")


class TrainIntegrateur(GitTestCase):
    def setUp(self):
        super().setUp()
        server.configure(PROJECT)

    def tableau(self, train="integ/train"):
        d = self.d
        d.branch("feat/a-train", "origin/dev")
        sha_a = d.commit("src/a/x.js")
        d.branch("feat/b-train", "origin/dev")
        sha_b = d.commit("src/b/y.js")
        d.branch(train, "origin/dev")
        git(d.work, "merge", "-q", "--no-ff", "-m", "train A", "feat/a-train")
        git(d.work, "merge", "-q", "--no-ff", "-m", "train B", "feat/b-train")
        s = server.empty_state()
        server.apply(s, {"kind": "assign", "id": "A", "scope": ["src/a/*"]})
        server.apply(s, {"kind": "assign", "id": "B", "scope": ["src/b/*"]})
        server.apply(s, {"kind": "assign", "id": "D", "scope": ["src/d/*"], "role": "integrator"})
        server.manager(s, "A")["branch"] = "feat/a-suite"  # A and B have already switched branches
        server.manager(s, "B")["branch"] = "feat/b-suite"
        server.manager(s, "D")["branch"] = train
        server.manager(s, "D")["worktrees"] = [str(d.work)]
        server.apply(s, {"kind": "merge", "branch": "feat/a-train", "sha": sha_a, "status": "merged"})
        server.apply(s, {"kind": "merge", "branch": "feat/b-train", "sha": sha_b[:7], "status": "ready"})
        return s

    def releve(self, s):
        for mid, files in server.scan_real_files(s).items():
            s["managers"][mid]["real_files"] = files
        return [a for a in server.alerts(s) if a["code"] in PERIMETRE and "D" in a["who"]]

    def test_train_sans_alerte(self):
        s = self.tableau()
        self.assertEqual(self.releve(s), [])
        self.assertEqual(s["managers"]["D"]["real_files"], [])

    def test_sans_la_file_les_fichiers_du_train_seraient_comptes(self):
        s = self.tableau("integ/train-sans-file")
        s["merge_queue"] = []
        self.assertIn("src/a/x.js", server.scan_real_files(s)["D"])

    def test_vraie_modification_hors_perimetre_alerte(self):
        s = self.tableau("integ/train-hors")
        self.d.commit("src/zz/hors.js")
        codes = {a["code"] for a in self.releve(s)}
        self.assertIn("scope_refused", codes)
        self.assertEqual(s["managers"]["D"]["real_files"], ["src/zz/hors.js"])

    def test_modification_poussee_sur_integ_alerte_aussi(self):
        s = self.tableau("integ/train-pousse")
        self.d.commit("src/zz/pousse.js")
        self.d.push("integ/train-pousse")
        self.assertIn("src/zz/pousse.js", server.scan_real_files(s)["D"])

    def test_modification_non_commitee_alerte(self):
        s = self.tableau("integ/train-sale")
        (self.d.work / "src" / "zz").mkdir(parents=True, exist_ok=True)
        (self.d.work / "src" / "zz" / "brouillon.js").write_text("x\n", encoding="utf-8")
        try:
            self.assertIn("scope_refused", {a["code"] for a in self.releve(s)})
        finally:
            (self.d.work / "src" / "zz" / "brouillon.js").unlink()

    def test_reference_inconnue_ignoree(self):
        s = self.tableau("integ/train-inconnu")
        s["merge_queue"].append({"branch": "feat/disparue", "sha": "deadbeef", "status": "ready"})
        self.assertEqual(server.scan_real_files(s)["D"], [])


if __name__ == "__main__":
    import unittest
    unittest.main()
