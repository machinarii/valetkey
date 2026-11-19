import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from relay.tests import support

TOOL = Path(__file__).resolve().parent.parent / "tools" / "vault-info.py"
VALUE = "pw-value-should-never-print-777"

STATE = {
    "status": "unlocked",
    "orgs": [{"id": "org-infra-id", "name": "infra-org"}, {"id": "org-local-id", "name": "local-org"}],
    "collections": [
        {"id": "c-infra-id", "name": "infra-broker-collection", "organizationId": "org-infra-id"},
        {"id": "c-pg-id", "name": "infra-broker-collection/shop", "organizationId": "org-infra-id"},
        {"id": "c-other-id", "name": "infra-owner-only", "organizationId": "org-infra-id"},
        {"id": "c-local-id", "name": "local-broker-collection", "organizationId": "org-local-id"},
    ],
    "items": [
        {"id": "i1", "name": "database-url-shop-qa", "organizationId": "org-infra-id",
         "collectionIds": ["c-pg-id"], "login": {"password": VALUE}},
        {"id": "i2", "name": "Bad Name", "organizationId": "org-infra-id",
         "collectionIds": ["c-infra-id"], "login": {"password": VALUE}},
        {"id": "i3", "name": "some-token", "organizationId": "org-infra-id",
         "collectionIds": ["c-infra-id"], "login": {"password": None}, "notes": None},
    ],
}
ROUTES = """
[project.shop]
org   = "infra"
nodes = ["devbox"]
items = ["*-shop-qa"]

[value.missing-thing]
org   = "infra"
item  = "missing-thing"
nodes = ["devbox"]

[value.local-only]
org   = "local"
item  = "local-only"
nodes = ["laptop"]
"""


class VaultInfoTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.state = os.path.join(self.dir.name, "state.json")
        support.write_state(self.state, STATE)
        self.routes = os.path.join(self.dir.name, "routes.toml")
        with open(self.routes, "w") as f:
            f.write(ROUTES)

    def run_tool(self, *args, appdata=None):
        env = support.fresh_env(self.state)
        env["BW_SESSION"] = "fake-session"
        if appdata:
            env["BITWARDENCLI_APPDATA_DIR"] = appdata
        return subprocess.run([sys.executable, "-I", str(TOOL), *args], env=env,
                              capture_output=True, text=True)

    def test_org_json_collects_broker_collection_and_nested(self):
        r = self.run_tool("org-json", "infra")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout), {
            "org_id": "org-infra-id",
            "collection": "infra-broker-collection",
            "collections": {"infra-broker-collection": "c-infra-id",
                            "infra-broker-collection/shop": "c-pg-id"}})
        self.assertEqual(r.stderr.strip(), "ok    infra: 2 collections")
        self.assertNotIn("id", r.stderr)

    def test_org_json_entry_has_both_orgs_and_missing_org_fails(self):
        r = self.run_tool("org-json", "entry")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(set(json.loads(r.stdout)), {"local", "infra"})
        state = dict(STATE, orgs=[STATE["orgs"][0]])
        support.write_state(self.state, state)
        r = self.run_tool("org-json", "entry")
        self.assertEqual(r.returncode, 1)
        self.assertIn("organization local-org not visible", r.stderr)
        self.assertEqual(r.stdout, "")

    def test_check_reports_items_routes_and_collections_without_values(self):
        org_ids = os.path.join(self.dir.name, "org.json")
        with open(org_ids, "w") as f:
            json.dump({"org_id": "org-infra-id",
                       "collections": {"infra-broker-collection": "c-infra-id"}}, f)
        r = self.run_tool("check", "infra", "--routes", self.routes, "--org-ids", org_ids, "--lock")
        self.assertEqual(r.returncode, 1)
        out = r.stdout
        self.assertIn("relay-infra: unlocked", out)
        self.assertIn("FAIL  Bad Name:", out)
        self.assertIn("FAIL  some-token: no password or notes value", out)
        self.assertIn("FAIL  [value.missing-thing] item missing-thing missing", out)
        self.assertIn("FAIL  [project.shop] 0 items match", out)
        self.assertNotIn("local-only", out)
        self.assertIn("collections relay-infra sees: infra-broker-collection, "
                      "infra-broker-collection/shop, infra-owner-only", out)
        self.assertIn("FAIL  collection infra-broker-collection/shop is new", out)
        for secret in (VALUE, "org-infra-id", "c-infra-id", "fake-session"):
            self.assertNotIn(secret, out + r.stderr)
        with open(self.state) as f:
            self.assertEqual(json.load(f)["status"], "locked")

    def test_check_all_ok(self):
        org_ids = os.path.join(self.dir.name, "org.json")
        with open(org_ids, "w") as f:
            json.dump({"org_id": "org-infra-id",
                       "collections": {"infra-broker-collection/shop": "c-pg-id",
                                       "infra-broker-collection": "c-infra-id"}}, f)
        state = dict(STATE, items=[STATE["items"][0]])
        support.write_state(self.state, state)
        routes = os.path.join(self.dir.name, "r2.toml")
        with open(routes, "w") as f:
            f.write(ROUTES.split("[value.missing-thing]")[0])
        r = self.run_tool("check", "infra", "--routes", routes, "--org-ids", org_ids)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("ok    database-url-shop-qa", r.stdout)
        self.assertIn("ok    [project.shop] 1 items match", r.stdout)

    def test_check_flags_an_api_item_whose_name_another_item_contains(self):
        org_ids = os.path.join(self.dir.name, "org.json")
        with open(org_ids, "w") as f:
            json.dump({"org_id": "org-local-id",
                       "collections": {"local-broker-collection": "c-local-id"}}, f)
        item = {"organizationId": "org-local-id", "collectionIds": ["c-local-id"],
                "login": {"password": VALUE}}
        routes = os.path.join(self.dir.name, "api.toml")
        with open(routes, "w") as f:
            f.write('''
[api.gh]
org      = "local"
item     = "gh-token"
upstream = "https://example.com"
auth     = "Bearer {}"
methods  = ["GET"]
paths    = ["/v1/"]
nodes    = ["laptop"]

[value.gh-raw]
org   = "local"
item  = "gh-token"
nodes = ["laptop"]
''')
        with_backup = [dict(item, id="g1", name="gh-token"),
                       dict(item, id="g2", name="gh-token-backup-20260101")]
        support.write_state(self.state, dict(STATE, items=with_backup))
        r = self.run_tool("check", "local", "--routes", routes, "--org-ids", org_ids)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("FAIL  [api.gh] item gh-token is ambiguous for fnox: delete or rename "
                      "gh-token-backup-20260101", r.stdout)
        self.assertIn("ok    [value.gh-raw] gh-token", r.stdout)  # values are read by exact name
        self.assertNotIn(VALUE, r.stdout + r.stderr)

        support.write_state(self.state, dict(STATE, items=with_backup[:1]))
        r = self.run_tool("check", "local", "--routes", routes, "--org-ids", org_ids)
        self.assertIn("ok    [api.gh] gh-token", r.stdout)
        self.assertNotIn("ambiguous", r.stdout)

    def test_check_syncs_before_listing(self):
        org_ids = os.path.join(self.dir.name, "org.json")
        with open(org_ids, "w") as f:
            json.dump({"org_id": "org-infra-id",
                       "collections": {"infra-broker-collection/shop": "c-pg-id",
                                       "infra-broker-collection": "c-infra-id"}}, f)
        appdata = os.path.join(self.dir.name, "bw-cli")
        os.mkdir(appdata)
        support.write_state(self.state, dict(STATE, items=[]))
        self.run_tool("check", "infra", "--routes", self.routes, "--org-ids", org_ids, appdata=appdata)
        support.write_state(self.state, dict(STATE, items=[STATE["items"][0]]))  # a write elsewhere
        r = self.run_tool("check", "infra", "--routes", self.routes, "--org-ids", org_ids,
                          appdata=appdata)
        self.assertIn("ok    database-url-shop-qa", r.stdout)
        support.write_state(self.state, dict(STATE, items=[], fail=["sync"]))
        r = self.run_tool("check", "infra", "--routes", self.routes, "--org-ids", org_ids,
                          appdata=appdata)
        self.assertIn("FAIL  bw sync failed", r.stdout)
        self.assertIn("ok    database-url-shop-qa", r.stdout)  # local copy, flagged stale


if __name__ == "__main__":
    unittest.main()
