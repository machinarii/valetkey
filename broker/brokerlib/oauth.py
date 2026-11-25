"""OAuth sign-in for a service, the way MCP servers offer it: discovery (RFC 9728, RFC 8414),
dynamic client registration (RFC 7591), authorization code with PKCE, and refresh.

Only valetkey-broker-entry signs in and refreshes; the read service only reads `state_of`. The
access token is the vault item's value. Everything a refresh needs is JSON in its notes.
No function here puts token text in an exception or a log line.
"""
import base64
import hashlib
import http.client
import ipaddress
import json
import re
import secrets
import socket
import ssl
import urllib.parse

TIMEOUT = 8
MAX_REPLY = 1 << 16
PENDING_FOR = 600  # seconds the owner has to finish a sign-in
EARLY = 60  # refresh this long before the access token runs out


class Failed(Exception):
    """A sign-in or refresh step failed. The text is for the owner or an agent to read."""


def public_fetch(url, form=None, body=None):
    """(status, JSON reply or None). https on port 443 to an address on the public internet only:
    the addresses come from an agent's request and from documents a service returns, and must
    not reach machines on the tailnet or the broker itself. No redirect is followed."""
    parts = urllib.parse.urlsplit(url)
    host = parts.hostname
    if parts.scheme != "https" or not host or parts.username is not None or parts.port not in (None, 443):
        raise Failed("sign-in addresses must be https on port 443")
    try:
        found = {info[4][0] for info in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
    except (OSError, UnicodeError):
        raise Failed(f"could not find {host}") from None
    if not found or not all(ipaddress.ip_address(a.split("%")[0]).is_global for a in found):
        raise Failed(f"{host} is not an address on the public internet")
    conn = http.client.HTTPSConnection(host, 443, timeout=TIMEOUT)
    try:
        # Connect to the address that was checked, not to whatever the name says a moment later.
        sock = socket.create_connection((sorted(found)[0], 443), timeout=TIMEOUT)
        conn.sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
        return _exchange(conn, parts, form, body)
    except (OSError, http.client.HTTPException):
        raise Failed(f"could not reach {host}") from None
    finally:
        conn.close()


def _exchange(conn, parts, form, body):
    headers = {"Accept": "application/json", "User-Agent": "valetkey"}
    data = None
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    conn.request("POST" if data else "GET", (parts.path or "/") + ("?" + parts.query if parts.query else ""),
                 data, headers)
    resp = conn.getresponse()
    raw = resp.read(MAX_REPLY + 1)
    try:
        return resp.status, json.loads(raw) if len(raw) <= MAX_REPLY else None
    except ValueError:
        return resp.status, None


def state_of(notes):
    """The refresh state kept in an OAuth item's notes, or None for any other item."""
    try:
        state = json.loads(notes) if isinstance(notes, str) else None
    except ValueError:
        return None
    return state if isinstance(state, dict) and state.get("valetkey_oauth") == 1 else None


def due(state, now):
    """True when the access token has run out, or is about to."""
    ends = state.get("expires_at")
    return isinstance(ends, (int, float)) and ends - EARLY <= now


class Client:
    def __init__(self, fetch=public_fetch, schemes=("https",)):
        self.fetch, self.schemes = fetch, schemes  # tests: a local fake server over http

    def _url(self, value, what):
        ok = isinstance(value, str) and len(value) <= 2000 and all(32 < ord(c) < 127 for c in value)
        parts = urllib.parse.urlsplit(value) if ok else None
        if not ok or parts.scheme not in self.schemes or not parts.hostname or parts.fragment:
            raise Failed(f"the service's {what} address is not usable")
        return value

    def resource_problem(self, value):
        """Why `value` cannot be the address of a service to sign in to, or None."""
        try:
            parts = urllib.parse.urlsplit(self._url(value, "own"))
        except Failed:
            return "the sign-in address must be an https address"
        host = parts.hostname
        if len(value) > 300 or parts.username is not None or parts.query:
            return "the sign-in address must be a plain https address"
        if self.schemes == ("https",) and (parts.port not in (None, 443) or not re.fullmatch(
                r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}", host)
                or host.endswith((".localhost", ".local", ".internal", ".ts.net"))):
            return "the sign-in address must be the service's public https address"
        return None

    def discover(self, resource):
        """(authorization server metadata, scopes to ask for) for the service at `resource`."""
        r = urllib.parse.urlsplit(self._url(resource, "own"))
        origin, about = f"{r.scheme}://{r.netloc}", {}
        for url in dict.fromkeys((f"{origin}/.well-known/oauth-protected-resource{r.path.rstrip('/')}",
                                  f"{origin}/.well-known/oauth-protected-resource")):
            status, doc = self.fetch(url)
            if status == 200 and isinstance(doc, dict):
                about = doc
                break
        servers = about.get("authorization_servers")
        issuer = self._url(servers[0], "sign-in") if isinstance(servers, list) and servers else origin
        i = urllib.parse.urlsplit(issuer)
        base, path = f"{i.scheme}://{i.netloc}", i.path.rstrip("/")
        for url in dict.fromkeys((f"{base}/.well-known/oauth-authorization-server{path}",
                                  f"{base}/.well-known/openid-configuration{path}",
                                  f"{base}{path}/.well-known/openid-configuration")):
            status, meta = self.fetch(url)
            if status == 200 and isinstance(meta, dict) and "authorization_endpoint" in meta:
                for key in ("authorization_endpoint", "token_endpoint"):
                    self._url(meta.get(key), "sign-in")
                scopes = about.get("scopes_supported")
                scopes = scopes if isinstance(scopes, list) and all(isinstance(s, str) for s in scopes) else []
                return meta, " ".join(scopes)[:1000]
        raise Failed("this service does not offer an OAuth sign-in that valetkey can find")

    def start(self, resource, redirect):
        """(address to send the owner's browser to, what `finish` needs). Registers valetkey as
        a client of the service first."""
        meta, scope = self.discover(resource)
        if "registration_endpoint" not in meta:
            raise Failed("this service wants an app registered by hand, which valetkey cannot do yet")
        status, doc = self.fetch(self._url(meta["registration_endpoint"], "registration"), body={
            "client_name": "valetkey", "redirect_uris": [redirect], "response_types": ["code"],
            "grant_types": ["authorization_code", "refresh_token"], "token_endpoint_auth_method": "none"})
        if status not in (200, 201) or not isinstance(doc, dict) or not isinstance(doc.get("client_id"), str):
            raise Failed("the service did not accept valetkey as a client")
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        ticket = secrets.token_urlsafe(32)
        query = {"response_type": "code", "client_id": doc["client_id"], "redirect_uri": redirect,
                 "state": ticket, "code_challenge": challenge, "code_challenge_method": "S256",
                 "resource": resource, **({"scope": scope} if scope else {})}
        sep = "&" if "?" in meta["authorization_endpoint"] else "?"
        pending = {"verifier": verifier, "redirect": redirect, "resource": resource,
                   "token_endpoint": meta["token_endpoint"], "client_id": doc["client_id"],
                   "client_secret": doc.get("client_secret") if isinstance(doc.get("client_secret"), str) else ""}
        return meta["authorization_endpoint"] + sep + urllib.parse.urlencode(query), ticket, pending

    def _token(self, state, form, now, old_refresh=""):
        form = dict(form, client_id=state["client_id"], resource=state["resource"])
        if state.get("client_secret"):
            form["client_secret"] = state["client_secret"]
        status, doc = self.fetch(self._url(state["token_endpoint"], "token"), form=form)
        if status != 200 or not isinstance(doc, dict) or not isinstance(doc.get("access_token"), str) \
                or not doc["access_token"]:
            raise Failed("the service did not give a token" if status < 500 else "the service is not answering")
        lasts = doc.get("expires_in")
        new = {"valetkey_oauth": 1, "token_endpoint": state["token_endpoint"], "client_id": state["client_id"],
               "client_secret": state.get("client_secret", ""), "resource": state["resource"],
               "refresh_token": doc["refresh_token"] if isinstance(doc.get("refresh_token"), str) else old_refresh,
               "expires_at": int(now + lasts) if isinstance(lasts, (int, float)) and not isinstance(lasts, bool)
               and lasts > 0 else None}
        return doc["access_token"], new

    def finish(self, pending, code, now):
        """(access token, refresh state) once the service sent the owner back with a code."""
        return self._token(pending, {"grant_type": "authorization_code", "code": code,
                                     "redirect_uri": pending["redirect"],
                                     "code_verifier": pending["verifier"]}, now)

    def refresh(self, state, now):
        """(new access token, new refresh state). The service may also replace the refresh token."""
        if not state.get("refresh_token"):
            raise Failed("the service gave no way to renew this sign-in")
        return self._token(state, {"grant_type": "refresh_token", "refresh_token": state["refresh_token"]},
                           now, state["refresh_token"])
