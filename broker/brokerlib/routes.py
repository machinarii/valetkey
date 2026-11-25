"""Load and validate /etc/valetkey-broker/routes.toml and entry.toml."""
import json
import re
import time
import urllib.parse
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ORGS = ("local", "infra")
NAME_RE = re.compile(r"\A[a-z0-9]+(-[a-z0-9]+)*\Z")
PROJECT_NAME_RE = re.compile(r"\A[a-z0-9]+(-[a-z0-9]+)*-(dev|qa|prod)\Z")
BACKUP_RE = re.compile(r"-backup-[0-9]{8}\Z")
ROUTE_NAME_RE = re.compile(r"\A[a-z0-9][a-z0-9-]{0,62}\Z")
RESERVED_ROUTES = ("value", "check", "refresh", "names", "v1", "backups")
PATTERN_RE = re.compile(r"\A(\*-)?[a-z0-9-]+\Z")
TOOL_NAME_RE = re.compile(r"\A[A-Za-z0-9_.:/-]{1,128}\Z")


class RoutesError(Exception):
    pass


def tool_limit(value):
    """A route's `tools` setting: None (every tool), "read-only" or a list of MCP tool names."""
    if value is None or value == "read-only":
        return value
    if (isinstance(value, list) and 1 <= len(value) <= 200
            and all(isinstance(t, str) and TOOL_NAME_RE.match(t) for t in value)):
        return sorted(set(value))
    raise RoutesError('tools must be "read-only" or a list of MCP tool names')


def validate_item_name(name, project):
    """Error text for an invalid Vaultwarden item name, or None."""
    if BACKUP_RE.search(name):
        return "name ends in -backup-YYYYMMDD, which is reserved for replaced items"
    if project is not None:
        if not PROJECT_NAME_RE.match(name):
            return "project item names must be lowercase words joined by '-' and end in -dev|qa|prod"
    elif not NAME_RE.match(name):
        return "item names must be lowercase letters, digits and single '-' between words"
    return None


def match_item(name, patterns):
    """True if name equals a pattern or matches a '*-suffix' glob ('*' only as leading wildcard)."""
    for p in patterns:
        if p.startswith("*-"):
            if name.endswith(p[1:]) and len(name) > len(p) - 1:
                return True
        elif "*" not in p and name == p:
            return True
    return False


@dataclass
class ApiRoute:
    name: str
    org: str
    item: str
    upstream: str
    auth: str
    methods: list
    paths: list
    nodes: list
    auth_header: str = "Authorization"
    model: bool = False
    # When an approval the owner limited in time ends: machine -> seconds, "*" for every machine.
    until: dict = field(default_factory=dict)
    tools: object = None  # MCP tools the route may call: None (all), "read-only" or a list of names

    def allows(self, node, now):
        forever = float("inf")
        return node in self.nodes and min(self.until.get(node, forever), self.until.get("*", forever)) > now


@dataclass
class ValueRoute:
    name: str
    org: str
    item: str
    nodes: list
    claude_setup_token: bool = False


@dataclass
class ProjectRoute:
    name: str
    org: str
    nodes: list
    items: list


@dataclass
class Routes:
    api: dict = field(default_factory=dict)
    values: dict = field(default_factory=dict)
    projects: dict = field(default_factory=dict)
    agent_nodes: list = field(default_factory=list)
    trusted_value_nodes: list = field(default_factory=list)

    def may_read_values(self, node):
        return node in self.trusted_value_nodes and node not in self.agent_nodes

    def allows_claude_setup_token(self, node, ref):
        route = self.values.get(ref)
        return (node in self.agent_nodes and ref == f"claude-code-oauth-{node}"
                and route is not None and route.claude_setup_token
                and route.org == "local" and route.item == ref and route.nodes == [node])

    def validate_owners(self, owners):
        if set(owners) & set(self.agent_nodes):
            raise RoutesError("agent_nodes must not also be owner_nodes")

    def for_org(self, org):
        def pick(d):
            return {k: v for k, v in d.items() if v.org == org}
        return Routes(pick(self.api), pick(self.values), pick(self.projects),
                      list(self.agent_nodes), list(self.trusted_value_nodes))


@dataclass
class EntryConfig:
    owner_nodes: list
    tailnet_name: str


def _fail(kind, name, problem):
    raise RoutesError(f"[{kind}.{name}] {problem}")


def _common(kind, name, e):
    if not isinstance(e, dict):
        _fail(kind, name, "must be a table")
    if not ROUTE_NAME_RE.match(name):
        _fail(kind, name, "bad route name (lowercase letters, digits, '-', max 63 chars)")
    if name in RESERVED_ROUTES:
        _fail(kind, name, f"route name '{name}' is reserved")
    org = e.get("org")
    if org not in ORGS:
        _fail(kind, name, f"unknown org {org!r} (expected one of {', '.join(ORGS)})")
    nodes = e.get("nodes")
    if not isinstance(nodes, list) or not nodes or not all(isinstance(n, str) and n for n in nodes):
        _fail(kind, name, "nodes must be a non-empty list of machine names")
    return org, nodes


def _item(kind, name, e):
    item = e.get("item")
    if not isinstance(item, str):
        _fail(kind, name, "item is required")
    base = item[:-len("/notes")] if item.endswith("/notes") else item
    err = validate_item_name(base, None)
    if err:
        _fail(kind, name, f"bad item name {item!r}: {err}")
    return item


def _str_list(kind, name, e, key):
    v = e.get(key)
    if not isinstance(v, list) or not v or not all(isinstance(x, str) and x for x in v):
        _fail(kind, name, f"{key} must be a non-empty list of strings")
    return v


def canonical_api_path(path):
    """Fail closed on paths proxies/upstreams can interpret differently.

    Decode once for policy, reject encoded separators, nested escapes and dot segments.
    Ordinary percent-encoded UTF-8 and spaces remain usable in resource identifiers.
    """
    if not isinstance(path, str) or not path.startswith("/"):
        return None
    if re.search(r"%(?![0-9a-fA-F]{2})|%(?:2f|5c|25)", path, re.I):
        return None
    try:
        decoded = urllib.parse.unquote(path, errors="strict")
    except UnicodeError:
        return None
    if (any(ord(c) < 32 or ord(c) == 127 for c in decoded)
            or any(c in decoded for c in "\\;?#%") or "//" in decoded
            or any(s in (".", "..") for s in decoded.split("/"))):
        return None
    return decoded


def api_path_allowed(path, paths):
    decoded = canonical_api_path(path)
    if decoded is None:
        return False
    # A prefix without a trailing slash allows the exact endpoint and its children,
    # never a sibling such as /emails-admin for /emails.
    return any(decoded == p or decoded.startswith(p if p.endswith("/") else p + "/")
               for p in paths)


def proposed_api(spec, node):
    """Web grants can create a new bearer API for the authenticated agent only."""
    if not isinstance(spec, dict) or set(spec) - {"auth", "tools"} != {"name", "org", "item", "upstream", "methods", "paths"}:
        raise RoutesError("expected route name, org, item, upstream, methods and paths")
    # Bearer by default; Basic sends the item as stored, so it must already be base64("<key>:").
    auth = {"bearer": "Bearer {}", "basic": "Basic {}"}.get(spec.get("auth", "bearer"))
    if auth is None:
        raise RoutesError("auth must be bearer or basic")
    name, org, item, origin = (spec[k] for k in ("name", "org", "item", "upstream"))
    if not all(isinstance(x, str) for x in (name, org, item, origin, node)):
        raise RoutesError("invalid route fields")
    if not ROUTE_NAME_RE.fullmatch(name) or name in RESERVED_ROUTES or org not in ORGS:
        raise RoutesError("invalid or reserved route name or org")
    if not ROUTE_NAME_RE.fullmatch(node) or len(item) > 128 or validate_item_name(item, None):
        raise RoutesError("invalid agent or item name")
    label = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    if (len(origin) > 253 or not re.fullmatch(r"https://(?:" + label + r"\.)+[a-z]{2,63}", origin)
            or "xn--" in origin or origin.endswith((".localhost", ".local", ".internal", ".ts.net"))):
        raise RoutesError("use the provider's canonical public HTTPS origin")
    methods, paths = spec["methods"], spec["paths"]
    if not isinstance(methods, list) or not 1 <= len(methods) <= 7 or any(
            m not in ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS") for m in methods):
        raise RoutesError("invalid HTTP methods")
    if not isinstance(paths, list) or not 1 <= len(paths) <= 16 or any(
            not isinstance(p, str) or not re.fullmatch(r"/[a-zA-Z0-9_~./-]{1,255}", p)
            or canonical_api_path(p) != p for p in paths):
        raise RoutesError("use explicit canonical path prefixes")
    return ApiRoute(name, org, item, origin, auth, sorted(set(methods)), sorted(set(paths)), [node],
                    tools=tool_limit(spec.get("tools")))


# What decides where a route's key goes. A machine that joins a route keeps it only while these
# stay as they were when the owner approved the join.
BINDING = ("org", "item", "upstream", "auth", "auth_header")


def join_binding(route):
    return {k: getattr(route, k) for k in BINDING}


# Fixed upstream origins and protocols for API-billed model traffic.
MODEL_ORIGINS = {
    "anthropic": "https://api.anthropic.com",
    "openai": "https://api.openai.com",
    "openrouter": "https://openrouter.ai/api",
}


def model_route(name, entry):
    org, nodes = _common("model", name, entry)
    if set(entry) != {"org", "item", "nodes", "provider", "client"}:
        _fail("model", name, "expected org, item, nodes, provider and client")
    provider, client = entry["provider"], entry["client"]
    if client == "claude" and provider in ("anthropic", "openrouter"):
        paths = ["/v1/messages", "/v1/messages/count_tokens"]
    elif client == "codex" and provider in ("openai", "openrouter"):
        paths = ["/v1/responses"]
        if provider == "openai":
            paths.append("/v1/responses/compact")
    else:
        _fail("model", name, "unsupported client/provider pair")
    item = _item("model", name, entry)
    if item.startswith("claude-code-oauth-") or "/" in item:
        _fail("model", name, "use a dedicated provider API key item")
    header, auth = ("x-api-key", "{}") if provider == "anthropic" else ("Authorization", "Bearer {}")
    return ApiRoute(name, org, item, MODEL_ORIGINS[provider], auth, ["POST"], paths, nodes,
                    auth_header=header, model=True)


def _grant(file, keys, now):
    """A grant file's content, or None for an approval whose time has run out."""
    grant = json.loads(file.read_bytes())
    extra = {"until"} if file.suffix == ".grant" else set()
    if not isinstance(grant, dict) or not keys <= set(grant) <= keys | extra:
        raise RoutesError("invalid web-approved grant")
    until = grant.get("until")
    if until is not None and type(until) is not int:
        raise RoutesError("invalid web-approved grant")
    return None if until is not None and until <= now else grant


def load_routes(path, now=None):
    now = time.time() if now is None else now
    with open(path, "rb") as f:
        conf = tomllib.load(f)
    routes = Routes()
    security = conf.get("security", {})
    if not isinstance(security, dict) or set(security) - {"agent_nodes", "trusted_value_nodes"}:
        raise RoutesError("security: expected agent_nodes and trusted_value_nodes only")
    for key in ("agent_nodes", "trusted_value_nodes"):
        nodes = security.get(key, [])
        if not isinstance(nodes, list) or not all(
                isinstance(n, str) and ROUTE_NAME_RE.fullmatch(n) for n in nodes):
            raise RoutesError(f"security: {key} must be a list of lowercase machine names")
        setattr(routes, key, list(nodes))
    if set(routes.agent_nodes) & set(routes.trusted_value_nodes):
        raise RoutesError("agent_nodes must not also be trusted_value_nodes")
    for name, e in conf.get("api", {}).items():
        org, nodes = _common("api", name, e)
        upstream = e.get("upstream")
        try:
            up = urllib.parse.urlsplit(upstream) if isinstance(upstream, str) else None
            valid = (up and up.scheme == "https" and up.hostname and up.port in (None, 443)
                     and up.username is None and up.path in ("", "/")
                     and not up.query and not up.fragment
                     and not any(c.isspace() or ord(c) < 32 for c in upstream))
        except ValueError:
            valid = False
        if not valid:
            _fail("api", name, "upstream must be an https:// origin on port 443, without userinfo, path or query")
        auth = e.get("auth")
        if (not isinstance(auth, str) or auth.count("{}") != 1
                or any(c in auth.replace("{}", "") for c in "{}\r\n")):
            _fail("api", name, "auth must be a string containing {}")
        paths = _str_list("api", name, e, "paths")
        if any(canonical_api_path(p) != p or "%" in p or "*" in p for p in paths):
            _fail("api", name, "paths must be canonical absolute paths without escapes or wildcards")
        methods = _str_list("api", name, e, "methods")
        if any(m not in ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS") for m in methods):
            _fail("api", name, "unsupported HTTP method")
        try:
            tools = tool_limit(e.get("tools"))
        except RoutesError as exc:
            _fail("api", name, str(exc))
        routes.api[name] = ApiRoute(name, org, _item("api", name, e), upstream, auth,
                                    methods, paths, nodes, tools=tools)
    models = conf.get("model", {})
    if not isinstance(models, dict):
        raise RoutesError("model: expected named route tables")
    for name, entry in models.items():
        if name in routes.api or name in conf.get("project", {}):
            _fail("model", name, "name conflicts with another route")
        routes.api[name] = model_route(name, entry)
    for name, e in conf.get("value", {}).items():
        org, nodes = _common("value", name, e)
        opt_in = e.get("claude_setup_token", False)
        if not isinstance(opt_in, bool):
            _fail("value", name, "claude_setup_token must be a boolean")
        routes.values[name] = ValueRoute(name, org, _item("value", name, e), nodes, opt_in)
        if opt_in and not routes.allows_claude_setup_token(nodes[0], name):
            _fail("value", name, "claude_setup_token requires one agent node and its exact "
                  "local/claude-code-oauth-<node> route and item")
    for name, e in conf.get("project", {}).items():
        org, nodes = _common("project", name, e)
        items = _str_list("project", name, e, "items")
        for pat in items:
            if not PATTERN_RE.match(pat):
                _fail("project", name, f"bad items entry {pat!r}")
        routes.projects[name] = ProjectRoute(name, org, nodes, items)
    for r in (*routes.values.values(), *routes.projects.values()):
        if isinstance(r, ValueRoute) and r.claude_setup_token:
            continue  # Exact local route and single agent were validated above.
        if set(r.nodes) & set(routes.agent_nodes):
            raise RoutesError(f"route {r.name}: agent_nodes cannot be granted raw-value routes")
    grants = Path(path).parent / "approved-routes.d"
    # <name>.grant is an approval with a limit added in 0.6: an end ("until") or a tool list. A
    # version from before reads only *.json, so after a rollback such an approval is gone, never
    # permanent or wider.
    files = sorted([*grants.glob("*.json"), *grants.glob("*.grant")])
    for file in (f for f in files if "+" not in f.stem):  # new routes: <route>.json
        grant = _grant(file, {"spec", "node", "request"}, now)
        if grant is None:
            continue
        route = proposed_api(grant["spec"], grant["node"])
        if file.stem != route.name or route.name in routes.api:
            raise RoutesError("web-approved route name conflicts with another route")
        if "until" in grant:  # the route itself is temporary, also for machines that join it
            route.until["*"] = grant["until"]
        if grant["node"] in routes.agent_nodes:
            routes.api[route.name] = route
    for file in (f for f in files if "+" in f.stem):  # joins: <route>+<machine>.json
        grant = _grant(file, {"join", "node", "request", "bind"}, now)
        if grant is None:
            continue
        if (not all(isinstance(grant[k], str) and ROUTE_NAME_RE.fullmatch(grant[k]) for k in ("join", "node"))
                or file.stem != f"{grant['join']}+{grant['node']}"):
            raise RoutesError("invalid web-approved join")
        route = routes.api.get(grant["join"])
        # A join follows the route only while its key, address and auth stay as approved.
        if (route and not route.model and grant["node"] in routes.agent_nodes
                and grant["node"] not in route.nodes and join_binding(route) == grant["bind"]):
            route.nodes = [*route.nodes, grant["node"]]
            if "until" in grant:
                route.until[grant["node"]] = grant["until"]
    return routes


def load_entry_config(path):
    with open(path, "rb") as f:
        conf = tomllib.load(f)
    owners = conf.get("owner_nodes")
    tailnet = conf.get("tailnet_name")
    if not isinstance(owners, list) or not owners or not all(isinstance(n, str) for n in owners):
        raise RoutesError("entry config: owner_nodes must be a non-empty list")
    if not isinstance(tailnet, str) or not tailnet:
        raise RoutesError("entry config: tailnet_name is required")
    return EntryConfig(list(owners), tailnet)
