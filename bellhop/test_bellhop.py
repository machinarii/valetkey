"""Desk and client against synthetic fixtures only: a fake tool with a fake login, a fake MCP server.

Run from the repository root:  python3 -m unittest bellhop/test_bellhop.py
"""
import hashlib
import http.client
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import desk  # noqa: E402
import sweep  # noqa: E402

spec = importlib.util.spec_from_file_location("fakemcp", os.path.join(HERE, "fakes", "fakemcp.py"))
fakemcp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fakemcp)

LOGIN = "synthetic-login-must-not-leave-the-desk"
MCP_KEY = "Bearer synthetic-mcp-key-must-not-leave-the-desk"
OPEN = urllib.request.build_opener(urllib.request.ProxyHandler({})).open


def serve(server):
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return "http://127.0.0.1:%d" % server.server_address[1]


class Bellhop(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.home, os.environ["HOME"] = os.environ.get("HOME"), os.path.join(cls.tmp, "home")
        os.makedirs(os.path.join(os.environ["HOME"], ".config", "fakefield"))
        with open(os.path.join(os.environ["HOME"], ".config", "fakefield", "credentials.json"), "w") as f:
            f.write(LOGIN)
        with open(os.path.join(cls.tmp, "mcp.key"), "w") as f:
            f.write(MCP_KEY + "\n")
        cls.mcp = fakemcp.make_server(("127.0.0.1", 0), MCP_KEY)
        mcp_url = serve(cls.mcp)
        cls.config = os.path.join(cls.tmp, "desk.toml")
        with open(cls.config, "w") as f:
            f.write(f"""
[desk]
identity = "address"
spool = "{cls.tmp}/spool"
max_upload_mb = 1
[address]
"127.0.0.1" = "agent"
[tool.fakefield]
command = "{HERE}/fakes/fakefield"
callers = ["agent"]
allow = ["whoami", "upload", "make", "fail"]
deny = ["--debug"]
[tool.private]
command = "{HERE}/fakes/fakefield"
callers = ["someone-else"]
allow = ["whoami"]
[mcp.fakemcp]
upstream = "{mcp_url}/mcp"
header = "Authorization"
value_file = "{cls.tmp}/mcp.key"
callers = ["agent"]
[mcp.private]
upstream = "{mcp_url}/mcp"
callers = ["someone-else"]
""")
        cls.audit = io.StringIO()
        cls.desk = desk.build_server(cls.config, "127.0.0.1:0", audit_out=cls.audit)
        cls.url = serve(cls.desk)

    @classmethod
    def tearDownClass(cls):
        for server in (cls.desk, cls.mcp):
            server.shutdown()
            server.server_close()
        os.environ["HOME"] = cls.home or "/"
        shutil.rmtree(cls.tmp)

    def http(self, method, path, data=None, headers=None):
        req = urllib.request.Request(self.url + path, data=data, method=method, headers=headers or {})
        try:
            with OPEN(req, timeout=20) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def run_tool(self, tool, args, files=()):
        status, body = self.http("POST", "/run/" + tool, json.dumps({"args": args, "files": list(files)}).encode())
        return status, (json.loads(body) if status == 200 else body.decode())

    def test_tool_runs_with_the_desk_login_and_never_shows_it(self):
        status, out = self.run_tool("fakefield", ["whoami"])
        self.assertEqual((status, out["exit"], out["stdout"]), (200, 0, "signed in\n"))
        self.assertNotIn(LOGIN, json.dumps(out))

    def test_uploaded_file_reaches_the_tool_under_the_desk_name(self):
        data = b"scene bytes" * 1000
        status, fid = self.http("PUT", "/file?ext=png", data)
        self.assertEqual(status, 200)
        fid = fid.decode().strip()
        self.assertRegex(fid, r"^[a-f0-9]{32}\.png$")
        status, out = self.run_tool("fakefield", ["upload", fid], [fid])
        self.assertEqual(status, 200)
        self.assertIn(hashlib.sha256(data).hexdigest(), out["stdout"])
        self.assertEqual(self.run_tool("fakefield", ["upload", fid], [fid])[0], 400)  # used once, then gone

    def test_file_the_tool_writes_comes_back_once(self):
        status, out = self.run_tool("fakefield", ["make", "result.txt"])
        self.assertEqual([f["name"] for f in out["files"]], ["result.txt"])
        fid = out["files"][0]["id"]
        self.assertEqual(self.http("GET", "/file/" + fid), (200, b"made by fakefield\n"))
        self.assertEqual(self.http("GET", "/file/" + fid)[0], 404)

    def test_exit_code_and_error_output_come_back(self):
        status, out = self.run_tool("fakefield", ["fail"])
        self.assertEqual((status, out["exit"], out["stderr"]), (200, 3, "something went wrong\n"))

    def test_refusals(self):
        cases = {
            "subcommand that prints the login": ["auth", "token"],
            "denied option": ["whoami", "--debug"],
            "denied option with a value": ["whoami", "--debug=1"],
            "absolute path": ["upload", os.path.join(os.environ["HOME"], ".config/fakefield/credentials.json")],
            "home path": ["upload", "~/.config/fakefield/credentials.json"],
            "path in an option": ["upload", "--file=/etc/passwd"],
            "path glued to a short option": ["upload", "-f/etc/passwd"],
            "file url": ["upload", "file:///etc/passwd"],
            "step up out of the job folder": ["upload", "../../home/.config/fakefield/credentials.json"],
        }
        for what, args in cases.items():
            with self.subTest(what):
                status, text = self.run_tool("fakefield", args)
                self.assertEqual(status, 403, text)
                self.assertNotIn(LOGIN, text)
        self.assertEqual(self.run_tool("private", ["whoami"])[0], 403)   # another caller's tool
        self.assertEqual(self.run_tool("nosuch", ["whoami"])[0], 403)
        self.assertEqual(self.http("POST", "/run/fakefield", b"not json")[0], 400)
        self.assertEqual(self.run_tool("fakefield", ["upload", "0" * 32], ["0" * 32])[0], 400)  # never uploaded
        conn = http.client.HTTPConnection("127.0.0.1", self.desk.server_address[1], timeout=20)
        conn.putrequest("PUT", "/file?ext=png")   # announce more than the limit; refused before any byte
        conn.putheader("Content-Length", str(1024 * 1024 + 1))
        conn.endheaders()
        self.assertEqual(conn.getresponse().status, 413)
        conn.close()
        self.assertEqual(self.http("PUT", "/file?ext=../x", b"x")[0], 400)
        self.assertEqual(self.http("GET", "/file/..%2F..%2Fdesk.toml")[0], 404)

    def test_unknown_caller_gets_nothing(self):
        cfg = self.desk.ctx["cfg"]
        saved, cfg["addresses"] = cfg["addresses"], {}
        try:
            self.assertEqual(self.http("GET", "/tools")[0], 403)
            self.assertEqual(self.run_tool("fakefield", ["whoami"])[0], 403)
            self.assertEqual(self.http("POST", "/mcp/fakemcp", b"{}")[0], 403)
        finally:
            cfg["addresses"] = saved

    def test_mcp_gets_the_key_from_the_desk_only(self):
        ask = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "whoami"}}).encode()
        status, body = self.http("POST", "/mcp/fakemcp", ask, {"Content-Type": "application/json"})
        self.assertEqual(status, 200)
        self.assertIn("synthetic test account", body.decode())
        self.assertNotIn("synthetic-mcp-key", body.decode())
        # A key the caller sends is dropped, not passed on: the desk's own key still decides.
        status, _ = self.http("POST", "/mcp/fakemcp", ask, {"Authorization": "Bearer wrong"})
        self.assertEqual(status, 200)
        self.assertEqual(self.http("POST", "/mcp/private", ask)[0], 403)
        self.assertEqual(self.http("POST", "/mcp/fakemcp/..%2Fadmin", ask)[0], 400)

    def test_list_shows_only_this_callers_names(self):
        status, body = self.http("GET", "/tools")
        self.assertEqual(json.loads(body), {"tools": ["fakefield"], "mcp": ["fakemcp"]})

    def test_audit_has_names_and_outcomes_but_no_values(self):
        self.run_tool("fakefield", ["whoami"])
        log = self.audit.getvalue()
        self.assertIn("agent run:fakefield POST /run/fakefield 200 exit=0", log)
        self.assertNotIn(LOGIN, log)
        self.assertNotIn("synthetic-mcp-key", log)

    def test_client_under_the_tools_name(self):
        work = os.path.join(self.tmp, "work")
        os.makedirs(work, exist_ok=True)
        link = os.path.join(self.tmp, "fakefield")
        if not os.path.lexists(link):
            os.symlink(os.path.join(HERE, "bellhop"), link)
        with open(os.path.join(work, "scene.png"), "wb") as f:
            f.write(b"local picture")
        with open(os.path.join(work, "result.txt"), "w") as f:
            f.write("mine, keep\n")
        env = {"BELLHOP_URL": self.url, "PATH": "/usr/bin:/bin", "HOME": work}

        def client(*args):
            return subprocess.run([link, *args], cwd=work, env=env, capture_output=True, text=True, timeout=30)

        done = client("upload", "--image=scene.png")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn(hashlib.sha256(b"local picture").hexdigest(), done.stdout)
        done = client("make", "result.txt")
        self.assertIn("saved result.txt.1", done.stderr)   # the local file is never overwritten
        for name, content in (("result.txt", "mine, keep\n"), ("result.txt.1", "made by fakefield\n")):
            with open(os.path.join(work, name)) as f:
                self.assertEqual(f.read(), content)
        self.assertEqual(client("fail").returncode, 3)
        refused = client("auth", "token")
        self.assertEqual(refused.returncode, 126)
        self.assertNotIn(LOGIN, refused.stdout + refused.stderr)


    def test_request_is_filed_for_the_owner_and_cleared_when_done(self):
        ask = {"kind": "mcp", "name": "buffer", "source": "https://mcp.buffer.com/mcp", "note": "post\x1b[2Jdrafts"}
        status, rid = self.http("POST", "/request", json.dumps(ask).encode())
        self.assertEqual(status, 200)
        cfg = self.desk.ctx["cfg"]
        filed = [r for r in desk.waiting(cfg) if r["id"] == rid.decode().strip()]
        self.assertEqual((filed[0]["who"], filed[0]["kind"], filed[0]["name"]), ("agent", "mcp", "buffer"))
        self.assertEqual(filed[0]["note"], "post?[2Jdrafts")   # no control characters reach the owner's terminal
        out = subprocess.run([sys.executable, os.path.join(HERE, "desk.py"), "--config", self.config,
                              "--done", filed[0]["id"]], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual([r for r in desk.waiting(cfg) if r["id"] == filed[0]["id"]], [])

    def test_bad_requests_are_refused(self):
        for ask in ({"kind": "run", "name": "x"}, {"kind": "cli", "name": "x; rm -rf /"},
                    {"kind": "cli", "name": "x", "note": "n" * 501}, ["cli"]):
            with self.subTest(ask=ask):
                self.assertEqual(self.http("POST", "/request", json.dumps(ask).encode())[0], 400)
        cfg = self.desk.ctx["cfg"]
        saved, cfg["requesters"] = cfg["requesters"], set()
        try:
            self.assertEqual(self.http("POST", "/request", b'{"kind":"cli","name":"x"}')[0], 403)
        finally:
            cfg["requesters"] = saved


class TailscaleNames(unittest.TestCase):
    """The production path: relay_front on loopback reports the real peer; whois names it."""

    def test_caller_is_the_machine_the_front_door_reports(self):
        class Whois:
            def machine(self, ip):
                return {"100.64.0.5": "agent"}.get(ip, "")

        with tempfile.TemporaryDirectory() as tmp:
            config = os.path.join(tmp, "desk.toml")
            with open(config, "w") as f:
                f.write(f'[desk]\nspool = "{tmp}/spool"\n[tool.fakefield]\n'
                        f'command = "{HERE}/fakes/fakefield"\ncallers = ["agent"]\nallow = ["whoami"]\n')
            server = desk.build_server(config, "127.0.0.1:0", audit_out=io.StringIO(), whois=Whois())
            url = serve(server)
            try:
                def tools(headers):
                    try:
                        with OPEN(urllib.request.Request(url + "/tools", headers=headers), timeout=20) as r:
                            return r.status
                    except urllib.error.HTTPError as e:
                        return e.code
                self.assertEqual(tools({"X-Relay-Peer": "100.64.0.5"}), 200)
                self.assertEqual(tools({"X-Relay-Peer": "100.64.0.9"}), 403)   # a machine nobody listed
                self.assertEqual(tools({}), 403)                               # no front door, no name
            finally:
                server.shutdown()
                server.server_close()


class Sweep(unittest.TestCase):
    """Fake logins planted in a temporary home folder: found by name, contents never reported."""
    SECRET = "synthetic-secret-value-0123456789"

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.home = os.path.join(self.tmp, "home", "agent")
        self.desk = "http://desk.test:8460"

    def put(self, rel, text):
        path = os.path.join(self.home, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)
        return path

    def run_sweep(self, **kw):
        return sweep.sweep([self.home], self.desk, state=os.path.join(self.tmp, "state"),
                           mcp_prefixes=["https://broker.test"], **kw)

    def test_finds_logins_and_keys_by_name_only(self):
        self.put(".config/higgsfield/credentials.json", self.SECRET)
        self.put("projects/app/.env", f"API_KEY={self.SECRET}\n")
        self.put("projects/app/.env.example", "API_KEY=${API_KEY}\nOTHER_TOKEN=<your token>\n")
        self.put(".ssh/id_ed25519", "-----BEGIN OPENSSH PRIVATE KEY-----\n" + self.SECRET)
        self.put(".npmrc", "registry=https://registry.npmjs.org/\n")          # no token: fine
        self.put(".claude/.credentials.json", self.SECRET)                      # the agent's own login: fine
        self.put("projects/app/.mcp.json", json.dumps({"mcpServers": {
            "viadesk": {"type": "http", "url": self.desk + "/mcp/buffer"},
            "viabroker": {"type": "http", "url": "https://broker.test/local/acme/mcp"},
            "byreference": {"type": "http", "url": self.desk + "/mcp/x", "headers": {"Authorization": "Bearer ${KEY}"}},
            "holdskey": {"type": "http", "url": self.desk + "/mcp/y", "headers": {"Authorization": "Bearer " + self.SECRET}},
            "direct": {"type": "http", "url": "https://mcp.example.com/mcp"},
            "local": {"command": "npx", "args": ["some-server"]}}}))
        # A plugin's bundled server list: only an entry that holds a key is worth a line.
        self.put(".claude/plugins/synced/p/.mcp.json", json.dumps({"mcpServers": {
            "catalogue": {"type": "http", "url": "https://mcp.example.com/mcp", "headers": {"X-Plugin-Bundle": "abc-123"}},
            "pluginkey": {"type": "http", "url": "https://x.example/mcp", "headers": {"X-Key": self.SECRET}}}}))
        lines = self.run_sweep()
        text = "\n".join(lines)
        self.assertIn("mcp pluginkey", text)
        self.assertNotIn("catalogue", text)
        for expected in (".config/higgsfield: a tool's login", "projects/app/.env: holds what looks like a key",
                         ".ssh/id_ed25519: private key", "mcp holdskey", "holds a key in headers",
                         "mcp direct", "does not go through the broker or the desk", "mcp local", "runs on this machine"):
            self.assertIn(expected, text)
        for fine in (".env.example", ".npmrc", ".claude/.credentials.json", "viadesk", "viabroker", "byreference"):
            self.assertNotIn(fine, text)
        self.assertNotIn(self.SECRET, text)
        self.assertEqual(len(lines), 7, text)
        allowed = self.run_sweep(allow=["mcp:local", "mcp:pluginkey", "*/.config/higgsfield", "*/.ssh/*"])
        self.assertEqual(len(allowed), 3, allowed)

    def test_reports_tools_installed_after_the_known_list(self):
        self.put(".local/bin/existing", "#!/bin/sh\n")
        self.assertEqual(self.run_sweep(), [])                   # first run records what is there
        self.put(".local/bin/newtool", "#!/bin/sh\n")
        self.assertEqual(len(self.run_sweep()), 1)
        self.assertIn("newtool: installed since", self.run_sweep()[0])
        self.assertEqual(self.run_sweep(allow=["tool:newtool"]), [])
        self.assertEqual(self.run_sweep(accept=True), [])
        self.assertEqual(self.run_sweep(), [])


class Config(unittest.TestCase):
    def check(self, text):
        with tempfile.NamedTemporaryFile("w", suffix=".toml") as f:
            f.write(text)
            f.flush()
            return desk.load(f.name)

    def test_unclear_configuration_is_refused(self):
        bad = {
            "tool without an allow list": '[tool.x]\ncommand="/bin/true"\ncallers=["a"]',
            "tool with a relative command": '[tool.x]\ncommand="true"\ncallers=["a"]\nallow=["go"]',
            "tool nobody may call": '[tool.x]\ncommand="/bin/true"\ncallers=[]\nallow=["go"]',
            "mcp over plain http to another machine": '[mcp.x]\nupstream="http://example.com/mcp"\ncallers=["a"]',
            "mcp header without a key file": '[mcp.x]\nupstream="https://example.com/mcp"\nheader="X"\ncallers=["a"]',
            "unknown identity mode": '[desk]\nidentity="trust-me"',
        }
        for what, text in bad.items():
            with self.subTest(what):
                self.assertRaises(ValueError, self.check, text)

    def test_example_configuration_loads(self):
        cfg = desk.load(os.path.join(HERE, "desk.example.toml"))
        self.assertEqual(sorted(cfg["tools"]), ["higgsfield"])
        self.assertEqual(sorted(cfg["mcp"]), ["local-tool"])


if __name__ == "__main__":
    unittest.main()
