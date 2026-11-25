"""Write valetkey's web pages as HTML files, with made-up services, for the README screenshots.

  python3 docs/images/pages.py OUT_DIR      run from the repository root

The pages come from the real page code: the entry page, the page shown after a key is stored, and
the route approval page (served by an entry service started the way its tests start it, with a
stand-in approval worker). No vault, no network, no real name. Development only."""
import datetime, re, sys, tempfile, time
from pathlib import Path
from unittest import mock
sys.path.insert(0, ".")
from broker import broker_entry as be
from broker.brokerlib.requests_store import RequestsStore
from broker.tests import test_broker_entry as T

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
template = open("broker/entry-page.html").read()
tmp = Path(tempfile.mkdtemp())
(tmp / "routes.toml").write_text("")

class NoVault:
    def vault(self, org):
        return [], {}

def page(title, body):
    return template.replace("Credential entry", title).replace("{{result}}", "").replace("{{pending}}", body)

# 1. the entry page: one key to paste, one service to sign in to
store = RequestsStore(tmp / "a.json")
store.create("paperplane-api-key", "local", None, "password", "Paperplane email API, to send the nightly build report", False, requester="devbox")
store.create("notehive-oauth", "local", None, "password", "Notehive notes MCP server, for the research agent", False,
             requester="devbox", oauth="https://mcp.notehive.example/mcp")
ctx = {"store": store, "entry": NoVault(), "prefix": "/entry", "page_token": "t", "clock": time.time, "routes_path": str(tmp / "routes.toml")}
(out / "entry.html").write_text(page("Credential entry", be.render_lists(ctx)[0]))

# 2. after storing: what the key opens
store = RequestsStore(tmp / "b.json")
r = store.create("trackboard-api-key", "local", None, "password", "Trackboard issue tracker", False, requester="devbox")
store.set_status(r.id, "stored", "stored trackboard-api-key in local-org")
tool = lambda n, d, g: {"name": n, "description": d, "group": g}
store.set_access(r.id, {"expires": (datetime.date.today() + datetime.timedelta(days=365)).isoformat(), "routes": [{
    "name": "trackboard", "nodes": ["devbox", "ci-runner"], "upstream": "https://mcp.trackboard.example",
    "methods": ["POST"], "paths": ["/mcp"], "check": "ok", "server": "trackboard-mcp", "tools": [
        tool("search_issues", "Find issues by text, label or assignee", "read"),
        tool("get_issue", "Read one issue with its comments", "read"),
        tool("list_projects", "List the projects you can see", "read"),
        tool("create_issue", "Open a new issue", "change"),
        tool("add_comment", "Comment on an issue", "change"),
        tool("delete_issue", "Remove an issue for good", "delete")]}]})
ctx["store"] = store
(out / "stored.html").write_text(page("Key stored", be.render_access(ctx, store.get(r.id))))

# 3. the approval page, from a running entry service with a stand-in approval worker
t = T.BrokerEntryTest("test_join_review_page_names_the_route_and_its_machines")
t.setUp(); t.start()
rpc, key = be.approval_rpc, b"synthetic front key"
t.srv.ctx["approval_front"] = key
spec = {"name": "trackboard", "org": "local", "item": "trackboard-api-key", "upstream": "https://mcp.trackboard.example",
        "methods": ["POST"], "paths": ["/mcp"], "tools": "read-only"}
t.srv.ctx["approval_rpc"] = mock.Mock(return_value=(200, {"node": "devbox", "spec": spec, "state": "pending", "expires": t.now + 840}))
path = "/approve/" + "b" * 40
html = t.req(path, peer=T.OWNER_IP, headers={rpc.HEADER: rpc.proof(key, "GET", path, T.OWNER_IP)})[2].decode()
(out / "approve.html").write_text(html)
t.doCleanups()
print("wrote", sorted(p.name for p in out.glob("*.html")))
