#!/usr/bin/python3 -I
"""bellhop desk: runs listed tools and forwards listed MCP servers for other machines.

Runs on a machine that holds logins and has no agent. A caller (an agent machine) may:

  PUT  /file?ext=png     upload one file, get an id back
  POST /run/<tool>       run a listed tool with checked arguments in an empty folder
  GET  /file/<id>        fetch a file the tool wrote (deleted once fetched)
  ANY  /mcp/<name>/...   reach a listed MCP server; the desk adds its key
  GET  /tools            what this caller may use
  POST /request          ask the owner to install a tool or MCP server here (names only)

Owner, on the desk:  desk.py --requests   and   desk.py --done ID

Callers are named by Tailscale machine name. Listens on plain HTTP: put valetkey's relay_front
in front of it for TLS (it reports the real peer in X-Relay-Peer, trusted from loopback only).
"""
import argparse
import http.client
import json
import os
import re
import secrets
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (HERE, os.path.join(HERE, "..", "relay")):  # installed next to relaylib/, or in the checkout
    if os.path.isdir(os.path.join(_p, "relaylib")):
        sys.path.insert(0, _p)
        break
from relaylib.httpbase import RelayHandler, RelayServer  # noqa: E402
from relaylib.identity import Whois, peer_ip  # noqa: E402

NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")
FILE_ID = re.compile(r"[a-f0-9]{32}(\.[a-z0-9]{1,8})?")
# An argument that points outside the job folder: /x, ~x, -o/x, key=/x, @/x, file:/x, or a .. step.
PATHY = re.compile(r"(^|[=@:,])[/~]|^-[A-Za-z0-9][/~]|(^|/)\.\.(/|$)")
MAX_ARGS, MAX_ARG, MAX_OUTPUT, MAX_PENDING, KEEP_SECONDS = 64, 4096, 1 << 20, 20, 3600
MAX_MCP_BODY = 10 * 1024 * 1024
MAX_REQUESTS = 100
REQUESTS_LOCK = threading.Lock()
HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer",
       "transfer-encoding", "upgrade", "host", "content-length"}
DROP_IN = HOP | {"authorization", "cookie", "x-relay-peer", "x-relay-loopback"}
DROP_OUT = HOP | {"server", "date", "set-cookie"}


def load(path):
    """Parsed and checked configuration. Anything unclear is an error: the desk must not start."""
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    desk = raw.get("desk", {})
    cfg = {"identity": desk.get("identity", "tailscale"), "addresses": raw.get("address", {}),
           "listen": desk.get("listen", "127.0.0.1:8460"), "spool": desk.get("spool", "/var/lib/bellhop"),
           "max_upload": int(desk.get("max_upload_mb", 50)) * 1024 * 1024, "tools": {}, "mcp": {}}
    if cfg["identity"] not in ("tailscale", "address"):
        raise ValueError("desk.identity must be 'tailscale' or 'address'")

    def callers(kind, name, entry):
        who = entry.get("callers")
        if not (NAME.fullmatch(name) and isinstance(who, list) and who
                and all(isinstance(w, str) and NAME.fullmatch(w) for w in who)):
            raise ValueError(f"{kind}.{name}: needs a lowercase name and a non-empty callers list")
        return who

    if not all(NAME.fullmatch(str(n)) for n in cfg["addresses"].values()):
        raise ValueError("address: names must be lowercase machine names")
    for name, t in raw.get("tool", {}).items():
        allow = t.get("allow")
        if not (isinstance(allow, list) and allow and all(isinstance(a, str) and a.split() for a in allow)):
            raise ValueError(f"tool.{name}: allow must list the subcommands callers may run")
        if not os.path.isabs(t.get("command", "")):
            raise ValueError(f"tool.{name}: command must be an absolute path")
        cfg["tools"][name] = {"command": t["command"], "allow": [a.split() for a in allow],
                              "deny": set(t.get("deny", [])), "timeout": int(t.get("timeout", 300)),
                              "callers": callers("tool", name, t)}
    for name, m in raw.get("mcp", {}).items():
        u = urllib.parse.urlsplit(m.get("upstream", ""))
        local = u.hostname in ("127.0.0.1", "localhost", "::1")
        if not u.hostname or u.username or u.query or u.fragment or not (
                u.scheme == "https" or (u.scheme == "http" and local)):
            raise ValueError(f"mcp.{name}: upstream must be https (http only for this machine)")
        if bool(m.get("header")) != bool(m.get("value_file")):
            raise ValueError(f"mcp.{name}: give header and value_file together, or neither")
        cfg["mcp"][name] = {"url": u, "header": m.get("header"), "value_file": m.get("value_file"),
                            "callers": callers("mcp", name, m)}
    # Who may file an install request: anyone already named above, unless desk.requesters says otherwise.
    named = {w for kind in ("tools", "mcp") for e in cfg[kind].values() for w in e["callers"]}
    cfg["requesters"] = set(desk.get("requesters", named | set(cfg["addresses"].values())))
    return cfg


def waiting(cfg):
    """Install requests the owner has not handled yet."""
    try:
        with open(os.path.join(cfg["spool"], "requests.jsonl")) as f:
            return [json.loads(line) for line in f if line.strip()]
    except FileNotFoundError:
        return []


def check_args(tool, args, files):
    """Reason the call is refused, or None. The allow list is the control; the rest is a second layer."""
    if not (isinstance(args, list) and len(args) <= MAX_ARGS and all(
            isinstance(a, str) and len(a) <= MAX_ARG and "\0" not in a for a in args)):
        return "bad arguments"
    if not (isinstance(files, list) and len(files) <= MAX_PENDING and all(
            isinstance(f, str) and FILE_ID.fullmatch(f) for f in files)):
        return "bad file list"
    if not any(args[:len(seq)] == seq for seq in tool["allow"]):
        return "that command is not on the list"
    for a in args:
        if a in tool["deny"] or a.split("=", 1)[0] in tool["deny"]:
            return "that option is not allowed"
        if PATHY.search(re.sub(r"https?://[^\s,]*", "", a)):
            return "paths outside the job folder are not allowed"
    return None


def sweep(folder):
    """Drop uploads and results nobody collected."""
    cutoff = time.time() - KEEP_SECONDS
    for entry in os.scandir(folder):
        if entry.stat(follow_symlinks=False).st_mtime < cutoff:
            shutil.rmtree(entry.path) if entry.is_dir(follow_symlinks=False) else os.unlink(entry.path)


class Handler(RelayHandler):
    server_version = "bellhop"

    def handle_guarded(self):
        cfg = self.server.ctx["cfg"]
        if cfg["identity"] == "address":  # test networks: direct peers only, no forwarded header
            who = cfg["addresses"].get(self.client_address[0], "")
        else:
            who = self.server.ctx["whois"].machine(peer_ip(self))
        self.who = who if NAME.fullmatch(who or "") else ""
        self.guarded(self.dispatch, self.who, "-")

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = handle_guarded

    def reply(self, route, status, text):
        self.send_text(status, text)
        self.audit(self.who, route, status)

    def box(self, kind):
        path = os.path.join(self.server.ctx["cfg"]["spool"], self.who, kind)
        os.makedirs(path, mode=0o700, exist_ok=True)
        sweep(path)
        return path

    def dispatch(self):
        cfg, method = self.server.ctx["cfg"], self.command
        if not self.who:
            return self.reply("-", 403, "unknown caller")
        if "#" in self.path or not self.path.startswith("/"):
            return self.reply("-", 400, "bad request target")
        parts = urllib.parse.urlsplit(self.path)
        seg = parts.path.split("/")[1:]
        if seg[0] == "tools" and method == "GET" and len(seg) == 1:
            mine = {k: sorted(n for n, e in cfg[k].items() if self.who in e["callers"]) for k in ("tools", "mcp")}
            self.send_json(200, mine)
            return self.audit(self.who, "tools", 200)
        if seg[0] == "file" and method == "PUT" and len(seg) == 1:
            return self.upload(urllib.parse.parse_qs(parts.query))
        if seg[0] == "file" and method == "GET" and len(seg) == 2:
            return self.fetch(seg[1])
        if seg[0] == "run" and method == "POST" and len(seg) == 2:
            return self.run(seg[1])
        if seg[0] == "request" and method == "POST" and len(seg) == 1:
            return self.install_request()
        if seg[0] == "mcp" and len(seg) >= 2:
            return self.mcp(seg[1], parts.path[len("/mcp/" + seg[1]):], parts.query)
        self.reply("-", 404, "not found")

    def install_request(self):
        """Note that a caller wants a tool or MCP server installed here. The owner decides; nothing runs."""
        cfg = self.server.ctx["cfg"]
        if self.who not in cfg["requesters"]:
            return self.reply("request", 403, "not allowed")
        try:
            ask = json.loads(self.read_body(8192) or b"")
            entry = {k: ask.get(k, "") for k in ("kind", "name", "source", "note")}
        except (ValueError, AttributeError):
            return self.reply("request", 400, "body must be JSON")
        if not (entry["kind"] in ("mcp", "cli") and all(isinstance(v, str) for v in entry.values())
                and re.fullmatch(r"[A-Za-z0-9@][A-Za-z0-9@/._-]{0,99}", entry["name"])
                and len(entry["source"]) <= 300 and len(entry["note"]) <= 500):
            return self.reply("request", 400, "needs kind mcp or cli, a plain name, and a short source and note")
        for k in ("source", "note"):  # the owner reads these in a terminal: no control characters
            entry[k] = re.sub(r"[^\x20-\x7e]", "?", entry[k])
        entry.update(id=secrets.token_hex(4), who=self.who, at=time.strftime("%Y-%m-%d %H:%M"))
        with REQUESTS_LOCK:
            if len(waiting(cfg)) >= MAX_REQUESTS:
                return self.reply("request", 429, "too many requests waiting")
            with open(os.path.join(cfg["spool"], "requests.jsonl"), "a") as f:
                f.write(json.dumps(entry) + "\n")
        self.send_text(200, entry["id"])
        self.audit(self.who, "request", 200, f"{entry['kind']} id={entry['id']}")

    def upload(self, query):
        ext = query.get("ext", [""])[0]
        length = self.headers.get("Content-Length", "")
        if not re.fullmatch(r"[a-z0-9]{0,8}", ext) or not length.isdigit():
            return self.reply("file", 400, "needs a length and a short lowercase extension")
        if int(length) > self.server.ctx["cfg"]["max_upload"]:
            self.close_connection = True
            return self.reply("file", 413, "file too large")
        box = self.box("in")
        if len(os.listdir(box)) >= MAX_PENDING:
            return self.reply("file", 429, "too many files waiting")
        fid = secrets.token_hex(16) + ("." + ext if ext else "")  # our name, never the caller's
        left, path = int(length), os.path.join(box, fid)
        with open(path, "xb") as out:
            while left:
                chunk = self.rfile.read(min(left, 65536))
                if not chunk:
                    break
                out.write(chunk)
                left -= len(chunk)
        self._body_read = True
        if left:
            os.unlink(path)
            self.close_connection = True
            return self.reply("file", 400, "upload cut short")
        self.reply("file", 200, fid)

    def fetch(self, fid):
        path = os.path.join(self.box("out"), fid)
        if not re.fullmatch(r"[a-f0-9]{32}", fid) or not os.path.isfile(path):
            return self.reply("file", 404, "not found")
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(os.path.getsize(path)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        with open(path, "rb") as f:
            shutil.copyfileobj(f, self.wfile)
        os.unlink(path)
        self.audit(self.who, "file", 200)

    def run(self, name):
        tool = self.server.ctx["cfg"]["tools"].get(name)
        route = "run:" + name if tool else "run"
        if not tool or self.who not in tool["callers"]:
            return self.reply(route, 403, "not allowed")
        try:
            call = json.loads(self.read_body(1 << 20) or b"")
            args, files = call["args"], call.get("files", [])
        except (ValueError, KeyError, TypeError, AttributeError):
            return self.reply(route, 400, "body must be JSON with an args list")
        why = check_args(tool, args, files)
        if why:
            return self.reply(route, 403, why)
        inbox, outbox = self.box("in"), self.box("out")
        if not all(os.path.isfile(os.path.join(inbox, f)) for f in files):
            return self.reply(route, 400, "unknown file id")
        job = tempfile.mkdtemp(dir=self.box("job"))
        try:
            for f in files:
                os.rename(os.path.join(inbox, f), os.path.join(job, f))
            before = {fn: os.stat(os.path.join(job, fn)).st_mtime_ns for fn in os.listdir(job)}
            env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": os.environ.get("HOME", "/nonexistent"),
                   "LANG": "C.UTF-8"}
            try:
                # ponytail: output arrives when the tool exits; stream it if long jobs need live progress
                done = subprocess.run([tool["command"], *args], cwd=job, env=env, stdin=subprocess.DEVNULL,
                                      capture_output=True, timeout=tool["timeout"])
            except subprocess.TimeoutExpired:
                return self.reply(route, 504, "the tool took too long")
            except OSError:
                return self.reply(route, 502, "the tool could not be started")
            made = []
            # New files, and uploads the tool wrote over, go back to the caller.
            # ponytail: only files at the top of the job folder come back; add folders if a tool needs them
            for fn in sorted(os.listdir(job)):
                src = os.path.join(job, fn)
                if (os.path.isfile(src) and not os.path.islink(src)
                        and before.get(fn) != os.stat(src).st_mtime_ns):
                    fid = secrets.token_hex(16)
                    os.rename(src, os.path.join(outbox, fid))
                    made.append({"id": fid, "name": fn})
        finally:
            shutil.rmtree(job, ignore_errors=True)
        self.send_json(200, {"exit": done.returncode, "files": made,
                             "stdout": done.stdout[-MAX_OUTPUT:].decode(errors="replace"),
                             "stderr": done.stderr[-MAX_OUTPUT:].decode(errors="replace")})
        self.audit(self.who, route, 200, f"exit={done.returncode}")

    def mcp(self, name, rest, query):
        m = self.server.ctx["cfg"]["mcp"].get(name)
        route = "mcp:" + name if m else "mcp"
        if not m or self.who not in m["callers"]:
            return self.reply(route, 403, "not allowed")
        if ".." in urllib.parse.unquote(rest).split("/") or re.search(r"%(2f|5c)", rest, re.I) or "\\" in rest:
            return self.reply(route, 400, "bad path")
        body = None
        if self.headers.get("Content-Length", "").strip() not in ("", "0"):
            body = self.read_body(MAX_MCP_BODY)
            if body is None:
                self.close_connection = True
                return self.reply(route, 413, "body too large")
        u = m["url"]
        headers = {k: v for k, v in self.headers.items()
                   if k.lower() not in DROP_IN and k.lower() != (m["header"] or "").lower()}
        headers["Host"] = u.netloc
        if m["header"]:
            try:
                with open(m["value_file"]) as f:  # read per request: a rotated key needs no restart
                    headers[m["header"]] = f.read().strip()
            except OSError:
                return self.reply(route, 502, "key not available on the desk")
        target = (u.path.rstrip("/") + rest or "/") + ("?" + query if query else "")
        if u.scheme == "https":
            conn = http.client.HTTPSConnection(u.hostname, u.port or 443, timeout=600,
                                               context=ssl.create_default_context())
        else:
            conn = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=600)
        try:
            conn.request(self.command, target, body=body, headers=headers)
            resp = conn.getresponse()
        except (OSError, http.client.HTTPException):
            conn.close()
            return self.reply(route, 502, "MCP server unavailable")
        self.relay(resp, conn)
        self.audit(self.who, route, resp.status)

    def relay(self, resp, conn):
        """Stream the reply through, so server-sent events stay live. Same loop as relay_front."""
        self.send_response(resp.status, resp.reason)
        for k, v in resp.getheaders():
            if k.lower() not in DROP_OUT:
                self.send_header(k, v)
        length = resp.getheader("Content-Length")
        chunked = length is None and resp.status not in (204, 304)
        if length is not None:
            self.send_header("Content-Length", length)
        elif chunked:
            self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        try:
            while True:
                chunk = resp.read1(65536)
                if not chunk:
                    break
                self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk) if chunked else chunk)
                self.wfile.flush()
            if chunked:
                self.wfile.write(b"0\r\n\r\n")
        except (OSError, http.client.HTTPException):
            self.close_connection = True
        finally:
            conn.close()


def build_server(config, bind=None, **overrides):
    """Configured, not yet serving. Overrides (tests): audit_out, whois."""
    cfg = load(config)
    os.makedirs(cfg["spool"], mode=0o700, exist_ok=True)
    host, _, port = (bind or cfg["listen"]).rpartition(":")
    ctx = {"cfg": cfg, "whois": Whois()}
    ctx.update(overrides)
    return RelayServer((host, int(port)), Handler, ctx)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="desk.py")
    ap.add_argument("--config", default="/etc/bellhop/desk.toml")
    ap.add_argument("--bind", help="HOST:PORT, overrides desk.listen")
    ap.add_argument("--requests", action="store_true", help="list install requests that are waiting")
    ap.add_argument("--done", metavar="ID", help="remove a handled install request")
    args = ap.parse_args(argv)
    if args.requests or args.done:
        cfg = load(args.config)
        items = waiting(cfg)
        if args.done:
            with open(os.path.join(cfg["spool"], "requests.jsonl"), "w") as f:
                f.writelines(json.dumps(i) + "\n" for i in items if i["id"] != args.done)
            items = [i for i in items if i["id"] != args.done]
        for i in items:
            print(f"{i['id']}  {i['at']}  {i['who']}  {i['kind']}  {i['name']}  {i['source']}  {i['note']}".rstrip())
        print(f"{len(items)} request(s) waiting")
        return
    srv = build_server(args.config, args.bind)
    cfg = srv.ctx["cfg"]
    print(f"bellhop desk on {srv.server_address[0]}:{srv.server_address[1]}: tools "
          f"{sorted(cfg['tools'])}, mcp {sorted(cfg['mcp'])}, callers by {cfg['identity']}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
