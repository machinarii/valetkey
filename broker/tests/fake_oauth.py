"""A small OAuth server for tests: discovery documents, client registration, a sign-in page that
sends the browser straight back with a code, tokens with PKCE and refresh, and one protected
MCP address. Synthetic tokens only."""
import base64
import hashlib
import http.client
import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from broker.brokerlib import oauth


def local_fetch(url, form=None, body=None):
    """What oauth.public_fetch does, for the fake server on loopback over plain http."""
    parts = urllib.parse.urlsplit(url)
    conn = http.client.HTTPConnection(parts.hostname, parts.port, timeout=5)
    try:
        return oauth._exchange(conn, parts, form, body)
    finally:
        conn.close()


class FakeOAuth:
    def __init__(self, lasts=3600, register=True):
        self.lasts, self.register = lasts, register
        self.codes, self.refresh, self.access, self.seen, self.n = {}, {}, set(), [], 0
        fake = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def reply(self, status, obj=None, location=None):
                body = json.dumps(obj).encode() if obj is not None else b""
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                if location:
                    self.send_header("Location", location)
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                parts = urllib.parse.urlsplit(self.path)
                q = dict(urllib.parse.parse_qsl(parts.query))
                fake.seen.append(("GET", parts.path, q))
                if parts.path.startswith("/.well-known/oauth-protected-resource"):
                    return self.reply(200, {"resource": fake.base + "/mcp", "authorization_servers": [fake.base],
                                            "scopes_supported": ["read", "write"]})
                if parts.path == "/.well-known/oauth-authorization-server":
                    meta = {"issuer": fake.base, "authorization_endpoint": fake.base + "/authorize",
                            "token_endpoint": fake.base + "/token", "code_challenge_methods_supported": ["S256"]}
                    if fake.register:
                        meta["registration_endpoint"] = fake.base + "/register"
                    return self.reply(200, meta)
                if parts.path == "/authorize":  # the owner "signs in" and is sent back at once
                    code = fake.new("code")
                    fake.codes[code] = q
                    back = q["redirect_uri"] + "?" + urllib.parse.urlencode({"code": code, "state": q["state"]})
                    return self.reply(302, location=back)
                self.reply(404)

            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                path = urllib.parse.urlsplit(self.path).path
                if path == "/register":
                    fake.seen.append(("POST", path, json.loads(raw)))
                    return self.reply(201, {"client_id": fake.new("client")})
                if path == "/token":
                    form = dict(urllib.parse.parse_qsl(raw.decode()))
                    fake.seen.append(("POST", path, {k: v for k, v in form.items() if k in ("grant_type", "resource")}))
                    return self.reply(*fake.token(form))
                if path == "/mcp":
                    given = (self.headers.get("Authorization") or "").removeprefix("Bearer ")
                    fake.seen.append(("POST", path, given in fake.access))
                    return self.reply(200, {"ok": True}) if given in fake.access else self.reply(401, {"error": "sign in"})
                self.reply(404)

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.srv.daemon_threads = True
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def new(self, kind):
        self.n += 1
        return f"synthetic-{kind}-{self.n:04d}"

    def token(self, form):
        if form.get("grant_type") == "authorization_code":
            asked = self.codes.pop(form.get("code"), None)
            digest = base64.urlsafe_b64encode(hashlib.sha256(form.get("code_verifier", "").encode()).digest())
            if (not asked or asked["client_id"] != form.get("client_id") or asked["redirect_uri"] != form.get("redirect_uri")
                    or asked["code_challenge"] != digest.decode().rstrip("=")):
                return 400, {"error": "invalid_grant"}
            client = asked["client_id"]
        elif form.get("grant_type") == "refresh_token":
            client = self.refresh.pop(form.get("refresh_token"), None)  # each refresh token works once
            if not client or client != form.get("client_id"):
                return 400, {"error": "invalid_grant"}
        else:
            return 400, {"error": "unsupported_grant_type"}
        access, again = self.new("access"), self.new("refresh")
        self.access, self.refresh[again] = {access}, client  # the earlier access token stops working
        return 200, {"access_token": access, "refresh_token": again, "token_type": "Bearer",
                     **({"expires_in": self.lasts} if self.lasts else {})}

    def sign_in(self, address):
        """What the owner's browser does at the service: returns where it is sent back to."""
        parts = urllib.parse.urlsplit(address)
        conn = http.client.HTTPConnection(parts.hostname, parts.port, timeout=5)
        try:
            conn.request("GET", parts.path + "?" + parts.query)
            resp = conn.getresponse()
            resp.read()
            return resp.getheader("Location")
        finally:
            conn.close()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()
