"""broker/migrate-names.sh on a fake machine: a directory in place of /, and stand-ins for getent,
usermod, groupmod, systemctl and systemd-creds that act on files there. Nothing real is touched."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "broker/migrate-names.sh"
STUBS = {
    "getent": 'grep -qx "$2" "$FAKE/$1"',
    "usermod": 'echo "usermod $*" >> "$FAKE/log"; [ "$1" = -l ] && [ "$3" = -d ] || exit 2\n'
               'sed -i "s/^$5\\$/$2/" "$FAKE/passwd"; echo "$2 $4" >> "$FAKE/homes"',
    "groupmod": 'echo "groupmod $*" >> "$FAKE/log"; [ "$1" = -n ] || exit 2; sed -i "s/^$3\\$/$2/" "$FAKE/group"',
    "systemctl": 'echo "systemctl $*" >> "$FAKE/log"',
    # "Encrypted" here is the name in front of the text: decrypting under another name fails, as it does for real.
    "systemd-creds": 'name=${2#--name=}\n'
                     'case $1 in\n'
                     '  decrypt) text=$(cat "$3"); case $text in "enc:$name:"*) printf %s "${text#enc:$name:}" ;; *) exit 1 ;; esac ;;\n'
                     '  encrypt) printf "enc:%s:%s" "$name" "$(cat)" > "$4" ;;\n'
                     'esac',
}


class MigrateNamesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="valetkey-test-"))
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", str(self.tmp)]))
        self.root, self.fake, bin_dir = self.tmp / "root", self.tmp / "fake", self.tmp / "bin"
        for d in (self.fake, bin_dir):
            d.mkdir()
        for name, body in STUBS.items():
            (bin_dir / name).write_text("#!/bin/bash\n" + body + "\n")
            (bin_dir / name).chmod(0o755)
        (self.fake / "log").write_text("")
        self.env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "FAKE": str(self.fake), "BROKER_MIGRATE_ROOT": str(self.root)}

    def old_broker(self):
        """What a 0.4 broker has on disk."""
        names = "".join(f"relay-{n}\n" for n in ("local", "infra", "entry", "front"))
        (self.fake / "passwd").write_text("root\n" + names)
        (self.fake / "group").write_text("root\n" + names)
        for d in ("etc/relay/approved-routes.d", "etc/credstore.encrypted", "var/lib/relay/tls", "var/lib/relay/update/backup/libexec",
                  "var/lib/relay/update/repo.git", "var/lib/broker/.config", "usr/local/libexec/broker",
                  "var/lib/relay-local/bw-cli", "var/lib/relay-infra", "var/lib/relay-entry", "var/lib/relay-approval",
                  "etc/systemd/system/relay-front.service.d", "etc/systemd/system/relay-local.service.d",
                  "etc/systemd/system/multi-user.target.wants", "usr/local/libexec/relay/bin"):
            (self.root / d).mkdir(parents=True)
        for f, text in {"etc/relay/routes.toml": "routes", "etc/relay/approved-routes.d/a.json": "{}",
                        "var/lib/relay-local/org.json": "{}", "var/lib/relay-local/bw-cli/data.json": "login",
                        "var/lib/relay-approval/state.sqlite": "pairings", "var/lib/relay/update/in-progress": "abc",
                        "var/lib/relay/tls/cert.pem": "certificate", "var/lib/broker/.config/own": "the machine's own",
                        "usr/local/libexec/broker/heartbeat": "the machine's own",
                        "etc/credstore.encrypted/relay-approval-key": "enc:relay-approval-key:PAIRING",
                        "etc/credstore.encrypted/relay-update-key": "enc:relay-update-key:DEPLOY\nKEY",
                        "etc/credstore.encrypted/relay-loopback": "enc:relay-loopback:x",
                        "etc/credstore.encrypted/bw-unlock-local": "enc:bw-unlock-local:x",
                        "etc/systemd/system/relay-front.service.d/approval-front.conf": "ours",
                        "etc/systemd/system/relay-local.service.d/memory.conf": "the owner's",
                        "etc/systemd/system/broker-heartbeat.timer": "the machine's own",
                        "etc/systemd/system/relay-cert.timer": "unit"}.items():
            (self.root / f).write_text(text)
        for n in ("front", "local", "infra", "entry", "approval"):
            (self.root / f"etc/systemd/system/relay-{n}.service").write_text("unit")
        (self.root / "etc/systemd/system/multi-user.target.wants/relay-front.service").symlink_to("../relay-front.service")

    def run_script(self):
        return subprocess.run(["bash", str(SCRIPT)], env=self.env, capture_output=True, text=True, timeout=30)

    def test_moves_a_broker_from_relay_to_broker_once(self):
        self.old_broker()
        r = self.run_script()
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        root, cs = self.root, self.root / "etc/credstore.encrypted"
        # Folders moved with what they hold; the two the running updater still uses stay as links.
        self.assertEqual((root / "etc/valetkey-broker/routes.toml").read_text(), "routes")
        self.assertTrue((root / "etc/valetkey-broker/approved-routes.d/a.json").exists())
        self.assertEqual((root / "var/lib/valetkey-broker-local/bw-cli/data.json").read_text(), "login")
        self.assertEqual((root / "var/lib/valetkey-broker-approval/state.sqlite").read_text(), "pairings")
        self.assertEqual((root / "var/lib/valetkey-broker-front/tls/cert.pem").read_text(), "certificate")
        self.assertTrue((root / "var/lib/valetkey-broker-update/repo.git").is_dir())
        self.assertEqual(os.readlink(root / "etc/relay"), "valetkey-broker")
        # The updater still running works from the old path: it finds its folder through a link.
        self.assertEqual(os.readlink(root / "var/lib/relay/update"), "../valetkey-broker-update")
        self.assertTrue((root / "var/lib/relay/update/repo.git").is_dir())
        # A folder of the machine's own called /var/lib/broker, and its files, are not touched.
        self.assertEqual((root / "var/lib/broker/.config/own").read_text(), "the machine's own")
        self.assertEqual(sorted(p.name for p in (root / "var/lib/broker").iterdir()), [".config"])
        self.assertEqual((root / "usr/local/libexec/broker/heartbeat").read_text(), "the machine's own")
        self.assertEqual((root / "etc/systemd/system/broker-heartbeat.timer").read_text(), "the machine's own")
        for gone in ("var/lib/relay-local", "var/lib/relay-infra", "var/lib/relay-entry", "var/lib/relay-approval"):
            self.assertFalse((root / gone).exists(), gone)
        # The two keys that cannot be made again are readable under the new name; the old files stay for now.
        self.assertEqual((cs / "valetkey-broker-approval-key").read_text(), "enc:valetkey-broker-approval-key:PAIRING")
        self.assertEqual((cs / "valetkey-broker-update-key").read_text(), "enc:valetkey-broker-update-key:DEPLOY\nKEY")
        self.assertEqual(oct((cs / "valetkey-broker-update-key").stat().st_mode & 0o777), "0o600")
        self.assertTrue((cs / "relay-approval-key").exists())
        self.assertFalse((cs / "valetkey-broker-loopback").exists())  # setup.sh makes a new one
        self.assertEqual((cs / "bw-unlock-local").read_text(), "enc:bw-unlock-local:x")  # vault unlocks: untouched
        # Users and groups renamed, homes pointed at the new folders.
        want = ["root"] + [f"valetkey-broker-{n}" for n in ("local", "infra", "entry", "front")]
        self.assertEqual((self.fake / "passwd").read_text().split(), want)
        self.assertEqual((self.fake / "group").read_text().split(), want)
        self.assertIn("valetkey-broker-local /var/lib/valetkey-broker-local", (self.fake / "homes").read_text())
        self.assertIn("valetkey-broker-front /var/lib/valetkey-broker-front\n", (self.fake / "homes").read_text())
        # Old units gone; the owner's own settings beside a unit moved to the new name, ours dropped.
        units = root / "etc/systemd/system"
        self.assertEqual(sorted(p.name for p in units.iterdir()),
                         ["broker-heartbeat.timer", "multi-user.target.wants", "valetkey-broker-local.service.d"])
        self.assertEqual((units / "valetkey-broker-local.service.d/memory.conf").read_text(), "the owner's")
        self.assertEqual(list((units / "multi-user.target.wants").iterdir()), [])
        self.assertIn("check any path in them that says relay", r.stdout)
        # The updater's copy of the old code is gone, so nothing can put old-named code onto the new names.
        self.assertFalse((root / "var/lib/valetkey-broker-update/backup").exists())
        self.assertFalse((root / "var/lib/valetkey-broker-update/in-progress").exists())
        # Services were stopped before any user was renamed.
        log = (self.fake / "log").read_text().splitlines()
        self.assertLess(next(i for i, l in enumerate(log) if l.startswith("systemctl stop relay-front.service")),
                        next(i for i, l in enumerate(log) if l.startswith("usermod")))
        self.assertIn("systemctl daemon-reload", log)
        # A second run finds nothing to do and runs nothing.
        (self.fake / "log").write_text("")
        r = self.run_script()
        self.assertEqual((r.returncode, r.stdout, (self.fake / "log").read_text()), (0, "", ""))

    def test_changes_nothing_when_a_check_fails(self):
        for break_it, message in ((lambda: (self.root / "etc/credstore.encrypted/relay-update-key").write_text("damaged"),
                                   "relay-update-key cannot be decrypted"),
                                  (lambda: (self.root / "etc/valetkey-broker").mkdir(), "etc/valetkey-broker both exist"),
                                  (lambda: (self.fake / "passwd").write_text("relay-local\nvaletkey-broker-local\n"),
                                   "relay-local and valetkey-broker-local both exist")):
            with self.subTest(message=message):
                subprocess.run(["rm", "-rf", str(self.root)])
                (self.fake / "log").write_text("")
                self.old_broker()
                break_it()
                users = (self.fake / "passwd").read_text()
                r = self.run_script()
                self.assertEqual(r.returncode, 3, r.stdout)  # 3: a check failed, nothing touched
                self.assertIn(message, r.stderr)
                self.assertIn("nothing was changed", r.stderr)
                self.assertEqual((self.fake / "log").read_text(), "")  # not even a service stopped
                self.assertTrue((self.root / "etc/relay/routes.toml").is_file())
                self.assertFalse((self.root / "etc/relay").is_symlink())
                self.assertEqual((self.fake / "passwd").read_text(), users)
                self.assertFalse((self.root / "etc/credstore.encrypted/valetkey-broker-approval-key").exists())

    def test_the_withdrawn_0_5_0_layout_is_refused_untouched(self):
        (self.fake / "passwd").write_text("root\nbroker-local\n")
        (self.fake / "group").write_text("root\n")
        (self.root / "etc/broker").mkdir(parents=True)
        (self.root / "etc/broker/routes.toml").write_text("routes")
        (self.root / "etc/systemd/system").mkdir(parents=True)
        r = self.run_script()
        self.assertEqual(r.returncode, 3, r.stdout)
        self.assertIn("installed with 0.5.0", r.stderr)
        self.assertEqual((self.fake / "log").read_text(), "")
        self.assertFalse((self.root / "etc/valetkey-broker").exists())

    def test_a_new_broker_is_left_alone(self):
        (self.fake / "passwd").write_text("root\nvaletkey-broker-local\n")
        (self.fake / "group").write_text("root\n")
        (self.root / "etc/valetkey-broker").mkdir(parents=True)
        (self.root / "etc/systemd/system").mkdir(parents=True)
        r = self.run_script()
        self.assertEqual((r.returncode, r.stdout, r.stderr, (self.fake / "log").read_text()), (0, "", "", ""))

    def test_setup_runs_it_first_and_the_old_updater_finds_its_module(self):
        setup = (ROOT / "broker/setup.sh").read_text()
        self.assertLess(setup.index('bash "$here/migrate-names.sh"'), setup.index("# --- users and directories ---"))
        # The updater of 0.4 and earlier imports relaylib.routes from relay/ before it installs anything.
        check = ("import sys; sys.path.insert(0, sys.argv[1]); "
                 "from relaylib.routes import RoutesError, load_routes, load_entry_config; print(load_routes.__module__)")
        out = subprocess.run(["python3", "-I", "-c", check, str(ROOT / "relay")], capture_output=True, text=True, timeout=30,
                             env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"})
        self.assertEqual((out.returncode, out.stdout.strip()), (0, "brokerlib.routes"), out.stderr)


if __name__ == "__main__":
    unittest.main()
