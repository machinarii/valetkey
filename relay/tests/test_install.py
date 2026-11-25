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

    def test_installed_client_version_without_broker(self):
        r = self.run_install("client", "--agent", "--prefix", str(self.prefix))
        self.assertEqual(r.returncode, 0, r.stderr)
        for flag in ("version", "--version"):
            r = subprocess.run([str(self.prefix / "valetkey-broker"), flag], cwd=self.home,
                               env={"HOME": str(self.home), "PATH": os.environ["PATH"]},
                               capture_output=True, text=True, timeout=10)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout, (ROOT / "VERSION").read_text())

    def test_version_tools_install_upgrade_and_preserve_full_client(self):
        args = ("--prefix", str(self.prefix))
        self.prefix.mkdir()
        (self.prefix / "relay-version").write_text((ROOT / "client/broker").read_text())  # an install before the rename
        (self.prefix / "relay").symlink_to("relay-version")
        (self.prefix / "valetkey-relay-version").write_text("#!/usr/bin/env python3\n"
                                                           '"""relay: ask the credential relay about names"""\n')  # 0.3
        (self.prefix / "valetkey-relay").symlink_to("valetkey-relay-version")
        for command in ("version-tools", "update"):
            result = self.run_install(command, *args)
            self.assertEqual(result.returncode, 0, result.stderr)
        relay = self.prefix / "valetkey-broker"
        self.assertEqual(os.readlink(relay), "valetkey-broker-version")
        for old in ("relay", "relay-version", "valetkey-relay", "valetkey-relay-version"):
            self.assertFalse((self.prefix / old).exists() or (self.prefix / old).is_symlink(), old)
        for cmd in ("version", "--version"):
            self.assertEqual(subprocess.check_output([relay, cmd], text=True), (ROOT / "VERSION").read_text())
        help_text = subprocess.check_output([relay, "--help"], text=True)
        self.assertIn("usage: valetkey-broker", help_text)
        self.assertIn("update", help_text)
        self.assertNotIn("request", help_text)
        self.assertFalse((self.home / ".config").exists())
        result = self.run_install("client", "--agent", "--prefix", str(self.prefix))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(relay.is_symlink())
        self.assertFalse((self.prefix / "valetkey-broker-version").exists())
        result = self.run_install("update", *args)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("request", subprocess.check_output([relay, "--help"], text=True))

    def test_version_tools_leave_an_unrelated_relay_alone(self):
        self.prefix.mkdir()
        foreign = self.home / "foreign"
        foreign.write_text("unmanaged")
        other = self.prefix / "relay"  # another program of the old name: not ours to remove
        other.symlink_to(foreign)
        result = self.run_install("update", "--prefix", str(self.prefix))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(other.resolve(), foreign)
        self.assertEqual(foreign.read_text(), "unmanaged")
        taken = self.prefix / "valetkey-broker"  # our own name held by something else: refuse, change nothing
        taken.unlink()
        taken.symlink_to(foreign)
        result = self.run_install("update", "--prefix", str(self.prefix))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(foreign.read_text(), "unmanaged")

    def test_client_installs_tools_and_saves_url(self):
        r = self.run_install("client", "--prefix", str(self.prefix),
                             "--broker-url", "https://broker.example.ts.net/")
        self.assertEqual(r.returncode, 0, r.stderr)
        for t in ("valetkey-broker", "vault", "valetkey-fnox-to-broker"):
            self.assertTrue(os.access(self.prefix / t, os.X_OK), t)
        # The same command under the project's name, and it says so in its help.
        named = subprocess.run([str(self.prefix / "valetkey-broker"), "--help"], capture_output=True, text=True,
                               env={"HOME": str(self.home), "PATH": os.environ["PATH"]}, timeout=10)
        self.assertIn("usage: valetkey-broker", named.stdout)
        self.assertFalse((self.prefix / "valetkey-entry").exists())  # owner tool only with --owner
        for old in ("relay", "vault-entry", "fnox-to-relay"):  # the names before valetkey-*
            self.assertFalse((self.prefix / old).exists(), old)
        url = (self.home / ".config" / "valetkey" / "broker-url").read_text()
        self.assertEqual(url, "https://broker.example.ts.net\n")

    def test_client_keeps_the_address_saved_under_the_earlier_file_name(self):
        cfg = self.home / ".config" / "valetkey"
        cfg.mkdir(parents=True)
        (cfg / "relay-url").write_text("http://127.0.0.1:9\n")  # nothing listens there
        # A client upgraded without the installer (Homebrew) still finds it.
        r = subprocess.run(["python3", str(ROOT / "client/broker"), "check", "local", "a"], capture_output=True,
                           text=True, timeout=20, env={"HOME": str(self.home), "PATH": os.environ["PATH"]})
        self.assertIn("cannot reach broker", r.stderr)
        r = self.run_install("client", "--prefix", str(self.prefix))  # the installer renames the file
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("no broker URL yet", r.stderr)
        self.assertEqual((cfg / "broker-url").read_text(), "http://127.0.0.1:9\n")
        self.assertFalse((cfg / "relay-url").exists())
        (self.prefix / "valetkey-relay").write_text("#!/usr/bin/env python3\n")  # the 0.3 client and tool
        (self.prefix / "valetkey-fnox-to-relay").write_text("#!/usr/bin/env python3\n")
        self.assertEqual(self.run_install("client", "--prefix", str(self.prefix)).returncode, 0)
        for old in ("valetkey-relay", "valetkey-fnox-to-relay"):
            self.assertFalse((self.prefix / old).exists(), old)

    def test_menu_installs_an_owner_device(self):
        answers = self.home / "answers"
        answers.write_text("6\nhttps://broker.example.ts.net\n")
        r = self.run_install(VALETKEY_TTY=str(answers))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.home / ".local/bin/valetkey-entry").exists())
        url = (self.home / ".config" / "valetkey" / "broker-url").read_text()
        self.assertEqual(url, "https://broker.example.ts.net\n")
        # A wrong number, an empty and a non-https broker URL are asked again, not fatal.
        answers.write_text("9\n6\n\nhttp://broker.example.ts.net\nhttps://broker2.example.ts.net\n")
        r = self.run_install(VALETKEY_TTY=str(answers))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Pick a number", r.stderr)
        self.assertIn("This one is needed", r.stderr)
        self.assertIn("not an https:// address", r.stderr)
        url = (self.home / ".config" / "valetkey" / "broker-url").read_text()
        self.assertEqual(url, "https://broker2.example.ts.net\n")
        answers.write_text("9\n")      # answers run out: stop, never loop
        self.assertNotEqual(self.run_install(VALETKEY_TTY=str(answers)).returncode, 0)

    def test_client_owner_adds_vault_entry(self):
        r = self.run_install("client", "--prefix", str(self.prefix), "--owner")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.prefix / "valetkey-entry").exists())
        self.assertIn("no broker URL yet", r.stderr)

    def test_agent_install_and_offline_guidance(self):
        r = self.run_install("client", "--agent", "--prefix", str(self.prefix))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.prefix / "valetkey-broker").exists())
        for tool in ("vault", "valetkey-entry", "valetkey-fnox-to-broker"):
            self.assertFalse((self.prefix / tool).exists())
        r = subprocess.run([str(self.prefix / "valetkey-broker"), "guide"],
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

    def test_optional_claude_mem_with_and_without_existing_install(self):
        plugin = self.home / ".claude/plugins/cache/thedotmack/claude-mem"
        marker = plugin / "fixture"
        for installed in (False, True):
            with self.subTest(installed=installed):
                if installed:
                    plugin.mkdir(parents=True)
                    marker.write_text("existing plugin")
                result = self.run_install("client", "--agent", "--prefix", str(self.prefix))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("works with or without claude-mem", result.stdout)
                guide = subprocess.run([str(self.prefix / "valetkey-broker"), "guide", "claude-mem"],
                                       env={"HOME": str(self.home), "PATH": os.environ["PATH"]},
                                       capture_output=True, text=True, timeout=10)
                self.assertEqual(guide.returncode, 0, guide.stderr)
                self.assertTrue(guide.stdout.startswith("## Optional claude-mem integration"))
                self.assertIn("remote worker", guide.stdout)
                self.assertNotIn("## Allowed workflow", guide.stdout)
                self.assertEqual(plugin.exists(), installed)
                if installed:
                    self.assertEqual(marker.read_text(), "existing plugin")

    def test_agent_and_owner_install_modes_conflict(self):
        r = self.run_install("client", "--agent", "--owner", "--prefix", str(self.prefix))
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(self.prefix.exists())

    def test_client_rejects_plain_http(self):
        r = self.run_install("client", "--prefix", str(self.prefix), "--broker-url", "http://broker")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("https://", r.stderr)

    def test_uninstall_leaves_other_vault_binaries(self):
        self.run_install("client", "--prefix", str(self.prefix), "--broker-url", "https://b.example")
        (self.prefix / "vault").write_bytes(b"\x7fELF not our shim")
        r = self.run_install("uninstall-client", "--prefix", str(self.prefix))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.prefix / "vault").exists())
        self.assertFalse((self.prefix / "valetkey-broker").exists())
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
        r = self.run_install("agent", "--broker-url", "https://broker.example.ts.net")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("sudo", r.stderr)
        self.assertFalse((self.home / ".config" / "valetkey").exists())


class T3OriginTests(unittest.TestCase):
    """The T3 Code address the agent policy allows: T3's own record, loopback only."""

    def test_record_default_and_planted_origin(self):
        text = INSTALL.read_text()
        funcs = text[text.index("t3_origin() {"):text.index("cmd_agent() {")]
        with tempfile.TemporaryDirectory() as home:
            record = Path(home, ".t3/userdata/server-runtime.json")
            def origin():
                return subprocess.run(["bash", "-c", funcs + '\nt3_origin "$1"', "_", home],
                                      capture_output=True, text=True, timeout=10).stdout.strip()
            self.assertEqual(origin(), "")  # T3 Code not used here
            record.parent.mkdir(parents=True)
            self.assertEqual(origin(), "http://127.0.0.1:3773")
            record.write_text('{"origin": "http://127.0.0.1:4000"}')
            self.assertEqual(origin(), "http://127.0.0.1:4000")
            record.write_text('{"origin": "https://mcp.example.com"}')  # written by the agent's user
            self.assertEqual(origin(), "http://127.0.0.1:3773")


class AptPackageTests(unittest.TestCase):
    """The .deb carries the installer and one command; its files are root-owned and not group-writable."""

    def test_package_contents_and_repository(self):
        if not (shutil.which("dpkg-deb") and shutil.which("dpkg-scanpackages") and (ROOT / ".git").exists()):
            self.skipTest("needs dpkg-dev and a git checkout")
        with tempfile.TemporaryDirectory() as out:
            env = {"PATH": os.environ["PATH"], "HOME": out}
            r = subprocess.run(["bash", str(ROOT / "packaging/apt/build.sh"), out + "/apt"], env=env,
                               capture_output=True, text=True, timeout=120)
            self.assertEqual(r.returncode, 0, r.stderr)
            version = (ROOT / "VERSION").read_text().strip()
            deb = f"{out}/apt/valetkey_{version}_all.deb"
            listing = subprocess.check_output(["dpkg-deb", "-c", deb], text=True)
            for wanted in ("./usr/bin/valetkey-setup", "./usr/share/valetkey/install.sh",
                           "./usr/share/valetkey/client/broker", "./usr/share/valetkey/broker/setup.sh"):
                self.assertIn(wanted, listing)
            for unwanted in ("relay/tests", "testvm", "docs/", ".git"):
                self.assertNotIn(unwanted, listing)
            for line in listing.splitlines():
                mode, owner = line.split()[:2]
                self.assertEqual(owner, "root/root", line)
                self.assertNotIn("w", mode[4:], line)  # nothing group- or world-writable
            self.assertIn(f"Version: {version}", subprocess.check_output(["dpkg-deb", "-I", deb], text=True))
            packages = Path(out, "apt/Packages").read_text()
            self.assertIn(f"Filename: ./valetkey_{version}_all.deb", packages)
            self.assertIn("Packages.gz", Path(out, "apt/Release").read_text())
            self.assertFalse(Path(out, "apt/InRelease").exists())  # unsigned without APT_GPG_KEY


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
