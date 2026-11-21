"""install.sh (client side, version, doctor) and a syntax check of every shell script."""
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
INSTALL = ROOT / "install.sh"


class InstallClientTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.prefix = self.home / "bin"

    def run_install(self, *args, **extra):
        env = {"HOME": str(self.home), "PATH": os.environ["PATH"],
               "XDG_CONFIG_HOME": str(self.home / ".config"), **extra}
        return subprocess.run(["bash", str(INSTALL), *args], env=env, capture_output=True,
                              text=True, timeout=60)

    def test_version(self):
        r = self.run_install("version")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "valetkey " + (ROOT / "VERSION").read_text().strip())

    def test_client_installs_tools_and_saves_url(self):
        r = self.run_install("client", "--prefix", str(self.prefix),
                             "--relay-url", "https://broker.example.ts.net/")
        self.assertEqual(r.returncode, 0, r.stderr)
        for t in ("relay", "vault", "fnox-to-relay"):
            self.assertTrue(os.access(self.prefix / t, os.X_OK), t)
        self.assertFalse((self.prefix / "vault-entry").exists())  # owner tool only with --owner
        url = (self.home / ".config" / "valetkey" / "relay-url").read_text()
        self.assertEqual(url, "https://broker.example.ts.net\n")

    def test_client_owner_adds_vault_entry(self):
        r = self.run_install("client", "--prefix", str(self.prefix), "--owner")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.prefix / "vault-entry").exists())
        self.assertIn("no broker URL yet", r.stderr)

    def test_agent_install_and_offline_guidance(self):
        r = self.run_install("client", "--agent", "--prefix", str(self.prefix))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.prefix / "relay").exists())
        for tool in ("vault", "vault-entry", "fnox-to-relay"):
            self.assertFalse((self.prefix / tool).exists())
        r = subprocess.run([str(self.prefix / "relay"), "guide"],
                           env={"PATH": os.environ["PATH"], "HOME": str(self.home)},
                           capture_output=True, text=True, timeout=10)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, (ROOT / "agents/credential-rules.md").read_text())

    def test_with_deps_links_the_pinned_fnox_but_not_on_agent_machines(self):
        if shutil.which("fnox"):
            self.skipTest("fnox is installed here")
        fake, log = self.home / "fake", self.home / "mise.log"
        fake.mkdir()
        (fake / "tailscale").write_text("#!/bin/bash\n")
        (fake / "mise").write_text('#!/bin/bash\ncase $1 in install) echo "$2" >> "$MISE_LOG" ;; '
                                   'exec) echo /opt/fake/fnox ;; esac\n')
        for tool in ("tailscale", "mise"):
            (fake / tool).chmod(0o755)
        env = {"PATH": f"{fake}:{os.environ['PATH']}", "MISE_LOG": str(log)}
        r = self.run_install("client", "--agent", "--with-deps", "--prefix", str(self.prefix), **env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(log.exists())  # agents use API routes only: no fnox
        r = self.run_install("client", "--with-deps", "--prefix", str(self.prefix), **env)
        self.assertEqual(r.returncode, 0, r.stderr)
        pin = re.search(r"FNOX_VERSION:-([0-9.]+)\}", (ROOT / "broker/deps.sh").read_text()).group(1)
        self.assertEqual(log.read_text(), f"fnox@{pin}\n")
        self.assertEqual(os.readlink(self.prefix / "fnox"), "/opt/fake/fnox")

    def test_agent_and_owner_install_modes_conflict(self):
        r = self.run_install("client", "--agent", "--owner", "--prefix", str(self.prefix))
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(self.prefix.exists())

    def test_client_rejects_plain_http(self):
        r = self.run_install("client", "--prefix", str(self.prefix), "--relay-url", "http://broker")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("https://", r.stderr)

    def test_uninstall_leaves_other_vault_binaries(self):
        self.run_install("client", "--prefix", str(self.prefix), "--relay-url", "https://b.example")
        (self.prefix / "vault").write_bytes(b"\x7fELF not our shim")
        r = self.run_install("uninstall-client", "--prefix", str(self.prefix))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.prefix / "vault").exists())
        self.assertFalse((self.prefix / "relay").exists())
        self.assertFalse((self.home / ".config" / "valetkey").exists())

    def test_broker_refuses_without_root(self):
        if os.geteuid() == 0:
            self.skipTest("running as root")
        r = self.run_install("broker")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("sudo", r.stderr)


    def test_agent_machine_setup_refuses_without_root(self):
        if os.geteuid() == 0:
            self.skipTest("running as root")
        r = self.run_install("agent", "--relay-url", "https://broker.example.ts.net")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("sudo", r.stderr)
        self.assertFalse((self.home / ".config" / "valetkey").exists())


class ShellSyntaxTests(unittest.TestCase):
    def test_every_shell_script_parses(self):
        scripts = [p for p in ROOT.rglob("*") if p.is_file() and ".git" not in p.parts
                   and (p.suffix == ".sh" or p.read_bytes()[:20].startswith(b"#!/bin/bash"))]
        self.assertGreater(len(scripts), 10)
        for p in scripts:
            r = subprocess.run(["bash", "-n", str(p)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, f"{p.relative_to(ROOT)}: {r.stderr}")


if __name__ == "__main__":
    unittest.main()
