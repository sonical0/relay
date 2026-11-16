"""reprise.py: the sheets and the index are written under the test root, and nowhere else."""
import json
import os
import unittest
from unittest import mock

from tests.base import PROJECT, REAL, SUP, IsolatedTestCase

import config
import reprise


def etat():
    return {"project": "Essai", "plan": "", "managers": {
        "A": {"id": "A", "surface": "surf-test-A", "session": "essai-a", "status": "working", "progress": 40,
              "domain": "Domain A", "task": "task A", "scope": ["src/a/*"], "workers": {}},
        "B": {"id": "B", "surface": "surf-test-B", "session": "essai-b", "status": "review", "role": "integrator",
              "scope": ["src/b/*"], "workers": {}}},
        "tasks": {}, "merge_queue": [], "requests": {}, "events": [], "supervisor_notes": []}


class Reprise(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        self.cfg = config.load(PROJECT)
        self.out = self.root / "relay-supervision"

    def test_fiches_sous_la_racine_de_test(self):
        reprise.write_all(etat(), {"surface": "surf-test-S", "session": "essai-s"}, self.cfg)
        self.assertEqual(sorted(p.name for p in (self.out / PROJECT).iterdir()), ["A.md", "B.md", "S.md"])
        index = json.loads((self.out / "index.json").read_text(encoding="utf-8"))
        self.assertEqual(index, {"surf-test-S": f"{PROJECT}/S.md", "surf-test-A": f"{PROJECT}/A.md",
                                 "surf-test-B": f"{PROJECT}/B.md"})
        self.assertIn("task A", (self.out / PROJECT / "A.md").read_text(encoding="utf-8"))
        # Nothing in the real directory: no sub-directory for the fake project, no index entry.
        self.assertFalse((REAL[".relay-supervision"] / PROJECT).exists())
        real_index = REAL[".relay-supervision"] / "index.json"
        if real_index.exists():
            self.assertNotIn("surf-test-", real_index.read_text(encoding="utf-8"))
        written = {p.relative_to(self.root).as_posix() for p in self.root.rglob("*") if p.is_file()}
        self.assertTrue({f"relay-supervision/{PROJECT}/A.md", "relay-supervision/index.json"} <= written)

    def test_index_garde_les_autres_projets(self):
        self.out.mkdir(parents=True, exist_ok=True)
        (self.out / "index.json").write_text(json.dumps({"surf-autre": "autre/A.md", "surf-test-A": "autre/B.md"}),
                                             encoding="utf-8")
        reprise.write_all(etat(), {}, self.cfg)
        index = json.loads((self.out / "index.json").read_text(encoding="utf-8"))
        self.assertEqual(index["surf-autre"], "autre/A.md")
        self.assertEqual(index["surf-test-A"], f"{PROJECT}/A.md")  # pane moved to this project

    def test_ecriture_hors_racine_refusee(self):
        # Harmless directory in the Relay tree: even a broken guard would damage nothing there.
        cible = SUP / "tests" / "_jamais_ecrit"
        with mock.patch.dict(os.environ, {"SUP_RELAY_STATE": str(cible)}):
            with self.assertRaises(PermissionError):
                reprise.write_all(etat(), {}, self.cfg)
        self.assertFalse(cible.exists())


    def test_fin_de_supervision_desinscrit_le_projet(self):
        reprise.write_all(etat(), {"surface": "surf-test-S", "session": "essai-s"}, self.cfg)
        guard = json.loads((self.out / f"{PROJECT}.guard.json").read_text(encoding="utf-8"))
        self.assertEqual(guard["port"], self.cfg["port"])
        index = self.out / "index.json"
        autres = {**json.loads(index.read_text(encoding="utf-8")), "surf-autre": "autre/A.md"}
        index.write_text(json.dumps(autres), encoding="utf-8")
        self.assertEqual(reprise.unregister(PROJECT), 3)
        self.assertEqual(json.loads(index.read_text(encoding="utf-8")), {"surf-autre": "autre/A.md"})
        self.assertFalse((self.out / f"{PROJECT}.guard.json").exists())
        self.assertTrue((self.out / PROJECT / "S.md").exists())  # les fiches restent
        self.assertEqual(reprise.unregister(PROJECT), 0)


if __name__ == "__main__":
    unittest.main()
