import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from relay.tests import support

SCRIPT = Path(__file__).resolve().parent.parent.parent / "broker" / "bin" / "with-bw-session"
FAKE_FNOX = """#!/bin/bash
echo "fnox-ran proxy=${HTTPS_PROXY:-none}"
"""


class WithBwSessionTests(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.d = d.name
        for sub in ("creds", "run", "bin"):
            os.mkdir(os.path.join(self.d, sub))
        with open(os.path.join(self.d, "creds", "bw-unlock-infra"), "w") as f:
            f.write("pw")
        self.fnox = os.path.join(self.d, "bin", "fnox")
        with open(self.fnox, "w") as f:
            f.write(FAKE_FNOX)
        os.chmod(self.fnox, 0o755)
        self.state = os.path.join(self.d, "state.json")
        support.write_state(self.state, {"status": "locked"})
        self.cfg = os.path.join(self.d, "api.toml")

    def run_chain(self, cfg_text):
        with open(self.cfg, "w") as f:
            f.write(cfg_text)
        env = support.fresh_env(self.state)
        env.update(CREDENTIALS_DIRECTORY=os.path.join(self.d, "creds"),
                   RUNTIME_DIRECTORY=os.path.join(self.d, "run"),
                   BITWARDENCLI_APPDATA_DIR="/var/lib/relay-infra/bw-cli",
                   HTTPS_PROXY="http://127.0.0.1:9", SSL_CERT_FILE="/x/ca.pem")
        relay = ["env", "-u", "BW_SESSION", "sh", "-c",
                 'echo "relay-ran session=${BW_SESSION:-none} proxy=${HTTPS_PROXY:-none} ca=${SSL_CERT_FILE:-none}"']
        return subprocess.run(["bash", str(SCRIPT), "bw-unlock-infra", self.fnox, "--no-daemon",
                               "-c", self.cfg, "proxy", "run", "--", *relay],
                              env=env, capture_output=True, text=True, check=True).stdout

    def test_no_proxy_rules_runs_relay_without_fnox(self):
        out = self.run_chain('root = true\n[secrets]\n\n[proxy]\negress = "strict"\n')
        self.assertEqual(out.strip(), "relay-ran session=none proxy=none ca=none")
        with open(os.path.join(self.d, "run", "bw.env")) as f:
            self.assertIn("BW_SESSION=fake-session\n", f.read())
        self.assertEqual(os.stat(os.path.join(self.d, "run", "bw.env")).st_mode & 0o777, 0o600)

    def test_proxy_rules_go_through_fnox(self):
        out = self.run_chain('[proxy]\negress = "strict"\n\n[[proxy.rules]]\nsecret = "X"\n')
        self.assertTrue(out.startswith("fnox-ran"), out)


if __name__ == "__main__":
    unittest.main()
