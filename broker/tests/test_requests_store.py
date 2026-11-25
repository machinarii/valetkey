import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from broker.brokerlib.requests_store import EntryRequest, RequestsStore


class RequestsStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "requests.json"
        self.now = 1_000_000.0

    def store(self):
        return RequestsStore(self.path, clock=lambda: self.now)

    def test_create_and_get_round_trip_file_mode_600(self):
        s = self.store()
        r = s.create("resend-api-key-blog-qa", "infra", "blog", "password", "send mail", False)
        self.assertIsInstance(r, EntryRequest)
        self.assertRegex(r.id, r"^r_[a-z2-7]{10}$")
        self.assertEqual((r.status, r.created, r.flags, r.result), ("pending", self.now, {}, ""))
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)

        again = self.store().get(r.id)  # reloaded from the file
        self.assertEqual(again, r)
        self.assertIsNone(self.store().get("r_nosuchthing"))

        s.set_status(r.id, "stored", "stored resend-api-key-blog-qa in infra-org")
        got = self.store().get(r.id)
        self.assertEqual((got.status, got.result),
                         ("stored", "stored resend-api-key-blog-qa in infra-org"))
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)
        self.assertEqual(os.listdir(self.tmp.name), ["requests.json"])  # no temp files left

    def test_pending_expires_after_24h(self):
        s = self.store()
        old = s.create("old-key", "local", None, "password", "", False)
        self.now += 3600
        new = s.create("new-key", "local", None, "notes", "", False)
        self.now += 86400 - 3600 + 1  # old is now just over 24 h, new is 23 h
        self.assertEqual([r.id for r in s.pending()], [new.id])
        self.assertEqual(s.get(old.id).status, "expired")
        self.assertEqual(self.store().get(old.id).status, "expired")  # persisted

    def test_mark_suspect_and_list(self):
        s = self.store()
        self.assertEqual(s.suspects(), [])
        s.mark_suspect("stripe-api-key-shop-live", "local", True)
        s.mark_suspect("x-api-key-trader", "infra", True)
        s.mark_suspect("x-api-key-trader", "infra", True)  # idempotent
        self.assertEqual(sorted(self.store().suspects()),
                         [("stripe-api-key-shop-live", "local"),
                          ("x-api-key-trader", "infra")])
        self.assertEqual(self.store().suspects_since()[0][2], self.now)
        s.mark_suspect("x-api-key-trader", "infra", False)
        s.mark_suspect("never-marked", "infra", False)
        self.assertEqual(self.store().suspects(), [("stripe-api-key-shop-live", "local")])

    def test_state_file_without_requester_still_loads(self):
        s = self.store()
        r = s.create("old-key", "local", None, "password", "", False, requester="devbox")
        data = json.loads(self.path.read_text())
        del data["requests"][r.id]["requester"]  # written by an older version
        self.path.write_text(json.dumps(data))
        self.assertEqual(self.store().get(r.id).requester, "")

    def test_limit_counts_pending_requests_per_requester(self):
        s = self.store()
        a = s.create("a-key", "local", None, "password", "", False, requester="devbox", limit=1)
        self.assertIsNone(s.create("b-key", "local", None, "password", "", False, requester="devbox", limit=1))
        self.assertIsNotNone(s.create("b-key", "local", None, "password", "", False, requester="bot1", limit=1))
        s.set_status(a.id, "stored")
        self.assertIsNotNone(s.create("b-key", "local", None, "password", "", False, requester="devbox", limit=1))

    def test_file_never_contains_value_key(self):
        s = self.store()
        r = s.create("database-url-shop-qa", "infra", "shop", "password", "db", True)
        s.mark_suspect("database-url-shop-qa", "infra", True)
        s.set_status(r.id, "replaced", "replaced database-url-shop-qa; old value kept as "
                     "database-url-shop-qa-backup-20261106")
        text = self.path.read_text()
        self.assertNotIn('"value"', text)
        json.loads(text)  # valid JSON


if __name__ == "__main__":
    unittest.main()
