import datetime
import io
import json
import os
import re
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from relay import relay_entry
from relay.relaylib.identity import Whois
from relay.tests import support

SECRET = "loop-secret-entry"
OWNER_IP, AGENT_IP = "100.64.0.10", "100.64.0.20"
ORG_IDS = {
    "local": {"org_id": "org-local", "collections": {"local-broker-collection": "c-local"}},
    "infra": {"org_id": "org-infra", "collections": {"infra-broker-collection": "c-infra",
                                                     "infra-broker-collection/blog": "c-blog"}},
}
COLLECTIONS = [
    {"id": "c-local", "organizationId": "org-local", "name": "local-broker-collection"},
    {"id": "c-infra", "organizationId": "org-infra", "name": "infra-broker-collection"},
    {"id": "c-blog", "organizationId": "org-infra", "name": "infra-broker-collection/blog"},
]
ENTRY_TOML = 'owner_nodes = ["laptop"]\ntailnet_name = "broker.test.ts.net"\n'


def item(i, name, org, cols, pw=None, notes=None):
    return {"id": i, "name": name, "organizationId": org, "collectionIds": cols,
            "login": {"password": pw}, "notes": notes}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **kw):
        return None


OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())


def call(url, method="GET", headers=None, data=None):
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with OPENER.open(req, timeout=10) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        with e:
            return e.code, dict(e.headers), e.read()


class FakeReadRelay(BaseHTTPRequestHandler):
    """Stands in for relay_read's loopback endpoints: GET /names, POST /refresh."""

    def log_message(self, *a):
        pass

    def reply(self, status, body=b""):
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def ok_secret(self):
        if self.headers.get("X-Relay-Loopback") != SECRET:
            self.server.bad_secret += 1
            self.reply(403, b"not allowed\n")
            return False
        return True

    def do_GET(self):
        if self.path == "/names" and self.ok_secret():
            self.reply(200, json.dumps({"names": sorted(self.server.names)}).encode())

    def do_POST(self):
        if self.path == "/refresh" and self.ok_secret():
            self.server.refreshes += 1
            self.reply(204)


def fake_relay(names):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeReadRelay)
    srv.daemon_threads = True
    srv.names, srv.refreshes, srv.bad_secret = list(names), 0, 0
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class TamperBw(relay_entry.Bw):
    """Bw whose read-back returns a different password than was stored. The Task 1 fake bw has
    no 'corrupt_create' switch, so test_verify_mismatch_deletes_new_item swaps this class in."""

    def get_item(self, item_id):
        it = super().get_item(item_id)
        it["login"]["password"] = (it["login"].get("password") or "") + "-tampered"
        return it


class BrokenCreateBw(relay_entry.Bw):
    """Bw whose create_item fails with something other than BwError."""

    def create_item(self, *a, **kw):
        raise KeyError("id")


class RelayEntryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state_path = self.path("bw-state.json")
        self.now = time.time()
        self.today = time.strftime("%Y%m%d", time.localtime(self.now))
        self.bw_state([])

    def path(self, name):
        return os.path.join(self.tmp.name, name)

    def write(self, name, text):
        with open(self.path(name), "w") as f:
            f.write(text)
        return self.path(name)

    def bw_state(self, items):
        support.write_state(self.state_path, {
            "status": "locked", "collections": COLLECTIONS, "items": items,
            "orgs": [{"id": "org-local", "name": "local-org"}, {"id": "org-infra", "name": "infra-org"}]})

    def read_bw(self):
        with open(self.state_path) as f:
            return json.load(f)

    def start(self, local_names=(), infra_names=(), local_url=None, infra_url=None, org_ids=None,
              owner_tags=None):
        self.local = fake_relay(local_names)
        self.infra = fake_relay(infra_names)
        for s in (self.local, self.infra):
            self.addCleanup(s.server_close)
            self.addCleanup(s.shutdown)
        whois = {"127.0.0.1": "devbox.test.ts.net.", OWNER_IP: "laptop.test.ts.net.",
                 AGENT_IP: "devbox.test.ts.net."}
        env = support.fresh_env(self.state_path, whois)

        def runner(cmd, **kw):
            if owner_tags is not None and cmd[-1] == OWNER_IP:
                return subprocess.CompletedProcess(cmd, 0, json.dumps({"Node": {
                    "Name": "laptop.test.ts.net.", "Tags": owner_tags}}).encode())
            return subprocess.run(cmd, env=env, **kw)

        bw_env = self.write("bw.env", "\n".join([
            f"PATH={support.fake_path()}", f"FAKE_BW_STATE={self.state_path}",
            f"BITWARDENCLI_APPDATA_DIR={self.tmp.name}", ""]))
        argv = ["--port", "0",
                "--routes", self.write("routes.toml", ""),
                "--entry-config", self.write("entry.toml", ENTRY_TOML),
                "--state", self.path("requests.json"),
                "--local-url", local_url or f"http://127.0.0.1:{self.local.server_address[1]}",
                "--infra-url", infra_url or f"http://127.0.0.1:{self.infra.server_address[1]}",
                "--loopback-secret-file", self.write("loopback", SECRET + "\n"),
                "--unlock-file", self.write("unlock", "not-a-real-password\n"),
                "--bw-env-file", bw_env,
                "--org-ids", self.write("org-ids.json", json.dumps(org_ids or ORG_IDS))]
        self.audit = io.StringIO()
        srv = relay_entry.build_server(argv, whois=Whois(runner=runner), clock=lambda: self.now,
                                       audit_out=self.audit)
        self.srv = srv
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()

        def stop():
            srv.shutdown()
            srv.server_close()
            t.join(5)
        self.addCleanup(stop)
        self.base = f"http://127.0.0.1:{srv.server_address[1]}"

    def req(self, path, method="GET", body=None, peer=AGENT_IP, headers=None, form=None):
        h = {"X-Relay-Peer": peer} if peer else {}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        if form is not None:
            data = urllib.parse.urlencode(form).encode()
            h["Content-Type"] = "application/x-www-form-urlencoded"
        h.update(headers or {})
        return call(self.base + path, method, h, data)

    def test_owner_pairing_page_and_confirmation(self):
        self.start()
        ctx = self.srv.ctx
        rpc = relay_entry.approval_rpc
        key = b"synthetic front key"
        ctx["approval_front"] = key
        ctx["approval_rpc"] = mock.Mock(return_value=(200, "synthetic-uri"))
        ctx["qr_png"] = mock.Mock(return_value=b"test-png")
        rid = "a" * 40
        path = "/pair/" + rid

        def signed(peer, method="GET", **kw):
            headers = {rpc.HEADER: rpc.proof(key, method, path, peer)}
            return self.req(path, method, peer=peer, headers=headers, **kw)

        self.assertEqual(self.req(path, peer=OWNER_IP)[0], 403)
        self.assertEqual(signed(AGENT_IP)[0], 403)
        ctx["approval_rpc"].assert_not_called()
        status, headers, body = signed(OWNER_IP)
        self.assertEqual(status, 200)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertIn(b"data:image/png;base64,", body)
        self.assertNotIn(b"synthetic-uri", body)
        ctx["qr_png"].assert_called_once_with("synthetic-uri")
        token = re.search(rb'name="token" value="([0-9a-f]+)"', body)[1].decode()
        self.assertEqual(signed(OWNER_IP, "POST", form={"code": "123456"})[0], 403)
        ctx["approval_rpc"].return_value = (200, "Authenticator paired")
        result = signed(OWNER_IP, "POST", form={"code": "123456", "token": token})
        self.assertIn(b"Authenticator paired", result[2])
        ctx["approval_rpc"].assert_called_with(dict(peer=OWNER_IP, op="pair-confirm", id=rid, code="123456"))
        self.assertNotIn(rid, self.audit.getvalue())
        self.assertNotIn("123456", self.audit.getvalue())

    def test_join_review_page_names_the_route_and_its_machines(self):
        self.start()
        ctx = self.srv.ctx
        rpc = relay_entry.approval_rpc
        key = b"synthetic front key"
        ctx["approval_front"] = key
        spec = {"join": "motion", "org": "local", "item": "motion-api-key", "upstream": "https://motion.example.com",
                "auth": "Basic {}", "auth_header": "Authorization", "methods": ["POST"], "paths": ["/graphql"],
                "nodes": ["devbox"]}
        ctx["approval_rpc"] = mock.Mock(return_value=(200, {"node": "kls5", "spec": spec, "state": "pending"}))
        path = "/approve/" + "b" * 40
        page = self.req(path, peer=OWNER_IP, headers={rpc.HEADER: rpc.proof(key, "GET", path, OWNER_IP)})[2].decode()
        for text in ("asks to join route", "local/motion-api-key", "Already used by", "Basic (stored value)",
                     "Nothing else about the route changes", 'name="code"'):
            self.assertIn(text, page)
        self.assertEqual(relay_entry.auth_label("X-Api-Key", "{}"), "X-Api-Key header")
        self.assertEqual(relay_entry.auth_label("Authorization", "Bearer {}"), "Bearer token")

    def test_owner_approves_exact_agent_proposal(self):
        from pathlib import Path
        from relay import approval_worker
        from relay.relaylib.approvals import Approvals, Refused, totp
        from relay.relaylib.routes import load_routes
        from relay.tests.test_approvals import SPEC

        self.start()
        routes = Path(self.write("routes.toml", '[security]\nagent_nodes=["devbox"]\n'))
        (routes.parent / "approved-routes.d").mkdir()
        ctx = self.srv.ctx
        install = mock.Mock(return_value=True)
        def store():
            return Approvals(self.path("approvals.sqlite"), bytes(range(32)), routes, self.path("entry.toml"),
                             install, clock=lambda: self.now)

        def rpc(message):
            state = store()
            try:
                return 200, approval_worker.dispatch(state, ctx["whois"], message, False)
            except Refused as e:
                return e.status, str(e)
            finally:
                state.db.close()

        ctx["approval_rpc"] = rpc
        ctx["approval_front"] = b"test-front"
        def call(path, peer, method="GET", **kwargs):
            proof = relay_entry.approval_rpc.proof(b"test-front", method, path, peer)
            return self.req(path, method, peer=peer, headers={relay_entry.approval_rpc.HEADER: proof}, **kwargs)

        state = store()
        self.addCleanup(state.db.close)
        ticket = state.open_pairing("laptop")
        seed = state.seed("laptop", ticket)
        state.pair("laptop", ticket, totp(seed, int(self.now) // 30))
        self.now += 30
        status, _, data = call("/v1/route-requests", AGENT_IP, "POST", body=SPEC)
        self.assertEqual(status, 201)
        path = "/approve/" + json.loads(data)["id"]
        self.assertEqual(call(path, AGENT_IP)[0], 403)
        status, _, page = call(path, OWNER_IP)
        self.assertEqual(status, 200)
        for value in (b"devbox", b"local/reports-api-key", b"https://reports.example.com", b"/v1/reports"):
            self.assertIn(value, page)
        install.assert_not_called()
        token = re.search(rb'name="token" value="([0-9a-f]+)"', page)[1].decode()
        response = call(path, OWNER_IP, "POST", form=dict(token=token, code=totp(seed, int(self.now) // 30),
                                                          upstream="https://ignored.example"))
        self.assertEqual(response[0], 200)
        self.assertIn(b"Route active", response[2])
        self.assertEqual(load_routes(routes).api["reports"].upstream, SPEC["upstream"])
        install.assert_called_once()

    def request(self, name, org="infra", project=None, field="password", purpose="testing",
                replace=False, peer=AGENT_IP):
        return self.req("/v1/requests", "POST", {"name": name, "org": org, "project": project,
                                                  "field": field, "purpose": purpose,
                                                  "replace": replace}, peer=peer)

    def new_request(self, *a, **kw):
        status, _, body = self.request(*a, **kw)
        self.assertEqual(status, 201, body)
        return json.loads(body)

    def submit(self, rid, value):
        return self.req(f"/v1/requests/{rid}/value", "POST", {"value": value}, peer=OWNER_IP)

    def items_named(self, name):
        return [i for i in self.read_bw()["items"] if i["name"] == name]

    def page_token(self):
        status, _, body = self.req("/", peer=OWNER_IP)
        self.assertEqual(status, 200)
        return re.search(r'name="token" value="([^"]+)"', body.decode()).group(1)

    # --- agent side ---

    def test_agent_creates_request_with_valid_name(self):
        self.start()
        r = self.new_request("resend-api-key-blog-qa", project="blog", purpose="send mail")
        self.assertRegex(r["id"], r"^r_[a-z2-7]{10}$")
        self.assertEqual((r["status"], r["name"], r["purpose"], r["org"]),
                         ("pending", "resend-api-key-blog-qa", "send mail", "infra"))
        status, _, body = self.req(f"/v1/requests/{r['id']}")
        self.assertEqual(status, 200)
        got = json.loads(body)
        self.assertEqual((got["status"], got["purpose"]), ("pending", "send mail"))
        self.assertEqual(self.infra.bad_secret, 0)
        self.assertIn(f"devbox request/{r['id']} POST /v1/requests 201", self.audit.getvalue())

    def test_bad_name_is_400_with_reason(self):
        self.start()
        status, _, body = self.request("Resend_Key")
        self.assertEqual(status, 400)
        self.assertIn(b"lowercase", body)
        status, _, body = self.request("resend-api-key-blog", project="blog")
        self.assertEqual(status, 400)
        self.assertIn(b"-dev, -qa or -prod", body)  # the env suffix
        cases = [
            ({"name": "notion-api-key-backup-20261106"}, b"name is reserved\n"),
            ({"name": "notion-api-key", "org": "cloud"}, b"org must be local or infra\n"),
            ({"name": "notion-api-key", "field": "username"}, b"field must be password or notes\n"),
        ]
        for extra, msg in cases:
            body = {"name": "x", "org": "local", "field": "password", "purpose": "", "replace": False}
            body.update(extra)
            status, _, got = self.req("/v1/requests", "POST", body)
            self.assertEqual((status, got), (400, msg), extra)

    def test_existing_item_without_replace_is_409(self):
        self.start(local_names=["notion-api-key"])
        status, _, body = self.request("notion-api-key", org="local")
        self.assertEqual((status, body), (409, b"exists\n"))
        self.new_request("notion-api-key", org="local", replace=True)

    def owner_flags(self):
        listing = json.loads(self.req("/v1/requests?status=pending", peer=OWNER_IP)[2])
        return {r["name"]: r["flags"] for r in listing["requests"]}

    def test_near_duplicate_flag(self):
        self.start(infra_names=["resend-api-key-blog-qa"])
        r = self.new_request("resend-api-key-qa", project="blog")
        self.assertEqual(r["flags"].get("near_duplicate"), True)
        r = self.new_request("resend-api-key-blog-prod", project="blog")
        self.assertEqual(r["flags"], {})  # a changed segment is not an added or removed one
        r = self.new_request("resend-api-key-blog", org="infra")
        self.assertEqual(r["flags"].get("near_duplicate"), True)
        r = self.new_request("unrelated-key", org="infra")
        self.assertEqual(r["flags"], {})
        owner = self.owner_flags()
        self.assertEqual(owner["resend-api-key-qa"]["near_duplicate"], "resend-api-key-blog-qa")
        self.assertEqual(owner["resend-api-key-blog"]["near_duplicate"], "resend-api-key-blog-qa")

    def test_agent_views_never_name_another_item(self):
        other = "resend-api-key-blog-qa"
        self.start(infra_names=[other])
        status, _, body = self.request("resend-api-key-qa", project="blog")
        self.assertEqual(status, 201)
        self.assertNotIn(other.encode(), body)
        rid = json.loads(body)["id"]
        for peer in (AGENT_IP, OWNER_IP):  # agents on owner devices use the same endpoints
            status, _, body = self.req(f"/v1/requests/{rid}", peer=peer)
            self.assertEqual(status, 200)
            self.assertNotIn(other.encode(), body)
            self.assertIs(json.loads(body)["flags"]["near_duplicate"], True)
        self.assertEqual(self.owner_flags()["resend-api-key-qa"]["near_duplicate"], other)
        _, _, page = self.req("/", peer=OWNER_IP)
        self.assertIn(f"Similar existing item: {other}", page.decode())

    def test_read_relay_unreachable_flags_names_unavailable(self):
        self.start(infra_url="http://127.0.0.1:9")
        r = self.new_request("notion-api-key")
        self.assertEqual(r["flags"], {"names_unavailable": True})

    def test_unknown_caller_is_403(self):
        self.start()
        status, _, body = self.request("notion-api-key", peer="100.64.0.99")
        self.assertEqual((status, body), (403, b"unknown caller\n"))

    def test_non_owner_cannot_list_or_submit(self):
        self.start()
        r = self.new_request("notion-api-key")
        self.assertEqual(self.req("/v1/requests?status=pending")[0], 403)
        status, _, _ = self.req(f"/v1/requests/{r['id']}/value", "POST", {"value": "v-1"})
        self.assertEqual(status, 403)
        self.assertEqual(self.req("/")[0], 403)
        self.assertEqual(self.req("/v1/replace", "POST", {"name": "a", "org": "local"})[0], 403)
        self.assertEqual(self.items_named("notion-api-key"), [])

    # --- owner submissions ---

    def test_owner_submit_stores_and_refreshes(self):
        self.start()
        r = self.new_request("notion-api-key", purpose="notes sync")
        status, _, body = self.submit(r["id"], "ntn-value-123")
        self.assertEqual((status, body), (200, b"stored notion-api-key in infra-org\n"))
        [it] = self.items_named("notion-api-key")
        self.assertEqual((it["login"]["password"], it["organizationId"], it["collectionIds"],
                          it["notes"]), ("ntn-value-123", "org-infra", ["c-infra"], "notes sync"))
        self.assertEqual(self.read_bw()["status"], "locked")
        self.assertEqual((self.infra.refreshes, self.local.refreshes), (1, 0))
        got = json.loads(self.req(f"/v1/requests/{r['id']}")[2])
        self.assertEqual((got["status"], got["result"]),
                         ("stored", "stored notion-api-key in infra-org"))
        self.assertIn(f"laptop request/{r['id']} POST /v1/requests/{r['id']}/value 200 stored",
                      self.audit.getvalue())
        status, _, body = self.submit(r["id"], "again")
        self.assertEqual((status, body), (409, b"request is stored\n"))

    def test_expiry_date_is_checked_and_stored_on_the_item(self):
        self.start()
        today = datetime.date.fromtimestamp(self.now)
        for bad, why in (("2027/01/01", b"YYYY-MM-DD"), ("2027-02-30", b"not a real date"),
                         (today.isoformat(), b"after today"), (5, b"YYYY-MM-DD")):
            r = self.new_request("x-api-key-exp", purpose="expiry")
            status, _, body = self.req(f"/v1/requests/{r['id']}/value", "POST",
                                       {"value": "v-123", "expires": bad}, peer=OWNER_IP)
            self.assertEqual(status, 400, bad)
            self.assertIn(why, body)
            self.assertEqual(self.items_named("x-api-key-exp"), [])
        later = (today + datetime.timedelta(days=400)).isoformat()
        status, _, _ = self.req(f"/v1/requests/{r['id']}/value", "POST",
                                {"value": "v-123", "expires": later}, peer=OWNER_IP)
        self.assertEqual(status, 200)
        [it] = self.items_named("x-api-key-exp")
        self.assertEqual(it["fields"], [{"name": "expires", "value": later, "type": 0}])
        # Without a date the key does not expire: no field at all.
        r = self.new_request("y-api-key-exp", purpose="no expiry")
        self.assertEqual(self.submit(r["id"], "v-456")[0], 200)
        self.assertNotIn("fields", self.items_named("y-api-key-exp")[0])

    def test_no_expiration_box_wins_over_the_date(self):
        self.start()
        r = self.new_request("z-api-key-exp", purpose="never")
        later = (datetime.date.fromtimestamp(self.now) + datetime.timedelta(days=30)).isoformat()
        status, _, _ = self.req("/submit", "POST", peer=OWNER_IP, form={
            "id": r["id"], "value": "v-7", "token": self.page_token(), "expires": later, "never": "1"})
        self.assertEqual(status, 303)
        self.assertNotIn("fields", self.items_named("z-api-key-exp")[0])

    def test_default_expiry_is_one_year_later(self):
        stamp = lambda y, m, d: time.mktime((y, m, d, 12, 0, 0, 0, 0, -1))
        self.assertEqual(relay_entry.default_expiry(stamp(2026, 11, 24)), "2027-11-24")
        self.assertEqual(relay_entry.default_expiry(stamp(2028, 2, 29)), "2029-02-28")

    def test_local_notes_item(self):
        self.start()
        r = self.new_request("ssh-key-pve", org="local", field="notes")
        status, _, body = self.submit(r["id"], "line-1\nline-2\n")
        self.assertEqual((status, body), (200, b"stored ssh-key-pve in local-org\n"))
        [it] = self.items_named("ssh-key-pve")
        self.assertEqual((it["notes"], it["collectionIds"]), ("line-1\nline-2", ["c-local"]))
        self.assertEqual((self.infra.refreshes, self.local.refreshes), (0, 1))

    def test_submit_normalises_crlf_and_trailing_newline(self):
        self.start()
        r = self.new_request("notion-api-key")
        self.assertEqual(self.submit(r["id"], "abc\r\n")[0], 200)
        self.assertEqual(self.items_named("notion-api-key")[0]["login"]["password"], "abc")

    def test_replace_preserves_existing_same_day_backup(self):
        name = "resend-api-key-blog-qa"
        backup = f"{name}-backup-{self.today}"
        self.bw_state([item("old-1", name, "org-infra", ["c-blog"], pw="old-value-1")])
        self.start(infra_names=[name])
        r = self.new_request(name, project="blog", replace=True)
        status, _, body = self.submit(r["id"], "new-value-2")
        self.assertEqual((status, body.decode()),
                         (200, f"replaced {name}; old value kept as {backup}\n"))
        [cur], [bak] = self.items_named(name), self.items_named(backup)
        self.assertEqual((cur["login"]["password"], cur["collectionIds"]), ("new-value-2", ["c-blog"]))
        self.assertEqual(bak["login"]["password"], "old-value-1")
        self.assertEqual(json.loads(self.req(f"/v1/requests/{r['id']}")[2])["status"], "replaced")

        r2 = self.new_request(name, project="blog", replace=True)
        self.assertEqual(self.submit(r2["id"], "new-value-3")[0], 409)
        [cur], [bak] = self.items_named(name), self.items_named(backup)
        self.assertEqual((cur["login"]["password"], bak["login"]["password"]),
                         ("new-value-2", "old-value-1"))
        self.assertEqual(len(self.read_bw()["items"]), 2)
        self.assertEqual(self.read_bw()["status"], "locked")

    def test_tagged_owner_cannot_submit_or_view_entry(self):
        self.start(owner_tags=["tag:workload"])
        r = self.new_request("test-key")
        for path in ("/", "/v1/requests?status=pending"):
            self.assertEqual(self.req(path, peer=OWNER_IP)[0], 403)
        self.assertEqual(self.submit(r["id"], "synthetic-key")[0], 403)
        self.assertEqual(self.read_bw()["items"], [])

    def test_agent_policy_overrides_owner_membership_at_runtime(self):
        self.start()
        self.srv.ctx["agent_nodes"].add("laptop")
        self.assertEqual(self.req("/", peer=OWNER_IP)[0], 403)

    def test_entry_page_csp_allows_only_bundled_script_and_style(self):
        self.start()
        status, headers, _ = self.req("/", peer=OWNER_IP)
        self.assertEqual(status, 200)
        policy = headers["Content-Security-Policy"]
        for directive in ("default-src 'none'", "form-action 'self'", "frame-ancestors 'none'",
                          "script-src 'sha256-", "style-src 'sha256-"):
            self.assertIn(directive, policy)
        self.assertNotIn("unsafe-inline", policy)

    def test_double_submit_of_replace_keeps_original_backup(self):
        name = "notion-api-key"
        backup = f"{name}-backup-{self.today}"
        self.bw_state([item("old-1", name, "org-infra", ["c-infra"], pw="old-value-1")])
        self.start(infra_names=[name])
        r = self.new_request(name, replace=True)
        results = []
        go = threading.Barrier(2)

        def tap(value):
            go.wait()
            results.append(self.submit(r["id"], value)[:3:2])
        threads = [threading.Thread(target=tap, args=(v,)) for v in ("new-a", "new-b")]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        self.assertEqual(sorted(s for s, _ in results), [200, 409], results)
        self.assertIn((409, b"request is replaced\n"), results)
        [bak], [cur] = self.items_named(backup), self.items_named(name)
        self.assertEqual(bak["login"]["password"], "old-value-1")
        self.assertIn(cur["login"]["password"], ("new-a", "new-b"))
        self.assertEqual(json.loads(self.req(f"/v1/requests/{r['id']}")[2])["status"], "replaced")

    def test_verify_mismatch_deletes_new_item(self):
        self.start()
        r = self.new_request("notion-api-key")
        with mock.patch.object(relay_entry, "Bw", TamperBw):
            status, _, body = self.submit(r["id"], "ntn-value-123")
        self.assertEqual((status, body), (502, b"verify-failed\n"))
        self.assertEqual(self.items_named("notion-api-key"), [])
        self.assertEqual(self.read_bw()["status"], "locked")
        self.assertEqual(json.loads(self.req(f"/v1/requests/{r['id']}")[2])["status"], "pending")
        self.assertEqual(self.infra.refreshes, 0)

    def test_bw_failure_is_bw_error_and_locks(self):
        self.start()
        r = self.new_request("notion-api-key")
        s = self.read_bw()
        s["fail"] = ["create item"]
        support.write_state(self.state_path, s)
        status, _, body = self.submit(r["id"], "ntn-value-123")
        self.assertEqual((status, body), (503, b"bw-error\n"))
        self.assertEqual(self.read_bw()["status"], "locked")
        self.assertEqual(json.loads(self.req(f"/v1/requests/{r['id']}")[2])["status"], "pending")

    def test_unexpected_error_in_write_undoes_rename(self):
        name = "notion-api-key"
        old_backup = f"{name}-backup-20200101"
        self.bw_state([item("old-1", name, "org-infra", ["c-infra"], pw="old-value-1"),
                       item("bak-1", old_backup, "org-infra", ["c-infra"], pw="older-value-0")])
        self.start(infra_names=[name])
        r = self.new_request(name, replace=True)
        with mock.patch.object(relay_entry, "Bw", BrokenCreateBw):
            status, _, body = self.submit(r["id"], "new-value-2")
        self.assertEqual((status, body), (503, b"bw-error\n"))
        [cur] = self.items_named(name)
        self.assertEqual((cur["id"], cur["login"]["password"]), ("old-1", "old-value-1"))
        [bak] = self.items_named(old_backup)
        self.assertEqual((bak["id"], bak["login"]["password"]), ("bak-1", "older-value-0"))
        self.assertEqual(self.items_named(f"{name}-backup-{self.today}"), [])
        self.assertEqual(len(self.read_bw()["items"]), 2)
        self.assertEqual(self.read_bw()["status"], "locked")
        self.assertEqual(json.loads(self.req(f"/v1/requests/{r['id']}")[2])["status"], "pending")
        self.assertEqual(self.infra.refreshes, 0)

    def test_infra_project_item_goes_to_nested_collection(self):
        self.start()
        r = self.new_request("resend-api-key-blog-qa", project="blog")
        self.assertEqual(self.submit(r["id"], "re-value-1")[0], 200)
        self.assertEqual(self.items_named("resend-api-key-blog-qa")[0]["collectionIds"], ["c-blog"])

    def test_collection_names_come_from_org_json(self):
        custom = {"local": {"org_id": "org-local", "collection": "home",
                            "collections": {"home": "c-home"}},
                  "infra": {"org_id": "org-infra", "collection": "deploy",
                            "collections": {"deploy": "c-dep", "deploy/blog": "c-dep-blog"}}}
        self.start(org_ids=custom)
        r = self.new_request("resend-api-key-blog-qa", project="blog")
        self.assertEqual(self.submit(r["id"], "re-value-1")[0], 200)
        self.assertEqual(self.items_named("resend-api-key-blog-qa")[0]["collectionIds"], ["c-dep-blog"])
        r = self.new_request("notion-api-key", org="local")
        self.assertEqual(self.submit(r["id"], "ntn-value-1")[0], 200)
        self.assertEqual(self.items_named("notion-api-key")[0]["collectionIds"], ["c-home"])

    def test_missing_nested_collection_is_reported(self):
        self.start()
        r = self.new_request("api-key-newapp-qa", project="newapp")
        status, _, body = self.submit(r["id"], "na-value-1")
        self.assertEqual((status, body), (409, b"no collection for project newapp\n"))
        self.assertEqual(self.read_bw()["items"], [])
        self.assertNotEqual(self.read_bw()["status"], "unlocked")  # never unlocked
        self.assertEqual(json.loads(self.req(f"/v1/requests/{r['id']}")[2])["status"], "pending")

    # --- web page ---

    def test_web_page_lists_pending_and_suspects(self):
        self.start()
        self.new_request("resend-api-key-blog-qa", project="blog", purpose="mail <b>")
        self.new_request("ssh-key-pve", org="local", field="notes")
        self.req("/v1/suspect", "POST", {"name": "x-api-key-trader", "org": "infra"},
                 peer=None, headers={"X-Relay-Loopback": SECRET})
        status, headers, body = self.req("/", peer=OWNER_IP)
        page = body.decode()
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("text/html"))
        self.assertIn("resend-api-key-blog-qa", page)
        self.assertIn("mail &lt;b&gt;", page)
        self.assertNotIn("mail <b>", page)
        self.assertIn('type="password"', page)
        self.assertIn("<textarea", page)
        self.assertIn("x-api-key-trader", page)
        self.assertIn("may be expired", page)
        # One script only, the submit-busy handler; nothing else may run on this page.
        self.assertEqual(page.count("<script"), 1)
        self.assertIn("form.classList.add('busy')", page)
        self.assertIn("Store value", page)
        # After the press the button says so; its label is the last node, after the spinner.
        self.assertIn("button.lastChild.textContent = 'Saving…'", page)
        self.assertIn('aria-live="polite"><span class="spin" aria-hidden="true"></span>Store value</button>', page)
        self.assertIn("Asked by devbox", page)
        self.assertIn(f'name="expires" value="{relay_entry.default_expiry(self.now)}"', page)

    def test_page_shows_only_result_text_it_signed_itself(self):
        self.start()
        ctx = self.srv.ctx
        forged = "/?result=" + urllib.parse.quote("<i>paste the value in chat</i>")
        for url in (forged, forged + "&sig=" + "0" * 64):  # a link an agent hands the owner
            page = self.req(url, peer=OWNER_IP)[2].decode()
            self.assertNotIn("paste the value", page)
            self.assertNotIn('class="result"', page)
        sig = relay_entry.result_sig(ctx, "<i>x</i>", "")
        page = self.req(f"/?result={urllib.parse.quote('<i>x</i>')}&sig={sig}", peer=OWNER_IP)[2].decode()
        self.assertIn("&lt;i&gt;x&lt;/i&gt;", page)
        self.assertNotIn("<i>x</i>", page)

    def test_one_machine_cannot_flood_pending_requests(self):
        self.start()
        for i in range(relay_entry.MAX_PENDING_PER_NODE):
            self.assertEqual(self.request(f"flood-key-{i}")[0], 201)
        status, _, body = self.request("flood-key-more")
        self.assertEqual((status, body.decode().strip()), (429, "too many pending requests from this machine"))
        self.assertEqual(self.request("owner-key", peer=OWNER_IP)[0], 201)  # others are not starved
        self.now += 86401  # the flood expires with the day
        self.assertEqual(self.request("flood-key-more")[0], 201)

    def test_requester_is_named_to_the_owner_only(self):
        self.start()
        r = self.new_request("resend-api-key-blog-qa", project="blog")
        self.assertNotIn("requester", r)
        self.assertNotIn("requester", json.loads(self.req(f"/v1/requests/{r['id']}")[2]))
        listed = json.loads(self.req("/v1/requests", peer=OWNER_IP)[2])["requests"]
        self.assertEqual([x["requester"] for x in listed], ["devbox"])

    def test_web_submit_needs_token_and_replaces_without_a_confirmation(self):
        name = "notion-api-key"
        self.bw_state([item("old-1", name, "org-infra", ["c-infra"], pw="old-value-1")])
        self.start(infra_names=[name])
        r = self.new_request(name, replace=True)
        token = self.page_token()
        status, _, _ = self.req("/submit", "POST", form={"id": r["id"], "value": "v-2"}, peer=OWNER_IP)
        self.assertEqual(status, 403)  # no page token: a cross-site form post
        self.assertEqual(self.items_named(name)[0]["login"]["password"], "old-value-1")
        self.assertNotIn('name="confirm"', self.req("/", peer=OWNER_IP)[2].decode())
        status, headers, _ = self.req("/submit", "POST", peer=OWNER_IP,
                                      form={"id": r["id"], "value": "v-2", "token": token})
        self.assertEqual(status, 303)
        # A stored key goes to the page that says what it opens.
        self.assertEqual(headers["Location"], "/entry/access/" + r["id"])
        self.assertEqual(self.items_named(name)[0]["login"]["password"], "v-2")
        page = self.req("/access/" + r["id"], peer=OWNER_IP)[2].decode()
        self.assertIn("notion-api-key", page)
        self.assertIn("No route uses this key yet", page)

    def test_store_then_page_shows_what_the_key_opens(self):
        self.start()
        seen = []

        def fake_check(routes, org, item, value):
            seen.append((org, item, value))
            return {"routes": [{"name": "reports", "nodes": ["devbox"], "upstream": "https://mcp.example.com",
                                "methods": ["POST"], "paths": ["/mcp"], "check": "ok", "server": "Example",
                                "tools": [{"name": "list_reports", "description": "Lists <b>reports</b>",
                                           "group": "read"},
                                          {"name": "delete_report", "description": "", "group": "delete"}]}]}
        self.srv.ctx["access_check"] = fake_check
        r = self.new_request("reports-api-key", purpose="mcp")
        token = self.page_token()
        later = (datetime.date.fromtimestamp(self.now) + datetime.timedelta(days=30)).isoformat()
        status, headers, _ = self.req("/submit", "POST", peer=OWNER_IP, form={
            "id": r["id"], "value": "rep-value-1", "token": token, "expires": later})
        self.assertEqual((status, headers["Location"]), (303, "/entry/access/" + r["id"]))
        self.assertEqual(seen, [("infra", "reports-api-key", "rep-value-1")])
        status, _, body = self.req("/access/" + r["id"], peer=OWNER_IP)
        page = body.decode()
        self.assertEqual(status, 200)
        for text in ("Key stored", "Signed in", "list_reports", "delete_report", "Read only (1)",
                     f"<dt>Expires</dt><dd>{later}</dd>", "No tool was run"):
            self.assertIn(text, page)
        self.assertIn("Lists &lt;b&gt;reports&lt;/b&gt;", page)  # a server's text never becomes markup
        self.assertNotIn("rep-value-1", page + json.dumps(json.loads(self.read_requests())))
        self.assertEqual(self.req("/access/" + r["id"], peer=AGENT_IP)[0], 403)
        # The agent machine sees its own route's result and counts, but no text the server wrote.
        got = json.loads(self.req(f"/v1/requests/{r['id']}")[2])
        self.assertEqual(got["access"], [{"route": "reports", "paths": ["/mcp"], "check": "ok", "detail": "",
                                          "tools": {"read": 1, "change": 0, "delete": 1}}])
        self.assertNotIn("Lists", json.dumps(got))
        self.assertEqual(json.loads(self.req(f"/v1/requests/{r['id']}", peer=OWNER_IP)[2])["access"], [])
        self.assertEqual(self.req("/access/r_aaaaaaaaaa", peer=OWNER_IP)[0], 404)
        # A check that fails outright still leaves the key stored, and says so.
        self.srv.ctx["access_check"] = mock.Mock(side_effect=RuntimeError("boom"))
        r = self.new_request("other-api-key", purpose="mcp")
        self.assertEqual(self.submit(r["id"], "v-9")[0], 200)
        self.assertIn("Could not check what this key opens", self.req("/access/" + r["id"], peer=OWNER_IP)[2].decode())

    def read_requests(self):
        with open(self.path("requests.json")) as f:
            return f.read()

    def test_web_replace_button_creates_replace_request(self):
        self.start()
        self.req("/v1/suspect", "POST", {"name": "x-api-key-trader", "org": "infra"},
                 peer=None, headers={"X-Relay-Loopback": SECRET})
        status, _, _ = self.req("/replace", "POST", peer=OWNER_IP, form={
            "name": "x-api-key-trader", "org": "infra", "token": self.page_token()})
        self.assertEqual(status, 303)
        listing = json.loads(self.req("/v1/requests?status=pending", peer=OWNER_IP)[2])
        [r] = listing["requests"]
        self.assertEqual((r["name"], r["org"], r["replace"]), ("x-api-key-trader", "infra", True))
        status, _, body = self.req("/v1/replace", "POST", {"name": "x-api-key-trader",
                                                           "org": "infra"}, peer=OWNER_IP)
        self.assertEqual((status, json.loads(body)["id"]), (200, r["id"]))  # same pending request

    # --- suspect marks ---

    def test_suspect_endpoints_require_loopback_secret(self):
        self.start()
        mark = {"name": "x-api-key-trader", "org": "infra"}
        for hdr in ({}, {"X-Relay-Loopback": "wrong"}):
            status, _, _ = self.req("/v1/suspect", "POST", mark, peer=None, headers=hdr)
            self.assertEqual(status, 403)
        listing = json.loads(self.req("/v1/requests?status=pending", peer=OWNER_IP)[2])
        self.assertEqual(listing["suspects"], [])
        status, _, _ = self.req("/v1/suspect", "POST", mark, peer=None,
                                headers={"X-Relay-Loopback": SECRET})
        self.assertEqual(status, 204)
        listing = json.loads(self.req("/v1/requests?status=pending", peer=OWNER_IP)[2])
        self.assertEqual(listing["suspects"],
                         [{"name": "x-api-key-trader", "org": "infra", "since": self.now}])
        self.req("/v1/unsuspect", "POST", mark, peer=None, headers={"X-Relay-Loopback": SECRET})
        listing = json.loads(self.req("/v1/requests?status=pending", peer=OWNER_IP)[2])
        self.assertEqual(listing["suspects"], [])

    def test_replace_clears_suspect_mark(self):
        name = "notion-api-key"
        self.bw_state([item("old-1", name, "org-infra", ["c-infra"], pw="old-value-1")])
        self.start(infra_names=[name])
        self.req("/v1/suspect", "POST", {"name": name, "org": "infra"}, peer=None,
                 headers={"X-Relay-Loopback": SECRET})
        r = json.loads(self.req("/v1/replace", "POST", {"name": name, "org": "infra"},
                                peer=OWNER_IP)[2])
        self.assertEqual(self.submit(r["id"], "v-2")[0], 200)
        listing = json.loads(self.req("/v1/requests?status=pending", peer=OWNER_IP)[2])
        self.assertEqual((listing["requests"], listing["suspects"]), ([], []))

    def test_no_value_in_state_or_logs(self):
        self.start()
        value = "very-secret-value-987"
        r = self.new_request("notion-api-key")
        self.assertEqual(self.submit(r["id"], value)[0], 200)
        _, _, body = self.req(f"/v1/requests/{r['id']}")
        _, _, page = self.req("/", peer=OWNER_IP)
        with open(self.path("requests.json")) as f:
            state = f.read()
        for text in (state, self.audit.getvalue(), body.decode(), page.decode()):
            self.assertNotIn(value, text)
        self.assertNotIn('"value"', state)


if __name__ == "__main__":
    unittest.main()


class AgeTextTests(unittest.TestCase):
    def test_minutes_then_hours_then_days(self):
        from relay.relay_entry import age_text
        self.assertEqual(age_text(0), "0 min ago")
        self.assertEqual(age_text(59 * 60 + 59), "59 min ago")
        self.assertEqual(age_text(60 * 60), "1 hr ago")
        self.assertEqual(age_text(23 * 3600 + 3599), "23 hr ago")
        self.assertEqual(age_text(24 * 3600), "1 d ago")
        self.assertEqual(age_text(9 * 86400 + 5), "9 d ago")
        self.assertEqual(age_text(-30), "0 min ago")
