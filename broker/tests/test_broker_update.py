"""broker-update install against a synthetic repository under a temporary root.

The fixture repository carries the real brokerlib (for the config check) and a stub install.sh
that copies its own payload into the fake libexec and decides which ports "listen" afterwards.
ss, systemctl and broker-routes are stubs; nothing here needs root, systemd or a network.
"""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
TOOL = ROOT / "broker/bin/broker-update"
ENTRY = 'owner_nodes = ["laptop"]\ntailnet_name = "broker.test.ts.net"\n'
INSTALL_SH = """#!/bin/bash
# Stub of install.sh broker: put this commit's payload in place, then set the listening ports.
here=$(cd "$(dirname "$0")" && pwd)
cp "$here/payload" "$BROKER_UPDATE_ROOT/usr/local/libexec/broker/payload"
[ ! -f "$here/extra" ] || cp "$here/extra" "$BROKER_UPDATE_ROOT/usr/local/libexec/broker/extra"
cp "$here/ports" "$FAKE_PORTS"
"""


def sh(*cmd, cwd=None, env=None, stdin=""):
    return subprocess.run(cmd, cwd=cwd, env=env, input=stdin, capture_output=True, text=True, timeout=120)


@unittest.skipUnless(shutil.which("git"), "git not installed")
class BrokerUpdateTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.root = self.tmp / "root"
        self.libexec = self.root / "usr/local/libexec/broker"
        self.sbin = self.root / "usr/local/sbin"
        self.ports = self.tmp / "ports"
        for d in (self.libexec, self.sbin, self.root / "etc/broker", self.root / "etc/systemd/system",
                  self.tmp / "bin"):
            d.mkdir(parents=True)
        (self.root / "etc/broker/routes.toml").write_text("")
        (self.root / "etc/broker/entry.toml").write_text(ENTRY)
        (self.libexec / "payload").write_text("old\n")
        (self.libexec / "CONFIG_EPOCH").write_text("1\n")
        self.ports.write_text("8441 8442 8443 443\n")
        self.stub("bin/ss", 'for p in $(cat "$FAKE_PORTS"); do echo "LISTEN 0 128 127.0.0.1:$p 0.0.0.0:*"; done')
        self.stub("bin/systemctl", "exit 0")
        self.stub("root/usr/local/sbin/broker-routes", 'echo "$*" >> "$BROKER_UPDATE_ROOT/routes-ran"')
        self.env = {"PATH": f"{self.tmp / 'bin'}:{os.environ.get('PATH', '/usr/bin:/bin')}",
                    "HOME": str(self.tmp), "BROKER_UPDATE_ROOT": str(self.root),
                    "FAKE_PORTS": str(self.ports), "BROKER_START_WAIT": "2",
                    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
        self.src = self.tmp / "src"
        (self.src / "broker").mkdir(parents=True)
        shutil.copytree(ROOT / "broker/brokerlib", self.src / "broker/brokerlib",
                        ignore=shutil.ignore_patterns("__pycache__"))
        (self.src / "install.sh").write_text(INSTALL_SH)
        (self.src / "broker/CONFIG_EPOCH").write_text("1\n")
        self.git("init", "--quiet", "-b", "main")
        self.first = self.commit("first", payload="old\n")
        (self.libexec / "COMMIT").write_text(self.first + "\n")
        (self.root / "etc/broker/update.env").write_text(f"UPDATE_REPO={self.src}\nUPDATE_BRANCH=main\n")

    def stub(self, rel, body):
        path = self.tmp / rel
        path.write_text("#!/bin/bash\n" + body + "\n")
        path.chmod(0o755)

    def git(self, *args):
        r = sh("git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args, cwd=self.src, env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def commit(self, title, **files):
        for name, text in {"ports": "8441 8442 8443 443\n", **files}.items():
            (self.src / name.replace("__", "/")).write_text(text)
        self.git("add", "-A")
        self.git("commit", "--quiet", "-m", title)
        return self.git("rev-parse", "HEAD")

    def install(self, answer="y\n"):
        return sh("bash", str(TOOL), "install", env=self.env, stdin=answer)

    def payload(self):
        return (self.libexec / "payload").read_text()

    def test_installs_after_yes_and_records_the_commit(self):
        new = self.commit("second \x1b[2J title", payload="new\n")
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.payload(), "new\n")
        self.assertEqual((self.libexec / "COMMIT").read_text().strip(), new)
        self.assertIn("Commits:   1", r.stdout)
        self.assertNotIn("\x1b", r.stdout)  # a commit title cannot drive the console
        self.assertFalse((self.root / "var/lib/broker/update/in-progress").exists())
        r = self.install("")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("already up to date", r.stdout)
        # An update that starts but misbehaves: the owner asks for the previous code back.
        r = sh("bash", str(TOOL), "rollback", env=self.env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.payload(), "old\n")
        self.assertEqual((self.libexec / "COMMIT").read_text().strip(), self.first)

    def test_nothing_from_the_repository_runs_or_lands_without_yes(self):
        self.commit("second", payload="new\n")
        for answer in ("", "n\n", "yes\n", "no\n", "\n", "Y\n", "yes\nn\n"):
            r = self.install(answer)
            self.assertEqual(r.returncode, 1, (answer, r.stdout, r.stderr))
            self.assertEqual(self.payload(), "old\n")
            self.assertFalse((self.root / "var/lib/broker/update/staged").exists())
            self.assertEqual((self.libexec / "COMMIT").read_text().strip(), self.first)

    def test_config_the_new_code_rejects_changes_nothing(self):
        self.commit("second", payload="new\n")
        (self.root / "etc/broker/routes.toml").write_text('[value.x]\norg = "nowhere"\nnodes = ["a"]\nitem = "x"\n')
        r = self.install()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("rejects /etc/broker", r.stderr)
        self.assertEqual(self.payload(), "old\n")
        self.assertFalse((self.root / "var/lib/broker/update/backup").exists())

    def test_ports_that_do_not_come_back_restore_the_previous_code_exactly(self):
        self.commit("broken", payload="new\n", extra="added by the broken version\n", ports="8441\n")
        r = self.install()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("previous code is running", r.stderr)
        self.assertEqual(self.payload(), "old\n")
        self.assertFalse((self.libexec / "extra").exists())  # a file the failed version added is gone
        self.assertEqual((self.libexec / "COMMIT").read_text().strip(), self.first)
        self.assertEqual((self.root / "routes-ran").read_text(), "install\n")  # api.toml regenerated
        self.assertFalse((self.root / "var/lib/broker/update/in-progress").exists())

    def test_cut_off_install_is_rolled_back_by_the_next_run(self):
        self.commit("second", payload="new\n")
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        state = self.root / "var/lib/broker/update"
        (self.libexec / "payload").write_text("half-written\n")
        (state / "in-progress").write_text("x\n")
        r = self.install("")
        self.assertIn("cut off", r.stdout)
        self.assertEqual(self.payload(), "old\n")  # the copy kept by the last install
        self.assertFalse((state / "in-progress").exists())

    def test_rewritten_history_and_changed_epoch_are_called_out(self):
        self.commit("second", payload="new\n", broker__CONFIG_EPOCH="2\n")
        r = self.install("no\n")
        self.assertIn("needs manual steps first", r.stdout)
        self.assertNotIn("does not contain the installed commit", r.stdout)
        (self.libexec / "COMMIT").write_text("0" * 40 + "\n")
        r = self.install("no\n")
        self.assertIn("does not contain the installed commit", r.stdout)
        (self.libexec / "COMMIT").unlink()
        r = self.install("no\n")
        self.assertIn("does not know what it runs", r.stdout)

    def test_setup_for_a_public_repository_needs_no_key(self):
        r = sh("bash", str(TOOL), "setup", "https://github.com/acme/valetkey.git", env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.root / "etc/broker/update.env").read_text(),
                         "UPDATE_REPO=https://github.com/acme/valetkey.git\nUPDATE_BRANCH=main\n")
        self.assertFalse((self.root / "etc/credstore.encrypted").exists())
        r = sh("bash", str(TOOL), "setup", "https://example.com/acme/valetkey.git", env=self.env)
        self.assertEqual(r.returncode, 1)

    def test_refuses_without_setup_and_without_root(self):
        (self.root / "etc/broker/update.env").unlink()
        r = self.install()
        self.assertEqual(r.returncode, 1)
        self.assertIn("not set up", r.stderr)
        if os.geteuid() != 0:
            env = {k: v for k, v in self.env.items() if k != "BROKER_UPDATE_ROOT"}
            r = sh("bash", str(TOOL), "install", env=env)
            self.assertEqual(r.returncode, 1)
            self.assertIn("sudo", r.stderr)


if __name__ == "__main__":
    unittest.main()
