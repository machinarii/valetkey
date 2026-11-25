#!/usr/bin/python3 -I
"""Entry service: agents request credentials by name, the owner enters values (design §6).

Runs as relay-entry on loopback behind the front door, which strips /entry and adds
X-Relay-Peer. Endpoints:

  POST /v1/requests, GET /v1/requests/<id>        any machine known to whois (no other
                                                  item's name: near_duplicate is only true)
  GET  /v1/requests?status=pending                approver devices (entry.toml owner_nodes)
  POST /v1/requests/<id>/value, POST /v1/replace  approver devices
  GET  /, POST /submit, POST /replace             approver devices, the web page
  POST /v1/suspect, POST /v1/unsuspect            loopback secret (read services)

The vault is unlocked only for one write and locked again on every path. Values never reach
the state file, a response, an exception or an audit line.
"""
import argparse
import base64
import datetime
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_COLLECTIONS = {"local": "local-broker-collection", "infra": "infra-broker-collection"}

if __package__:
    from .relaylib import access_check, approval_rpc
    from .relaylib import routes as routes_mod
    from .relaylib.bwclient import Bw, BwError, value_of
    from .relaylib.httpbase import RelayHandler, RelayServer
    from .relaylib.identity import Whois, peer_ip
    from .relaylib.requests_store import RequestsStore
    from .relay_read import read_env_file
else:  # installed as a script next to relaylib/
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from relaylib import access_check, approval_rpc
    from relaylib import routes as routes_mod
    from relaylib.bwclient import Bw, BwError, value_of
    from relaylib.httpbase import RelayHandler, RelayServer
    from relaylib.identity import Whois, peer_ip
    from relaylib.requests_store import RequestsStore
    from relay_read import read_env_file

MAX_JSON = 64 * 1024
MAX_FORM = 128 * 1024
MAX_PURPOSE = 500
MAX_RESULT = 300
MAX_PENDING_PER_NODE = 20  # any tailnet machine may ask; one must not flood the owner's page
FIELDS = ("password", "notes")
PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "entry-page.html")
REQ_PATH = re.compile(r"^/v1/requests/(r_[a-z2-7]{10})(/value)?$")
NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))
OUTCOME_STATUS = {"stored": 200, "replaced": 200, "verify-failed": 502, "bw-error": 503}


def validate(name, org, project, field):
    """One-line reason a request is invalid, or None."""
    if org not in routes_mod.ORGS:
        return "org must be local or infra"
    if field not in FIELDS:
        return "field must be password or notes"
    if project is not None and not (isinstance(project, str) and routes_mod.NAME_RE.match(project)):
        return "project must be lowercase kebab-case"
    if not isinstance(name, str) or not routes_mod.NAME_RE.match(name):
        return "name must be lowercase kebab-case"
    if routes_mod.BACKUP_RE.search(name):
        return "name is reserved"
    if project is not None and not routes_mod.PROJECT_NAME_RE.match(name):
        return "project names end in -dev, -qa or -prod"
    return None


def one_segment_apart(a, b):
    """True if b is a with exactly one '-segment' added or removed (at any position)."""
    sa, sb = a.split("-"), b.split("-")
    if len(sa) < len(sb):
        sa, sb = sb, sa
    if len(sa) != len(sb) + 1:
        return False
    return any(sa[:i] + sa[i + 1:] == sb for i in range(len(sa)))


def near_duplicate(name, names):
    return next((n for n in sorted(names) if n != name and one_segment_apart(name, n)), None)


def default_expiry(now):
    """One year after the day of `now`, YYYY-MM-DD; 29 February becomes 28 February."""
    d = datetime.date.fromtimestamp(now)
    return (d.replace(year=d.year + 1) if (d.month, d.day) != (2, 29)
            else d.replace(year=d.year + 1, day=28)).isoformat()


def check_expiry(text, now):
    """(YYYY-MM-DD or None, error or None). Empty or absent: the key does not expire."""
    if text is None or text == "":
        return None, None
    if not isinstance(text, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return None, "expiration date must be YYYY-MM-DD"
    try:
        day = datetime.date.fromisoformat(text)
    except ValueError:
        return None, "expiration date is not a real date"
    if day <= datetime.date.fromtimestamp(now):
        return None, "expiration date must be after today"
    return text, None


def normalise(value):
    value = value.replace("\r\n", "\n")
    return value[:-1] if value.endswith("\n") else value


def outcome(result):
    """Short audit word for a result text."""
    first = result.split(" ", 1)[0]
    if first in OUTCOME_STATUS or first == "exists":
        return first
    return "no-collection" if result.startswith("no collection") else "refused"


def digest(text):
    return hashlib.sha256(text.encode()).digest()


class Entry:
    """Request creation and the per-submission vault write."""

    def __init__(self, store, org_ids, urls, secret, unlock_file, bw_env, clock):
        self.store, self.org_ids, self.urls, self.secret = store, org_ids, urls, secret
        self.unlock_file, self.bw_env, self.clock = unlock_file, bw_env, clock
        self.write_lock = threading.Lock()

    def _loopback(self, org, path, method):
        req = urllib.request.Request(self.urls[org].rstrip("/") + path, method=method,
                                     headers={"X-Relay-Loopback": self.secret})
        with NO_PROXY.open(req, timeout=2) as r:
            return r.read()

    def names(self, org):
        """Item names the read service of org serves, or None if it cannot be asked."""
        try:
            names = json.loads(self._loopback(org, "/names", "GET"))["names"]
            return [n for n in names if isinstance(n, str)]
        except Exception:
            return None

    def refresh(self, org):
        try:
            self._loopback(org, "/refresh", "POST")
        except Exception:
            pass

    def create(self, name, org, project, field, purpose, replace, requester="", encode=""):
        """(http status, request dict | reason)."""
        err = validate(name, org, project, field)
        if err:
            return 400, err
        if encode not in ("", "basic") or (encode and field != "password"):
            return 400, "encode must be basic, for a single-line key"
        flags = {}
        names = self.names(org)
        if names is None:
            flags["names_unavailable"] = True
        else:
            if name in names and not replace:
                return 409, "exists"
            other = near_duplicate(name, names)
            if other:
                flags["near_duplicate"] = other
        r = self.store.create(name, org, project, field, purpose, replace, flags=flags,
                              requester=requester, limit=MAX_PENDING_PER_NODE, encode=encode)
        if r is None:
            return 429, "too many pending requests from this machine"
        return 201, r

    def replace_request(self, name, org):
        """Pending replace request for name in org, reusing an open one. (status, request)."""
        err = validate(name, org, None, "password")
        if err:
            return 400, err
        for r in self.store.pending():
            if r.name == name and r.org == org and r.replace:
                return 200, r
        suspect = (name, org) in self.store.suspects()
        purpose = "replace: broker saw 401/403 upstream" if suspect else "replace: requested by owner"
        return 201, self.store.create(name, org, None, "password", purpose, True)

    def collection(self, req):
        """(collection id, None) or (None, reason)."""
        block = self.org_ids[req.org]
        cols = block["collections"]
        # org.json names the org's top-level collection (vault.env LOCAL_/INFRA_COLLECTION);
        # files written before that field existed use the default names.
        parent = block.get("collection") or DEFAULT_COLLECTIONS[req.org]
        if req.org == "infra" and req.project:
            key = f"{parent}/{req.project}"
        else:
            key = parent
        if key not in cols:
            return None, (f"no collection for project {req.project}" if req.project
                          else f"no collection {key}")
        return cols[key], None

    def store_value(self, req, value, expires=None):
        """Write value for req and return the result text. Locks the vault on every path."""
        value = normalise(value)
        with self.write_lock:
            # Re-check under the lock: a second submission of the same request (double tap,
            # two devices) must not run another write, which for a replace would overwrite
            # the backup holding the original value.
            current = self.store.get(req.id)
            if current is None or current.status != "pending":
                return f"request is {current.status if current else 'unknown'}"
            req = current
            err = validate(req.name, req.org, req.project, req.field)
            col, cerr = self.collection(req) if not err else (None, None)
            if err or cerr:
                self.store.set_status(req.id, "pending", err or cerr)
                return err or cerr
            if req.encode == "basic":  # HTTP Basic with the key as user name and no password
                value = base64.b64encode((value + ":").encode()).decode()
            backup = f"{req.name}-backup-{time.strftime('%Y%m%d', time.localtime(self.clock()))}"
            session = None
            try:
                session = Bw(env=self.bw_env).unlock(self.unlock_file)
                bw = Bw(env={**self.bw_env, "BW_SESSION": session})
                result, status = self._write(bw, req, value, col, backup, expires)
            except Exception:  # BwError or anything unexpected: _write has undone what it could
                result, status = "bw-error", "pending"
            finally:
                lock_env = dict(self.bw_env, **({"BW_SESSION": session} if session else {}))
                session = None
                try:
                    Bw(env=lock_env).lock()
                except BwError:
                    pass
            self.store.set_status(req.id, status, result)  # before the next waiter re-checks
        if status in ("stored", "replaced"):
            self.store.mark_suspect(req.name, req.org, False)
            self.refresh(req.org)
        return result

    def _write(self, bw, req, value, col, backup, expires=None):
        org_id = self.org_ids[req.org]["org_id"]
        bw.sync()
        items = bw.list_items(org_id)
        existing = next((i for i in items if i.get("name") == req.name), None)
        renamed = None
        if existing:
            if not req.replace:
                return "exists", "rejected"
            if any(i.get("name") == backup for i in items):
                # A second replacement must not silently destroy an existing credential.
                return "backup already exists; owner must resolve the dated backup first", "pending"
            bw.rename_item(existing["id"], backup)
            renamed = existing["id"]
            if req.project is None and existing.get("collectionIds"):
                col = existing["collectionIds"][0]  # a replace stays where the item was
        new_id = None
        try:
            new_id = bw.create_item(req.name, org_id, col, value, req.field,
                                    req.purpose if req.field == "password" else None, expires)
            stored = value_of(bw.get_item(new_id), req.field) or ""
            ok = hmac.compare_digest(digest(stored), digest(value))
            stored = None
            if not ok:
                bw.delete_item(new_id)
                new_id = None
                if renamed:
                    bw.rename_item(renamed, req.name)
                return "verify-failed", "pending"
        except Exception:  # not only BwError: never leave the item under its backup name
            self._undo(bw, new_id, renamed, req.name)
            raise
        if renamed:
            return f"replaced {req.name}; old value kept as {backup}", "replaced"
        return f"stored {req.name} in {req.org}-org", "stored"

    @staticmethod
    def _undo(bw, new_id, renamed, name):
        """Best effort after a failed write: drop the new item, give the old one its name back."""
        for step in ((lambda: bw.delete_item(new_id)) if new_id else None,
                     (lambda: bw.rename_item(renamed, name)) if renamed else None):
            try:
                if step:
                    step()
            except Exception:
                pass


def public(r, owner=False, who=None):
    """A request as JSON. Agent-facing views (any tailnet machine) never name another item: a
    near-duplicate is only `true` there; owner listings show its name. `who` also gets the access
    check for the routes that name it: route facts, the result and tool counts, never text the
    upstream server wrote."""
    flags = dict(r.flags)
    if not owner and flags.get("near_duplicate"):
        flags["near_duplicate"] = True
    out = {"id": r.id, "name": r.name, "org": r.org, "project": r.project, "field": r.field,
           "purpose": r.purpose, "replace": r.replace, "status": r.status, "created": r.created,
           "flags": flags, "result": r.result, "encode": r.encode}
    if owner:  # who asked: the purpose text is the requester's own words
        out["requester"] = r.requester
    if who and r.access.get("routes"):
        out["access"] = [{"route": a["name"], "paths": a["paths"], "check": a.get("check", ""),
                          "detail": a.get("detail", ""),
                          "tools": {g: sum(t.get("group") == g for t in a.get("tools", []))
                                    for g in ("read", "change", "delete")}}
                         for a in r.access["routes"] if who in a["nodes"]]
    return out


class Handler(RelayHandler):
    def end_headers(self):
        self.send_header("Content-Security-Policy", self.server.ctx["csp"] + "; img-src data:")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

    def handle_guarded(self):
        self.guarded(self.handle_any, "?", "-")

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = handle_guarded

    def deny(self, who, route, status, msg, note=""):
        self.send_text(status, msg)
        self.audit(who, route, status, note)

    def handle_any(self):
        ctx = self.server.ctx
        parts = urllib.parse.urlsplit(self.path)
        path = parts.path
        if approval_rpc.sensitive(path):
            key = ctx["approval_front"]
            provided = self.headers.get(approval_rpc.HEADER, "").encode()
            if not (key and self.client_address[0] == "127.0.0.1" and hmac.compare_digest(
                    provided, approval_rpc.proof(key, self.command, path, peer_ip(self)).encode())):
                return self.deny("?", "approval", 403, "authenticated broker front required")
        if path in ("/v1/suspect", "/v1/unsuspect"):
            return self.mark(path[len("/v1/"):])
        who = ctx["whois"].machine(peer_ip(self))
        if not who:
            return self.deny(who, "entry", 403, "unknown caller")
        owner = (who not in ctx["agent_nodes"]
                 and ctx["whois"].owner_machine(peer_ip(self)) in ctx["owners"])
        if path == "/v1/route-requests":
            if self.command != "POST":
                return self.deny(who, "proposal", 405, "POST required")
            spec = self.json_body(who, "proposal")
            if spec is not None:
                rid = self.approval_call(who, op="propose", spec=spec)
                if rid is not None:
                    self.send_json(201, {"id": rid})
                    self.audit(who, "proposal", 201)
            return
        match = re.fullmatch(r"/(pair|approve)/([a-f0-9]{40})", path)
        if match:
            if not owner:
                return self.deny(who, "approval", 403, "approver device required")
            return self.approval_page(who, *match.groups())
        access = re.fullmatch(r"/access/(r_[a-z2-7]{10})", path)
        if access:
            if not owner:
                return self.deny(who, "entry", 403, "not allowed")
            return self.only("GET", who, "entry") or self.access_page(who, access.group(1))
        m = REQ_PATH.match(path)
        if path == "/v1/requests" and self.command == "POST":
            return self.create(who)
        if m and not m.group(2):
            return self.only("GET", who, f"request/{m.group(1)}") or self.show(who, m.group(1))
        known = path in ("/v1/requests", "/v1/replace", "/", "/submit", "/replace") or m
        if not known:
            return self.deny(who, "entry", 404, "not found")
        route = f"request/{m.group(1)}" if m else "entry"
        if not owner:
            return self.deny(who, route, 403, "not allowed")
        if m:
            return self.only("POST", who, route) or self.submit_json(who, m.group(1))
        if path == "/v1/requests":
            return self.only("GET", who, route) or self.listing(who, parts.query)
        if path == "/v1/replace":
            return self.only("POST", who, route) or self.replace_json(who)
        if path == "/":
            return self.only("GET", who, route) or self.page(who, parts.query)
        if path == "/submit":
            return self.only("POST", who, route) or self.submit_form(who)
        return self.only("POST", who, route) or self.replace_form(who)

    def approval_call(self, who, **msg):
        try:
            status, data = self.server.ctx["approval_rpc"](dict(peer=peer_ip(self), **msg))
        except (ValueError, OSError):
            return self.deny(who, "approval", 503, "approval worker unavailable; pair an approver device on the broker")
        if status != 200:
            return self.deny(who, "approval", status, data)
        return data

    def approval_page(self, who, kind, rid):
        ctx = self.server.ctx
        token = hmac.digest(ctx["page_token"].encode(), f"{who}/{kind}/{rid}".encode(), "sha256").hex()
        pairing = kind == "pair"
        title = "Pair your authenticator" if pairing else "Approve API access"
        if self.command == "POST":
            form = self.body(who, "approval", "application/x-www-form-urlencoded", 1024)
            if form is None:
                return
            if not hmac.compare_digest(form.get("token", "").encode(), token.encode()):
                return self.deny(who, "approval", 403, "reload the review page")
            result = self.approval_call(who, op="pair-confirm" if pairing else "approve",
                                        id=rid, code=form.get("code", ""))
            if result is None:
                return
            card = f'<p class="result">{html.escape(result)}</p>'
        elif self.command == "GET":
            data = self.approval_call(who, op="pair-view" if pairing else "review", id=rid)
            if data is None:
                return
            show_form = True
            if pairing:
                try:
                    png = ctx["qr_png"](data)
                except (OSError, subprocess.SubprocessError):
                    return self.deny(who, "pair", 503, "broker QR encoder unavailable")
                image = base64.b64encode(png).decode()
                card = ('<p>Scan this QR code with your authenticator. Then enter its six-digit code '
                        'to confirm pairing.</p><img alt="Authenticator pairing QR code" '
                        f'src="data:image/png;base64,{image}">')
            else:
                spec, state, e = data["spec"], data["state"], html.escape
                show_form = state == "pending"
                key = e(spec["org"] + "/" + spec["item"])
                if "join" in spec:
                    card = (f'<p class="lead"><b class="id">{e(data["node"])}</b> asks to join route '
                            f'<b class="id">{e(spec["join"])}</b>, which sends the key <b class="id">{key}</b> '
                            f'to <b class="id">{e(spec["upstream"])}</b>.</p>')
                    check = ('Approve only if this machine should use that key the way the machines below '
                             'already do. Nothing else about the route changes.')
                else:
                    card = (f'<p class="lead"><b class="id">{e(data["node"])}</b> asks to use the key '
                            f'<b class="id">{key}</b> on <b class="id">{e(spec["upstream"])}</b>.</p>')
                    check = 'Approve only if this address belongs to the provider that issued the key.'
                if show_form:
                    card += f'<p class="check">{check}</p>'
                pill = {"pending": ("pending", "Waiting for approval"), "active": ("active", "Active")}.get(
                    state, ("", state.capitalize()))
                rows = [("Route", f'<code>{e(spec.get("join") or spec["name"])}</code>'),
                        ("Methods", "".join(f'<span class="chip">{e(m)}</span>' for m in spec["methods"])),
                        ("Paths", "".join(f"<code>{e(p)}</code><br>" for p in spec["paths"])
                         + '<span class="hint">and everything under them</span>'),
                        *([("Already used by", "".join(f'<span class="chip">{e(n)}</span>' for n in spec["nodes"]))]
                          if "join" in spec else []),
                        ("Key sent as", e(auth_label(spec.get("auth_header", "Authorization"),
                                                     AUTH_FORMATS.get(spec.get("auth"), spec.get("auth"))
                                                     or "Bearer {}"))),
                        ("Status", f'<span class="pill {pill[0]}">{e(pill[1])}</span>')]
                card += ('<dl class="spec">' + "".join(f"<div><dt>{k}</dt><dd>{v}</dd></div>" for k, v in rows)
                         + "</dl>")
            if show_form:
                button = "Confirm pairing" if pairing else "Approve this route"
                card += (f'<form method="post" action="{html.escape(ctx["prefix"])}/{kind}/{rid}">'
                         f'<input type="hidden" name="token" value="{token}">'
                         '<label id="code-label" for="code">Authenticator code</label>'
                         '<input id="code" class="code" type="password" '
                         'inputmode="numeric" pattern="[0-9]{6}" maxlength="6" autocomplete="one-time-code" '
                         f'name="code" required><button class="wide" type="submit" aria-live="polite">'
                         f'<span class="spin" aria-hidden="true"></span>{button}</button></form>')
        else:
            return self.deny(who, "approval", 405, "GET or POST required")
        page = ctx["template"].replace("Credential entry", title).replace("{{result}}", "").replace("{{pending}}", card)
        self.send_bytes(200, page.encode(), "text/html; charset=utf-8")
        self.audit(who, kind, 200)

    def only(self, method, who, route):
        if self.command != method and not (method == "GET" and self.command == "HEAD"):
            self.deny(who, route, 405, "method not allowed")
            return True
        return False

    # --- bodies ---

    def body(self, who, route, ctype, limit):
        if self.headers.get("Content-Type", "").split(";")[0].strip().lower() != ctype:
            return self.deny(who, route, 415, f"content type must be {ctype}")
        raw = self.read_body(limit)
        if raw is None:
            self.close_connection = True
            return self.deny(who, route, 413, "body too large")
        try:
            text = raw.decode("utf-8")
            if ctype == "application/json":
                obj = json.loads(text)
            else:
                obj = {k: v[0] for k, v in urllib.parse.parse_qs(
                    text, keep_blank_values=True, max_num_fields=20).items()}
        except ValueError:
            return self.deny(who, route, 400, "bad body")
        if not isinstance(obj, dict):
            return self.deny(who, route, 400, "body must be a JSON object")
        return obj

    def json_body(self, who, route):
        return self.body(who, route, "application/json", MAX_JSON)

    def form_body(self, who, route):
        form = self.body(who, route, "application/x-www-form-urlencoded", MAX_FORM)
        if form is not None and not hmac.compare_digest(
                form.get("token", "").encode(), self.server.ctx["page_token"].encode()):
            return self.deny(who, route, 403, "reload the page and try again", "bad-token")
        return form

    # --- loopback marks ---

    def mark(self, kind):
        ctx = self.server.ctx
        given = self.headers.get("X-Relay-Loopback", "")
        if not hmac.compare_digest(given.encode(), ctx["loopback_secret"].encode()):
            return self.deny("loopback", kind, 403, "not allowed")
        if self.only("POST", "loopback", kind):
            return
        b = self.json_body("loopback", kind)
        if b is None:
            return
        name, org = b.get("name"), b.get("org")
        if org not in routes_mod.ORGS or not isinstance(name, str) or not routes_mod.NAME_RE.match(name):
            return self.deny("loopback", kind, 400, "bad mark")
        ctx["store"].mark_suspect(name, org, kind == "suspect")
        self.send_empty(204)
        self.audit("loopback", kind, 204, f"{org}/{name}")

    # --- agent side ---

    def create(self, who):
        b = self.json_body(who, "request")
        if b is None:
            return
        purpose, replace, encode = b.get("purpose") or "", b.get("replace", False), b.get("encode", "")
        if not isinstance(purpose, str) or len(purpose) > MAX_PURPOSE:
            return self.deny(who, "request", 400, f"purpose must be text up to {MAX_PURPOSE} characters")
        if not isinstance(replace, bool):
            return self.deny(who, "request", 400, "replace must be true or false")
        status, r = self.server.ctx["entry"].create(
            b.get("name"), b.get("org"), b.get("project"), b.get("field", "password"), purpose, replace,
            who, encode)
        if status != 201:
            return self.deny(who, "request", status, r)
        self.send_json(201, public(r))
        self.audit(who, f"request/{r.id}", 201, r.name)

    def show(self, who, rid):
        r = self.server.ctx["store"].get(rid)
        if not r:
            return self.deny(who, f"request/{rid}", 404, "unknown request")
        self.send_json(200, public(r, who=who))
        self.audit(who, f"request/{rid}", 200)

    # --- owner side ---

    def listing(self, who, query):
        status = urllib.parse.parse_qs(query).get("status", ["pending"])[0]
        if status != "pending":
            return self.deny(who, "entry", 400, "only status=pending is supported")
        store = self.server.ctx["store"]
        self.send_json(200, {"requests": [public(r, owner=True) for r in store.pending()],
                             "suspects": [{"name": n, "org": o, "since": s}
                                          for n, o, s in store.suspects_since()]})
        self.audit(who, "entry", 200)

    def submit(self, rid, value, expires=None):
        """(http status, result text) for a value submission."""
        r = self.server.ctx["store"].get(rid)
        if not r:
            return 404, "unknown request"
        if r.status != "pending":
            return 409, f"request is {r.status}"
        if not isinstance(value, str) or not normalise(value):
            return 400, "value must be a non-empty string"
        expires, err = check_expiry(expires, self.server.ctx["clock"]())
        if err:
            return 400, err
        ctx = self.server.ctx
        result = ctx["entry"].store_value(r, value, expires)
        if outcome(result) in ("stored", "replaced"):
            # Which routes this key opens, and for an MCP server its tools; the value goes only
            # to those routes' own upstreams. Never fatal: the key is already stored.
            try:
                access = ctx["access_check"](routes_mod.load_routes(ctx["routes_path"]), r.org, r.name, value)
            except Exception:
                access = {"error": "Could not check what this key opens."}
            ctx["store"].set_access(rid, dict(access, expires=expires or ""))
        value = None
        return OUTCOME_STATUS.get(outcome(result), 409), result

    def submit_json(self, who, rid):
        b = self.json_body(who, f"request/{rid}")
        if b is None:
            return
        status, result = self.submit(rid, b.get("value"), b.get("expires"))
        b = None
        self.send_text(status, result)
        self.audit(who, f"request/{rid}", status, outcome(result))

    def replace_json(self, who):
        b = self.json_body(who, "entry")
        if b is None:
            return
        status, r = self.server.ctx["entry"].replace_request(b.get("name"), b.get("org"))
        if status >= 400:
            return self.deny(who, "entry", status, r)
        self.send_json(status, public(r, owner=True))
        self.audit(who, f"request/{r.id}", status, "replace")

    def see_other(self, who, route, location, note):
        self.send_response(303)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.audit(who, route, 303, note)

    def access_page(self, who, rid):
        r = self.server.ctx["store"].get(rid)
        if not r or r.status not in ("stored", "replaced"):
            return self.deny(who, f"request/{rid}", 404, "no stored key with this id")
        text = self.server.ctx["template"].replace("Credential entry", "Key stored").replace(
            "{{result}}", "").replace("{{pending}}", render_access(self.server.ctx, r))
        self.send_bytes(200, text.encode(), "text/html; charset=utf-8")
        self.audit(who, f"request/{rid}", 200, "access")

    def redirect(self, who, route, result, note, rid=None):
        """Back to the page. `rid` puts the result under that request's card, where the
        owner just pressed the button, instead of at the top of the page."""
        result, rid = result[:MAX_RESULT], rid or ""
        loc = (f"{self.server.ctx['prefix']}/?result={urllib.parse.quote_plus(result)}"
               f"&sig={result_sig(self.server.ctx, result, rid)}")
        if rid:
            loc += f"&for={urllib.parse.quote_plus(rid)}"
        self.send_response(303)
        self.send_header("Location", loc)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.audit(who, route, 303, note)

    def submit_form(self, who):
        form = self.form_body(who, "entry")
        if form is None:
            return
        rid = form.get("id", "")
        known = bool(REQ_PATH.match(f"/v1/requests/{rid}"))
        route = f"request/{rid}" if known else "entry"
        expires = "" if form.get("never") else form.get("expires", "")  # the box wins over a date
        _, result = self.submit(rid, form.get("value"), expires)
        form = None
        if outcome(result) in ("stored", "replaced"):
            return self.see_other(who, route, f"{self.server.ctx['prefix']}/access/{rid}", outcome(result))
        self.redirect(who, route, result, outcome(result), rid if known else None)

    def replace_form(self, who):
        form = self.form_body(who, "entry")
        if form is None:
            return
        status, r = self.server.ctx["entry"].replace_request(form.get("name"), form.get("org"))
        if status >= 400:
            return self.redirect(who, "entry", r, "refused")
        self.redirect(who, f"request/{r.id}", f"replace request for {r.name} is open here", "replace", r.id)

    def page(self, who, query):
        ctx = self.server.ctx
        q = urllib.parse.parse_qs(query)
        result = q.get("result", [""])[0][:MAX_RESULT]
        rid = q.get("for", [""])[0]
        # Only text this service put in its own redirect: a link from elsewhere must not be
        # able to speak in the page's voice.
        if not hmac.compare_digest(q.get("sig", [""])[0].encode(),
                                   result_sig(ctx, result, rid).encode()):
            result = ""
        # The result goes under the card it belongs to; only an unplaceable one shows at the top.
        lists, placed = render_lists(ctx, result, rid)
        parts = {"pending": lists, "result": (
            f'<p class="result" role="status">{html.escape(result)}</p>' if result and not placed else "")}
        text = re.sub(r"\{\{(pending|result)\}\}", lambda m: parts[m.group(1)], ctx["template"])
        self.send_bytes(200, text.encode(), "text/html; charset=utf-8")
        self.audit(who, "entry", 200)


AUTH_FORMATS = {"bearer": "Bearer {}", "basic": "Basic {}"}  # a proposed route names its format


def auth_label(header, auth):
    """How a route sends its key, in words: "Bearer token", "Basic (stored value)", "X-Api-Key header"."""
    if header.lower() == "authorization":
        scheme = auth.replace("{}", "").strip()
        return {"Bearer": "Bearer token", "Basic": "Basic (stored value)", "": "Authorization header"}.get(
            scheme, f"{scheme} token")
    return f"{header} header"


GROUPS = (("read", "Read only"), ("change", "Write"),
          ("delete", "Can change or delete (or not labelled)"))
CHECK_PILL = {"ok": ("active", "Signed in"), "refused": ("bad", "Key refused"),
              "not-tested": ("", "Not tested"), "error": ("bad", "Could not check")}


def render_access(ctx, r):
    """The confirmation page: where the key is, which routes use it, and what each one opens."""
    e, a = html.escape, r.access or {}
    routes = a.get("routes") or []
    expires = e(a["expires"]) if a.get("expires") else "Never"
    # With routes the expiry is the first row of each card; without, it stays in the sentence.
    tail = "" if routes else (f" It expires on {expires}." if a.get("expires") else " It does not expire.")
    out = [f'<p class="lead"><b class="id">{e(r.name)}</b> is in the vault, in the {e(r.org)} org.{tail}</p>',
           "<h2>What agents can do with it</h2>"]
    if a.get("error"):
        out.append(f'<p class="check">{e(a["error"])}</p>')
    elif not routes:
        out.append('<p class="check">No route uses this key yet, so no agent can use it. It becomes '
                   'usable when you approve a route request or add a route on the broker.</p>')
    for rt in routes:
        cls, label = CHECK_PILL.get(rt.get("check"), ("", "Not tested"))
        rows = [("Expires", expires),
                ("Machines", "".join(f'<span class="chip">{e(n)}</span>' for n in rt["nodes"])),
                ("Address", f'<code>{e(rt["upstream"])}</code>'),
                ("Methods", "".join(f'<span class="chip">{e(m)}</span>' for m in rt["methods"])),
                ("Paths", "".join(f"<code>{e(p)}</code><br>" for p in rt["paths"])
                 + '<span class="hint">and everything under them</span>')]
        card = [f'<li><div class="head"><span class="name">{e(rt["name"])}</span>'
                f'<span class="pill {cls}">{e(label)}</span></div>']
        # What the check found, right under the route's name; then the route's facts; then the tools.
        if rt.get("detail"):
            card.append(f'<p class="meta">{e(rt["detail"])}</p>')
        tools = rt.get("tools") or []
        if rt.get("check") == "ok":
            server = f' ({e(rt["server"])})' if rt.get("server") else ""
            card.append(f'<p class="meta">The MCP server{server} accepted the key and lists '
                        f'{len(tools)} tool{"" if len(tools) == 1 else "s"}. The server labels its own tools; '
                        'treat the groups as its claim, not a guarantee. No tool was run.</p>')
        card.append('<dl class="spec">' + "".join(f"<div><dt>{k}</dt><dd>{v}</dd></div>" for k, v in rows) + "</dl>")
        if rt.get("check") == "ok":
            for key, title in GROUPS:
                group = [t for t in tools if t.get("group") == key]
                if group:
                    items = "".join(f'<li><code>{e(t["name"])}</code>'
                                    + (f'<span class="hint">{e(t["description"])}</span>' if t["description"] else "")
                                    + "</li>" for t in group)
                    card.append(f'<details class="tools {key}"{" open" if key != "read" else ""}>'
                                f'<summary>{title} ({len(group)})</summary><ul>{items}</ul></details>')
        card.append("</li>")
        out.append("<ul>" + "".join(card) + "</ul>")
    out.append(f'<p><a class="back" href="{e(ctx["prefix"])}/">Back to requests</a></p>')
    return "\n".join(out)


def result_sig(ctx, result, rid):
    return hmac.new(ctx["page_token"].encode(), f"{rid}\0{result}".encode(), "sha256").hexdigest()


def age_text(seconds):
    """How long ago, in the coarsest unit that is at least one: 12 min ago, 3 hr ago, 2 d ago."""
    s = max(0, int(seconds))
    if s < 3600:
        return f"{s // 60} min ago"
    if s < 86400:
        return f"{s // 3600} hr ago"
    return f"{s // 86400} d ago"


def render_lists(ctx, result="", rid=""):
    """(html, placed): the lists, and whether `result` was shown under request `rid`."""
    e, prefix, token = html.escape, html.escape(ctx["prefix"]), html.escape(ctx["page_token"])
    now = ctx["clock"]()
    hidden = f'<input type="hidden" name="token" value="{token}">'
    expiry = default_expiry(now)
    tomorrow = (datetime.date.fromtimestamp(now) + datetime.timedelta(days=1)).isoformat()
    out = ['<p class="intro">Keys your agents asked for. A value you store goes straight into the vault; '
           'agents never see it.</p><h2>Waiting for a value</h2>']
    pending = ctx["store"].pending()
    note = f'<p class="result" role="status">{e(result)}</p>' if result else ""
    placed = False
    # A request just stored or replaced is no longer pending; its card stays, in its old
    # place, carrying the result, so the answer appears where the button was pressed.
    done = ctx["store"].get(rid) if result and rid else None
    cards = sorted(pending + ([done] if done and done.status != "pending" else []),
                   key=lambda r: r.created)
    if not cards:
        out.append('<p class="empty">Nothing is waiting. When an agent asks for a key, it appears here.</p>')
    else:
        out.append("<ul>")
    for r in cards:
        if r.status != "pending":
            ok = " active" if r.status in ("stored", "replaced") else ""
            out.append(f'<li><div class="head"><span class="name">{e(r.name)}</span>'
                       f'<span class="pill{ok}">{e(r.status.capitalize())}</span></div>'
                       f'<p class="meta">In the {e(r.org)} org.</p>{note}</li>')
            placed = True
            continue
        where = ((f"Asked by {r.requester}" if r.requester else "Requested") + f" for the {r.org} org"
                 + (f", project {r.project}" if r.project else "") + "."
                 + (" Multi-line value." if r.field == "notes" else "")
                 + (" Paste the key as it is; it is stored for Basic auth." if r.encode == "basic" else ""))
        flags = ""
        if r.flags.get("near_duplicate"):
            flags += f'<p class="flag">Similar existing item: {e(r.flags["near_duplicate"])}</p>'
        if r.flags.get("names_unavailable"):
            flags += '<p class="flag">Could not check existing names.</p>'
        if r.result:
            flags += f'<p class="flag">Last attempt: {e(r.result)}</p>'
        fid = f"v-{e(r.id)}"
        field = (f'<textarea id="{fid}" name="value" required autocomplete="off" spellcheck="false">'
                 '</textarea>' if r.field == "notes" else
                 f'<input id="{fid}" type="password" name="value" required autocomplete="off">')
        out.append(
            f'<li><div class="head"><span class="name">{e(r.name)}</span>'
            f'<span class="age">{e(age_text(now - r.created))}</span></div><p class="meta">{e(where)}</p>'
            f'<p class="purpose">{e(r.purpose)}</p>{flags}'
            f'<form method="post" action="{prefix}/submit">'
            f'<input type="hidden" name="id" value="{e(r.id)}">{hidden}'
            f'<label for="{fid}">Value</label>{field}'
            f'<label for="x-{e(r.id)}">Expires</label>'
            f'<div class="expiry"><input id="x-{e(r.id)}" class="date" type="date" name="expires" '
            f'value="{expiry}" min="{e(tomorrow)}" aria-describedby="xh-{e(r.id)}">'
            '<label class="never"><input type="checkbox" name="never" value="1"> No expiration</label></div>'
            f'<p id="xh-{e(r.id)}" class="hint">Set to one year from today.</p>'
            f'<button type="submit" class="submit" aria-live="polite">'
            f'<span class="spin" aria-hidden="true"></span>'
            f'{"Replace value" if r.replace else "Store value"}</button>'
            '<p class="working" role="status" hidden><span class="bar" aria-hidden="true"></span>'
            'Storing the key in the vault, then checking what it opens. This can take up to half a '
            'minute; keep this page open.</p></form>'
            f'{note if r.id == rid else ""}</li>')
        if r.id == rid:
            placed = True
    if cards:
        out.append("</ul>")
    suspects = ctx["store"].suspects_since()
    if suspects:
        out.append("<h2>May be expired</h2><ul>")
        for name, org, since in suspects:
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(since))
            out.append(
                f'<li><div class="head"><span class="name">{e(name)}</span></div>'
                f'<p class="meta">In the {e(org)} org. The service refused it on {e(when)}, '
                f'so it may be expired.</p>'
                f'<form method="post" action="{prefix}/replace">'
                f'<input type="hidden" name="name" value="{e(name)}">'
                f'<input type="hidden" name="org" value="{e(org)}">{hidden}'
                f'<button type="submit" class="secondary">Replace</button></form></li>')
        out.append("</ul>")
    return "\n".join(out), placed


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="relay_entry.py")
    for opt in ("--routes", "--entry-config", "--state", "--local-url", "--infra-url",
                "--loopback-secret-file", "--unlock-file", "--bw-env-file", "--org-ids"):
        ap.add_argument(opt, required=True)
    ap.add_argument("--port", required=True, type=int)
    ap.add_argument("--bind", default="127.0.0.1")
    ap.add_argument("--public-prefix", default="/entry", help="path the front door serves us under")
    return ap.parse_args(argv)


def qr_png(uri):
    # qrencode reads stdin. No seed in argv, a file, a URL request or a third-party QR service.
    return subprocess.run(["/usr/bin/qrencode", "-t", "PNG", "-s", "4", "-o", "-"],
                          input=uri.encode(), capture_output=True, check=True, timeout=5).stdout


def build_server(args, **overrides):
    """Configured, not yet serving. Overrides (tests): whois, clock, audit_out."""
    if not isinstance(args, argparse.Namespace):
        args = parse_args(args)
    routes = routes_mod.load_routes(args.routes)  # fail fast on a broken routes file
    entry_conf = routes_mod.load_entry_config(args.entry_config)
    routes.validate_owners(entry_conf.owner_nodes)
    with open(args.org_ids) as f:
        org_ids = json.load(f)
    for org in routes_mod.ORGS:
        if org not in org_ids or "org_id" not in org_ids[org] or "collections" not in org_ids[org]:
            raise SystemExit(f"org ids file lacks {org}")
    with open(args.loopback_secret_file) as f:
        secret = f.read().strip()
    if not secret:
        raise SystemExit("loopback secret file is empty")
    bw_env = {k: v for k, v in read_env_file(args.bw_env_file).items() if k != "BW_SESSION"}
    with open(PAGE) as f:
        template = f.read()
    def hashes(tag):
        return " ".join("'sha256-" + base64.b64encode(hashlib.sha256(s.encode()).digest()).decode() + "'"
                        for s in re.findall(f"<{tag}>(.*?)</{tag}>", template, re.S))
    csp = ("default-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'; "
           f"script-src {hashes('script')}; style-src {hashes('style')}")
    clock = overrides.pop("clock", time.time)
    store = RequestsStore(Path(args.state), clock=clock)
    ctx = {
        "store": store,
        "entry": Entry(store, org_ids, {"local": args.local_url, "infra": args.infra_url}, secret,
                       args.unlock_file, bw_env, clock),
        "owners": set(entry_conf.owner_nodes),
        "agent_nodes": set(routes.agent_nodes),
        "csp": csp,
        "approval_front": approval_rpc.front_key(),
        "qr_png": qr_png,
        "approval_rpc": approval_rpc.request,
        "whois": overrides.pop("whois", None) or Whois(),
        "clock": clock,
        "loopback_secret": secret,
        "page_token": secrets.token_urlsafe(32),
        "prefix": args.public_prefix.rstrip("/"),
        "template": template,
        "routes_path": args.routes,
        "access_check": access_check.check,
    }
    ctx.update(overrides)
    return RelayServer((args.bind, args.port), Handler, ctx)


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    srv = build_server(args)
    host, port = srv.server_address[:2]
    print(f"relay-entry listening on {host}:{port}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
