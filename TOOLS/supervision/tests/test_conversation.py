"""conversation.py: the supervisor tab follows the transcript without reloading the page."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import conversation


def ligne(role, text):
    content = [{"type": "text", "text": text}] if role == "assistant" else text
    return json.dumps({"type": role, "timestamp": "t", "message": {"content": content}}) + "\n"


class Suivi(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "p").mkdir()
        patcher = mock.patch.object(conversation, "PROJECTS", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def session(self, sid, *lines):
        path = self.root / "p" / f"{sid}.jsonl"
        with open(path, "a", encoding="utf-8") as f:
            f.writelines(lines)
        return path

    def test_suite_apres_relance_a_neuf(self):
        """New session (/clear): numbers go on, the client asking for what follows gets it."""
        c = conversation.Conversation()
        self.session("s1", ligne("user", "bonjour"), ligne("assistant", "salut"), ligne("assistant", "encore"))
        c.attach("s1")
        nxt = c.since(-1)["next"]
        self.session("s2", ligne("user", "after clear"), ligne("assistant", "answer"))
        c.attach("s2")
        r = c.since(nxt)
        self.assertEqual([x["text"] for x in r["items"]], ["after clear", "answer"])
        self.assertGreater(r["next"], nxt)

    def test_suite_apres_la_coupe(self):
        """Beyond MAX_ITEMS, new items keep increasing numbers."""
        c = conversation.Conversation()
        path = self.session("s1", *[ligne("user", f"m{k}") for k in range(5)])
        c.attach("s1")
        with mock.patch.object(conversation, "MAX_ITEMS", 3):
            nxt = c.since(-1)["next"]
            with open(path, "a", encoding="utf-8") as f:
                f.write(ligne("assistant", "nouveau"))
            r = c.since(nxt)
        self.assertEqual([x["text"] for x in r["items"]], ["nouveau"])

    def test_client_en_avance_apres_redemarrage(self):
        """Server restarted (numbers back to zero): the client ahead gets the end, not nothing."""
        c = conversation.Conversation()
        self.session("s1", ligne("user", "a"), ligne("assistant", "b"))
        c.attach("s1")
        r = c.since(500)
        self.assertEqual([x["text"] for x in r["items"]], ["a", "b"])
        self.assertEqual(r["next"], 2)


if __name__ == "__main__":
    unittest.main()
