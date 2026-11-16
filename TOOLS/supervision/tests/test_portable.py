"""Portability outside Windows: /proc and `ss` readers, wmux found on the PATH, tests without real I/O."""
import os
import unittest
from pathlib import Path
from unittest import mock

from tests.base import IsolatedTestCase

import cloture
import sysstats
import wmuxcli


class Proc(unittest.TestCase):
    def test_proc_stat(self):
        idle, total = sysstats.parse_proc_stat("cpu  100 5 50 800 20 0 10 0 0 0")
        self.assertEqual((idle, total), (820, 985))  # idle + I/O wait; user..steal

    def test_meminfo(self):
        texte = "MemTotal:       16000000 kB\nMemFree:         1000000 kB\nMemAvailable:    4000000 kB\nBuffers: 10 kB\n"
        charge, total, dispo = sysstats.parse_meminfo(texte)
        self.assertEqual((charge, total, dispo), (75, 16000000 * 1024, 4000000 * 1024))

    def test_meminfo_sans_memavailable(self):
        charge, _, dispo = sysstats.parse_meminfo("MemTotal: 1000 kB\nMemFree: 250 kB\n")
        self.assertEqual((charge, dispo), (75, 250 * 1024))


class Ss(unittest.TestCase):
    def test_ports_a_l_ecoute_avec_pid(self):
        sortie = ('LISTEN 0 511 127.0.0.1:3000 0.0.0.0:* users:(("node",pid=4242,fd=19))\n'
                  'LISTEN 0 128 *:5173 *:* users:(("vite",pid=777,fd=12))\n'
                  'LISTEN 0 4096 [::]:22 [::]:*\n')  # no pid (another user): ignored
        self.assertEqual(cloture.parse_ss(sortie), {3000: 4242, 5173: 777})


class WmuxDansLePath(IsolatedTestCase):
    def test_wmux_du_path_quand_pas_d_installation_par_utilisateur(self):
        faux = self.root / "bin" / "resources" / "cli-bin"
        faux.mkdir(parents=True)
        (faux / "wmux").write_text("#!/bin/sh\n", encoding="utf-8")
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": str(self.root / "vide")}), \
                mock.patch.object(wmuxcli, "_LOCAL", Path(self.root / "vide")), \
                mock.patch.dict(os.environ, {"WMUX_BIN": ""}), \
                mock.patch("shutil.which", return_value=str(faux / "wmux")):
            self.assertEqual(Path(wmuxcli._default_wmux()).resolve(), (faux / "wmux").resolve())

    def test_wmux_bin_prime(self):
        with mock.patch.dict(os.environ, {"WMUX_BIN": "/opt/wmux/cli-bin/wmux"}):
            self.assertEqual(wmuxcli._default_wmux(), "/opt/wmux/cli-bin/wmux")


if __name__ == "__main__":
    unittest.main()
