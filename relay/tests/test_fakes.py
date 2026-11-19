import base64
import json
import os
import subprocess
import tempfile
import unittest

from relay.tests import support


def run(cmd, env, stdin=None):
    return subprocess.run(cmd, env=env, input=stdin, capture_output=True, text=True)


class FakeBwTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state_path = os.path.join(self.tmp.name, "state.json")

    def env(self, state):
        support.write_state(self.state_path, state)
        return support.fresh_env(self.state_path)

    def test_list_items_filters_by_collection(self):
        state = {
            "status": "unlocked",
            "items": [
                {"id": "a", "name": "A", "organizationId": "o1", "collectionIds": ["c1"]},
                {"id": "b", "name": "B", "organizationId": "o1", "collectionIds": ["c2"]},
            ],
        }
        env = self.env(state)
        r = run(["bw", "list", "items", "--organizationid", "o1", "--collectionid", "c1"], env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([i["id"] for i in json.loads(r.stdout)], ["a"])

    def test_create_then_get_round_trip(self):
        env = self.env({"status": "unlocked", "items": []})
        item = {"name": "N", "organizationId": "o1", "collectionIds": ["c1"],
                "login": {"password": "pw"}, "notes": "n"}
        enc = run(["bw", "encode"], env, stdin=json.dumps(item))
        self.assertEqual(json.loads(base64.b64decode(enc.stdout.strip())), item)
        created = run(["bw", "create", "item", enc.stdout.strip()], env)
        self.assertEqual(created.returncode, 0, created.stderr)
        item_id = json.loads(created.stdout)["id"]
        got = json.loads(run(["bw", "get", "item", item_id], env).stdout)
        self.assertEqual(got["name"], "N")
        self.assertEqual(got["login"]["password"], "pw")

    def test_data_dir_reads_its_own_copy_until_sync(self):
        a = {"id": "a", "name": "A", "organizationId": "o1", "collectionIds": ["c1"]}
        b = dict(a, id="b", name="B")
        env = self.env({"status": "unlocked", "items": [a]})
        env["BITWARDENCLI_APPDATA_DIR"] = self.tmp.name
        ls = ["bw", "list", "items", "--organizationid", "o1"]
        self.assertEqual([i["id"] for i in json.loads(run(ls, env).stdout)], ["a"])
        support.write_state(self.state_path, {"status": "unlocked", "items": [a, b]})
        self.assertEqual([i["id"] for i in json.loads(run(ls, env).stdout)], ["a"])
        self.assertEqual(run(["bw", "sync"], env).returncode, 0)
        self.assertEqual([i["id"] for i in json.loads(run(ls, env).stdout)], ["a", "b"])

    def test_failing_subcommand_exits_nonzero(self):
        env = self.env({"status": "unlocked", "items": [], "fail": ["get item"]})
        r = run(["bw", "get", "item", "x"], env)
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stderr, "fake bw failure\n")


class FakeTailscaleTest(unittest.TestCase):
    def test_whois_known_and_unknown(self):
        env = support.fresh_env("/nonexistent", whois={"100.64.0.5": "DEVBOX.test.ts.net."})
        r = run(["tailscale", "whois", "--json", "100.64.0.5"], env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["Node"]["Name"], "DEVBOX.test.ts.net.")
        r = run(["tailscale", "whois", "--json", "100.64.0.6"], env)
        self.assertEqual(r.returncode, 1)


if __name__ == "__main__":
    unittest.main()
