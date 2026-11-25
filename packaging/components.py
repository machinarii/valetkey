#!/usr/bin/python3 -I
"""Component list (CycloneDX 1.5 JSON) for a valetkey release, written beside the .deb.

  packaging/components.py valetkey_<version>_all.deb > valetkey_<version>.cdx.json

valetkey ships no third-party code: its own Python uses the standard library only. What the list
adds is what a machine gets around it: the package's dependencies, and what the broker role's
installer (broker/deps.sh) fetches, with the version where one is pinned.
"""
import hashlib
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def components(deb):
    version = (REPO / "VERSION").read_text().strip()
    fnox = re.search(r"^FNOX_VERSION=\$\{FNOX_VERSION:-([0-9.]+)\}$", (REPO / "broker/deps.sh").read_text(), re.M)[1]

    def part(name, how, version=None, shipped=False):
        c = {"type": "application", "name": name, "scope": "required" if shipped else "optional",
             "properties": [{"name": "valetkey:source", "value": how}]}
        if version:
            c["version"] = version
        return c

    latest = "latest at install time, recorded in /var/log/valetkey-install.log"
    return {
        "bomFormat": "CycloneDX", "specVersion": "1.5", "version": 1,
        "metadata": {"component": {
            "type": "application", "name": "valetkey", "version": version,
            "purl": f"pkg:deb/valetkey@{version}?arch=all",
            "hashes": [{"alg": "SHA-256", "content": hashlib.sha256(Path(deb).read_bytes()).hexdigest()}]}},
        "components": [
            part("python3", "package dependency (>= 3.9; the broker role needs >= 3.11), from the distribution", shipped=True),
            part("bash", "package dependency, from the distribution", shipped=True),
            part("ca-certificates", "package dependency, from the distribution", shipped=True),
            part("fnox", "broker role: installed by broker/deps.sh through mise, pinned", fnox),
            part("bitwarden-cli", f"broker role: vendor download; {latest}"),
            part("tailscale", f"every role: vendor install script; {latest}"),
            part("mise", "broker role: the vendor's signed apt repository"),
            *(part(n, "broker role: from the distribution") for n in
              ("curl", "unzip", "jq", "gpg", "qrencode", "git", "openssh-client")),
        ]}


if __name__ == "__main__":
    json.dump(components(sys.argv[1]), sys.stdout, indent=1)
    print()
