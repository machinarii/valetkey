#!/usr/bin/python3
"""Stand-in MCP server that demands a key. Test fixture only: speaks just enough MCP to connect.

  fakemcp.py HOST:PORT KEYFILE     serve; every request must carry "Authorization: <keyfile text>"
"""
import http.server
import json
import sys


def make_server(address, key):
    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def answer(self, status, obj=None):
            body = json.dumps(obj).encode() if obj is not None else b""
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self.answer(405)  # no server-sent stream offered

        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            if self.headers.get("Authorization") != key:
                return self.answer(401, {"error": "bad or missing key"})
            try:
                msg = json.loads(raw)
            except ValueError:
                return self.answer(400, {"error": "not JSON"})
            method, mid = msg.get("method"), msg.get("id")
            if mid is None:
                return self.answer(202)  # a notification
            if method == "initialize":
                result = {"protocolVersion": msg.get("params", {}).get("protocolVersion", "2025-03-26"),
                          "capabilities": {"tools": {}}, "serverInfo": {"name": "fakemcp", "version": "1"}}
            elif method == "tools/list":
                result = {"tools": [{"name": "whoami", "description": "Say which account the server is signed in as.",
                                     "inputSchema": {"type": "object", "properties": {}}}]}
            elif method == "tools/call":
                result = {"content": [{"type": "text", "text": "signed in as the synthetic test account"}]}
            else:
                return self.answer(200, {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "unknown"}})
            self.answer(200, {"jsonrpc": "2.0", "id": mid, "result": result})

    return http.server.ThreadingHTTPServer(address, Handler)


if __name__ == "__main__":
    host, _, port = sys.argv[1].rpartition(":")
    make_server((host, int(port)), open(sys.argv[2]).read().strip()).serve_forever()
