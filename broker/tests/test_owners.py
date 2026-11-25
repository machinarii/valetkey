import os
import stat
import tempfile
import unittest

from broker.brokerlib import owners
from broker.brokerlib.routes import load_entry_config

STATUS = {
    "Self": {"DNSName": "broker.test.ts.net.", "TailscaleIPs": ["100.64.0.1"]},
    "Peer": {
        "a": {"DNSName": "janes-iphone.test.ts.net.", "OS": "iOS", "TailscaleIPs": ["100.64.0.7", "fd7a:115c:a1e0::7"]},
        "b": {"DNSName": "laptop.test.ts.net.", "OS": "macOS", "TailscaleIPs": ["100.64.0.8"]},
        "c": {"DNSName": "devbox.test.ts.net.", "OS": "linux", "TailscaleIPs": ["100.64.0.9"], "Tags": ["tag:agent"]},
        "d": {"DNSName": "phone.other.ts.net.", "OS": "android", "TailscaleIPs": ["100.64.0.10"]},
    },
}
ENTRY = '# your devices\nowner_nodes  = ["laptop", "tablet", "phone"]\ntailnet_name = "broker.test.ts.net"\n'


class OwnerTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = os.path.join(tmp.name, "entry.toml")
        with open(self.path, "w") as f:
            f.write(ENTRY)
        os.chmod(self.path, 0o644)

    def test_only_your_own_untagged_devices_of_this_tailnet_are_offered(self):
        self.assertEqual(owners.devices(STATUS), {"janes-iphone": ("iOS", ["100.64.0.7", "fd7a:115c:a1e0::7"]),
                                                  "laptop": ("macOS", ["100.64.0.8"])})

    def test_a_device_is_found_by_name_full_name_or_address(self):
        for typed in ("janes-iphone", " Janes-iPhone ", "janes-iphone.test.ts.net", "janes-iphone.test.ts.net.",
                      "100.64.0.7", "fd7a:115c:a1e0:0::7"):
            self.assertEqual(owners.resolve(STATUS, typed), "janes-iphone", typed)
        # a tagged machine, a machine of another tailnet, an unknown name or address, another tailnet's full name
        for typed in ("devbox", "100.64.0.9", "phone", "100.64.0.10", "nobody", "100.64.0.99", "laptop.other.ts.net", ""):
            with self.subTest(typed=typed), self.assertRaises(owners.OwnerError) as refused:
                owners.resolve(STATUS, typed)
            self.assertIn("Found: janes-iphone, laptop", str(refused.exception))

    def test_first_device_replaces_the_example_names_and_later_ones_are_added(self):
        self.assertIn("example names were removed", owners.add_owner(self.path, "janes-iphone"))
        self.assertEqual(load_entry_config(self.path).owner_nodes, ["janes-iphone"])  # no placeholder "phone" left
        self.assertEqual(owners.add_owner(self.path, "laptop"), "laptop is now an approver device")
        self.assertIn("already", owners.add_owner(self.path, "laptop"))
        conf = load_entry_config(self.path)
        self.assertEqual((conf.owner_nodes, conf.tailnet_name), (["janes-iphone", "laptop"], "broker.test.ts.net"))
        with open(self.path) as f:
            text = f.read()
        self.assertTrue(text.startswith("# your devices\nowner_nodes  = ["))  # the rest of the file is untouched
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o644)
        self.assertEqual(os.listdir(os.path.dirname(self.path)), ["entry.toml"])  # no temporary file left

    def test_agent_machines_bad_names_and_hand_made_lists_are_refused(self):
        for name, agents in (("devbox", ["devbox"]), ("Bad Name", []), ('x"]\nowner_nodes = ["evil', [])):
            with self.subTest(name=name), self.assertRaises(owners.OwnerError):
                owners.add_owner(self.path, name, agents)
        with open(self.path, "w") as f:
            f.write('owner_nodes = [\n  "laptop",\n]\ntailnet_name = "broker.test.ts.net"\n')
        with self.assertRaisesRegex(owners.OwnerError, "by hand"):
            owners.add_owner(self.path, "janes-iphone")


if __name__ == "__main__":
    unittest.main()
