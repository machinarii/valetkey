"""Shared HTTP plumbing for the relay services: plain responses and one audit line per request."""
import http.server
import json
import re
import sys
import urllib.parse


class RelayHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "relay"
    sys_version = ""
    timeout = 600  # idle client sockets

    def log_message(self, *a):  # replaced by audit()
        pass

    def _send(self, status, body, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_bytes(self, status, data, content_type):
        self._send(status, data, content_type)

    def send_text(self, status, text):
        self._send(status, (text + "\n").encode(), "text/plain")

    def send_json(self, status, obj):
        self._send(status, json.dumps(obj).encode(), "application/json")

    def send_empty(self, status):
        self.send_response(status)
        self.send_header("Cache-Control", "no-store")
        if status != 204:
            self.send_header("Content-Length", "0")
        self.end_headers()

    def audit(self, who, route, status, note=""):
        """`who route method path status [note]` — path without its query string, never values."""
        path = urllib.parse.urlsplit(self.path).path
        line = f"{who or '?'} {route or '-'} {self.command} {path} {status}" + (" " + note if note else "")
        # A request target may carry control bytes (ESC, NUL); journalctl hides such a line as
        # "[blob data]", so a caller could blank its own audit record.
        line = re.sub(r"[^\x20-\x7e]", "?", line)
        print(line, file=self.server.ctx.get("audit_out", sys.stdout), flush=True)

    def read_body(self, max_bytes):
        """Request body (b"" if none); None when larger than max_bytes or Content-Length is invalid."""
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None
        if n < 0 or n > max_bytes:
            return None
        body = self.rfile.read(n) if n else b""
        self._body_read = True
        return body

    def guarded(self, dispatch, who, route):
        """Run dispatch() so that no unread body bytes can be parsed as a next request.

        Any Transfer-Encoding is refused (400, connection closed). If dispatch() returns without
        reading a body that Content-Length announced, the connection is closed afterwards.
        """
        self._body_read = False
        if "Transfer-Encoding" in self.headers:
            self.close_connection = True
            self.send_text(400, "unsupported transfer encoding")
            return self.audit(who, route, 400)
        lengths = self.headers.get_all("Content-Length") or []
        if len(lengths) > 1 or (lengths and not lengths[0].isascii()) or (
                lengths and not lengths[0].isdigit()):
            self.close_connection = True
            self.send_text(400, "bad content length")
            return self.audit(who, route, 400)
        try:
            dispatch()
        finally:
            if not self._body_read and self.headers.get("Content-Length", "0").strip() not in ("", "0"):
                self.close_connection = True


class RelayServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 128  # listen backlog: fnox opens ~70 connections at once (default 5)

    def __init__(self, address, handler, ctx=None):
        self.ctx = ctx if ctx is not None else {}
        super().__init__(address, handler)

    def handle_error(self, request, client_address):
        # One line, exception type only: a message or traceback could carry request data.
        print(f"relay: request error {sys.exc_info()[0].__name__}", file=sys.stderr, flush=True)
