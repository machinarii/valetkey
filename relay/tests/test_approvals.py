import base64
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from unittest.mock import Mock

from relay import approval_worker
from relay.relaylib.approvals import Approvals, Refused, totp
from relay.relaylib.routes import load_routes

SPEC = dict(name="reports", org="local", upstream="https://reports.example.com",
            item="reports-api-key", methods=["GET"], paths=["/v1/reports"])


class ApprovalTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / "approved-routes.d").mkdir()
        self.routes = self.root / "routes.toml"
        self.routes.write_text('[security]\nagent_nodes=["devbox"]\n')
        self.owners = self.root / "entry.toml"
        self.owners.write_text('owner_nodes=["laptop","tablet"]\ntailnet_name="broker.test.ts.net"\n')
        self.now = 1710000000
        self.install = Mock(return_value=True)
        self.store = self.open_store()

    def open_store(self):
        store = Approvals(self.root / "test.sqlite", bytes(range(32)), self.routes, self.owners,
                          self.install, clock=lambda: self.now)
        self.addCleanup(store.db.close)
        return store

    def enroll(self):
        rid = self.store.open_pairing("laptop")
        with self.assertRaises(Refused):
            self.store.pair("tablet", rid)
        uri = urlsplit(self.store.pair("laptop", rid))
        self.assertEqual((uri.scheme, uri.netloc), ("otpauth", "totp"))
        params = parse_qs(uri.query)
        self.assertEqual(params["issuer"], ["Valetkey"])
        seed = base64.b32decode(params["secret"][0])
        self.store.pair("laptop", rid, totp(seed, self.now // 30))
        with self.assertRaises(Refused):
            self.store.pair("laptop", rid)
        return seed

    def test_totp_reference_and_activation(self):
        self.assertEqual(totp(b"12345678901234567890", 1), "287082")
        seed = self.enroll()
        rid = self.store.propose("devbox", SPEC)
        self.assertEqual(rid, self.store.propose("devbox", SPEC))
        self.assertEqual(self.store.review("laptop", rid)["spec"], SPEC)
        self.store = self.open_store()
        with self.assertRaises(Refused):
            self.store.approve("laptop", rid, totp(seed, self.now // 30))
        self.now += 30
        self.assertEqual(self.store.approve("laptop", rid, totp(seed, self.now // 30)), "Route active")
        self.assertEqual(self.store.review("laptop", rid)["state"], "active")
        self.store.approve("laptop", rid, "000000")
        self.install.assert_called_once()
        routes = load_routes(self.routes)
        self.assertEqual(routes.api["reports"].nodes, ["devbox"])
        self.assertFalse(routes.may_read_values("devbox"))
        self.routes.write_text('[security]\nagent_nodes=[]\n')
        self.assertNotIn("reports", load_routes(self.routes).api)

    def test_failed_activation_and_restart_recovery(self):
        seed = self.enroll()
        rid = self.store.propose("devbox", SPEC)
        self.now += 30
        self.install.side_effect = [False, False, True]
        with self.assertRaises(Refused):
            self.store.approve("laptop", rid, totp(seed, self.now // 30))
        self.assertNotIn("reports", load_routes(self.routes).api)
        self.assertEqual(self.store.review("laptop", rid)["state"], "recovery")
        self.store = self.open_store()
        self.store.recover()
        self.assertEqual(self.store.review("laptop", rid)["state"], "failed")

    def test_refresh_rate_expiry_and_policy(self):
        seed = self.enroll()
        old = self.store.open_pairing("laptop")
        new = self.store.open_pairing("laptop")
        with self.assertRaises(Refused):
            self.store.pair("laptop", old)
        for _ in range(5):
            with self.assertRaises(Refused):
                self.store.pair("laptop", new, "invalid")
        self.store = self.open_store()
        with self.assertRaises(Refused) as exc:
            self.store.pair("laptop", new, "invalid")
        self.assertEqual(exc.exception.status, 429)
        self.now += 600
        with self.assertRaises(Refused):
            self.store.pair("laptop", new)
        rid = self.store.propose("devbox", SPEC)
        self.store.approve("laptop", rid, totp(seed, self.now // 30))
        self.now += 900
        with self.assertRaises(Refused):
            self.store.review("laptop", rid)

    def test_agent_and_owner_roles(self):
        identity = Mock()
        identity.identity.return_value = ("laptop", True)
        rid = self.store.open_pairing("laptop")
        for msg in (dict(op="open-pair", owner="laptop"), dict(op="pair-view", peer="127.0.0.1", id=rid)):
            with self.assertRaises(Refused):
                approval_worker.dispatch(self.store, identity, msg, False)
        for node, spec in (("laptop", SPEC), ("devbox", dict(SPEC, nodes=["tablet"])),
                           ("devbox", dict(SPEC, name="value")), ("devbox", dict(SPEC, paths=["/"]))):
            with self.subTest(node=node, spec=spec), self.assertRaises(Refused):
                self.store.propose(node, spec)
