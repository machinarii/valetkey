"""Exercise the real installer entry point with simulated Proxmox commands."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]


class ProxmoxInstallTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        shutil.copyfile(ROOT / "install.sh", self.root / "install.sh")
        (self.root / "VERSION").write_text("test")
        (self.root / "broker").mkdir()
        (self.root / "broker/broker_read.py").touch()
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        for name, body in {"qm": ":", "pct": ":", "pveversion": ":",
                           "id": "echo 0", "pvesh": 'echo "$INVENTORY"'}.items():
            path = bin_dir / name
            path.write_text("#!/bin/sh\n" + body + "\n")
            path.chmod(0o755)
        for path, value in [("deploy/proxmox/vaultwarden-ct.sh", "vault:$CTID"),
                            ("deploy/proxmox/broker-vm.sh", "$ROLE:$VMID"),
                            ("bellhop/proxmox-bellhop-vm.sh", "bellhop:$VMID")]:
            child = self.root / path
            child.parent.mkdir(parents=True, exist_ok=True)
            child.write_text(f'echo "{value}" >> "$LOG"\n')
        self.log = self.root / "actions"
        self.env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(self.root),
                    "INVENTORY": "[]", "LOG": str(self.log)}

    def install(self, *args, **env):
        return subprocess.run(["bash", self.root / "install.sh", "proxmox", *args],
                              env={**self.env, **env}, capture_output=True, text=True)

    def test_full_and_vault_broker_only(self):
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.log.read_text().splitlines(), ["vault:210", "broker:211", "bellhop:212"])
        self.log.unlink()
        result = self.install("--no-bellhop", CTID="310", BROKER_VMID="311",
                              INVENTORY='[{"vmid":212}]')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.log.read_text().splitlines(), ["vault:310", "broker:311"])

    def test_preflight_fails_before_any_guest(self):
        for args, env in [((), {"INVENTORY": '[{"vmid":212}]'}), ((), {"BELLHOP_VMID": "211"}),
                          ((), {"CTID": "invalid"}), ((), {"INVENTORY": ""}),
                          (("--typo",), {})]:
            with self.subTest(args=args, env=env):
                self.assertNotEqual(self.install(*args, **env).returncode, 0)
                self.assertFalse(self.log.exists())
        os.unlink(self.root / "bin/pveversion")
        self.assertNotEqual(self.install().returncode, 0)
        self.assertFalse(self.log.exists())
