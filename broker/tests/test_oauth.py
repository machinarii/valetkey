import json
import unittest
import urllib.parse

from broker.brokerlib import oauth
from broker.tests.fake_oauth import FakeOAuth, local_fetch

BACK = "https://broker.test.ts.net/entry/oauth/callback"


class OAuthTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeOAuth()
        self.addCleanup(self.fake.close)
        self.client = oauth.Client(local_fetch, ("http",))
        self.resource = self.fake.base + "/mcp"

    def sign_in(self, now=1000):
        address, ticket, pending = self.client.start(self.resource, BACK)
        back = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.fake.sign_in(address)).query))
        self.assertEqual(back["state"], ticket)
        return self.client.finish(pending, back["code"], now), pending

    def test_sign_in_then_refresh(self):
        (token, state), pending = self.sign_in()
        self.assertIn(token, self.fake.access)
        asked = next(q for m, p, q in self.fake.seen if p == "/authorize")
        self.assertEqual((asked["code_challenge_method"], asked["resource"], asked["scope"], asked["redirect_uri"]),
                         ("S256", self.resource, "read write", BACK))
        self.assertNotIn(pending["verifier"], json.dumps(asked))  # only its hash leaves the broker
        self.assertEqual(next(q for m, p, q in self.fake.seen if p == "/register")["redirect_uris"], [BACK])
        self.assertEqual((state["expires_at"], state["valetkey_oauth"], state["resource"]), (4600, 1, self.resource))
        self.assertEqual(oauth.state_of(json.dumps(state)), state)
        self.assertEqual((oauth.due(state, 4539), oauth.due(state, 4540)), (False, True))
        newer, state2 = self.client.refresh(state, 5000)
        self.assertEqual((newer in self.fake.access, token in self.fake.access, state2["expires_at"]), (True, False, 8600))
        self.assertNotEqual(state2["refresh_token"], state["refresh_token"])
        with self.assertRaises(oauth.Failed) as failed:  # the old refresh token was replaced
            self.client.refresh(state, 5000)
        self.assertNotIn("synthetic", str(failed.exception))

    def test_a_wrong_verifier_or_a_used_code_gets_no_token(self):
        address, _, pending = self.client.start(self.resource, BACK)
        code = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.fake.sign_in(address)).query))["code"]
        with self.assertRaises(oauth.Failed):
            self.client.finish(dict(pending, verifier="other"), code, 1000)
        with self.assertRaises(oauth.Failed):
            self.client.finish(pending, code, 1000)

    def test_a_token_without_an_end_is_never_due_and_one_without_refresh_cannot_renew(self):
        self.fake.lasts = 0
        (_, state), _ = self.sign_in()
        self.assertEqual((state["expires_at"], oauth.due(state, 10 ** 12)), (None, False))
        with self.assertRaises(oauth.Failed):
            self.client.refresh(dict(state, refresh_token=""), 1000)

    def test_a_service_without_registration_or_sign_in_is_refused_in_words(self):
        self.fake.register = False
        with self.assertRaisesRegex(oauth.Failed, "registered by hand"):
            self.client.start(self.resource, BACK)
        with self.assertRaisesRegex(oauth.Failed, "does not offer an OAuth sign-in"):
            oauth.Client(lambda url, **kw: (404, None), ("http",)).start(self.resource, BACK)
        with self.assertRaises(oauth.Failed):  # production accepts https addresses only
            oauth.Client(local_fetch).start(self.resource, BACK)

    def test_only_public_https_addresses_are_fetched(self):
        for url in ("http://example.com/", "https://example.com:8443/", "https://user@example.com/",
                    "https://127.0.0.1/", "https://[::1]/", "https://10.0.0.5/", "https://192.168.1.1/",
                    "https://100.100.100.100/", "https://169.254.169.254/latest/meta-data"):
            with self.subTest(url=url), self.assertRaises(oauth.Failed):
                oauth.public_fetch(url)

    def test_state_of_reads_only_valetkey_oauth_notes(self):
        for notes in (None, "", "a purpose", "[1]", '{"refresh_token": "x"}', '{"valetkey_oauth": 2}'):
            self.assertIsNone(oauth.state_of(notes), notes)


if __name__ == "__main__":
    unittest.main()
