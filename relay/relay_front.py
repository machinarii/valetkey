#!/usr/bin/python3 -I
"""TLS front door of the credential relay (design §4).

The only listener on the broker VM's Tailscale address. Dispatches /local/..., /infra/... and
/entry/... to the loopback services, stripping the prefix and replacing X-Relay-Peer with the
TCP peer address of the TLS connection. Backends trust that header only from 127.0.0.1, so no
incoming X-Relay-Peer or X-Relay-Loopback ever passes through. Backend paths meant for loopback
callers only (refresh, names, the entry suspect marks) are answered 404 here without forwarding.
"""
import argparse
import http.client
import os
import ssl
import subprocess
import sys
import urllib.parse

if __package__:
    from .relaylib import approval_rpc
    from .relaylib.httpbase import RelayHandler, RelayServer
else:  # installed as a script next to relaylib/
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from relaylib import approval_rpc
    from relaylib.httpbase import RelayHandler, RelayServer

MAX_BODY = 50 * 1024 * 1024
HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer",
       "transfer-encoding", "upgrade", "host", "content-length"}
DROP_IN = HOP | {"x-relay-peer", "x-relay-loopback", approval_rpc.HEADER.lower()}
# Added by this server (or by send_response) on every reply, so the backend's copy is dropped.
DROP_OUT = HOP | {"server", "date", "strict-transport-security", "x-frame-options",
                  "x-content-type-options"}
LOOPBACK_SEGMENTS = {"refresh", "names"}
LOOPBACK_PATHS = {"/v1/suspect", "/v1/unsuspect"}


def loopback_only(prefix, rest):
    """True for backend paths that only loopback callers may use."""
    for path in (rest, urllib.parse.unquote(rest)):
        path = "/" + path.strip("/")
        if path.split("/")[1] in LOOPBACK_SEGMENTS:
            return True
        if prefix == "entry" and path in LOOPBACK_PATHS:
            return True
    return False


class Handler(RelayHandler):
    def handle_guarded(self):
        self.guarded(self.handle_any, self.client_address[0], "-")

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = handle_guarded

    def deny(self, route, status, msg):
        self.send_text(status, msg)
        self.audit(self.client_address[0], route, status)

    def end_headers(self):
        self.send_header("Strict-Transport-Security", "max-age=31536000")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

    def handle_any(self):
        who = self.client_address[0]
        if "#" in self.path:  # no legitimate client sends a fragment
            return self.deny("-", 400, "bad request target")
        # Same parser the backends use, so the two can never disagree on what the path is.
        parts = urllib.parse.urlsplit("/" + self.path.lstrip("/") if self.path.startswith("/") else "/\0")
        seg = parts.path.lstrip("/").split("/", 1)
        prefix = seg[0]
        backends = self.server.ctx["backends"]
        if prefix not in backends:
            return self.deny("-", 404, "not found")
        rest = "/" + (seg[1] if len(seg) > 1 else "")
        rest = "/" + rest.lstrip("/")
        if loopback_only(prefix, rest):
            return self.deny(prefix, 404, "not found")
        if len(self.headers.get_all("Content-Length") or []) > 1:
            self.close_connection = True
            return self.deny(prefix, 400, "bad content length")
        body = None
        if self.headers.get("Content-Length", "").strip() not in ("", "0"):
            body = self.read_body(MAX_BODY)
            if body is None:
                self.close_connection = True
                return self.deny(prefix, 413, "body too large or invalid")
        host, port = backends[prefix]
        headers = {k: v for k, v in self.headers.items() if k.lower() not in DROP_IN}
        headers["Host"] = f"{host}:{port}"
        headers["X-Relay-Peer"] = who
        if prefix == "entry" and approval_rpc.sensitive(rest):
            key = self.server.ctx["approval_front"]
            if not key:
                return self.deny("approval", 503, "broker approvals unavailable")
            headers[approval_rpc.HEADER] = approval_rpc.proof(key, self.command, rest, who)
        target = rest + ("?" + parts.query if parts.query else "")
        conn = http.client.HTTPConnection(host, port, timeout=600)
        try:
            conn.request(self.command, target, body=body, headers=headers)
            resp = conn.getresponse()
        except (OSError, http.client.HTTPException):
            conn.close()
            return self.deny(prefix, 502, "backend unavailable")
        self.relay(resp, conn)
        self.audit(who, prefix, resp.status)

    def relay(self, resp, conn):
        self.send_response(resp.status, resp.reason)
        for k, v in resp.getheaders():
            if k.lower() not in DROP_OUT:
                self.send_header(k, v)
        length = resp.getheader("Content-Length")
        chunked = length is None and self.command != "HEAD" and resp.status not in (204, 304)
        if length is not None:
            self.send_header("Content-Length", length)
        elif chunked:
            self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        try:
            while True:  # stream (server-sent events stay live)
                chunk = resp.read1(65536)
                if not chunk:
                    break
                self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk) if chunked else chunk)
                self.wfile.flush()
            if chunked:
                self.wfile.write(b"0\r\n\r\n")
        except (OSError, http.client.HTTPException):
            self.close_connection = True  # client went away or backend broke mid-body
        finally:
            conn.close()


def parse_backend(text):
    name, sep, addr = text.partition("=")
    host, sep2, port = addr.rpartition(":")
    if not (sep and sep2 and name and host and port.isdigit()):
        raise argparse.ArgumentTypeError(f"backend must be NAME=HOST:PORT, got {text!r}")
    return name, (host, int(port))


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="relay_front.py")
    ap.add_argument("--backend", action="append", type=parse_backend, required=True,
                    help="NAME=HOST:PORT, repeatable")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--tls-dir", help="listen on the Tailscale IPv4, port 443, cert.pem/key.pem here")
    mode.add_argument("--plain", action="store_true", help="no TLS (tests); needs --bind")
    ap.add_argument("--bind", help="HOST:PORT for --plain")
    args = ap.parse_args(argv)
    if args.plain and not args.bind:
        ap.error("--plain needs --bind HOST:PORT")
    return args


def build_server(args, **overrides):
    """Configured, not yet serving. Overrides (tests): audit_out."""
    if not isinstance(args, argparse.Namespace):
        args = parse_args(args)
    ctx = {"backends": dict(args.backend), "approval_front": approval_rpc.front_key()}
    ctx.update(overrides)
    if args.plain:
        host, _, port = args.bind.rpartition(":")
        return RelayServer((host, int(port)), Handler, ctx)
    ip = subprocess.run(["tailscale", "ip", "-4"], capture_output=True, text=True, check=True).stdout.split()[0]
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.minimum_version = ssl.TLSVersion.TLSv1_2
    tls.load_cert_chain(os.path.join(args.tls_dir, "cert.pem"), os.path.join(args.tls_dir, "key.pem"))
    srv = RelayServer((ip, 443), Handler, ctx)
    # Handshake in the handler thread, so a slow or silent peer can't stall accept().
    srv.socket = tls.wrap_socket(srv.socket, server_side=True, do_handshake_on_connect=False)
    return srv


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    srv = build_server(args)
    host, port = srv.server_address[:2]
    print(f"relay-front listening on {host}:{port}: {', '.join(sorted(srv.ctx['backends']))}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
