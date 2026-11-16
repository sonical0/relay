"""Isolation safety checks: those of the test base (base.py) and those of the code (config.py, server.py)."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.base import (REAL, SUP, RELAY_ROOT, IsolatedTestCase, IsolationError, assert_isolated,
                        isolation_problems, make_env, safe_rmtree)

import config


class GardeFouBase(IsolatedTestCase):
    """The base refuses to start on a non-isolated environment."""

    def test_environnement_courant_isole(self):
        self.assertEqual(isolation_problems(os.environ, self.root), [])

    def test_variable_manquante(self):
        for v in ("SUP_STATE_ROOT", "SUP_RELAY_STATE", "SUP_BACKUP_DIR", "SUP_CHECK_LOCK", "SUP_VAULT_DIR"):
            env = make_env(self.root)
            env.pop(v)
            with self.subTest(v=v), self.assertRaisesRegex(IsolationError, f"{v} not set"):
                assert_isolated(env, self.root)

    def test_variable_vers_un_vrai_dossier(self):
        for v, real in (("SUP_STATE_ROOT", REAL["run/"]), ("SUP_RELAY_STATE", REAL[".relay-supervision"]),
                        ("SUP_RELAY_STATE", REAL[".relay-state"]), ("SUP_BACKUP_DIR", REAL["BACKUPS"]),
                        ("SUP_CHECK_LOCK", REAL["real lock"]), ("SUP_STATE_ROOT", RELAY_ROOT)):
            env = make_env(self.root)
            env[v] = str(real)
            with self.subTest(v=v, real=real), self.assertRaisesRegex(IsolationError, "real directory|outside the test root"):
                assert_isolated(env, self.root)

    def test_ancien_nom_hors_racine(self):
        env = make_env(self.root)
        env["SUP_REPRISE_DIR"] = str(REAL[".relay-supervision"])
        with self.assertRaisesRegex(IsolationError, "SUP_REPRISE_DIR"):
            assert_isolated(env, self.root)

    def test_port_reserve_et_wmux_reel(self):
        env = make_env(self.root)
        env["SUP_URL"] = "http://127.0.0.1:8765"
        with self.assertRaisesRegex(IsolationError, "live server"):
            assert_isolated(env, self.root)
        env = make_env(self.root)
        env["WMUX_PIPE"] = r"\\.\pipe\wmux"
        with self.assertRaisesRegex(IsolationError, "real wmux"):
            assert_isolated(env, self.root)
        env = make_env(self.root)
        env.pop("SUP_TEST")
        with self.assertRaisesRegex(IsolationError, "SUP_TEST"):
            assert_isolated(env, self.root)

    def test_racine_hors_temp_refusee(self):
        with self.assertRaisesRegex(IsolationError, "temp directory|Relay tree"):
            assert_isolated(make_env(SUP / "tests"), SUP / "tests")

    def test_suppression_limitee_a_la_racine(self):
        # Non-existent paths: even a failing safety check would erase nothing.
        for p in (REAL[".relay-supervision"] / "sup-test-n-existe-pas", SUP / "run" / "sup-test-n-existe-pas"):
            with self.subTest(p=p), self.assertRaises(IsolationError):
                safe_rmtree(p)
        d = Path(tempfile.mkdtemp(prefix="pas-un-test-"))
        try:
            with self.assertRaisesRegex(IsolationError, "prefix"):
                safe_rmtree(d)
            self.assertTrue(d.is_dir())
        finally:
            d.rmdir()


class GardeFouCode(IsolatedTestCase):
    """config.py and server.py: test mode, roots, writes and port."""

    def test_racines_suivent_les_variables(self):
        self.assertTrue(config.test_mode())
        self.assertEqual(config.state_dir("zz"), self.root / "run" / "zz")
        self.assertEqual(config.relay_state_dir(), self.root / "relay-supervision")
        self.assertEqual(config.backup_dir(), self.root / "backups")
        self.assertEqual(config.isolation_errors(), [])
        cfg = config.load()
        self.assertEqual(Path(cfg["run_dir"]), self.root / "run" / "zz-test")
        self.assertEqual(Path(cfg["lock"]), self.root / "depot" / ".claude" / "check.lock")
        self.assertEqual(Path(cfg["vault_dir"]), self.root / "vault")

    def test_alias_anciens_noms(self):
        with mock.patch.dict(os.environ, {"SUP_RUN_DIR": str(self.root / "ancien"),
                                          "SUP_REPRISE_DIR": str(self.root / "ancien-fiches")}):
            os.environ.pop("SUP_RELAY_STATE")
            self.assertEqual(config.state_dir("zz"), self.root / "ancien")  # SUP_RUN_DIR = the project directory
            self.assertEqual(config.relay_state_dir(), self.root / "ancien-fiches")

    def test_isolation_incomplete_refusee(self):
        for v, val in (("SUP_BACKUP_DIR", None), ("SUP_RELAY_STATE", str(REAL[".relay-supervision"])),
                       ("SUP_STATE_ROOT", str(REAL["run/"])), ("SUP_CHECK_LOCK", str(REAL["real lock"]))):
            with self.subTest(v=v), mock.patch.dict(os.environ):
                if val is None:
                    os.environ.pop(v)
                else:
                    os.environ[v] = val
                with self.assertRaisesRegex(RuntimeError, v):
                    config.check_isolation()

    def test_ecritures_hors_racines_refusees(self):
        self.assertEqual(config.guard_write(self.root / "run" / "x.json"), self.root / "run" / "x.json")
        for p in (REAL[".relay-supervision"] / "index.json", REAL["run/"] / "example-app" / "state.json",
                  REAL[".relay-state"] / "x.md", REAL["BACKUPS"] / "x.db", self.root / "ailleurs.txt",
                  Path(tempfile.gettempdir()) / "hors-racine.txt"):
            with self.subTest(p=p), self.assertRaises(PermissionError):
                config.guard_write(p)

    def test_port_reserve(self):
        import server
        with self.assertRaisesRegex(RuntimeError, "8765"):
            config.check_port(8765)
        with self.assertRaisesRegex(RuntimeError, "8765"):
            server.make_server(8765)
        srv = server.make_server(0)  # free port: allowed
        try:
            self.assertNotEqual(srv.server_address[1], 8765)
        finally:
            srv.server_close()

    def test_serveur_refuse_de_demarrer_sans_isolation(self):
        import server
        with mock.patch.dict(os.environ, {"SUP_RELAY_STATE": str(REAL[".relay-supervision"])}):
            with self.assertRaisesRegex(RuntimeError, "isolation"):
                server.configure()

    def test_serveur_en_ligne_de_commande_refuse_8765(self):
        r = subprocess.run([sys.executable, str(SUP / "server.py"), "--port", "8765"], capture_output=True,
                           text=True, encoding="utf-8", timeout=60, env=dict(os.environ))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("8765", r.stderr)

    def test_sans_variable_valeurs_reelles_inchangees(self):
        # Compatibility: with no variable at all, the real paths and no test mode (read only).
        env = {k: v for k, v in os.environ.items() if not k.startswith("SUP_")}
        code = ("import config, json; print(json.dumps([config.test_mode(), str(config.state_dir('example-app')), "
                "str(config.relay_state_dir()), str(config.backup_dir())]))")
        r = subprocess.run([sys.executable, "-c", code], cwd=str(SUP), capture_output=True, text=True,
                           encoding="utf-8", timeout=30, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        import json
        mode, run, fiches, backups = json.loads(r.stdout)
        self.assertFalse(mode)
        self.assertEqual(Path(run), SUP / "run" / "example-app")
        self.assertEqual(Path(fiches), RELAY_ROOT / ".relay-supervision")
        self.assertEqual(Path(backups), RELAY_ROOT / "BACKUPS")


if __name__ == "__main__":
    unittest.main()
