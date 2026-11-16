"""Agents start with the same Claude account as the supervisor (CLAUDE_CONFIG_DIR reused); no override by default."""
import os
import unittest
from unittest import mock

import launch


class LaunchAccountTest(unittest.TestCase):
    def test_reprend_le_compte_du_superviseur(self):
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": r"C:\cfg\.claude-alt"}):
            bash, ps = launch.start_cmds("p", "A")
        self.assertTrue(ps.startswith(r"$env:CLAUDE_CONFIG_DIR='C:\cfg\.claude-alt'; $env:SUP_AGENT='p/A'; $env:SUP_PROJECT='p'; claude"))
        self.assertTrue(bash.startswith(r"CLAUDE_CONFIG_DIR='C:\cfg\.claude-alt' SUP_AGENT=p/A"))

    def test_sans_compte_explicite_rien_n_est_ajoute(self):
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CONFIG_DIR"}
        with mock.patch.dict(os.environ, env, clear=True):
            bash, ps = launch.start_cmds("p", "B")
        self.assertTrue(bash.startswith("SUP_AGENT=p/B SUP_PROJECT=p claude"))
        self.assertNotIn("CLAUDE_CONFIG_DIR", ps)

    def test_hooks_d_etat_charges_pour_la_session_seulement(self):
        """sup-hook.js is not in the global settings: it comes through --settings at launch."""
        bash, ps = launch.start_cmds("p", "A")
        for cmd in (bash, ps):
            self.assertIn("claude --settings", cmd)
            self.assertIn("sup-hook.js' --emit-settings", cmd)


if __name__ == "__main__":
    unittest.main()
