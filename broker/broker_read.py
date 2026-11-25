#!/usr/bin/python3 -I
"""Read service: one instance per vault (`--org local|infra`), on loopback behind the front door.

The front door strips the /local or /infra prefix and adds X-Broker-Peer. Callers are identified
by Tailscale machine name. Endpoints (design §5.2):

  GET  /value/<name>             [value.<name>] item, caller in nodes
  GET  /<project>/value/<ref>    [project.<project>] item (ref = name or name/notes)
  GET  /check?names=a,b/notes    {"a": "ok"|"missing"|"not-allowed"}: ok = that ref has a value
  GET  /backups/<name>           {"name": ..., "backups": ["YYYYMMDD", ...]} newest first, no values

A backup <name>-backup-YYYYMMDD is readable by every caller that may read <name> (design §6.4).
  POST /refresh, GET /names      loopback only (X-Broker-Loopback secret), for valetkey-broker-entry
  *    /<route>/<path>           [api.<route>] forwarded through fnox's proxy with a placeholder

One audit line per request; never values, bw output or query strings.
"""
import argparse
import base64
import hmac
import http.client
import json
import os
import re
import ssl
import sys
import threading
import time
import urllib.parse
import urllib.request

if __package__:
    from .brokerlib import access_check, routes as routes_mod
    from .brokerlib.bwclient import Bw, BwError, ItemCache, split_ref
    from .brokerlib.httpbase import BrokerHandler, BrokerServer
    from .brokerlib.identity import Whois, peer_ip
else:  # installed as a script next to brokerlib/
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from brokerlib import access_check, routes as routes_mod
    from brokerlib.bwclient import Bw, BwError, ItemCache, split_ref
    from brokerlib.httpbase import BrokerHandler, BrokerServer
    from brokerlib.identity import Whois, peer_ip

MAX_BODY = 50 * 1024 * 1024
MAX_CHECK_NAMES = 500
RATE_PER_MINUTE = 300
TOOLS_TTL = 600  # how long a server's list of read-only tools is trusted
HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer",
       "transfer-encoding", "upgrade", "host", "content-length"}
# accept-encoding: the broker asks for plain replies so it can look for the key in them.
DROP_IN = HOP | {"authorization", "cookie", "x-api-key", "forwarded", "x-forwarded-for", "accept-encoding",
                 "x-broker-peer", "x-broker-loopback", "x-http-method-override", "x-http-method",
                 "x-method-override", "x-original-url", "x-rewrite-url"}
BW_ENV_KEYS = ("BW_SESSION", "BITWARDENCLI_APPDATA_DIR")
NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))
BACKUP_NAME_RE = re.compile(r"\A(?P<base>.+)-backup-(?P<date>[0-9]{8})\Z")


def no_twins(pairs):
    """JSON objects with a repeated key are refused: the broker and the service could each read
    a different one of the two values."""
    d = dict(pairs)
    if len(d) != len(pairs):
        raise ValueError("repeated key")
    return d


MIN_HIDE = 8  # shorter values would also mask ordinary text


def secret_forms(value):
    """The ways a service can echo the key it was sent: as stored, in base64 (alone or as a Basic
    credential) and, for a key stored as a Basic credential, decoded. Longest first."""
    raw = value.encode()
    if len(raw) < MIN_HIDE:
        return []
    forms = {raw, base64.b64encode(raw), base64.b64encode(raw + b":"), base64.b64encode(b":" + raw)}
    try:
        plain = base64.b64decode(raw, validate=True)
        if plain.isascii() and plain.decode().isprintable():
            forms |= {plain, *plain.split(b":")}
    except ValueError:
        pass
    return sorted((f for f in forms if len(f) >= MIN_HIDE), key=len, reverse=True)


class Hider:
    """Masks a route's own key in the service's reply, in a stream, without delaying it.

    The mask has the key's length, so Content-Length stays true. Only bytes that could be the
    start of the key are held back until the next chunk shows whether they are.
    shortcut: exact forms only (see secret_forms); a key the service re-encodes (JSON or URL
    escapes, compression it was not asked for) passes. Upgrade if a route's service does that."""

    def __init__(self, value):
        self.forms = secret_forms(value) if value else []
        self.held, self.hits = b"", 0

    def mask(self, data):
        for f in self.forms:
            if f in data:
                self.hits += 1
                data = data.replace(f, b"*" * len(f))
        return data

    def feed(self, data):
        data = self.mask(self.held + data)
        keep = max((k for f in self.forms for k in range(1, min(len(f), len(data) + 1))
                    if data.endswith(f[:k])), default=0)
        self.held = data[len(data) - keep:]
        return data[:len(data) - keep]


def placeholder(route):
    return f"fnox-ph-{route}-not-a-secret"


def upstream_conn(host, port):
    """HTTPS connection to host through fnox's proxy (HTTPS_PROXY), trusting fnox's CA."""
    proxy = urllib.parse.urlsplit(os.environ["HTTPS_PROXY"])
    ctx = ssl.create_default_context(cafile=os.environ.get("SSL_CERT_FILE"))
    # fnox signs its proxy certificates without an Authority Key Identifier, which Python 3.13's
    # default VERIFY_X509_STRICT rejects. The chain to fnox's CA and the host name are still checked.
    ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
    conn = http.client.HTTPSConnection(proxy.hostname, proxy.port, timeout=600, context=ctx)
    headers = {}
    if proxy.username is not None:
        cred = f"{urllib.parse.unquote(proxy.username)}:{urllib.parse.unquote(proxy.password or '')}"
        headers["Proxy-Authorization"] = "Basic " + base64.b64encode(cred.encode()).decode()
    conn.set_tunnel(host, port, headers=headers)
    return conn


class RateLimiter:
    """Token bucket per caller: `per_minute` capacity, refilled continuously."""

    def __init__(self, per_minute, clock):
        self.cap, self.rate, self.clock = float(per_minute), per_minute / 60.0, clock
        self._b = {}
        self._lock = threading.Lock()

    def take(self, who):
        now = self.clock()
        with self._lock:
            tokens, last = self._b.get(who, (self.cap, now))
            tokens = min(self.cap, tokens + (now - last) * self.rate)
            ok = tokens >= 1.0
            self._b[who] = (tokens - 1.0 if ok else tokens, now)
            return ok


def post_mark(entry_url, secret, kind, name, org):
    """Best-effort suspect/unsuspect notice to valetkey-broker-entry, in the background."""
    def send():
        req = urllib.request.Request(
            f"{entry_url.rstrip('/')}/v1/{kind}", method="POST",
            data=json.dumps({"name": name, "org": org}).encode(),
            headers={"Content-Type": "application/json", "X-Broker-Loopback": secret})
        try:
            NO_PROXY.open(req, timeout=2).close()
        except Exception:
            pass
    threading.Thread(target=send, daemon=True).start()


def plain_name(name):
    return routes_mod.NAME_RE.match(name) is not None


def base_name(name):
    """The item a backup name belongs to (`<base>-backup-YYYYMMDD` -> base), else name itself."""
    m = BACKUP_NAME_RE.match(name)
    return m.group("base") if m else name


class Handler(BrokerHandler):
    def handle_any(self):
        ctx = self.server.ctx
        parts = urllib.parse.urlsplit(self.path)
        seg = parts.path.lstrip("/").split("/", 1)
        first, rest = seg[0], (seg[1] if len(seg) > 1 else "")

        if first in ("refresh", "names") and not rest:
            return self.loopback(first)
        who = ctx["whois"].machine(peer_ip(self))
        if not who:
            return self.deny(who, first, 403, "unknown caller")
        if first == "value":
            return self.value_route(who, rest)
        if first == "check" and not rest:
            return self.check(who, parts.query)
        if first == "backups":
            return self.backups(who, rest)
        routes = ctx["routes"]
        if first in routes.projects:
            return self.project_value(who, routes.projects[first], rest)
        if first in routes.api:
            return self.api(who, routes.api[first], "/" + rest, parts.query)
        return self.deny(who, first, 404, "not found")

    def handle_guarded(self):
        first = urllib.parse.urlsplit(self.path).path.lstrip("/").split("/", 1)[0]
        self.guarded(self.handle_any, "?", first)

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = handle_guarded

    def deny(self, who, route, status, msg, note=""):
        self.send_text(status, msg)
        self.audit(who, route, status, note)

    def sync_note(self, before, note=""):
        """note, plus "sync-failed" if a refresh during this request could not `bw sync`."""
        failed = self.server.ctx["cache"].sync_failures > before
        return " ".join(x for x in (note, "sync-failed" if failed else "") if x)

    # --- loopback (valetkey-broker-entry) ---

    def loopback(self, what):
        ctx = self.server.ctx
        given = self.headers.get("X-Broker-Loopback", "")
        if not hmac.compare_digest(given.encode(), ctx["loopback_secret"].encode()):
            return self.deny("loopback", what, 403, "not allowed")
        want = "POST" if what == "refresh" else "GET"
        if self.command != want:
            return self.deny("loopback", what, 405, "method not allowed")
        if what == "refresh":
            ctx["cache"].invalidate()
            self.send_empty(204)
            return self.audit("loopback", what, 204)
        before = ctx["cache"].sync_failures
        try:
            names = sorted(ctx["cache"].names())
            expires = ctx["cache"].expiries()
        except BwError:
            return self.deny("loopback", what, 503, "bw-error", self.sync_note(before, "bw-error"))
        self.send_json(200, {"names": names, "expires": expires})
        self.audit("loopback", what, 200, self.sync_note(before))

    # --- values ---

    def serve_value(self, who, route, ref):
        ctx = self.server.ctx
        # Final enforcement point: HTTP clients and modified shims cannot bypass this.
        routes = ctx["routes"]
        setup_token = route == f"value/{ref}" and routes.allows_claude_setup_token(who, ref)
        if not (routes.may_read_values(who) or setup_token):
            return self.deny(who, route, 403, "not allowed")
        if not ctx["limiter"].take(who):
            return self.deny(who, route, 429, "rate limited", "rate-limited")
        before = ctx["cache"].sync_failures
        try:
            val = ctx["cache"].get(ref)
        except BwError:
            return self.deny(who, route, 503, "bw-error", self.sync_note(before, "bw-error"))
        if val is None:
            return self.deny(who, route, 404, "not found", self.sync_note(before))
        self.send_bytes(200, val.encode(), "application/octet-stream")
        self.audit(who, route, 200, self.sync_note(before))

    def value_route(self, who, name):
        route = f"value/{name}"
        values = self.server.ctx["routes"].values
        v = values.get(name)
        if v:
            ref = v.item if who in v.nodes else None
        else:  # a backup of a route's item: <item>-backup-YYYYMMDD, same nodes
            ref, m = None, BACKUP_NAME_RE.match(name)
            for r in values.values() if m else ():
                item, field = split_ref(r.item)
                if item == m.group("base") and who in r.nodes:
                    ref = name + ("/notes" if field == "notes" else "")
                    break
        if ref is None:
            return self.deny(who, route, 403, "not allowed")
        if self.command != "GET":
            return self.deny(who, route, 405, "method not allowed")
        self.serve_value(who, route, ref)

    def project_value(self, who, p, rest):
        if not rest.startswith("value/"):
            return self.deny(who, p.name, 404, "not found")
        ref = rest[len("value/"):]
        name, _ = split_ref(ref)
        if who not in p.nodes or not plain_name(name) or not routes_mod.match_item(base_name(name), p.items):
            return self.deny(who, p.name, 403, "not allowed")
        if self.command != "GET":
            return self.deny(who, p.name, 405, "method not allowed")
        self.serve_value(who, p.name, ref)

    # --- check ---

    def allowed(self, who, name):
        """May `who` read item `name` (or, for a backup name, the item it backs up)?"""
        routes = self.server.ctx["routes"]
        if not routes.may_read_values(who):
            return False
        base = base_name(name)
        if any(split_ref(v.item)[0] == base and who in v.nodes for v in routes.values.values()):
            return True
        return plain_name(name) and any(
            who in p.nodes and routes_mod.match_item(base, p.items) for p in routes.projects.values())

    # --- backups ---

    def backups(self, who, name):
        """Dates of the backups of an item the caller may read; names only, never values."""
        if not name or "/" in name or not self.allowed(who, name):
            return self.deny(who, "backups", 403, "not allowed")
        if self.command != "GET":
            return self.deny(who, "backups", 405, "method not allowed")
        before = self.server.ctx["cache"].sync_failures
        try:
            names = self.server.ctx["cache"].names()
        except BwError:
            return self.deny(who, "backups", 503, "bw-error", self.sync_note(before, "bw-error"))
        if name not in names:
            return self.deny(who, "backups", 404, "not found", self.sync_note(before))
        dates = sorted({m.group("date") for m in map(BACKUP_NAME_RE.match, names)
                        if m and m.group("base") == name}, reverse=True)
        self.send_json(200, {"name": name, "backups": dates})
        self.audit(who, "backups", 200, self.sync_note(before))

    def check(self, who, query):
        if self.command != "GET":
            return self.deny(who, "check", 405, "method not allowed")
        raw = urllib.parse.parse_qs(query).get("names", [""])[0]
        names = list(dict.fromkeys(n.strip() for n in raw.split(",") if n.strip()))
        if len(names) > MAX_CHECK_NAMES:
            return self.deny(who, "check", 413, "too many names")
        result = {}
        before = self.server.ctx["cache"].sync_failures
        try:
            for n in names:  # n is a ref (name or name/notes), as the shim would fetch it
                api_allowed = any(r.item == n and r.allows(who, self.server.ctx["now"]())
                                  for r in self.server.ctx["routes"].api.values())
                setup_token = self.server.ctx["routes"].allows_claude_setup_token(who, n)
                if not (api_allowed or setup_token or self.allowed(who, split_ref(n)[0])):
                    result[n] = "not-allowed"
                else:
                    result[n] = "ok" if self.server.ctx["cache"].get(n) is not None else "missing"
        except BwError:
            return self.deny(who, "check", 503, "bw-error", self.sync_note(before, "bw-error"))
        self.send_json(200, result)
        self.audit(who, "check", 200, self.sync_note(before))

    # --- api routes (prototype forwarding through fnox proxy) ---

    def read_only_tools(self, r):
        """Names the MCP server itself labels read-only, asked the way a forwarded call goes
        (fnox swaps the placeholder for the key). Empty when the list cannot be had."""
        ctx = self.server.ctx
        known = ctx["tools"].get(r.name)
        if known and ctx["clock"]() - known[0] < TOOLS_TTL:
            return known[1]
        up = urllib.parse.urlsplit(r.upstream)

        def connect(host):
            if ctx["upstream_scheme"] == "http":  # tests only
                return http.client.HTTPConnection(host, up.port or 80, timeout=access_check.TIMEOUT)
            conn = upstream_conn(host, up.port or 443)
            conn.timeout = access_check.TIMEOUT
            return conn
        path = next((p for p in r.paths if access_check.looks_like_mcp(r, p)), r.paths[0])
        try:
            listed = access_check.Session(r, path, placeholder(r.name), connect).tools()["tools"]
        except (access_check.Refused, access_check.NotMcp, OSError, http.client.HTTPException, ValueError):
            return set()  # not remembered: the next call asks again
        names = {t["name"] for t in listed if t["group"] == "read"}
        ctx["tools"][r.name] = (ctx["clock"](), names)
        return names

    def tool_refused(self, who, r, body):
        """On a route limited to some MCP tools: answer a call to any other tool here. True if so."""
        try:
            msgs = json.loads(body, object_pairs_hook=no_twins)
        except (ValueError, RecursionError):
            msgs = None
        msgs = msgs if isinstance(msgs, list) else [msgs]
        if not all(isinstance(m, dict) for m in msgs):
            self.deny(who, r.name, 400, "this route takes MCP (JSON-RPC) requests only", "not JSON-RPC")
            return True
        allowed = None
        for m in msgs:
            if m.get("method") != "tools/call":
                continue
            if allowed is None:
                allowed = self.read_only_tools(r) if r.tools == "read-only" else set(r.tools)
            name = m["params"].get("name") if isinstance(m.get("params"), dict) else None
            if isinstance(name, str) and name in allowed:
                continue
            shown = re.sub(r"[^\w.:/-]", "?", name if isinstance(name, str) else "")[:60]
            rid = m.get("id")
            if len(msgs) > 1 or isinstance(rid, bool) or not isinstance(rid, (str, int, float)):
                self.deny(who, r.name, 403, "the owner has not allowed that tool on this route",
                          f"tool refused: {shown}")
            else:  # an MCP client shows the model a JSON-RPC error, not an HTTP one
                self.send_json(200, {"jsonrpc": "2.0", "id": rid, "error": {
                    "code": -32602, "message": f"valetkey: the owner has not allowed the tool '{shown}' on "
                    "this route" + (" (only tools its server labels read-only)" if r.tools == "read-only" else "")}})
                self.audit(who, r.name, 200, f"tool refused: {shown}")
            return True
        return False

    def api(self, who, r, rest, query):
        ctx = self.server.ctx
        route = r.name
        if who not in r.nodes:
            return self.deny(who, route, 403, "not allowed")
        if not r.allows(who, ctx["now"]()):
            return self.deny(who, route, 403, "the owner's approval of this route has ended; ask again with "
                             "valetkey-broker request-route or join-route", "approval ended")
        if self.command not in r.methods:
            return self.deny(who, route, 405, "method not allowed")
        if r.model:
            # Model grants have exact operations. Prefix grants would also allow
            # distinct provider products such as the Anthropic Message Batches API.
            if rest not in r.paths or query not in ("", "beta=true"):
                return self.deny(who, route, 403, "model endpoint not allowed")
        elif not routes_mod.api_path_allowed(rest, r.paths):
            return self.deny(who, route, 403, "path not allowed")
        body = self.read_body(MAX_BODY)
        if body is None:
            self.close_connection = True
            return self.deny(who, route, 413, "body too large")
        if r.tools is not None and body and self.tool_refused(who, r, body):
            return

        up = urllib.parse.urlsplit(r.upstream)
        if r.model:
            allowed = {"content-type", "content-encoding", "accept", "anthropic-version", "anthropic-beta"}
            headers = {k: v for k, v in self.headers.items() if k.lower() in allowed}
        else:
            headers = {k: v for k, v in self.headers.items() if k.lower() not in DROP_IN}
        headers["Host"] = up.hostname
        headers[r.auth_header] = r.auth.format(placeholder(route))
        target = up.path.rstrip("/") + rest + ("?" + query if query else "")
        try:
            if ctx["upstream_scheme"] == "http":  # tests only: plain HTTP, no proxy
                conn = http.client.HTTPConnection(up.hostname, up.port or 80, timeout=600)
            else:
                conn = upstream_conn(up.hostname, up.port or 443)
            conn.request(self.command, target, body=body or None, headers=headers)
            resp = conn.getresponse()
        except Exception as e:
            return self.deny(who, route, 502, f"upstream error: {type(e).__name__}")

        if r.model and 300 <= resp.status < 400:
            conn.close()
            return self.deny(who, route, 502, "unexpected model provider redirect")
        item = split_ref(r.item)[0]
        if resp.status in (401, 403):
            post_mark(ctx["entry_url"], ctx["loopback_secret"], "suspect", item, r.org)
        elif 200 <= resp.status < 300:
            post_mark(ctx["entry_url"], ctx["loopback_secret"], "unsuspect", item, r.org)

        if r.model and resp.status >= 400:
            # Providers can quote a rejected credential in error text.
            conn.close()
            return self.deny(who, route, resp.status, "model provider rejected the request")
        note = ""
        try:
            hide = Hider(ctx["cache"].peek(r.item))
        except BwError:
            hide, note = Hider(None), "reply not checked: vault unavailable"
        if resp.getheader("Content-Encoding", "identity").lower() != "identity":
            hide, note = Hider(None), "reply not checked: encoded"
        self.send_response(resp.status, None if r.model else resp.reason)
        for k, v in resp.getheaders():
            if r.model and k.lower() not in {"content-type", "content-encoding"}:
                continue
            if k.lower() not in HOP | {"cache-control"}:
                self.send_header(k, hide.mask(v.encode("latin-1")).decode("latin-1"))
        self.send_header("Cache-Control", "no-store")
        length = resp.getheader("Content-Length")
        chunked = length is None and self.command != "HEAD" and resp.status not in (204, 304)
        if length is not None:
            self.send_header("Content-Length", length)
        elif chunked:
            self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        try:
            more = True
            while more:  # stream (server-sent events stay live)
                chunk = resp.read1(65536)
                more = bool(chunk)
                chunk = hide.feed(chunk) if more else hide.held
                if chunk:  # an empty chunk would end a chunked reply
                    self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk) if chunked else chunk)
                    self.wfile.flush()
            if chunked:
                self.wfile.write(b"0\r\n\r\n")
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        finally:
            conn.close()
        self.audit(who, route, resp.status, "key hidden in reply" if hide.hits else note)


def read_env_file(path):
    env = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v
    return env


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="broker_read.py")
    ap.add_argument("--org", required=True, choices=routes_mod.ORGS)
    ap.add_argument("--routes", required=True)
    ap.add_argument("--port", required=True, type=int)
    ap.add_argument("--org-ids", required=True)
    ap.add_argument("--loopback-secret-file", required=True)
    ap.add_argument("--entry-url", required=True)
    ap.add_argument("--bw-env-file")
    ap.add_argument("--upstream-scheme", choices=("https", "http"), default="https")
    ap.add_argument("--bind", default="127.0.0.1")
    return ap.parse_args(argv)


def build_server(args, **overrides):
    """Configured, not yet serving. Overrides (tests): whois, clock, audit_out, bw."""
    if not isinstance(args, argparse.Namespace):
        args = parse_args(args)
    routes = routes_mod.load_routes(args.routes).for_org(args.org)
    with open(args.org_ids) as f:
        ids = json.load(f)
    with open(args.loopback_secret_file) as f:
        secret = f.read().strip()
    if not secret:
        raise SystemExit("loopback secret file is empty")
    if args.bw_env_file:
        bw_env = read_env_file(args.bw_env_file)
    else:
        bw_env = {k: os.environ[k] for k in BW_ENV_KEYS if k in os.environ}
    clock = overrides.pop("clock", time.monotonic)
    bw = overrides.pop("bw", None) or Bw(env=bw_env)
    ctx = {
        "org": args.org,
        "routes": routes,
        "cache": ItemCache(bw, ids["org_id"], list(ids["collections"].values()), clock=clock),
        "whois": overrides.pop("whois", None) or Whois(),
        "limiter": RateLimiter(RATE_PER_MINUTE, clock),
        "clock": clock,
        "tools": {},  # route -> (when asked, the tools its server labels read-only)
        "now": time.time,  # approvals end by the calendar, not by this service's uptime
        "loopback_secret": secret,
        "entry_url": args.entry_url,
        "upstream_scheme": args.upstream_scheme,
    }
    ctx.update(overrides)
    return BrokerServer((args.bind, args.port), Handler, ctx)


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    srv = build_server(args)
    r = srv.ctx["routes"]
    host, port = srv.server_address[:2]
    print(f"valetkey-broker-{args.org} listening on {host}:{port}: {len(r.api)} api, {len(r.values)} value, "
          f"{len(r.projects)} project routes", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
