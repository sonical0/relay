"""supervision-guard.js: the PreToolUse hook refuses `git push` calls against the protocol, and lets everything else through."""
import json
import subprocess
import unittest

from tests.base import SUP, IsolatedTestCase

GUARD = SUP / "supervision-guard.js"
RULES = {"base": "dev"}
A = {"id": "A", "role": "", "branch": "feat/a"}
D = {"id": "D", "role": "integrator", "branch": "integ/x"}
SANS_BRANCHE = {"id": "B", "role": "", "branch": ""}

JS = """
const g = require(process.argv[1]);
const [cmd, who, current] = JSON.parse(process.argv[2]);
console.log(JSON.stringify(g.decide(cmd, who, {base: 'dev'}, () => current)));
"""


def decide(cmd, who=A, current="feat/a"):
    r = subprocess.run(["node", "-e", JS, str(GUARD), json.dumps([cmd, who, current])], capture_output=True, text=True,
                       encoding="utf-8", timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


class Decisions(unittest.TestCase):
    def test_autorises(self):
        for cmd in ("git push origin feat/a", "git push -u origin feat/a", "git push origin HEAD:feat/a", "git push",
                    "npm test && git push origin feat/a", "git -C ../wt push origin feat/a", "echo hello", "git status"):
            self.assertIsNone(decide(cmd), cmd)

    def test_jamais_de_force_ni_de_suppression(self):
        for cmd in ("git push --force origin feat/a", "git push -f origin feat/a", "git push origin +feat/a",
                    "git push --force-with-lease origin feat/a", "git push --delete origin feat/a", "git push --all",
                    "git push --mirror"):
            self.assertIn("never under supervision", decide(cmd) or "", cmd)

    def test_la_base_est_reservee_a_l_integrateur(self):
        self.assertIn("only the integrator", decide("git push origin HEAD:dev"))
        self.assertIn("only the integrator", decide("git push origin dev"))
        self.assertIn("only the integrator", decide("git push", current="dev"))
        self.assertIsNone(decide("git push origin HEAD:dev", who=D, current="integ/x"))

    def test_un_manager_ne_pousse_que_sa_branche(self):
        self.assertIn("pushes only its declared branch (feat/a)", decide("git push origin feat/autre"))
        self.assertIn("no branch declared", decide("git push origin feat/b", who=SANS_BRANCHE))

    def test_commande_chainee_une_violation_suffit(self):
        self.assertIn("only the integrator", decide("git push origin feat/a && git push origin HEAD:dev"))


class HookDeBout(IsolatedTestCase):
    def lancer(self, cmd, surface="surf-test-A", stdin=None):
        env = dict(__import__("os").environ, SUP_RELAY_STATE=str(self.dir))  # Windows: node needs SYSTEMROOT
        env.pop("WMUX_SURFACE_ID", None)
        if surface:
            env["WMUX_SURFACE_ID"] = surface
        data = stdin if stdin is not None else json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": str(self.dir)})
        return subprocess.run(["node", str(GUARD)], input=data, capture_output=True, text=True, encoding="utf-8", env=env,
                              timeout=30)

    def setUp(self):
        super().setUp()
        self.dir = self.root / "relay-supervision"
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "index.json").write_text(json.dumps({"surf-test-A": "essai/A.md", "surf-test-D": "essai/D.md"}), encoding="utf-8")
        (self.dir / "essai.guard.json").write_text(json.dumps({"base": "dev", "managers": {
            "A": {"role": "", "branch": "feat/a"}, "D": {"role": "integrator", "branch": "integ/x"}}}), encoding="utf-8")

    def test_refus_sortie_2_et_message(self):
        r = self.lancer("git push origin HEAD:dev")
        self.assertEqual(r.returncode, 2)
        self.assertIn("git push refused", r.stderr)
        self.assertEqual(self.lancer("git push origin HEAD:dev", surface="surf-test-D").returncode, 0)

    def test_laisse_passer_hors_supervision_et_au_moindre_doute(self):
        self.assertEqual(self.lancer("git push --force origin x", surface="surf-inconnu").returncode, 0)
        self.assertEqual(self.lancer("git push --force origin x", surface=None).returncode, 0)
        self.assertEqual(self.lancer("", stdin="not json").returncode, 0)
        self.assertEqual(self.lancer("ls -la").returncode, 0)
        (self.dir / "essai.guard.json").write_text("{broken", encoding="utf-8")
        self.assertEqual(self.lancer("git push origin HEAD:dev").returncode, 0)  # unreadable rules: cannot decide
        (self.dir / "index.json").write_text("{broken", encoding="utf-8")
        self.assertEqual(self.lancer("git push --force origin x").returncode, 0)  # unreadable index: supervised or not?

    def test_push_force_refuse_meme_si_les_regles_sont_illisibles(self):
        (self.dir / "essai.guard.json").write_text("{broken", encoding="utf-8")
        for cmd in ("git push --force origin feat/a", "git push origin +feat/a", "git push --delete origin x", "git push --all"):
            r = self.lancer(cmd)
            self.assertEqual(r.returncode, 2, cmd)
            self.assertIn("never under supervision", r.stderr)
        self.assertEqual(self.lancer("git push origin feat/a").returncode, 0)
        self.assertEqual(self.lancer("git push --force origin x", surface="surf-inconnu").returncode, 0)  # outside a supervision


    def test_tableau_arrete_inscription_perimee(self):
        """Supervision over, board stopped: the still-registered pane pushes like an ordinary session."""
        import socket
        def regles(port):
            (self.dir / "essai.guard.json").write_text(json.dumps({"base": "dev", "port": port, "managers": {
                "A": {"role": "", "branch": "feat/a"}}}), encoding="utf-8")
        libre = socket.socket(); libre.bind(("127.0.0.1", 0)); port_ferme = libre.getsockname()[1]; libre.close()
        regles(port_ferme)
        self.assertEqual(self.lancer("git push origin HEAD:dev").returncode, 0)
        ecoute = socket.socket(); ecoute.bind(("127.0.0.1", 0)); ecoute.listen()
        try:
            regles(ecoute.getsockname()[1])  # board running: the rule applies
            r = self.lancer("git push origin HEAD:dev")
            self.assertEqual(r.returncode, 2)
            self.assertIn("only the integrator", r.stderr)
        finally:
            ecoute.close()


if __name__ == "__main__":
    unittest.main()
