import io
import json
import os
import socket
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from broker import broker_read
from broker.brokerlib.identity import Whois
from broker.tests import support

SECRET = "loop-secret-xyz"
VALUES = {
    "database-url-shop-qa": "postgres://u:dbvalue-111@h/db",  # gitleaks:allow (fake test value)
    "acme-auth-id-shop-prod": "acme-value-222",
    "x-api-key-trader": "algo-value-444",
    "agentbot-hooks-token-bot1": "hooks-value-555",
}
NOTES = "notes-line-one\nnotes-line-two"
ORG_IDS = {
    "infra": {"org_id": "org-infra", "collections": {"infra-broker-collection": "c-infra",
                                                     "infra-broker-collection/shop": "c-pg"}},
    "local": {"org_id": "org-local", "collections": {"local-broker-collection": "c-local"}},
}
ROUTES = """
[security]
trusted_value_nodes = ["devbox", "bot1"]

[project.shop]
org   = "infra"
nodes = ["devbox"]
items = ["*-shop-qa", "*-shop-prod"]

[value.agentbot-hooks-token-bot1]
org   = "local"
item  = "agentbot-hooks-token-bot1"
nodes = ["bot1"]

[api.stripe-shop]
org      = "local"
item     = "stripe-api-key-shop-live"
upstream = "https://127.0.0.1"
auth     = "Bearer {{}}"
methods  = ["GET", "POST"]
paths    = ["/v1/"]
nodes    = ["devbox"]
"""


def item(i, name, org, cols, pw=None, notes=None):
    return {"id": i, "name": name, "organizationId": org, "collectionIds": cols,
            "login": {"password": pw}, "notes": notes}


def base_items():
    return [
        item("1", "database-url-shop-qa", "org-infra", ["c-pg"],
             pw=VALUES["database-url-shop-qa"]),
        item("2", "acme-auth-id-shop-prod", "org-infra", ["c-pg"],
             pw=VALUES["acme-auth-id-shop-prod"]),
        item("3", "pem-shop-qa", "org-infra", ["c-pg"], notes=NOTES),
        item("4", "x-api-key-trader", "org-infra", ["c-infra"], pw=VALUES["x-api-key-trader"]),
        item("5", "agentbot-hooks-token-bot1", "org-local", ["c-local"],
             pw=VALUES["agentbot-hooks-token-bot1"]),
    ]


NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def call(url, method="GET", headers=None, data=None):
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with NO_PROXY.open(req, timeout=10) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        with e:
            return e.code, dict(e.headers), e.read()


def wait_for(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


def serve(handler_cls):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class BrokerReadTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state_path = self.path("state.json")
        self.state()
        self.now = 1000.0

    def path(self, name):
        return os.path.join(self.tmp.name, name)

    def state(self, items=None, **extra):
        s = {"status": "unlocked", "items": base_items() if items is None else items}
        s.update(extra)
        support.write_state(self.state_path, s)

    def write(self, name, text):
        with open(self.path(name), "w") as f:
            f.write(text)
        return self.path(name)

    def start(self, org="infra", machine="devbox", upstream_port=9, entry_url="http://127.0.0.1:9",
              extra_whois=None, routes=ROUTES):
        whois = {"127.0.0.1": f"{machine}.test.ts.net."} if machine else {}
        whois.update(extra_whois or {})
        env = support.fresh_env(self.state_path, whois)

        def runner(cmd, **kw):
            return subprocess.run(cmd, env=env, **kw)

        bw_env = self.write("bw.env", "\n".join([
            f"PATH={support.fake_path()}", f"FAKE_BW_STATE={self.state_path}",
            "BW_SESSION=test-session", f"BITWARDENCLI_APPDATA_DIR={self.tmp.name}", ""]))
        argv = ["--org", org,
                "--routes", self.write("routes.toml", routes.format(upstream_port=upstream_port)),
                "--port", "0",
                "--org-ids", self.write(f"org-ids-{org}.json", json.dumps(ORG_IDS[org])),
                "--loopback-secret-file", self.write("loopback", SECRET + "\n"),
                "--entry-url", entry_url,
                "--bw-env-file", bw_env,
                "--upstream-scheme", "http"]
        self.audit = io.StringIO()
        srv = broker_read.build_server(argv, whois=Whois(runner=runner), clock=lambda: self.now,
                                      audit_out=self.audit)
        self.srv = srv
        # Only the test transport connects to the isolated fake upstream's dynamic port.
        if org == "local":
            srv.ctx["routes"].api["stripe-shop"].upstream = f"https://127.0.0.1:{upstream_port}"
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()

        def stop():
            srv.shutdown()
            srv.server_close()
            t.join(5)
        self.addCleanup(stop)
        self.base = f"http://127.0.0.1:{srv.server_address[1]}"
        return self.base

    def get(self, path, **kw):
        return call(self.base + path, **kw)

    def audit_has(self, text):
        return wait_for(lambda: text in self.audit.getvalue())

    def test_listen_backlog_is_128(self):
        self.start()
        self.assertEqual(self.srv.request_queue_size, 128)

    # --- project routes ---

    def test_project_value_for_project_item(self):
        self.start()
        status, headers, body = self.get("/shop/value/database-url-shop-qa")
        self.assertEqual(status, 200)
        self.assertEqual(body, VALUES["database-url-shop-qa"].encode())
        self.assertEqual(headers["Content-Type"], "application/octet-stream")
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertTrue(self.audit_has("/shop/value/database-url-shop-qa 200"))
        line = self.audit.getvalue()
        self.assertTrue(line.startswith("devbox shop GET "), line)
        self.assertNotIn("dbvalue-111", line)

    def test_project_prod_item_served(self):
        self.start()
        status, _, body = self.get("/shop/value/acme-auth-id-shop-prod")
        self.assertEqual((status, body), (200, VALUES["acme-auth-id-shop-prod"].encode()))

    def test_secret_outside_project_items_is_403(self):
        self.start()
        status, _, body = self.get("/shop/value/x-api-key-trader")
        self.assertEqual((status, body), (403, b"not allowed\n"))
        self.assertTrue(self.audit_has(" 403"))
        self.assertNotIn("algo-value-444", self.audit.getvalue())

    def test_project_ref_with_odd_name_is_403(self):
        self.start()
        for ref in ("x/database-url-shop-qa", "a..b-shop-qa", "UPPER-shop-qa"):
            status, _, _ = self.get("/shop/value/" + ref)
            self.assertEqual(status, 403, ref)

    def test_project_missing_item_is_404(self):
        self.start()
        status, _, body = self.get("/shop/value/nothing-here-shop-qa")
        self.assertEqual((status, body), (404, b"not found\n"))

    def test_project_value_notes_ref_returns_notes(self):
        self.start()
        status, _, body = self.get("/shop/value/pem-shop-qa/notes")
        self.assertEqual((status, body), (200, NOTES.encode()))
        status, _, _ = self.get("/shop/value/pem-shop-qa")
        self.assertEqual(status, 404)

    def test_caller_not_in_nodes_is_403(self):
        self.start(machine="tablet")
        status, _, body = self.get("/shop/value/database-url-shop-qa")
        self.assertEqual((status, body), (403, b"not allowed\n"))
        self.assertTrue(self.audit_has("tablet shop GET"))

    def test_unknown_caller_is_403(self):
        self.start(machine=None)
        status, _, body = self.get("/shop/value/database-url-shop-qa")
        self.assertEqual((status, body), (403, b"unknown caller\n"))
        status, _, body = self.get("/check?names=database-url-shop-qa")
        self.assertEqual((status, body), (403, b"unknown caller\n"))

    def test_wrong_org_project_is_404(self):
        self.start(org="local")
        status, _, _ = self.get("/shop/value/database-url-shop-qa")
        self.assertEqual(status, 404)

    def test_unknown_first_segment_is_404(self):
        self.start()
        self.assertEqual(self.get("/nope/value/x")[0], 404)
        self.assertEqual(self.get("/shop/other/x")[0], 404)

    def test_value_endpoints_are_get_only(self):
        self.start()
        status, _, _ = self.get("/shop/value/database-url-shop-qa", method="POST", data=b"")
        self.assertEqual(status, 405)

    # --- value routes ---

    def test_value_route_by_node(self):
        self.start(org="local", machine="devbox")
        status, _, body = self.get("/value/agentbot-hooks-token-bot1")
        self.assertEqual((status, body), (403, b"not allowed\n"))
        self.assertEqual(self.get("/value/no-such-route")[0], 403)

    def test_value_route_for_listed_node(self):
        self.start(org="local", machine="bot1")
        status, headers, body = self.get("/value/agentbot-hooks-token-bot1")
        self.assertEqual((status, body), (200, VALUES["agentbot-hooks-token-bot1"].encode()))
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertTrue(self.audit_has("bot1 value/agentbot-hooks-token-bot1 GET"))
        self.assertNotIn("hooks-value-555", self.audit.getvalue())

    def test_value_route_missing_item_is_404(self):
        self.state(items=[])
        self.start(org="local", machine="bot1")
        self.assertEqual(self.get("/value/agentbot-hooks-token-bot1")[:3:2], (404, b"not found\n"))

    # --- check ---

    def test_check_reports_ok_missing_not_allowed_and_dedupes(self):
        self.start()
        status, headers, body = self.get(
            "/check?names=database-url-shop-qa,database-url-shop-qa,"
            "gone-shop-qa,x-api-key-trader")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(json.loads(body), {"database-url-shop-qa": "ok",
                                            "gone-shop-qa": "missing",
                                            "x-api-key-trader": "not-allowed"})
        self.assertTrue(self.audit_has("devbox check GET /check 200"))
        self.assertNotIn("names=", self.audit.getvalue())

    def test_check_notes_refs_and_empty_fields(self):
        items = base_items() + [item("8", "empty-pw-shop-qa", "org-infra", ["c-pg"], pw="")]
        self.state(items=items)
        self.start()
        status, _, body = self.get("/check?names=pem-shop-qa/notes,pem-shop-qa,"
                                   "empty-pw-shop-qa,database-url-shop-qa/notes,"
                                   "x-api-key-trader/notes")
        self.assertEqual(json.loads(body), {"pem-shop-qa/notes": "ok",
                                            "pem-shop-qa": "missing",
                                            "empty-pw-shop-qa": "missing",
                                            "database-url-shop-qa/notes": "missing",
                                            "x-api-key-trader/notes": "not-allowed"})
        # check predicts the fetch exactly
        self.assertEqual(self.get("/shop/value/pem-shop-qa/notes")[0], 200)
        self.assertEqual(self.get("/shop/value/empty-pw-shop-qa")[0], 404)

    def test_check_value_route_item(self):
        self.start(org="local", machine="bot1")
        status, _, body = self.get("/check?names=agentbot-hooks-token-bot1")
        self.assertEqual(json.loads(body), {"agentbot-hooks-token-bot1": "ok"})

    def test_api_only_node_checks_presence_without_raw_or_backup_access(self):
        name = "stripe-api-key-shop-live"
        self.state(items=base_items() + [item("99", name, "org-local", ["c-local"], pw="synthetic-api-value")])
        self.start(org="local")
        self.srv.ctx["routes"].trusted_value_nodes = []
        self.srv.ctx["routes"].agent_nodes = ["devbox"]
        status, _, body = self.get(f"/check?names={name},{name}/notes,{name}-backup-20261117")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {name: "ok", name + "/notes": "not-allowed",
                                          name + "-backup-20261117": "not-allowed"})
        self.assertNotIn(b"synthetic-api-value", body)
        self.assertEqual(self.get(f"/value/{name}")[0], 403)
        self.assertEqual(self.get(f"/backups/{name}")[0], 403)

    def test_check_empty_and_too_many(self):
        self.start()
        status, _, body = self.get("/check?names=")
        self.assertEqual((status, json.loads(body)), (200, {}))
        names = ",".join(f"n{i}-shop-qa" for i in range(501))
        status, _, body = self.get("/check?names=" + names)
        self.assertEqual((status, body), (413, b"too many names\n"))
        names = ",".join(f"n{i}-shop-qa" for i in range(500))
        self.assertEqual(self.get("/check?names=" + names)[0], 200)

    # --- backups ---

    def with_backups(self, *dates, base="database-url-shop-qa", org="org-infra", cols=("c-pg",)):
        extra = [item(f"b{d}", f"{base}-backup-{d}", org, list(cols), pw=f"backup-value-{d}")
                 for d in dates]
        self.state(items=base_items() + extra)

    def test_project_backup_readable_by_base_caller(self):
        self.with_backups("20261106", "20261030")
        self.start()
        status, _, body = self.get("/shop/value/database-url-shop-qa-backup-20261106")
        self.assertEqual((status, body), (200, b"backup-value-20261106"))
        status, _, body = self.get("/check?names=database-url-shop-qa-backup-20261106,"
                                   "database-url-shop-qa-backup-20200101,"
                                   "x-api-key-trader-backup-20261106")
        self.assertEqual(json.loads(body), {"database-url-shop-qa-backup-20261106": "ok",
                                            "database-url-shop-qa-backup-20200101": "missing",
                                            "x-api-key-trader-backup-20261106": "not-allowed"})
        self.assertNotIn("backup-value", self.audit.getvalue())

    def test_backup_name_needs_ascii_digits_and_absolute_end(self):
        self.assertEqual(broker_read.base_name("a-key-backup-20261106"), "a-key")
        for name in ("a-key-backup-2026110\u0666", "a-key-backup-20261106\n", "a-key-backup-2026110"):
            self.assertEqual(broker_read.base_name(name), name, repr(name))

    def test_backup_notes_ref(self):
        extra = [item("b1", "pem-shop-qa-backup-20261106", "org-infra", ["c-pg"], notes="old-notes")]
        self.state(items=base_items() + extra)
        self.start()
        status, _, body = self.get("/shop/value/pem-shop-qa-backup-20261106/notes")
        self.assertEqual((status, body), (200, b"old-notes"))
        status, _, body = self.get("/check?names=pem-shop-qa-backup-20261106/notes")
        self.assertEqual(json.loads(body), {"pem-shop-qa-backup-20261106/notes": "ok"})

    def test_backup_not_allowed_when_base_not_allowed(self):
        self.with_backups("20261106", base="x-api-key-trader", cols=("c-infra",))
        self.start()
        status, _, body = self.get("/shop/value/x-api-key-trader-backup-20261106")
        self.assertEqual((status, body), (403, b"not allowed\n"))
        self.start(machine="tablet")
        status, _, _ = self.get("/shop/value/database-url-shop-qa-backup-20261106")
        self.assertEqual(status, 403)
        status, _, body = self.get("/check?names=database-url-shop-qa-backup-20261106")
        self.assertEqual(json.loads(body), {"database-url-shop-qa-backup-20261106": "not-allowed"})
        self.assertNotIn("backup-value", self.audit.getvalue())

    def test_backups_lists_dates_newest_first(self):
        self.with_backups("20261030", "20261106")
        self.start()
        status, headers, body = self.get("/backups/database-url-shop-qa")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(json.loads(body), {"name": "database-url-shop-qa",
                                            "backups": ["20261106", "20261030"]})
        self.assertNotIn(b"backup-value", body)
        self.assertTrue(self.audit_has("devbox backups GET /backups/database-url-shop-qa 200"))
        self.assertNotIn("backup-value", self.audit.getvalue())
        self.assertNotIn("dbvalue-111", self.audit.getvalue())

    def test_backups_empty_unknown_and_not_allowed(self):
        self.with_backups("20261106", base="x-api-key-trader", cols=("c-infra",))
        self.start()
        status, _, body = self.get("/backups/acme-auth-id-shop-prod")
        self.assertEqual((status, json.loads(body)),
                         (200, {"name": "acme-auth-id-shop-prod", "backups": []}))
        status, _, body = self.get("/backups/nothing-here-shop-qa")
        self.assertEqual((status, body), (404, b"not found\n"))
        status, _, body = self.get("/backups/x-api-key-trader")
        self.assertEqual((status, body), (403, b"not allowed\n"))
        self.assertEqual(self.get("/backups/nothing-at-all")[:3:2], (403, b"not allowed\n"))
        self.assertEqual(self.get("/backups/")[0], 403)
        self.assertEqual(self.get("/backups/database-url-shop-qa", method="POST", data=b"")[0], 405)
        self.assertTrue(self.audit_has("devbox backups GET /backups/x-api-key-trader 403"))

    def test_backups_unknown_caller_is_403(self):
        self.start(machine=None)
        status, _, body = self.get("/backups/database-url-shop-qa")
        self.assertEqual((status, body), (403, b"unknown caller\n"))

    def test_value_route_backup_for_its_node_only(self):
        self.with_backups("20261106", "20261030", base="agentbot-hooks-token-bot1", org="org-local",
                          cols=("c-local",))
        self.start(org="local", machine="bot1")
        status, _, body = self.get("/value/agentbot-hooks-token-bot1-backup-20261106")
        self.assertEqual((status, body), (200, b"backup-value-20261106"))
        self.assertEqual(self.get("/value/agentbot-hooks-token-bot1-backup-20200101")[0], 404)
        status, _, body = self.get("/check?names=agentbot-hooks-token-bot1-backup-20261030")
        self.assertEqual(json.loads(body), {"agentbot-hooks-token-bot1-backup-20261030": "ok"})
        status, _, body = self.get("/backups/agentbot-hooks-token-bot1")
        self.assertEqual(json.loads(body)["backups"], ["20261106", "20261030"])
        self.assertNotIn("backup-value", self.audit.getvalue())
        self.start(org="local", machine="devbox")
        self.assertEqual(self.get("/value/agentbot-hooks-token-bot1-backup-20261106")[0], 403)
        self.assertEqual(self.get("/backups/agentbot-hooks-token-bot1")[0], 403)

    def test_backups_not_rate_limited(self):
        self.with_backups("20261106")
        self.start()
        path = "/shop/value/database-url-shop-qa"
        for i in range(300):
            self.assertEqual(self.get(path)[0], 200, i)
        self.assertEqual(self.get(path)[0], 429)
        self.assertEqual(self.get("/shop/value/database-url-shop-qa-backup-20261106")[0], 429)
        for _ in range(5):
            self.assertEqual(self.get("/backups/database-url-shop-qa")[0], 200)

    # --- loopback endpoints ---

    def test_refresh_requires_loopback_secret_and_invalidates(self):
        self.start()
        path = "/shop/value/new-key-shop-qa"
        self.assertEqual(self.get(path)[0], 404)
        items = base_items() + [item("9", "new-key-shop-qa", "org-infra", ["c-pg"], pw="new-666")]
        self.state(items=items)
        self.assertEqual(self.get(path)[0], 404)  # cached
        status, _, _ = self.get("/refresh", method="POST", data=b"", headers={"X-Broker-Loopback": "wrong"})
        self.assertEqual(status, 403)
        status, _, _ = self.get("/refresh", method="POST", data=b"")
        self.assertEqual(status, 403)
        self.assertEqual(self.get(path)[0], 404)
        status, _, body = self.get("/refresh", method="POST", data=b"", headers={"X-Broker-Loopback": SECRET})
        self.assertEqual((status, body), (204, b""))
        self.assertEqual(self.get(path)[:3:2], (200, b"new-666"))

    def test_refresh_syncs_and_failed_sync_is_audited(self):
        self.start()
        path = "/shop/value/database-url-shop-qa"
        self.assertEqual(self.get(path)[:3:2], (200, VALUES["database-url-shop-qa"].encode()))
        items = base_items()
        items[0]["login"]["password"] = "replaced-777"
        items.append(item("9", "new-key-shop-qa", "org-infra", ["c-pg"], pw="new-666"))
        self.state(items=items, fail=["sync"])
        self.get("/refresh", method="POST", data=b"", headers={"X-Broker-Loopback": SECRET})
        # sync fails: the last local copy is still served, and the request says so
        self.assertEqual(self.get(path)[:3:2], (200, VALUES["database-url-shop-qa"].encode()))
        self.assertIn(f"devbox shop GET {path} 200 sync-failed", self.audit.getvalue())
        self.assertEqual(self.get("/shop/value/new-key-shop-qa")[0], 404)
        self.state(items=items)
        self.get("/refresh", method="POST", data=b"", headers={"X-Broker-Loopback": SECRET})
        self.assertEqual(self.get(path)[:3:2], (200, b"replaced-777"))
        self.assertEqual(self.get("/shop/value/new-key-shop-qa")[:3:2], (200, b"new-666"))
        status, _, body = self.get("/names", headers={"X-Broker-Loopback": SECRET})
        self.assertIn("new-key-shop-qa", json.loads(body)["names"])

    def test_names_requires_loopback_secret(self):
        dated = dict(item("9", "dated-key-shop-qa", "org-infra", ["c-pg"], pw="synthetic"),
                     fields=[{"name": "expires", "value": "2027-01-31", "type": 0},
                             {"name": "other", "value": "kept out", "type": 0}])
        self.state(items=base_items() + [dated])
        self.start()
        self.assertEqual(self.get("/names", headers={"X-Broker-Loopback": "nope"})[0], 403)
        status, _, body = self.get("/names", headers={"X-Broker-Loopback": SECRET})
        self.assertEqual(status, 200)
        # Names, and the expiry date of each key stored with one. Never a value or another field.
        self.assertEqual(json.loads(body), {"names": sorted(
            ["database-url-shop-qa", "acme-auth-id-shop-prod", "pem-shop-qa",
             "x-api-key-trader", "dated-key-shop-qa"]), "expires": {"dated-key-shop-qa": "2027-01-31"}})
        self.assertNotIn(b"synthetic", body)
        self.assertNotIn(b"kept out", body)

    # --- request smuggling ---

    def test_audit_line_never_carries_control_bytes(self):
        # ESC or NUL in a request target would make journalctl show the line as "[blob data]".
        self.start()
        self.raw(b"GET /value/a\x1b[2Jb\x00c HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
        self.assertTrue(self.audit_has("/value/a?[2Jb?c 403"))
        self.assertNotRegex(self.audit.getvalue(), r"[^\x20-\x7e\n]")

    def raw(self, data):
        """Send raw bytes on one connection; everything received until the server closes it."""
        port = int(self.base.rsplit(":", 1)[1])
        with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
            s.sendall(data)
            got = b""
            while True:
                chunk = s.recv(65536)  # socket.timeout here means the server kept it open
                if not chunk:
                    return got
                got += chunk

    def test_unread_body_is_not_parsed_as_next_request(self):
        self.start(machine="tablet", extra_whois={"10.0.0.5": "devbox.test.ts.net."})
        smuggled = (b"GET /shop/value/database-url-shop-qa HTTP/1.1\r\n"
                    b"Host: x\r\nX-Broker-Peer: 10.0.0.5\r\n\r\n")
        for path in (b"/check", b"/shop/value/database-url-shop-qa", b"/nope"):
            got = self.raw(b"POST " + path + b" HTTP/1.1\r\nHost: x\r\nContent-Length: "
                           + str(len(smuggled)).encode() + b"\r\n\r\n" + smuggled)
            self.assertEqual(got.count(b"HTTP/1.1 "), 1, got)
            self.assertNotIn(b"dbvalue-111", got)
        self.assertNotIn("devbox ", self.audit.getvalue())

    def test_transfer_encoding_rejected_and_closed(self):
        self.start(org="local", machine="devbox")
        for path in (b"/stripe-shop/v1/x", b"/check", b"/refresh"):
            got = self.raw(b"POST " + path + b" HTTP/1.1\r\nHost: x\r\nTransfer-Encoding: chunked\r\n\r\n"
                           b"5\r\nhello\r\n0\r\n\r\n")
            self.assertTrue(got.startswith(b"HTTP/1.1 400 "), got)
            self.assertTrue(got.endswith(b"unsupported transfer encoding\n"), got)
            self.assertEqual(got.count(b"HTTP/1.1 "), 1, got)

    def test_duplicate_content_length_rejected_by_backend(self):
        self.start()
        got = self.raw(b"POST /check HTTP/1.1\r\nHost: x\r\nContent-Length: 0\r\n"
                       b"Content-Length: 9\r\n\r\n")
        self.assertTrue(got.startswith(b"HTTP/1.1 400 "))
        self.assertEqual(got.count(b"HTTP/1.1 "), 1)

    # --- failures and limits ---

    def test_bw_failure_is_503_without_output(self):
        self.state(fail=["list items"])
        self.start()
        status, _, body = self.get("/shop/value/database-url-shop-qa")
        self.assertEqual((status, body), (503, b"bw-error\n"))
        status, _, body = self.get("/check?names=database-url-shop-qa")
        self.assertEqual((status, body), (503, b"bw-error\n"))
        self.assertTrue(self.audit_has("503 bw-error"))
        self.assertNotIn("fake bw failure", self.audit.getvalue())

    def test_rate_limit_429(self):
        self.start()
        path = "/shop/value/database-url-shop-qa"
        for i in range(300):
            self.assertEqual(self.get(path)[0], 200, i)
        status, _, body = self.get(path)
        self.assertEqual((status, body), (429, b"rate limited\n"))
        self.assertTrue(self.audit_has("429 rate-limited"))
        self.assertEqual(self.get("/check?names=database-url-shop-qa")[0], 200)
        self.now += 1.0  # 5 tokens per second
        for _ in range(5):
            self.assertEqual(self.get(path)[0], 200)
        self.assertEqual(self.get(path)[0], 429)

    # --- api routes ---

    def test_api_route_forwards_and_marks_suspect_on_401(self):
        seen = {"auth": [], "status": 401}
        marks, ctypes = [], []  # valetkey-broker-entry answers 415 without application/json

        class Upstream(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def do_GET(self):
                seen["auth"].append(self.headers.get("Authorization"))
                seen["override"] = self.headers.get("X-HTTP-Method-Override")
                seen["path"] = self.path
                body = b'{"error":"bad key"}' if seen["status"] == 401 else b'{"ok":true}'
                self.send_response(seen["status"])
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        class Entry(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                ctypes.append(self.headers.get("Content-Type"))
                marks.append((self.path, self.headers.get("X-Broker-Loopback"), json.loads(self.rfile.read(n))))
                self.send_response(204)
                self.end_headers()

        up, entry = serve(Upstream), serve(Entry)
        for s in (up, entry):
            self.addCleanup(s.server_close)
            self.addCleanup(s.shutdown)
        self.start(org="local", machine="devbox", upstream_port=up.server_address[1],
                   entry_url=f"http://127.0.0.1:{entry.server_address[1]}")
        self.srv.ctx["routes"].trusted_value_nodes = []
        self.srv.ctx["routes"].agent_nodes = ["devbox"]

        status, _, body = self.get("/stripe-shop/v1/charges?limit=1",
                                   headers={"Authorization": "Bearer caller-token", "X-HTTP-Method-Override": "DELETE"})
        self.assertEqual((status, body), (401, b'{"error":"bad key"}'))
        self.assertEqual(seen["auth"], ["Bearer fnox-ph-stripe-shop-not-a-secret"])
        self.assertEqual(seen["path"], "/v1/charges?limit=1")
        self.assertIsNone(seen["override"])
        self.assertTrue(wait_for(lambda: len(marks) == 1))
        self.assertEqual(marks[0], ("/v1/suspect", SECRET,
                                    {"name": "stripe-api-key-shop-live", "org": "local"}))
        self.assertTrue(self.audit_has("devbox stripe-shop GET /stripe-shop/v1/charges 401"))
        self.assertNotIn("limit=1", self.audit.getvalue())

        seen["status"] = 200
        status, _, body = self.get("/stripe-shop/v1/charges")
        self.assertEqual((status, body), (200, b'{"ok":true}'))
        self.assertTrue(wait_for(lambda: len(marks) == 2))
        self.assertEqual(marks[1][0], "/v1/unsuspect")
        self.assertEqual(ctypes, ["application/json", "application/json"])

    def test_hider_masks_every_form_across_chunks_without_delay(self):
        import base64
        key = "synthetic-key-0123456789"
        basic = base64.b64encode((key + ":").encode())
        h = broker_read.Hider(key)
        self.assertEqual(h.feed(b"data: hello\n\n"), b"data: hello\n\n")  # nothing held: streams stay live
        out = h.feed(b"a synthetic-key-01") + h.feed(b"23456789 b " + basic[:9]) + h.feed(basic[9:] + b" end")
        self.assertEqual(out + h.held, b"a " + b"*" * len(key) + b" b " + b"*" * len(basic) + b" end")
        self.assertEqual(h.feed(b"tail synthetic-"), b"tail ")  # could be the key: held
        self.assertEqual(h.feed(b"other"), b"synthetic-other")
        # a key stored as a Basic credential is also hidden decoded
        self.assertEqual(broker_read.Hider(basic.decode()).feed(b"k=" + key.encode() + b";"), b"k=" + b"*" * len(key) + b";")
        short = broker_read.Hider("abc")
        self.assertEqual((short.forms, short.feed(b"abc")), ([], b"abc"))

    def test_api_reply_that_echoes_the_key_is_masked(self):
        import gzip
        key = "synthetic-stripe-value-0123456789"
        seen = []

        class Upstream(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def do_GET(self):
                seen.append(self.headers.get("Accept-Encoding"))
                body = b'{"you sent": "Bearer %s"}' % key.encode()
                self.send_response(200)
                self.send_header("X-Echo", key)
                if self.path == "/v1/stream":
                    self.send_header("Transfer-Encoding", "chunked")
                    self.end_headers()
                    for part in (body[:20], body[20:]):  # the key is split over two chunks
                        self.wfile.write(b"%x\r\n%s\r\n" % (len(part), part))
                        self.wfile.flush()
                        time.sleep(0.05)
                    self.wfile.write(b"0\r\n\r\n")
                    return
                if self.path == "/v1/packed":
                    body = gzip.compress(body)
                    self.send_header("Content-Encoding", "gzip")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        up = serve(Upstream)
        self.addCleanup(up.server_close)
        self.addCleanup(up.shutdown)
        self.state(items=base_items() + [item("6", "stripe-api-key-shop-live", "org-local", ["c-local"], pw=key)])
        self.start(org="local", machine="devbox", upstream_port=up.server_address[1])
        masked = b'{"you sent": "Bearer %s"}' % (b"*" * len(key))
        for path in ("/stripe-shop/v1/echo", "/stripe-shop/v1/stream"):
            status, headers, body = self.get(path, headers={"Accept-Encoding": "gzip"})
            self.assertEqual((status, body, headers["X-Echo"]), (200, masked, "*" * len(key)), path)
        self.assertEqual(seen, ["identity", "identity"])  # the broker asks for a reply it can read
        self.assertTrue(self.audit_has("GET /stripe-shop/v1/stream 200 key hidden in reply"))
        status, _, body = self.get("/stripe-shop/v1/packed")
        self.assertEqual((status, gzip.decompress(body)), (200, masked.replace(b"*" * len(key), key.encode())))
        self.assertTrue(self.audit_has("GET /stripe-shop/v1/packed 200 reply not checked: encoded"))

    def test_api_route_whose_approval_ended_is_refused(self):
        self.start(org="local", machine="devbox")
        self.srv.ctx["routes"].api["stripe-shop"].until = {"devbox": 5000}
        self.srv.ctx["now"] = lambda: 5000
        status, _, body = self.get("/stripe-shop/v1/charges")
        self.assertEqual(status, 403)
        self.assertIn(b"approval of this route has ended", body)
        self.assertTrue(self.audit_has("GET /stripe-shop/v1/charges 403 approval ended"))
        self.assertEqual(json.loads(self.get("/check?names=stripe-api-key-shop-live")[2]),
                         {"stripe-api-key-shop-live": "not-allowed"})
        self.srv.ctx["now"] = lambda: 4999
        self.assertEqual(self.get("/stripe-shop/v1/charges")[0], 502)  # allowed: forwarded (nothing listens)
        self.assertEqual(json.loads(self.get("/check?names=stripe-api-key-shop-live")[2]),
                         {"stripe-api-key-shop-live": "missing"})

    def mcp_upstream(self, calls, listing=True):
        class Mcp(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def do_POST(self):
                msg = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                method = msg.get("method") if isinstance(msg, dict) else "batch"
                calls.append((method, (msg.get("params") or {}).get("name") if isinstance(msg, dict) else None,
                              self.headers.get("Authorization")))
                result = {"initialize": {"protocolVersion": "2025-06-18", "serverInfo": {"name": "fake"}},
                          "tools/list": {"tools": [{"name": "search", "annotations": {"readOnlyHint": True}},
                                                   {"name": "delete_page"}]}}.get(method, {"ok": True})
                listed = method not in ("initialize", "tools/list") or listing
                body = json.dumps({"jsonrpc": "2.0", "id": msg.get("id"), "result": result}).encode() if (
                    isinstance(msg, dict) and "id" in msg) else b""
                self.send_response((200 if body else 202) if listed else 500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        up = serve(Mcp)
        self.addCleanup(up.server_close)
        self.addCleanup(up.shutdown)
        self.start(org="local", machine="devbox", upstream_port=up.server_address[1])
        return self.srv.ctx["routes"].api["stripe-shop"]

    def rpc(self, method, name=None, raw=None, **extra):
        msg = dict({"jsonrpc": "2.0", "id": 7, "method": method}, **extra)
        if name is not None:
            msg["params"] = {"name": name, "arguments": {}}
        status, _, body = self.get("/stripe-shop/v1/mcp", method="POST", headers={"Content-Type": "application/json"},
                                   data=json.dumps(msg).encode() if raw is None else raw)
        return status, body

    def test_route_limited_to_named_tools_refuses_the_others_before_forwarding(self):
        calls = []
        self.mcp_upstream(calls).tools = ["search"]
        self.assertEqual(self.rpc("tools/call", "search"), (200, b'{"jsonrpc": "2.0", "id": 7, "result": {"ok": true}}'))
        self.assertEqual(self.rpc("tools/list")[0], 200)  # other MCP requests pass
        self.assertEqual(self.rpc("notifications/initialized", id=None)[0], 200)
        seen = len(calls)
        status, body = self.rpc("tools/call", "delete_page")
        self.assertEqual((status, json.loads(body)["id"], json.loads(body)["error"]["code"]), (200, 7, -32602))
        self.assertIn("has not allowed the tool 'delete_page'", json.loads(body)["error"]["message"])
        self.assertTrue(self.audit_has("POST /stripe-shop/v1/mcp 200 tool refused: delete_page"))
        twins = b'{"jsonrpc":"2.0","id":7,"method":"tools/call","params":{"name":"search","name":"delete_page"}}'
        batch = json.dumps([{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "search"}},
                            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "delete_page"}}]).encode()
        for raw, want in ((twins, 400), (b"not json", 400), (b'"text"', 400), (b"[1]", 400), (batch, 403),
                          (b'{"method":"tools/call","params":{"name":"delete_page"}}', 403),  # no id to answer
                          (b'{"id":1,"method":"tools/call","params":["search"]}', 200),
                          (b'{"id":1,"method":"tools\\u002fcall","params":{"name":"delete_page"}}', 200)):
            self.assertEqual(self.rpc(None, raw=raw)[0], want, raw)
        self.assertEqual(len(calls), seen)  # none of those reached the server
        self.assertIn(b'"error"', self.rpc(None, raw=b'{"id":1,"method":"tools\\u002fcall","params":{"name":"x"}}')[1])

    def test_route_limited_to_read_only_tools_follows_the_servers_labels(self):
        calls = []
        route = self.mcp_upstream(calls)
        route.tools = "read-only"
        self.assertEqual(self.rpc("tools/call", "search")[1], b'{"jsonrpc": "2.0", "id": 7, "result": {"ok": true}}')
        self.assertIn(b"only tools its server labels read-only", self.rpc("tools/call", "delete_page")[1])
        self.assertIn(b'"error"', self.rpc("tools/call", "unknown")[1])
        self.assertEqual([c[:2] for c in calls], [("initialize", None), ("notifications/initialized", None),
                                                  ("tools/list", None), ("tools/call", "search")])  # listed once
        self.assertEqual({c[2] for c in calls}, {"Bearer fnox-ph-stripe-shop-not-a-secret"})  # never the key itself
        self.now += broker_read.TOOLS_TTL
        self.rpc("tools/call", "search")
        self.assertEqual([c[0] for c in calls].count("tools/list"), 2)

    def test_read_only_route_refuses_every_call_when_the_tool_list_cannot_be_had(self):
        calls = []
        self.mcp_upstream(calls, listing=False).tools = "read-only"
        self.assertIn(b'"error"', self.rpc("tools/call", "search")[1])
        self.assertNotIn("tools/call", [c[0] for c in calls])

    def test_model_inference_forwards_only_approved_operations_and_headers(self):
        seen = []
        class Provider(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                seen.append((self.path, self.headers, body))
                status = int(body)
                self.send_response(status)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("X-Provider-Diagnostic", "fixture-private")
                self.end_headers()
                self.wfile.write(b"data: fixture\n\n" if status == 200 else b"fixture-private")

        upstream = serve(Provider)
        self.addCleanup(upstream.server_close)
        self.addCleanup(upstream.shutdown)
        model = ('\n[model.model-test]\nclient="claude"\nprovider="openrouter"\n'
                 'org="local"\nitem="model-key"\nnodes=["worker"]\n')
        self.start(org="local", machine="worker", routes=ROUTES + model)
        route = self.srv.ctx["routes"].api["model-test"]
        route.upstream = f"https://127.0.0.1:{upstream.server_port}/api"
        endpoint = "/model-test/v1/messages"
        headers = {"Authorization": "discard", "X-Api-Key": "discard", "Cookie": "discard",
                   "X-Custom": "discard", "Anthropic-Version": "2023-06-01"}
        status, response_headers, body = self.get(endpoint + "?beta=true", method="POST", headers=headers, data=b"200")
        self.assertEqual((status, body), (200, b"data: fixture\n\n"))
        self.assertNotIn("X-Provider-Diagnostic", response_headers)
        path, received, payload = seen[0]
        self.assertEqual((path, payload), ("/api/v1/messages?beta=true", b"200"))
        self.assertEqual(received["Authorization"], "Bearer " + broker_read.placeholder("model-test"))
        self.assertEqual(received["Anthropic-Version"], "2023-06-01")
        for key in ("Cookie", "X-Custom", "X-Api-Key"):
            self.assertIsNone(received[key])
        for path in (endpoint + "/batches", endpoint + "?extra=x", "/model-test/v1/responses"):
            self.assertEqual(self.get(path, method="POST", data=b"200")[0], 403)
        self.assertEqual(self.get(endpoint)[0], 405)
        self.assertEqual(self.get("/value/model-key")[0], 403)
        self.assertEqual(len(seen), 1)
        status, _, body = self.get(endpoint, method="POST", data=b"401")
        self.assertEqual(status, 401)
        self.assertNotIn(b"fixture-private", body)
        self.assertEqual(self.get(endpoint, method="POST", data=b"302")[0], 502)
        route.nodes = ["other"]
        self.assertEqual(self.get(endpoint, method="POST", data=b"200")[0], 403)

    def test_api_route_policy(self):
        self.start(org="local", machine="devbox")
        self.assertEqual(self.get("/stripe-shop/v2/x")[0], 403)
        self.assertEqual(self.get("/stripe-shop/v1/../v2")[0], 403)
        self.assertEqual(self.get("/stripe-shop/v1/x", method="DELETE")[0], 405)

    def test_api_rejects_ambiguous_paths_before_forwarding(self):
        self.start(org="local")
        for path in ("/v1/%2e%2e/admin", "/v1/%252e%252e/admin", "/v1/%2fadmin",
                     "/v1/%5cadmin", "/v1/./admin", "/v1//admin", "/v1/a;param",
                     "/v1/%00", "/v1/%0d%0aX-Test:yes", "/v1/%zz"):
            with self.subTest(path=path):
                self.assertEqual(self.get("/stripe-shop" + path)[0], 403)

    def test_raw_values_require_explicit_trusted_consumer_policy(self):
        self.start()
        self.srv.ctx["routes"].trusted_value_nodes = []
        for path in ("/shop/value/database-url-shop-qa", "/shop/value/pem-shop-qa/notes",
                     "/shop/value/database-url-shop-qa-backup-20261117"):
            self.assertEqual(self.get(path)[0], 403, path)
        status, _, body = self.get("/check?names=database-url-shop-qa")
        self.assertEqual(json.loads(body), {"database-url-shop-qa": "not-allowed"})

    def test_claude_token_grant_serves_only_current_password_to_its_node(self):
        name = "claude-code-oauth-worker"
        backup = name + "-backup-20261117"
        fixture = "synthetic-subscription-value"
        self.state(base_items() + [item("6", name, "org-local", ["c-local"], pw=fixture),
                                   item("7", backup, "org-local", ["c-local"], pw="synthetic-old")])
        config = ROUTES.replace("[security]", '[security]\nagent_nodes=["worker", "other"]')
        config += (f'\n[value.{name}]\norg="local"\nitem="{name}"\n'
                   'nodes=["worker"]\nclaude_setup_token=true\n')
        self.start(org="local", machine="worker", routes=config)
        self.assertEqual(self.get("/value/" + name)[::2], (200, fixture.encode()))
        for path in ("/value/" + backup, "/backups/" + name, "/value/" + name + "/notes",
                     "/value/agentbot-hooks-token-bot1"):
            self.assertEqual(self.get(path)[0], 403, path)
        self.assertEqual(self.get("/value/" + name, method="POST")[0], 405)
        check = "/check?names=" + ",".join([name, backup, name + "/notes"])
        self.assertEqual(json.loads(self.get(check)[2]),
                         {name: "ok", backup: "not-allowed", name + "/notes": "not-allowed"})
        self.assertNotIn(fixture, self.audit.getvalue())
        self.start(org="local", machine="other", routes=config)
        self.assertEqual(self.get("/value/" + name)[0], 403)
        self.assertEqual(json.loads(self.get("/check?names=" + name)[2]), {name: "not-allowed"})
        self.start(org="infra", machine="worker", routes=config)
        self.assertEqual(self.get("/value/" + name)[0], 403)

    def test_agent_policy_overrides_value_project_and_backup_routes(self):
        self.start(org="local", machine="bot1")
        self.srv.ctx["routes"].agent_nodes = ["bot1"]
        for path in ("/value/agentbot-hooks-token-bot1",
                     "/value/agentbot-hooks-token-bot1-backup-20261117",
                     "/backups/agentbot-hooks-token-bot1"):
            self.assertEqual(self.get(path)[0], 403, path)

    def test_api_route_caller_not_in_nodes(self):
        self.start(org="local", machine="tablet")
        self.assertEqual(self.get("/stripe-shop/v1/x")[:3:2], (403, b"not allowed\n"))


if __name__ == "__main__":
    unittest.main()
