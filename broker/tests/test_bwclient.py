import json
import os
import subprocess
import tempfile
import unittest
from unittest import mock

from broker.brokerlib import bwclient
from broker.tests import support

ORG = "org1"


def item(i, name, cols, pw=None, notes=None):
    return {"id": i, "name": name, "organizationId": ORG, "collectionIds": cols,
            "login": {"password": pw}, "notes": notes}


class BwClientTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state_path = os.path.join(self.tmp.name, "state.json")
        self.now = 0.0

    def state(self, items=(), collections=(), **extra):
        s = {"status": "unlocked", "orgs": [{"id": ORG, "name": "infra-org"}],
             "collections": list(collections), "items": list(items)}
        s.update(extra)
        support.write_state(self.state_path, s)

    def read_state(self):
        with open(self.state_path) as f:
            return json.load(f)

    def bw(self, **extra):
        env = {k: v for k, v in support.fresh_env(self.state_path).items() if k != "FAKE_WHOIS"}
        env["BW_SESSION"] = "s"
        env["BITWARDENCLI_APPDATA_DIR"] = self.tmp.name
        env.update(extra)
        return bwclient.Bw(env=env)

    def cache(self, cols=("c1",), ttl=300):
        return bwclient.ItemCache(self.bw(), ORG, list(cols), ttl=ttl, clock=lambda: self.now)

    def test_list_items_passes_collection_and_parses(self):
        self.state([item("a", "A", ["c1"]), item("b", "B", ["c2"])])
        bw = self.bw()
        self.assertEqual([i["id"] for i in bw.list_items(ORG, "c1")], ["a"])
        self.assertEqual(len(bw.list_items(ORG)), 2)

    def test_get_value_password_and_notes(self):
        self.state([item("a", "a", ["c1"], pw="pw-a"), item("b", "b", ["c1"], notes="line1\nline2")])
        c = self.cache()
        self.assertEqual(c.get("a"), "pw-a")
        self.assertEqual(c.get("b/notes"), "line1\nline2")
        self.assertIsNone(c.get("b"))
        self.assertIsNone(c.get("a/notes"))
        self.assertEqual(bwclient.split_ref("x/notes"), ("x", "notes"))
        self.assertEqual(bwclient.split_ref("x"), ("x", "password"))

    def test_missing_item_is_none_and_exists_false(self):
        self.state([item("a", "a", ["c1"], pw="p")])
        c = self.cache()
        self.assertIsNone(c.get("nope"))
        self.assertFalse(c.exists("nope"))
        self.assertTrue(c.exists("a"))

    def test_empty_field_counts_as_missing(self):
        self.state([item("a", "a", ["c1"], pw="")])
        self.assertIsNone(self.cache().get("a"))

    def test_cache_refreshes_after_ttl_only(self):
        self.state([item("a", "a", ["c1"], pw="p")])
        c = self.cache(ttl=300)
        self.assertEqual(c.names(), ["a"])
        self.state([item("a", "a", ["c1"], pw="p"), item("b", "b", ["c1"], pw="q")])
        self.now = 299
        self.assertEqual(c.names(), ["a"])
        self.now = 301
        self.assertEqual(sorted(c.names()), ["a", "b"])
        self.state([item("a", "a", ["c1"], pw="p")])
        self.assertEqual(sorted(c.names()), ["a", "b"])
        c.invalidate()
        self.assertEqual(c.names(), ["a"])

    def test_refresh_syncs_so_other_writers_become_visible(self):
        # bw list reads the data dir's local copy; only `bw sync` brings in other accounts' writes.
        self.state([item("a", "a", ["c1"], pw="p")])
        c = self.cache()
        self.assertEqual(c.names(), ["a"])
        self.state([item("a", "a", ["c1"], pw="p2"), item("b", "b", ["c1"], pw="q")])
        self.assertEqual([i["name"] for i in self.bw().list_items(ORG, "c1")], ["a"])  # stale copy
        c.invalidate()
        self.assertEqual(sorted(c.names()), ["a", "b"])
        self.assertEqual((c.get("a"), c.get("b")), ("p2", "q"))
        self.assertEqual(c.sync_failures, 0)

    def test_failed_sync_serves_last_local_copy(self):
        self.state([item("a", "a", ["c1"], pw="p")])
        c = self.cache()
        self.assertEqual(c.get("a"), "p")
        self.state([item("a", "a", ["c1"], pw="p2"), item("b", "b", ["c1"], pw="q")], fail=["sync"])
        c.invalidate()
        self.assertEqual((c.names(), c.get("a")), (["a"], "p"))
        self.assertEqual(c.sync_failures, 1)

    def test_create_item_password_then_notes_field(self):
        self.state()
        bw = self.bw()
        i1 = bw.create_item("one", ORG, "c1", "secret1", "password", "a note")
        i2 = bw.create_item("two", ORG, "c1", "multi\nline", "notes", "ignored")
        items = {i["id"]: i for i in bw.list_items(ORG, "c1")}
        self.assertEqual(items[i1]["login"]["password"], "secret1")
        self.assertEqual(items[i1]["notes"], "a note")
        self.assertEqual(items[i1]["type"], 1)
        self.assertEqual(items[i2]["notes"], "multi\nline")
        self.assertIsNone(items[i2]["login"]["password"])
        self.assertEqual(items[i2]["collectionIds"], ["c1"])

    def test_rename_and_delete(self):
        self.state([item("a", "old", ["c1"], pw="p")])
        bw = self.bw()
        bw.rename_item("a", "new")
        got = self.read_state()["items"][0]
        self.assertEqual(got["name"], "new")
        self.assertEqual(got["login"]["password"], "p")
        bw.delete_item("a")
        self.assertEqual(self.read_state()["items"], [])

    def test_rename_sends_full_item(self):
        self.state([item("a", "old", ["c1"], pw="p")])
        bw = self.bw()
        calls = []
        real = bw._run

        def spy(args, stdin=None):
            calls.append((args, stdin))
            return real(args, stdin)
        bw._run = spy
        bw.rename_item("a", "new")
        import base64
        self.assertEqual(calls[-1][0], ["edit", "item", "a"])
        sent = json.loads(base64.b64decode(calls[-1][1]))
        self.assertEqual(sent["name"], "new")
        self.assertEqual(sent["login"]["password"], "p")
        self.assertEqual(sent["collectionIds"], ["c1"])

    def test_values_never_in_bw_argv(self):
        self.state([item("a", "old", ["c1"], pw="old-secret-1")])
        argvs = []
        real = subprocess.run

        def spy(cmd, *a, **kw):
            argvs.append(list(cmd))
            return real(cmd, *a, **kw)
        bw = self.bw()
        with mock.patch.object(bwclient.subprocess, "run", spy):
            new_id = bw.create_item("new", ORG, "c1", "new-secret-2", "password", "n")
            bw.create_item("multi", ORG, "c1", "line-secret-3\nx", "notes", None)
            bw.rename_item("a", "renamed")
        self.assertTrue(any(a[1:3] == ["create", "item"] for a in argvs))
        self.assertTrue(any(a[1:3] == ["edit", "item"] for a in argvs))
        import base64
        for argv in argvs:
            joined = " ".join(argv)
            for secret in ("new-secret-2", "line-secret-3", "old-secret-1"):
                self.assertNotIn(secret, joined)
            for arg in argv:  # nor base64-encoded
                try:
                    decoded = base64.b64decode(arg, validate=True).decode("utf-8", "replace")
                except ValueError:
                    continue
                self.assertNotIn("secret", decoded)
        names = {i["name"]: i for i in self.read_state()["items"]}
        self.assertEqual(names["new"]["login"]["password"], "new-secret-2")
        self.assertEqual(names["new"]["id"], new_id)
        self.assertEqual(names["renamed"]["login"]["password"], "old-secret-1")

    def test_session_lock_sync(self):
        self.state(status="locked")
        bw = self.bw()
        pf = os.path.join(self.tmp.name, "pw")
        with open(pf, "w") as f:
            f.write("x")
        self.assertEqual(bw.unlock(pf), "fake-session")
        self.assertEqual(self.read_state()["status"], "unlocked")
        bw.sync()
        bw.lock()
        self.assertEqual(self.read_state()["status"], "locked")

    def test_collection_ids_under_parent_and_nested(self):
        self.state(collections=[
            {"id": "p", "name": "infra-broker-collection", "organizationId": ORG},
            {"id": "n", "name": "infra-broker-collection/shop", "organizationId": ORG},
            {"id": "o", "name": "other", "organizationId": ORG},
            {"id": "x", "name": "infra-broker-collectionX", "organizationId": ORG}])
        cols = self.bw().list_collections(ORG)
        self.assertEqual(bwclient.collection_ids_under(cols, "infra-broker-collection"),
                         {"infra-broker-collection": "p", "infra-broker-collection/shop": "n"})

    def test_cache_merges_collections_without_duplicates(self):
        self.state([item("a", "a", ["c1", "c2"], pw="p"), item("b", "b", ["c2"], pw="q")])
        self.assertEqual(sorted(self.cache(cols=("c1", "c2")).names()), ["a", "b"])

    def test_concurrent_reads_refresh_once(self):
        import threading
        import time

        class SlowBw:
            calls = 0

            def sync(self):
                pass

            def list_items(self, org, cid):
                SlowBw.calls += 1
                time.sleep(0.2)
                return [item("a", "a", [cid], pw="p")]

        c = bwclient.ItemCache(SlowBw(), ORG, ["c1"], clock=lambda: self.now)
        got = []
        threads = [threading.Thread(target=lambda: got.append(c.get("a"))) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(got, ["p"] * 5)
        self.assertEqual(SlowBw.calls, 1)

    def test_bw_failure_raises_bwerror_without_output(self):
        self.state(fail=["list items"])
        with self.assertRaises(bwclient.BwError) as cm:
            self.bw().list_items(ORG, "c1")
        self.assertEqual(str(cm.exception), "bw list items failed")
        self.assertNotIn("fake bw failure", str(cm.exception))

    def test_timeout_raises_bwerror(self):
        err = subprocess.TimeoutExpired("bw", 1)
        with mock.patch("subprocess.run", side_effect=err):
            with self.assertRaises(bwclient.BwError) as cm:
                bwclient.Bw(env={}).sync()
        self.assertEqual(str(cm.exception), "bw sync failed")

    def test_invalid_json_raises_bwerror(self):
        bw = bwclient.Bw(env={}, exe="echo")
        with self.assertRaises(bwclient.BwError) as cm:
            bw.list_items(ORG)
        self.assertEqual(str(cm.exception), "bw list items failed")

    def test_create_or_get_reply_without_id_is_bwerror(self):
        self.state()
        for reply in ('{"name": "x"}', '[1, 2]', '"text"', '{"id": 7}'):
            with mock.patch.object(bwclient.Bw, "_run", side_effect=lambda a, i=None, r=reply:
                                   "e30=" if a[0] == "encode" else r):
                bw = self.bw()
                with self.assertRaises(bwclient.BwError) as cm:
                    bw.create_item("one", ORG, "c1", "secret1", "password", None)
                self.assertEqual(str(cm.exception), "bw create item failed")
                with self.assertRaises(bwclient.BwError) as cm:
                    bw.get_item("i1")
                self.assertEqual(str(cm.exception), "bw get item failed")

    def test_env_is_minimal(self):
        dump = os.path.join(self.tmp.name, "env.txt")
        self.state([item("a", "a", ["c1"])])
        with mock.patch.dict(os.environ, {"SECRET_CANARY": "1"}):
            self.bw(FAKE_BW_ENVDUMP=dump).list_items(ORG)
        allowed = {"PATH", "HOME", "BW_SESSION", "BITWARDENCLI_APPDATA_DIR", "FAKE_BW_STATE",
                   "FAKE_BW_ENVDUMP", "_", "PWD", "SHLVL", "OLDPWD"}
        with open(dump) as f:
            names = set(f.read().split())
        self.assertIn("BW_SESSION", names)
        self.assertNotIn("SECRET_CANARY", names)
        self.assertEqual(names - allowed, set())


if __name__ == "__main__":
    unittest.main()
