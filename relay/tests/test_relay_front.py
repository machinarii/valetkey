import io
import socket
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

from relay import relay_front

NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class FakeBackend:
    """Tiny backend recording each request; replies with `reply` (status, headers, body-or-chunks)."""

    def __init__(self, name, reply=None):
        self.name, self.seen = name, []
        self.reply = reply or (200, {"Content-Type": "text/plain"}, f"hello from {name}".encode())
        outer = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def handle_any(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n) if n else b""
                outer.seen.append({"method": self.command, "path": self.path,
                                   "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body})
                status, headers, data = outer.reply
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                if isinstance(data, list):
                    self.send_header("Transfer-Encoding", "chunked")
                    self.end_headers()
                    for c in data:
                        self.wfile.write(b"%x\r\n%s\r\n" % (len(c), c))
                        self.wfile.flush()
                    self.wfile.write(b"0\r\n\r\n")
                else:
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    if self.command != "HEAD":
                        self.wfile.write(data)

            do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = handle_any

        self.httpd = HTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class FrontTest(unittest.TestCase):
    def setUp(self):
        self.audit = io.StringIO()
        self.backends = {n: FakeBackend(n) for n in ("local", "infra", "entry")}
        for b in self.backends.values():
            self.addCleanup(b.stop)
        self.start()

    def start(self):
        argv = ["--plain", "--bind", "127.0.0.1:0"]
        for n, b in self.backends.items():
            argv += ["--backend", f"{n}=127.0.0.1:{b.port}"]
        self.srv = relay_front.build_server(argv, audit_out=self.audit)
        t = threading.Thread(target=self.srv.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        t.start()
        self.addCleanup(self.stop_front, t)
        self.port = self.srv.server_address[1]

    def stop_front(self, t):
        self.srv.shutdown()
        self.srv.server_close()
        t.join(5)

    def get(self, path, method="GET", headers=None, data=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", method=method,
                                     headers=headers or {}, data=data)
        try:
            with NO_PROXY.open(req, timeout=5) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def raw(self, text):
        s = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        self.addCleanup(s.close)
        s.sendall(text.encode())
        out = b""
        while True:
            try:
                d = s.recv(65536)
            except socket.timeout:
                break
            if not d:
                break
            out += d
        return out.decode("latin-1")

    def test_dispatch_strips_prefix_and_adds_peer(self):
        status, headers, body = self.get("/infra/v1/items/x?a=1&b=2", "POST", {"X-Custom": "yes"}, b"payload")
        self.assertEqual((status, body), (200, b"hello from infra"))
        seen = self.backends["infra"].seen[0]
        self.assertEqual((seen["method"], seen["path"], seen["body"]), ("POST", "/v1/items/x?a=1&b=2", b"payload"))
        self.assertEqual(seen["headers"]["x-relay-peer"], "127.0.0.1")
        self.assertEqual(seen["headers"]["x-custom"], "yes")
        self.assertEqual(seen["headers"]["host"], f"127.0.0.1:{self.backends['infra'].port}")
        self.assertEqual(self.backends["local"].seen, [])
        self.assertEqual(headers["Strict-Transport-Security"], "max-age=31536000")
        self.assertEqual(headers["X-Frame-Options"], "DENY")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        for _ in range(200):  # the audit line follows the response
            line = self.audit.getvalue().strip()
            if line:
                break
            time.sleep(0.01)
        self.assertEqual(line, "127.0.0.1 infra POST /infra/v1/items/x 200")
        self.assertNotIn("a=1", line)

    def test_incoming_peer_and_loopback_headers_are_dropped(self):
        self.get("/local/x", headers={"X-Relay-Peer": "evil", "X-Relay-Loopback": "secret",
                                      "x-relay-peer": "evil2", "Connection": "close"})
        h = self.backends["local"].seen[0]["headers"]
        self.assertEqual(h["x-relay-peer"], "127.0.0.1")
        self.assertNotIn("x-relay-loopback", h)
        raw = self.raw("GET /local/y HTTP/1.1\r\nHost: x\r\nX-Relay-Peer: a\r\nX-Relay-Peer: b\r\n"
                       "X-Relay-Loopback: s\r\nConnection: close\r\n\r\n")
        self.assertIn("200", raw.split("\r\n")[0])
        h2 = self.backends["local"].seen[1]["headers"]
        self.assertEqual(h2["x-relay-peer"], "127.0.0.1")
        self.assertNotIn("x-relay-loopback", h2)

    def test_unknown_prefix_404(self):
        for p in ("/nope/x", "/", "/localx/a"):
            status, _, body = self.get(p)
            self.assertEqual((status, body), (404, b"not found\n"), p)
        self.assertTrue(all(not b.seen for b in self.backends.values()))

    def test_streams_chunked_response(self):
        self.backends["local"].reply = (200, {"Content-Type": "text/event-stream"}, [b"one ", b"two ", b"three"])
        status, headers, body = self.get("/local/stream")
        self.assertEqual((status, body), (200, b"one two three"))
        self.assertEqual(headers.get("Transfer-Encoding"), "chunked")

    def test_loopback_only_paths_are_blocked(self):
        for method, p in (("POST", "/local/refresh"), ("GET", "/infra/names"), ("POST", "/entry/v1/suspect"),
                          ("POST", "/entry/v1/unsuspect"), ("GET", "/infra/refresh/x"), ("GET", "/local/names?x=1"),
                          ("POST", "/entry/v1/suspect/"),
                          ("POST", "/entry//v1/suspect"), ("POST", "/entry/v1/unsuspect/?a=1")):
            status, _, _ = self.get(p, method, data=b"" if method == "POST" else None)
            self.assertEqual(status, 404, p)
        self.assertTrue(all(not b.seen for b in self.backends.values()))

    def test_backups_path_is_forwarded(self):
        for p in ("/local/backups/x", "/infra/backups/database-url-shop-qa"):
            self.assertEqual(self.get(p)[0], 200, p)
        self.assertEqual(self.backends["local"].seen[0]["path"], "/backups/x")
        self.assertEqual(self.backends["infra"].seen[0]["path"], "/backups/database-url-shop-qa")

    def test_fragment_targets_rejected_400(self):
        # urllib strips fragments itself, so send them raw
        for p in ("/local/refresh#x", "/infra/names#", "/entry/v1/suspect#a", "/local/ok#"):
            raw = self.raw(f"POST {p} HTTP/1.1\r\nHost: x\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            self.assertIn(" 400 ", raw.split("\r\n")[0], p)
        self.assertTrue(all(not b.seen for b in self.backends.values()))

    def test_entry_root_maps_to_slash(self):
        for p in ("/entry", "/entry/"):
            self.assertEqual(self.get(p)[0], 200)
        self.assertEqual([s["path"] for s in self.backends["entry"].seen], ["/", "/"])

    def test_duplicate_content_length_400(self):
        raw = self.raw("POST /local/x HTTP/1.1\r\nHost: x\r\nContent-Length: 3\r\nContent-Length: 3\r\n"
                       "Connection: close\r\n\r\nabc")
        self.assertIn(" 400 ", raw.split("\r\n")[0])
        self.assertEqual(self.backends["local"].seen, [])

    def test_oversize_body_413(self):
        raw = self.raw("POST /local/x HTTP/1.1\r\nHost: x\r\nContent-Length: 52428801\r\n\r\n")
        self.assertIn(" 413 ", raw.split("\r\n")[0])
        self.assertEqual(self.backends["local"].seen, [])

    def test_backend_down_502(self):
        self.backends["local"].stop()
        status, _, body = self.get("/local/x")
        self.assertEqual((status, body), (502, b"backend unavailable\n"))

    def test_listen_backlog_is_128(self):
        # fnox resolves ~70 secrets at once; the default backlog of 5 reset connections
        self.assertEqual(self.srv.request_queue_size, 128)

    def test_head_has_no_body(self):
        status, headers, body = self.get("/local/x", "HEAD")
        self.assertEqual((status, body), (200, b""))
        self.assertEqual(self.backends["local"].seen[0]["method"], "HEAD")


if __name__ == "__main__":
    unittest.main()
