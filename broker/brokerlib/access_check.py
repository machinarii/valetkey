"""What a key just stored opens: the routes that use it and, for an MCP server, its tools.

valetkey-broker-entry runs this right after the owner stores a value, with that value still in memory.
The value goes only to each route's own upstream, in the route's own auth header, the same way
the broker would send it. Only route facts, tool names, labels and short descriptions are kept.

No tool is ever called: a call could change or delete data or spend money. The read/write grouping
comes from the server's own tool annotations, which are hints the server chose to give.
"""
import http.client
import json
import re
import ssl
import urllib.parse

TIMEOUT = 8
MAX_BODY = 2 << 20
MAX_PAGES = 10
MAX_TOOLS = 300
PROTOCOL = "2025-06-18"


def looks_like_mcp(route, path):
    """Only these get an MCP handshake: posting JSON-RPC to any other POST endpoint could act."""
    host = urllib.parse.urlsplit(route.upstream).hostname or ""
    return host.startswith("mcp.") or "mcp" in path.split("/")


def https(host):
    return http.client.HTTPSConnection(host, 443, timeout=TIMEOUT, context=ssl.create_default_context())


def check(routes, org, item, value, connect=https):
    """{"routes": [...]}: every [api.*] route of org that uses item, each with its probe result."""
    out = []
    for r in sorted(routes.api.values(), key=lambda r: r.name):
        if r.org != org or r.item != item:
            continue
        entry = {"name": r.name, "nodes": list(r.nodes), "upstream": r.upstream,
                 "methods": list(r.methods), "paths": list(r.paths)}
        entry.update(probe(r, value, connect))
        out.append(entry)
    return {"routes": out}


def probe(route, value, connect):
    paths = [p for p in route.paths if looks_like_mcp(route, p)]
    if "POST" not in route.methods or not paths:
        return {"check": "not-tested", "detail": "Not an MCP server, so the key was not tested here."}
    try:
        return redact(Session(route, paths[0], value, connect).tools(), value)
    except Refused as e:
        return {"check": "refused", "detail": f"The service refused the key (HTTP {e.status})."}
    except NotMcp:
        return {"check": "not-tested", "detail": "No MCP server answered at this address."}
    except (OSError, http.client.HTTPException, ValueError):
        return {"check": "error", "detail": "Could not reach the service to check the key."}


class Refused(Exception):
    def __init__(self, status):
        self.status = status


class NotMcp(Exception):
    pass


class Session:
    def __init__(self, route, path, value, connect):
        parts = urllib.parse.urlsplit(route.upstream)
        self.host, self.path, self.connect = parts.hostname, path, connect
        self.headers = {route.auth_header: route.auth.replace("{}", value),
                        "Content-Type": "application/json",
                        "Accept": "application/json, text/event-stream",
                        "User-Agent": "valetkey-access-check"}
        self.next_id = 0

    def call(self, method, params=None, notify=False):
        body = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            body["params"] = params
        if not notify:
            self.next_id += 1
            body["id"] = self.next_id
        conn = self.connect(self.host)
        try:
            conn.request("POST", self.path, json.dumps(body).encode(), self.headers)
            resp = conn.getresponse()
            if resp.status in (401, 403):
                raise Refused(resp.status)
            if notify:
                return None
            if resp.status != 200:
                raise NotMcp()
            session = resp.getheader("Mcp-Session-Id")
            if session and re.fullmatch(r"[\x21-\x7e]{1,256}", session):
                self.headers["Mcp-Session-Id"] = session
            kind = (resp.getheader("Content-Type") or "").split(";")[0].strip().lower()
            reply = self.read(resp, kind, body["id"])
        finally:
            conn.close()
        if not isinstance(reply, dict) or reply.get("jsonrpc") != "2.0":
            raise NotMcp()
        if "error" in reply:
            raise NotMcp()
        return reply.get("result")

    def read(self, resp, kind, rid):
        if kind == "application/json":
            data = resp.read(MAX_BODY + 1)
            if len(data) > MAX_BODY:
                raise ValueError("reply too large")
            try:
                return json.loads(data)
            except ValueError:
                raise NotMcp()
        if kind != "text/event-stream":
            raise NotMcp()
        seen = 0
        while seen <= MAX_BODY:  # the first event that answers our id; the stream may stay open
            line = resp.readline(MAX_BODY)
            if not line:
                raise NotMcp()
            seen += len(line)
            if line.startswith(b"data:"):
                try:
                    msg = json.loads(line[5:])
                except ValueError:
                    continue
                if isinstance(msg, dict) and msg.get("id") == rid:
                    return msg
        raise ValueError("reply too large")

    def tools(self):
        init = self.call("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                        "clientInfo": {"name": "valetkey-access-check", "version": "1"}})
        if not isinstance(init, dict) or "protocolVersion" not in init:
            raise NotMcp()
        self.headers["MCP-Protocol-Version"] = str(init["protocolVersion"])[:32]
        self.call("notifications/initialized", notify=True)
        server = init.get("serverInfo") if isinstance(init.get("serverInfo"), dict) else {}
        tools, cursor = [], None
        for _ in range(MAX_PAGES):
            try:
                page = self.call("tools/list", {"cursor": cursor} if cursor else {})
            except NotMcp:  # signed in, but it lists no tools (or no more of them)
                break
            if not isinstance(page, dict):
                break
            for t in page.get("tools") or []:
                if isinstance(t, dict) and isinstance(t.get("name"), str) and len(tools) < MAX_TOOLS:
                    tools.append(tool(t))
            cursor = page.get("nextCursor")
            if not isinstance(cursor, str) or not cursor:
                break
        return {"check": "ok", "server": str(server.get("name") or "")[:100], "tools": tools}


def tool(t):
    """Name, a short description and the group its annotations put it in.

    MCP's defaults: readOnlyHint false, destructiveHint true. So an unlabelled tool counts as
    one that may change or delete, never as read only."""
    notes = t.get("annotations") if isinstance(t.get("annotations"), dict) else {}
    if notes.get("readOnlyHint") is True:
        group = "read"
    elif notes.get("destructiveHint") is False:
        group = "change"
    else:
        group = "delete"
    words = " ".join(str(t.get("description") or "").split())
    return {"name": t["name"][:120], "description": words[:200], "group": group}


def redact(result, value):
    """A key echoed back by a server must not reach the page: drop it and its parts."""
    secrets = [value] + [p for p in re.split(r"[:\s]", value) if len(p) >= 8]
    def clean(s):
        for x in secrets:
            if x:
                s = s.replace(x, "[hidden]")
        return s
    result["server"] = clean(result.get("server", ""))
    for t in result.get("tools", []):
        t["name"], t["description"] = clean(t["name"]), clean(t["description"])
    return result
