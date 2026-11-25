import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


class ComponentListTests(unittest.TestCase):
    def test_list_names_the_release_its_hash_and_the_pinned_fnox(self):
        with tempfile.NamedTemporaryFile(suffix=".deb") as deb:
            deb.write(b"synthetic package")
            deb.flush()
            out = subprocess.run([sys.executable, "-I", str(ROOT / "packaging/components.py"), deb.name],
                                 capture_output=True, text=True, check=True).stdout
        bom = json.loads(out)
        main = bom["metadata"]["component"]
        self.assertEqual((bom["bomFormat"], main["version"]), ("CycloneDX", (ROOT / "VERSION").read_text().strip()))
        self.assertEqual(main["hashes"][0]["content"], hashlib.sha256(b"synthetic package").hexdigest())
        parts = {c["name"]: c for c in bom["components"]}
        self.assertIn(f'FNOX_VERSION:-{parts["fnox"]["version"]}}}', (ROOT / "broker/deps.sh").read_text())
        self.assertNotIn("version", parts["bitwarden-cli"])  # not pinned: the list must not pretend it is


if __name__ == "__main__":
    unittest.main()
