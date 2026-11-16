"""Protocol evaluation scenarios (scenarios.json): the tool refuses and alerts as the skill promises."""
import json
import time
import unittest
from pathlib import Path

from tests.base import PROJECT, IsolatedTestCase

import server

SCENARIOS = json.loads(Path(__file__).with_name("scenarios.json").read_text(encoding="utf-8"))["scenarios"]


class Scenarios(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        server.configure(PROJECT)

    def neuf(self):
        s = server.empty_state()
        server.apply(s, {"kind": "assign", "id": "A", "scope": ["src/a/*"]})
        server.apply(s, {"kind": "assign", "id": "D", "scope": ["src/d/*"], "role": "integrator"})
        return s

    def rejouer(self, sc):
        s = self.neuf()
        for step in sc["steps"]:
            if "do" in step:
                if "error" in step:
                    with self.assertRaisesRegex(ValueError, step["error"], msg=sc["name"]):
                        server.apply(s, step["do"])
                else:
                    server.apply(s, step["do"])
            elif "set" in step:
                x = step["set"]
                s["managers"][x["manager"]][x["field"]] = x["value"]
            elif "age" in step:
                a = step["age"]
                t = int(time.time()) - a["seconds"]
                if "manager" in a:
                    s["managers"][a["manager"]]["updated"] = t
                else:
                    s["managers"][a["worker"][0]]["workers"][a["worker"]]["updated"] = t
        codes = {x.get("code") for x in server.alerts(s)}
        for code in sc.get("alerts", []):
            self.assertIn(code, codes, sc["name"])
        for code in sc.get("no_alerts", []):
            self.assertNotIn(code, codes, sc["name"])


def _fabrique(sc):
    return lambda self: self.rejouer(sc)


for _i, _sc in enumerate(SCENARIOS, 1):
    _nom = "".join(c if c.isalnum() else "_" for c in _sc["name"])[:50]
    setattr(Scenarios, f"test_{_i:02d}_{_nom}", _fabrique(_sc))


class Catalogue(unittest.TestCase):
    def test_vingt_scenarios_au_moins(self):
        self.assertGreaterEqual(len(SCENARIOS), 20)


if __name__ == "__main__":
    unittest.main()
