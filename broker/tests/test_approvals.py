import base64
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from unittest.mock import Mock

from broker import approval_worker
from broker.brokerlib.approvals import Approvals, Refused, totp
from broker.brokerlib.routes import load_routes

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
        self.assertEqual(params["issuer"], ["valetkey"])
        seed = base64.b32decode(params["secret"][0])
        self.store.pair("laptop", rid, totp(seed, self.now // 30))
        with self.assertRaises(Refused):
            self.store.pair("laptop", rid)
        return seed

    def test_a_pairing_link_says_when_it_runs_out(self):
        ticket = self.store.open_pairing("laptop")
        self.assertEqual(self.store.pairing_expires("laptop", ticket), self.now + 600)
        self.assertEqual(self.store.pairing_expires("laptop", "0" * 40), 0)
        owner = Mock()
        owner.identity.return_value = ("laptop", False)
        view = approval_worker.dispatch(self.store, owner, dict(op="pair-view", peer="127.0.0.1", id=ticket), False)
        self.assertEqual((view["uri"][:15], view["expires"]), ("otpauth://totp/", self.now + 600))

    def test_totp_reference_and_activation(self):
        self.assertEqual(totp(b"12345678901234567890", 1), "287082")
        seed = self.enroll()
        rid = self.store.propose("devbox", SPEC)
        self.assertEqual(rid, self.store.propose("devbox", SPEC))
        self.assertEqual(self.store.review("laptop", rid)["spec"], SPEC)
        self.assertEqual(self.store.review("laptop", rid)["expires"], self.now + 900)  # the page counts this down
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

    def test_join_adds_only_the_machine_and_follows_the_route_only_while_it_is_unchanged(self):
        route = ('[security]\nagent_nodes=["devbox","kls5"]\n[api.motion]\norg="local"\nitem="motion-api-key"\n'
                 'upstream="https://motion.example.com"\nauth="Basic {}"\nmethods=["POST"]\npaths=["/graphql"]\n'
                 'nodes=["devbox"]\n')
        self.routes.write_text(route)
        seed = self.enroll()
        for node, spec, status in (("kls5", {"join": "nope"}, 404), ("devbox", {"join": "motion"}, 409),
                                   ("laptop", {"join": "motion"}, 403),
                                   ("kls5", {"join": "motion", "nodes": ["kls5", "x"]}, 400)):
            with self.subTest(node=node, spec=spec), self.assertRaises(Refused) as exc:
                self.store.propose(node, spec)
            self.assertEqual(exc.exception.status, status)
        rid = self.store.propose("kls5", {"join": "motion"})
        self.assertEqual(rid, self.store.propose("kls5", {"join": "motion"}))
        shown = self.store.review("laptop", rid)["spec"]
        self.assertEqual((shown["join"], shown["nodes"], shown["auth"], shown["upstream"]),
                         ("motion", ["devbox"], "Basic {}", "https://motion.example.com"))
        self.now += 30
        self.assertEqual(self.store.approve("laptop", rid, totp(seed, self.now // 30)), "Route active")
        self.assertTrue((self.root / "approved-routes.d" / "motion+kls5.json").exists())
        self.assertEqual(self.install.call_args[0][0], "local")
        joined = load_routes(self.routes).api["motion"]
        self.assertEqual((joined.nodes, joined.upstream, joined.paths), (["devbox", "kls5"], "https://motion.example.com",
                                                                         ["/graphql"]))
        with self.assertRaises(Refused):
            self.store.propose("kls5", {"join": "motion"})  # already a member
        # Wider paths still apply to the joined machine; a new address or key does not.
        self.routes.write_text(route.replace('paths=["/graphql"]', 'paths=["/graphql","/files"]'))
        self.assertEqual(load_routes(self.routes).api["motion"].nodes, ["devbox", "kls5"])
        self.routes.write_text(route.replace("motion.example.com", "elsewhere.example.com"))
        self.assertEqual(load_routes(self.routes).api["motion"].nodes, ["devbox"])

    def test_an_approval_limited_in_time_ends_and_can_be_asked_for_again(self):
        seed = self.enroll()
        grants = self.root / "approved-routes.d"
        rid = self.store.propose("devbox", SPEC)
        self.now += 30
        for bad in (60, "3600", True, None):
            with self.assertRaises(Refused) as exc:
                self.store.approve("laptop", rid, totp(seed, self.now // 30), bad)
            self.assertEqual(exc.exception.status, 400)
        self.assertEqual(self.store.approve("laptop", rid, totp(seed, self.now // 30), 3600),
                         "Route active for 1 hour")
        self.assertEqual([f.name for f in grants.iterdir()], ["reports.grant"])  # an older version never reads it
        end = self.now + 3600
        route = load_routes(self.routes, self.now).api["reports"]
        self.assertEqual((route.allows("devbox", end - 1), route.allows("devbox", end), route.allows("x", 0)),
                         (True, False, False))
        with self.assertRaises(Refused):  # still in force: the name is taken
            self.store.propose("devbox", SPEC)
        self.now = end
        self.assertNotIn("reports", load_routes(self.routes, self.now).api)
        again = self.store.propose("devbox", SPEC)  # the ended grant is removed
        self.assertEqual(list(grants.iterdir()), [])
        self.now += 30
        self.assertEqual(self.store.approve("laptop", again, totp(seed, self.now // 30)), "Route active")
        self.assertEqual(load_routes(self.routes, self.now + 10 ** 9).api["reports"].until, {})

    def test_a_join_limited_in_time_ends_for_that_machine_only(self):
        self.routes.write_text('[security]\nagent_nodes=["devbox","kls5"]\n[api.motion]\norg="local"\n'
                               'item="motion-api-key"\nupstream="https://motion.example.com"\nauth="Bearer {}"\n'
                               'methods=["POST"]\npaths=["/graphql"]\nnodes=["devbox"]\n')
        seed = self.enroll()
        rid = self.store.propose("kls5", {"join": "motion"})
        self.now += 30
        self.store.approve("laptop", rid, totp(seed, self.now // 30), 28800)
        route = load_routes(self.routes, self.now).api["motion"]
        self.assertEqual(route.until, {"kls5": self.now + 28800})
        later = self.now + 28800
        self.assertEqual((route.allows("kls5", later - 1), route.allows("kls5", later), route.allows("devbox", later)),
                         (True, False, True))
        self.assertEqual(load_routes(self.routes, later).api["motion"].nodes, ["devbox"])

    def test_a_failed_activation_removes_a_time_limited_grant_too(self):
        seed = self.enroll()
        rid = self.store.propose("devbox", SPEC)
        self.now += 30
        self.install.side_effect = [False, True]
        with self.assertRaises(Refused):
            self.store.approve("laptop", rid, totp(seed, self.now // 30), 86400)
        self.assertEqual(list((self.root / "approved-routes.d").iterdir()), [])
        self.assertEqual(self.store.review("laptop", rid)["state"], "failed")

    def test_a_route_limited_to_some_tools_keeps_the_limit_and_shows_it_to_a_joiner(self):
        self.routes.write_text('[security]\nagent_nodes=["devbox","kls5"]\n')
        seed = self.enroll()
        rid = self.store.propose("devbox", dict(SPEC, methods=["POST"], tools=["search", "fetch", "search"]))
        self.assertEqual(self.store.review("laptop", rid)["spec"]["tools"], ["fetch", "search"])
        self.now += 30
        self.store.approve("laptop", rid, totp(seed, self.now // 30))
        # not *.json: a version that knows no tool limit must not load the route without it
        self.assertEqual([f.name for f in (self.root / "approved-routes.d").iterdir()], ["reports.grant"])
        self.assertEqual(load_routes(self.routes, self.now + 10 ** 9).api["reports"].tools, ["fetch", "search"])
        join = self.store.propose("kls5", {"join": "reports"})
        self.assertEqual(self.store.review("laptop", join)["spec"]["tools"], ["fetch", "search"])
        rid = self.store.propose("devbox", dict(SPEC, name="notes", auth="oauth"))  # a sign-in route: also 0.6 only
        self.now += 30
        self.store.approve("laptop", rid, totp(seed, self.now // 30))
        self.assertTrue((self.root / "approved-routes.d" / "notes.grant").exists())
        self.assertTrue(load_routes(self.routes, self.now).api["notes"].oauth)
        with self.assertRaises(Refused):
            self.store.propose("devbox", dict(SPEC, name="other", tools="anything"))

    def test_join_refused_when_the_route_changed_before_approval(self):
        route = ('[security]\nagent_nodes=["devbox","kls5"]\n[api.motion]\norg="local"\nitem="motion-api-key"\n'
                 'upstream="https://motion.example.com"\nauth="Bearer {}"\nmethods=["POST"]\npaths=["/graphql"]\n'
                 'nodes=["devbox"]\n')
        self.routes.write_text(route)
        seed = self.enroll()
        rid = self.store.propose("kls5", {"join": "motion"})
        self.routes.write_text(route.replace('item="motion-api-key"', 'item="other-api-key"'))
        self.now += 30
        with self.assertRaises(Refused) as exc:
            self.store.approve("laptop", rid, totp(seed, self.now // 30))
        self.assertEqual(exc.exception.status, 409)
        self.assertEqual(list((self.root / "approved-routes.d").iterdir()), [])
        self.install.assert_not_called()

    def test_basic_route_by_approval(self):
        seed = self.enroll()
        for bad in ("digest", "Basic {}"):
            with self.assertRaises(Refused):
                self.store.propose("devbox", dict(SPEC, auth=bad))
        rid = self.store.propose("devbox", dict(SPEC, auth="basic"))
        self.now += 30
        self.store.approve("laptop", rid, totp(seed, self.now // 30))
        self.assertEqual(load_routes(self.routes).api["reports"].auth, "Basic {}")

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
