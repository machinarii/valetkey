"""Client tools (client/relay, client/vault, client/vault-entry) against a fake broker."""
import json
import os
import pty
import select
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CLIENT = os.path.join(ROOT, "client")
PY = sys.executable
OLD_PY = "/usr/bin/python3"
SECRET = "s3cr3t-value-XYZ"


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def reply(self, status, body, ctype="text/plain"):
        raw = body if isinstance(body, bytes) else (
            json.dumps(body) if not isinstance(body, str) else body).encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self.server.log.append(("GET", self.path, None, self.headers.get("Content-Type")))
        p = self.path
        if "/moved" in p:  # a redirect that would hand out a value if a client followed it
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/infra/shop/value/x")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if p == "/infra/backups/x":
            return self.reply(200, {"name": "x", "backups": ["20261106", "20261030"]}, "application/json")
        if p == "/infra/backups/empty":
            return self.reply(200, {"name": "empty", "backups": []}, "application/json")
        if p == "/infra/backups/forbidden":
            return self.reply(403, "not allowed\n")
        if p == "/infra/backups/boom":
            return self.reply(503, "bw-error SECRETBODY")
        if p.startswith("/infra/check"):
            return self.reply(200, {"a": "ok", "b": "missing", "c": "not-allowed"})
        if p == "/local/value/claude-code-oauth-laptop":
            return self.reply(200, b"local-value-1")
        if p == "/infra/shop/value/x":
            return self.reply(200, SECRET.encode())
        if p == "/infra/shop/value/x-backup-20261106":
            return self.reply(200, b"backup-value-1")
        if p == "/infra/shop/value/x/notes":
            return self.reply(200, b"line1\nline2\n")
        if p.startswith("/infra/shop/value/boom"):
            return self.reply(503, "bw-error SECRETBODY")
        if p.startswith("/infra/shop/value/"):
            return self.reply(404, "not found")
        if p.startswith("/entry/v1/requests/r_1"):
            return self.reply(200, {"id": "r_1", "name": "n", "org": "infra", "project": None,
                                    "field": self.server.field, "purpose": "p",
                                    "status": self.server.req_status,
                                    "result": "stored n in infra-org", "flags": {}})
        if p.startswith("/entry/v1/requests"):
            return self.reply(200, {"requests": [
                {"id": "r_1", "name": "n", "org": "infra", "project": "pg", "field": "password",
                 "purpose": "for qa", "replace": True, "status": "pending",
                 "created": time.time() - 600, "result": None,
                 "flags": {"near_duplicate": "n-pg-qa", "names_unavailable": True}}],
                "suspects": [{"name": "old", "org": "local", "since": "2026-11-01T00:00:00Z"}]})
        self.reply(404, "not found")

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        self.server.log.append(("POST", self.path, body, self.headers.get("Content-Type")))
        if self.path == "/entry/v1/requests":
            if body.get("name") == "dup":
                return self.reply(409, "exists")
            return self.reply(201, {"id": "r_1", "status": "pending", "field": body.get("field")})
        if self.path == "/entry/v1/replace":
            return self.reply(201, {"id": "r_1", "status": "pending", "field": "password"})
        if self.path == "/entry/v1/requests/r_1/value":
            return self.reply(*getattr(self.server, "submission_reply", (200, "stored n in infra-org")))
        self.reply(404, "not found")


class ClientCase(unittest.TestCase):
    def setUp(self):
        self.srv = HTTPServer(("127.0.0.1", 0), Fake)
        self.srv.log = []
        self.srv.field = "password"
        self.srv.req_status = "stored"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_port}"
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)

    def run_tool(self, tool, *args, stdin="", py=PY, env=None, terminal=True):
        e = dict(os.environ, RELAY_URL=self.base, VAULT_ADDR=self.base + "/infra",
                 VAULT_TOKEN="ignored")
        e.update(env or {})
        if tool == "vault-entry" and stdin and terminal:
            return self.run_entry_terminal([py, os.path.join(CLIENT, tool), *args], e, stdin)
        return subprocess.run([py, os.path.join(CLIENT, tool), *args], input=stdin.encode(),
                              capture_output=True, env=e, timeout=30)

    def run_entry_terminal(self, cmd, env, value):
        """Synthetic input through a PTY, only after echo is disabled and each prompt appears."""
        master, slave = pty.openpty()
        proc = subprocess.Popen(cmd, stdin=slave, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env=env, start_new_session=True)
        os.close(slave)
        err = b""
        try:
            notes = "\n.\n" in value
            chunks = [v + "\n.\n" for v in value.split("\n.\n")[:2]] if notes else value.splitlines(keepends=True)
            for index, chunk in enumerate(chunks):
                marker = (b'(input hidden)' if index == 0 else b'Again, end with a single "."') if notes else (
                    b"Value: " if index == 0 else b"Again: ")
                received = b""
                end = time.monotonic() + 10
                while marker not in received:
                    if time.monotonic() > end or not select.select([proc.stderr], [], [], 10)[0]:
                        self.fail("owner prompt not received")
                    part = os.read(proc.stderr.fileno(), 4096)
                    if not part:
                        self.fail("owner tool exited before hidden prompt")
                    received += part
                err += received
                os.write(master, chunk.encode())
            out, tail = proc.communicate(timeout=10)
            return subprocess.CompletedProcess(cmd, proc.returncode, out, err + tail)
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait()
            os.close(master)
            proc.stdout.close()
            proc.stderr.close()

    def out(self, p):
        return p.stdout.decode(), p.stderr.decode()


class RelayTests(ClientCase):
    def test_check_prints_per_name_and_exit_code(self):
        p = self.run_tool("relay", "check", "infra", "a", "b", "c")
        out, err = self.out(p)
        self.assertEqual(out, "ok  a\nmissing  b\nnot-allowed  c\n")
        self.assertEqual(p.returncode, 1)
        self.assertEqual(self.srv.log[0][1], "/infra/check?names=a,b,c")

    def test_check_all_ok_exits_0(self):
        p = self.run_tool("relay", "check", "infra", "a")
        self.assertEqual(p.returncode, 0)
        self.assertEqual(self.out(p)[0], "ok  a\n")

    def test_request_prints_both_entry_methods(self):
        p = self.run_tool("relay", "request", "n", "--org", "infra", "--project", "pg",
                          "--purpose", "for qa", "--replace")
        out, _ = self.out(p)
        self.assertEqual(p.returncode, 0)
        self.assertIn("request r_1 (pending)", out)
        self.assertIn(f"Enter it with: vault-entry r_1   or   {self.base}/entry — your choice", out)
        method, path, body, ctype = self.srv.log[0]
        self.assertEqual(path, "/entry/v1/requests")
        self.assertEqual(ctype, "application/json")
        self.assertEqual(body, {"name": "n", "org": "infra", "project": "pg", "field": "password",
                                "purpose": "for qa", "replace": True})

    def test_request_notes_field_and_exists(self):
        self.run_tool("relay", "request", "n", "--org", "local", "--purpose", "x", "--notes")
        self.assertEqual(self.srv.log[0][2]["field"], "notes")
        self.assertIsNone(self.srv.log[0][2]["project"])
        p = self.run_tool("relay", "request", "dup", "--org", "local", "--purpose", "x")
        self.assertEqual(p.returncode, 1)
        self.assertIn("exists", self.out(p)[1] + self.out(p)[0])

    def test_backups_lists_dates_newest_first(self):
        pys = [PY] + ([OLD_PY] if os.path.exists(OLD_PY) else [])
        for py in pys:
            p = self.run_tool("relay", "backups", "infra", "x", py=py)
            self.assertEqual((p.returncode, self.out(p)), (0, ("20261106\n20261030\n", "")), py)
        self.assertEqual(self.srv.log[0][1], "/infra/backups/x")

    def test_backups_empty(self):
        p = self.run_tool("relay", "backups", "infra", "empty")
        self.assertEqual((p.returncode, self.out(p)[0]), (0, "no backups\n"))

    def test_backups_errors_exit_1(self):
        p = self.run_tool("relay", "backups", "infra", "forbidden")
        self.assertEqual((p.returncode, self.out(p)), (1, ("", "relay: not allowed (403)\n")))
        p = self.run_tool("relay", "backups", "infra", "unknown")
        self.assertEqual((p.returncode, self.out(p)), (1, ("", "relay: not found (404)\n")))
        p = self.run_tool("relay", "backups", "infra", "boom")
        self.assertEqual((p.returncode, self.out(p)), (1, ("", "relay: backups failed (503)\n")))
        p = self.run_tool("relay", "backups", "moved", "x")
        self.assertEqual((p.returncode, self.out(p)), (1, ("", "relay: backups failed (302)\n")))
        p = self.run_tool("relay", "backups", "infra", "x", env=STRAY_PROXY)
        self.assertEqual((p.returncode, self.out(p)[0]), (0, "20261106\n20261030\n"), p.stderr)
        self.assertEqual([x for x in self.srv.log if x[1] == "/infra/shop/value/x"], [])

    def test_status_prints_status_and_result(self):
        p = self.run_tool("relay", "status", "r_1")
        self.assertEqual(self.out(p)[0], "stored\nstored n in infra-org\n")


class ShimTests(ClientCase):
    def shim(self, *args, py=PY):
        return self.run_tool("vault", *args, py=py)

    def test_vault_shim_get_value_and_notes(self):
        pys = [PY] + ([OLD_PY] if os.path.exists(OLD_PY) else [])
        for py in pys:
            p = self.shim("kv", "get", "-field=value", "shop/x", py=py)
            self.assertEqual((p.returncode, p.stdout, p.stderr), (0, SECRET.encode(), b""), py)
        p = self.shim("kv", "get", "-field=notes", "shop/x")
        self.assertEqual(p.stdout, b"line1\nline2\n")
        self.assertEqual(self.srv.log[-1][1], "/infra/shop/value/x/notes")
        self.assertEqual(self.srv.log[0][1], "/infra/shop/value/x")

    def test_vault_shim_passes_backup_name_through(self):
        p = self.shim("kv", "get", "-field=value", "shop/x-backup-20261106")
        self.assertEqual((p.returncode, p.stdout, p.stderr), (0, b"backup-value-1", b""))
        self.assertEqual(self.srv.log[-1][1], "/infra/shop/value/x-backup-20261106")

    def test_vault_shim_missing_exits_2_with_message(self):
        p = self.shim("kv", "get", "-field=value", "shop/nope")
        self.assertEqual((p.returncode, p.stdout), (2, b""))
        self.assertEqual(p.stderr, b"No value found at shop/nope\n")

    def test_vault_shim_other_error_never_shows_body(self):
        p = self.shim("kv", "get", "-field=value", "shop/boom")
        self.assertEqual((p.returncode, p.stdout), (2, b""))
        self.assertEqual(p.stderr, b"relay error 503\n")
        self.assertEqual(len(self.srv.log), 1)  # an HTTP status is never retried

    def test_vault_shim_rejects_other_commands(self):
        for argv in (["kv", "get", "shop/x"], ["kv", "put", "-field=value", "a/b"],
                     ["login"], [], ["kv", "get", "-field=value", "nopath"],
                     ["kv", "get", "-field=value", "a/b/c"]):
            p = self.shim(*argv)
            self.assertEqual((p.returncode, p.stdout, p.stderr), (2, b"", b"unsupported\n"), argv)
        self.assertEqual(self.srv.log, [])


class EntryTests(ClientCase):
    def test_vault_entry_submits_hidden_value(self):
        p = self.run_tool("vault-entry", "r_1", stdin=f"{SECRET}\n{SECRET}\n")
        out, err = self.out(p)
        self.assertEqual(p.returncode, 0, err)
        self.assertNotIn(SECRET, out + err)
        self.assertIn("stored", out)
        post = [x for x in self.srv.log if x[0] == "POST"][0]
        self.assertEqual(post[1], "/entry/v1/requests/r_1/value")
        self.assertEqual(post[2], {"value": SECRET})
        self.assertEqual(post[3], "application/json")

    def test_vault_entry_mismatch(self):
        p = self.run_tool("vault-entry", "r_1", stdin="one\ntwo\n")
        self.assertEqual(p.returncode, 1)
        self.assertIn("values differ", self.out(p)[1] + self.out(p)[0])
        self.assertFalse([x for x in self.srv.log if x[0] == "POST"])

    def test_vault_entry_notes_reads_until_dot(self):
        self.srv.field = "notes"
        pem = "-----BEGIN KEY-----\nPEMLINE-SECRET-1\nPEMLINE-SECRET-2\n-----END KEY-----"
        p = self.run_tool("vault-entry", "r_1", stdin=f"{pem}\n.\n{pem}\n.\n")
        out, err = self.out(p)
        self.assertEqual(p.returncode, 0, err)
        posts = [x for x in self.srv.log if x[0] == "POST"]
        self.assertEqual([x[2] for x in posts], [{"value": pem}])  # asked twice, stored once
        self.assertIn('(input hidden)', err)
        for line in ("PEMLINE-SECRET-1", "PEMLINE-SECRET-2"):
            self.assertNotIn(line, out + err)

    def test_vault_entry_notes_mismatch(self):
        self.srv.field = "notes"
        p = self.run_tool("vault-entry", "r_1", stdin="a\nb\n.\na\nc\n.\n")
        self.assertEqual(p.returncode, 1)
        self.assertIn("values differ", self.out(p)[1])
        self.assertFalse([x for x in self.srv.log if x[0] == "POST"])

    def test_vault_entry_lists_pending_and_suspects(self):
        p = self.run_tool("vault-entry")
        out, _ = self.out(p)
        self.assertEqual(p.returncode, 0)
        self.assertIn("r_1", out)
        self.assertIn("for qa", out)
        self.assertIn("flags=replace,near_duplicate=n-pg-qa,names_unavailable", out)
        self.assertIn("age=10m", out)
        self.assertIn("may be expired: old (local)", out)
        self.assertEqual(self.srv.log[0][1], "/entry/v1/requests?status=pending")

    def test_vault_entry_id_shows_near_duplicate_from_owner_listing(self):
        self.srv.req_status = "pending"
        p = self.run_tool("vault-entry", "r_1", stdin=f"{SECRET}\n{SECRET}\n")
        out, err = self.out(p)
        self.assertEqual(p.returncode, 0, err)
        self.assertIn("near_duplicate=n-pg-qa", out)
        self.assertEqual([x[1] for x in self.srv.log if x[0] == "GET"],
                         ["/entry/v1/requests/r_1", "/entry/v1/requests?status=pending"])

    def test_vault_entry_add_and_replace(self):
        p = self.run_tool("vault-entry", "add", "n", "--org", "infra", "--purpose", "q",
                          stdin=f"{SECRET}\n{SECRET}\n")
        self.assertEqual(p.returncode, 0, self.out(p)[1])
        self.assertEqual([x[1] for x in self.srv.log if x[0] == "POST"],
                         ["/entry/v1/requests", "/entry/v1/requests/r_1/value"])
        self.srv.log.clear()
        p = self.run_tool("vault-entry", "replace", "n", "--org", "infra",
                          stdin=f"{SECRET}\n{SECRET}\n")
        self.assertEqual(p.returncode, 0, self.out(p)[1])
        posts = [x for x in self.srv.log if x[0] == "POST"]
        self.assertEqual(posts[0][1:3], ("/entry/v1/replace", {"name": "n", "org": "infra"}))
        self.assertNotIn(SECRET, self.out(p)[0])


STRAY_PROXY = {"HTTPS_PROXY": "http://127.0.0.1:1", "HTTP_PROXY": "http://127.0.0.1:1",
               "https_proxy": "http://127.0.0.1:1", "http_proxy": "http://127.0.0.1:1",
               "ALL_PROXY": "http://127.0.0.1:1", "NO_PROXY": "", "no_proxy": ""}


class HardeningTests(ClientCase):
    def test_submission_response_cannot_echo_value(self):
        for status in (200, 503):
            self.srv.submission_reply = (status, SECRET)
            p = self.run_tool("vault-entry", "r_1", stdin=f"{SECRET}\n{SECRET}\n")
            self.assertEqual(p.returncode, 0 if status == 200 else 1)
            self.assertNotIn(SECRET.encode(), p.stdout + p.stderr)

    def test_password_echo_fallback_aborts(self):
        import getpass
        import runpy
        import warnings
        from unittest import mock
        with warnings.catch_warnings():
            tool = runpy.run_path(os.path.join(CLIENT, "vault-entry"))
            def fallback(*args):
                warnings.warn("synthetic echo unavailable", getpass.GetPassWarning)
                self.fail("must abort before fallback input")
            with mock.patch("sys.stdin.isatty", return_value=True), mock.patch("getpass.getpass", fallback):
                with self.assertRaisesRegex(SystemExit, "hidden input unavailable"):
                    tool["read_value"]("password")

    def test_owner_entry_refuses_piped_values(self):
        for field, value in (("password", f"{SECRET}\n{SECRET}\n"), ("notes", "line\n.\nline\n.\n")):
            self.srv.field = field
            p = self.run_tool("vault-entry", "r_1", stdin=value, terminal=False)
            self.assertNotEqual(p.returncode, 0)
            self.assertIn(b"private terminal", p.stderr)
            self.assertNotIn(SECRET.encode(), p.stdout + p.stderr)
        self.assertFalse([x for x in self.srv.log if x[0] == "POST"])

    def test_entry_and_relay_reject_non_tls_remote_urls_before_network(self):
        for addr in ("http://broker.example.ts.net", "file:///tmp/value", "https://user@broker.example",
                     "https://broker.example?x=1", "https://broker.example\n/path"):
            for tool, args in (("relay", ("check", "local", "a")), ("vault-entry", ())):
                p = self.run_tool(tool, *args, env={"RELAY_URL": addr})
                self.assertNotEqual(p.returncode, 0)
                self.assertIn(b"bad RELAY_URL", p.stderr)
        self.assertEqual(self.srv.log, [])

    def test_guide_is_offline_and_has_no_url_requirement(self):
        with tempfile.TemporaryDirectory() as cfg:
            p = self.run_tool("relay", "guide", env={"RELAY_URL": "", "XDG_CONFIG_HOME": cfg})
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn(b"trusted_value_nodes", p.stdout)
        self.assertIn(b"Never read a value", p.stdout)
        self.assertEqual(self.srv.log, [])

    def test_relay_url_from_config_file(self):
        with tempfile.TemporaryDirectory() as cfg:
            os.makedirs(os.path.join(cfg, "valetkey"))
            with open(os.path.join(cfg, "valetkey", "relay-url"), "w") as f:
                f.write(self.base + "/\n")
            p = self.run_tool("relay", "check", "infra", "a", env={"RELAY_URL": "", "XDG_CONFIG_HOME": cfg})
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("a", p.stdout.decode())

    def test_no_relay_url_is_a_clear_error(self):
        with tempfile.TemporaryDirectory() as cfg:
            for tool, args in (("relay", ("check", "infra", "a")), ("vault-entry", ())):
                p = self.run_tool(tool, *args, env={"RELAY_URL": "", "XDG_CONFIG_HOME": cfg})
                self.assertNotEqual(p.returncode, 0)
                self.assertIn("no broker URL", p.stderr.decode())
        self.assertEqual(self.srv.log, [])

    def test_proxy_variables_are_ignored(self):
        p = self.run_tool("vault", "kv", "get", "-field=value", "shop/x", env=STRAY_PROXY)
        self.assertEqual((p.returncode, p.stdout), (0, SECRET.encode()), p.stderr)
        p = self.run_tool("relay", "check", "infra", "a", env=STRAY_PROXY)
        self.assertEqual((p.returncode, self.out(p)[0]), (0, "ok  a\n"), p.stderr)
        p = self.run_tool("vault-entry", env=STRAY_PROXY)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("r_1", self.out(p)[0])

    def test_redirects_are_errors(self):
        p = self.run_tool("vault", "kv", "get", "-field=value", "moved/x")
        self.assertEqual((p.returncode, p.stdout, p.stderr), (2, b"", b"relay error 302\n"))
        p = self.run_tool("relay", "check", "moved", "a")
        self.assertEqual(p.returncode, 1)
        self.assertIn("302", self.out(p)[1])
        p = self.run_tool("vault-entry", env={"RELAY_URL": self.base + "/moved"})
        self.assertEqual(p.returncode, 1)
        self.assertIn("302", self.out(p)[1])
        for proc in (p,):
            self.assertNotIn(SECRET, self.out(proc)[0] + self.out(proc)[1])
        self.assertEqual([x for x in self.srv.log if x[1] == "/infra/shop/value/x"], [])

    def test_shim_requires_https_vault_addr(self):
        for addr in ("http://broker.example.ts.net/infra", "ftp://x/infra", "broker/infra",
                     "http://127.0.0.1.evil.example/infra"):
            p = self.run_tool("vault", "kv", "get", "-field=value", "shop/x",
                              env={"VAULT_ADDR": addr})
            self.assertEqual((p.returncode, p.stdout, p.stderr), (2, b"", b"relay error bad VAULT_ADDR\n"), addr)
        self.assertEqual(self.srv.log, [])
        p = self.run_tool("vault", "kv", "get", "-field=value", "shop/x",
                          env={"VAULT_ADDR": f"http://localhost:{self.srv.server_port}/infra"})
        self.assertEqual((p.returncode, p.stdout), (0, SECRET.encode()), p.stderr)

    def test_shim_with_root_address_and_local_path(self):
        # LAPTOP launcher: VAULT_ADDR=<broker root>, `vault kv get -field=value local/<name>`
        # -> GET /local/value/<name>, which the front door routes to relay-local's [value.*].
        p = self.run_tool("vault", "kv", "get", "-field=value", "local/claude-code-oauth-laptop",
                          env={"VAULT_ADDR": self.base})
        self.assertEqual((p.returncode, p.stdout), (0, b"local-value-1"), p.stderr)
        self.assertEqual(self.srv.log[-1][1], "/local/value/claude-code-oauth-laptop")


class Flaky:
    """Raw HTTP server following a script per connection: "close" (no response), "reset" (RST),
    or "ok" (200 with `body`). Connections past the script get "ok"."""

    def __init__(self, script, body=b"ok"):
        self.script, self.body, self.paths = list(script), body, []
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(16)
        self.port = self.sock.getsockname()[1]
        self.sock.settimeout(0.1)  # so loop() notices `done`
        self.done = False
        self.thread = threading.Thread(target=self.loop, daemon=True)
        self.thread.start()

    def loop(self):
        while not self.done:
            try:
                conn, _ = self.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with conn:
                conn.settimeout(5)
                data = b""
                while b"\r\n\r\n" not in data:
                    chunk = conn.recv(65536)
                    if not chunk:
                        break
                    data += chunk
                head = data.split(b"\r\n\r\n", 1)[0]
                n = 0
                for line in head.split(b"\r\n")[1:]:
                    if line.lower().startswith(b"content-length:"):
                        n = int(line.split(b":", 1)[1])
                rest = data.split(b"\r\n\r\n", 1)[1] if b"\r\n\r\n" in data else b""
                while len(rest) < n:
                    rest += conn.recv(65536)
                self.paths.append(head.split(b" ")[1].decode() if b" " in head else "")
                what = self.script.pop(0) if self.script else "ok"
                if what == "reset":
                    conn.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
                elif what == "ok":
                    conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                                 b"Content-Length: %d\r\nConnection: close\r\n\r\n" % len(self.body)
                                 + self.body)

    def close(self):
        # Stop the thread before closing: one left in accept() goes on accepting on whatever
        # socket next gets this fd number, which is a later test's server.
        self.done = True
        self.thread.join(5)
        self.sock.close()


class RetryTests(unittest.TestCase):
    def flaky(self, script, body):
        f = Flaky(script, body)
        self.addCleanup(f.close)
        return f

    def run_tool(self, f, tool, *args, py=PY):
        base = f"http://127.0.0.1:{f.port}"
        e = dict(os.environ, RELAY_URL=base, VAULT_ADDR=base + "/infra")
        return subprocess.run([py, os.path.join(CLIENT, tool), *args], capture_output=True, env=e, timeout=30)

    def pys(self):
        return [PY] + ([OLD_PY] if os.path.exists(OLD_PY) else [])

    def test_shim_retries_once_after_dropped_connection(self):
        for py in self.pys():
            for first in ("close", "reset"):
                f = self.flaky([first], SECRET.encode())
                start = time.monotonic()
                p = self.run_tool(f, "vault", "kv", "get", "-field=value", "shop/x", py=py)
                self.assertEqual((p.returncode, p.stdout, p.stderr), (0, SECRET.encode(), b""), (py, first))
                self.assertGreaterEqual(time.monotonic() - start, 0.3)
                self.assertEqual(f.paths, ["/infra/shop/value/x"] * 2)

    def test_shim_gives_up_after_second_failure(self):
        f = self.flaky(["reset", "close", "ok"], SECRET.encode())
        p = self.run_tool(f, "vault", "kv", "get", "-field=value", "shop/x")
        self.assertEqual((p.returncode, p.stdout), (2, b""))
        self.assertRegex(p.stderr.decode(), r"^relay error [A-Za-z]+\n$")
        self.assertEqual(len(f.paths), 2)

    def test_relay_check_and_backups_retry_once(self):
        for py in self.pys():
            f = self.flaky(["reset"], b'{"a": "ok"}')
            p = self.run_tool(f, "relay", "check", "infra", "a", py=py)
            self.assertEqual((p.returncode, p.stdout), (0, b"ok  a\n"), (py, p.stderr))
            self.assertEqual(len(f.paths), 2)
            f = self.flaky(["close"], b'{"name": "x", "backups": ["20261106"]}')
            p = self.run_tool(f, "relay", "backups", "infra", "x", py=py)
            self.assertEqual((p.returncode, p.stdout), (0, b"20261106\n"), (py, p.stderr))
            self.assertEqual(len(f.paths), 2)

    def test_relay_gives_up_after_second_failure_and_never_retries_request(self):
        f = self.flaky(["reset", "close", "ok"], b'{"a": "ok"}')
        p = self.run_tool(f, "relay", "check", "infra", "a")
        self.assertEqual(p.returncode, 1)
        self.assertRegex(p.stderr.decode(), r"^relay: cannot reach broker \([A-Za-z]+\)\n$")
        self.assertEqual(len(f.paths), 2)
        f = self.flaky(["reset"], b'{"id": "r_1", "status": "pending"}')
        p = self.run_tool(f, "relay", "request", "n", "--org", "infra", "--purpose", "x")
        self.assertEqual(p.returncode, 1)
        self.assertEqual(f.paths, ["/entry/v1/requests"])


if __name__ == "__main__":
    unittest.main()
