"""access_check against a synthetic MCP server on loopback (plain HTTP stands in for HTTPS)."""
import http.client
import http.server
import json
import threading
import unittest

from broker.brokerlib import access_check
from broker.brokerlib.routes import ApiRoute, Routes

KEY = "synthetic-key-123456"
TOOLS = [
    {"name": "list_domains", "description": "Lists domains", "annotations": {"readOnlyHint": True}},
    {"name": "create_record", "description": "Adds a DNS record",
     "annotations": {"readOnlyHint": False, "destructiveHint": False}},
    {"name": "delete_record", "description": "Removes a record"},  # unlabelled: may change or delete
    {"name": "whoami", "description": f"Your key is {KEY}", "annotations": {"readOnlyHint": True}},
]


class FakeMcp(http.server.BaseHTTPRequestHandler):
    sse = False
    calls = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeMcp.calls.append((body["method"], self.headers.get("Mcp-Session-Id")))
        if self.headers.get("Authorization") != f"Bearer {KEY}":
            return self.reply(401, b"")
        if "id" not in body:
            return self.reply(202, b"")
        if body["method"] == "initialize":
            result = {"protocolVersion": "2025-06-18", "serverInfo": {"name": "fake"}, "capabilities": {}}
        elif body["method"] == "tools/list":  # two pages
            cursor = body.get("params", {}).get("cursor")
            result = {"tools": TOOLS[2:]} if cursor else {"tools": TOOLS[:2], "nextCursor": "p2"}
        else:
            return self.reply(200, json.dumps({"jsonrpc": "2.0", "id": body["id"],
                                               "error": {"code": -32601}}).encode())
        msg = json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": result}).encode()
        if FakeMcp.sse:
            self.reply(200, b"event: message\ndata: " + msg + b"\n\n", "text/event-stream")
        else:
            self.reply(200, msg)

    def reply(self, status, data, kind="application/json"):
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Mcp-Session-Id", "s-1")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def route(name="dns", upstream="https://mcp.example.com", paths=("/mcp",), methods=("POST",), item="dns-api-key"):
    return ApiRoute(name, "local", item, upstream, "Bearer {}", list(methods), list(paths), ["devbox"])


class AccessCheckTests(unittest.TestCase):
    def setUp(self):
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeMcp)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        self.connect = lambda host: http.client.HTTPConnection("127.0.0.1", self.srv.server_address[1], timeout=5)
        FakeMcp.calls, FakeMcp.sse = [], False

    def run_check(self, *routes, value=KEY):
        rs = Routes()
        for r in routes:
            rs.api[r.name] = r
        return access_check.check(rs, "local", "dns-api-key", value, connect=self.connect)

    def test_lists_tools_by_group_and_hides_the_key(self):
        for sse in (False, True):
            with self.subTest(sse=sse):
                FakeMcp.sse, FakeMcp.calls = sse, []
                [r] = self.run_check(route())["routes"]
                self.assertEqual((r["check"], r["server"], r["nodes"]), ("ok", "fake", ["devbox"]))
                self.assertEqual([(t["name"], t["group"]) for t in r["tools"]],
                                 [("list_domains", "read"), ("create_record", "change"),
                                  ("delete_record", "delete"), ("whoami", "read")])
                self.assertNotIn(KEY, json.dumps(r))
                # The session the server gave is used after initialize; no tool is ever called.
                self.assertEqual([m for m, _ in FakeMcp.calls],
                                 ["initialize", "notifications/initialized", "tools/list", "tools/list"])
                self.assertEqual({s for m, s in FakeMcp.calls[1:]}, {"s-1"})

    def test_refused_key(self):
        [r] = self.run_check(route(), value="wrong-key-000000")["routes"]
        self.assertEqual(r["check"], "refused")
        self.assertIn("401", r["detail"])

    def test_only_mcp_looking_post_routes_get_a_handshake(self):
        rs = self.run_check(route("rest", upstream="https://api.example.com", paths=("/v1/reports",)),
                            route("getonly", methods=("GET",)),
                            route("other", item="another-key"))["routes"]
        self.assertEqual([(r["name"], r["check"]) for r in rs], [("getonly", "not-tested"), ("rest", "not-tested")])
        self.assertEqual(FakeMcp.calls, [])

    def test_unreachable_service(self):
        self.connect = lambda host: http.client.HTTPConnection("127.0.0.1", 1, timeout=2)
        [r] = self.run_check(route())["routes"]
        self.assertEqual(r["check"], "error")


if __name__ == "__main__":
    unittest.main()
