#!/usr/bin/python3 -I
"""bellhop sweep: look for logins and new tools on an agent machine. Reports names, never contents.

Run as root from a timer, so the agent's user cannot switch it off or edit its lists.

  sweep.py             report; exit 1 when something was found
  sweep.py --accept    take the tools installed now as the known list

Finds: well-known login files, key files, .env files and MCP settings that hold a literal key,
MCP servers that do not go through the desk, and programs that appeared since the known list.
Expected items go in /etc/bellhop/sweep-allow, one path pattern, mcp:NAME or tool:NAME per line.
MCP servers may live at the desk (/etc/bellhop/url) and at the addresses in /etc/bellhop/mcp-prefixes
(one per line, for example the broker's).
"""
import argparse
import fnmatch
import json
import os
import re
import sys
import tomllib
import urllib.parse
import urllib.request

# Where tools keep a login, relative to a home folder. A pattern means: only if the file matches it.
LOGINS = {
    ".config/higgsfield": None, ".vercel": None, ".local/share/com.vercel.cli": None,
    ".config/gh/hosts.yml": None, ".config/stripe": None, ".config/gcloud": None, ".config/doctl": None,
    ".config/rclone": None, ".config/op": None, ".config/Bitwarden CLI": None, ".cloudflared": None,
    ".aws/credentials": None, ".kube/config": None, ".netrc": None, ".git-credentials": None,
    ".cache/huggingface/token": None, ".huggingface/token": None, ".mcp-auth": None, ".runpod": None,
    ".npmrc": r"_authToken|_auth\s*=|_password", ".pypirc": r"password", ".docker/config.json": r'"auth"\s*:',
}
SECRET_LINE = re.compile(r"^\s*(export\s+)?[A-Za-z0-9_]*(KEY|TOKEN|SECRET|PASSWORD|PASSWD)[A-Za-z0-9_]*\s*=\s*"
                         r"[\"']?(?!\$\{|<|your|xxx|changeme|example)[^\s\"'#]{12,}", re.I | re.M)
PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
REFERENCE = re.compile(r"\s*((Bearer|Basic|Token)\s+)?\$\{?[A-Za-z_][A-Za-z0-9_]*\}?\s*", re.I)
SKIP = {"node_modules", ".git", ".cache", ".npm", ".venv", "venv", "site-packages", "__pycache__", ".cargo",
        ".rustup", "go", ".ssh"}
TOOL_DIRS = (".local/bin", "bin", ".npm-global/bin", ".cargo/bin", "go/bin", ".bun/bin", ".deno/bin")
# A header or variable counts as a key by its name; "X-Plugin-Bundle: abc" is a label, not a login.
KEY_NAME = re.compile(r"auth|key|token|secret|passw|cookie|credential", re.I)
MAX_DEPTH, MAX_READ = 6, 256 * 1024


def read(path):
    """Start of a small text file, or "" when it is big, binary or unreadable. Never shown to anyone."""
    try:
        if os.path.getsize(path) > MAX_READ:
            return ""
        with open(path, errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def literal(value):
    """True when a setting holds a value itself, not a ${NAME} reference."""
    return isinstance(value, str) and bool(value.strip()) and not REFERENCE.fullmatch(value)


def mcp_findings(path, servers, prefixes, keys_only=False):
    """keys_only: a plugin's bundled server list is a catalogue, not a setting; only a key in it matters."""
    for name, s in (servers or {}).items():
        if not isinstance(s, dict):
            continue
        held = [k for k in ("headers", "env", "http_headers") for n, v in (s.get(k) or {}).items()
                if literal(v) and KEY_NAME.search(n)]
        held += [k for k in ("bearer_token", "api_key") if literal(s.get(k))]
        url = s.get("url") or ""
        if held:
            yield f"mcp:{name}", f"mcp {name} in {path}: holds a key in {sorted(set(held))[0]}"
        elif keys_only:
            continue
        elif not url:
            yield f"mcp:{name}", f"mcp {name} in {path}: runs on this machine, not through the broker or the desk"
        elif not url.startswith(tuple(prefixes)):
            yield f"mcp:{name}", f"mcp {name} in {path}: does not go through the broker or the desk"


def scan_home(home, prefixes):
    """(allow-key, line) for everything suspicious under one home folder."""
    reported = []
    for rel, pattern in LOGINS.items():
        path = os.path.join(home, rel)
        present = bool(os.listdir(path)) if os.path.isdir(path) else os.path.isfile(path) and os.path.getsize(path) > 0
        if present and (pattern is None or re.search(pattern, read(path))):
            reported.append(path)
            yield path, f"login {path}: a tool's login is stored here"
    for name in os.listdir(os.path.join(home, ".ssh")) if os.path.isdir(os.path.join(home, ".ssh")) else ():
        if PRIVATE_KEY.search(read(os.path.join(home, ".ssh", name))):
            yield os.path.join(home, ".ssh", name), f"login {os.path.join(home, '.ssh', name)}: private key"
    claude = os.path.join(home, ".claude.json")
    try:
        data = json.loads(read(claude) or "{}")
        yield from mcp_findings(claude, data.get("mcpServers"), prefixes)
        for project in (data.get("projects") or {}).values():
            yield from mcp_findings(claude, (project or {}).get("mcpServers"), prefixes)
    except (ValueError, AttributeError):
        pass
    codex = os.path.join(home, ".codex", "config.toml")
    try:
        yield from mcp_findings(codex, tomllib.loads(read(codex)).get("mcp_servers"), prefixes)
    except (ValueError, AttributeError):
        pass
    # ponytail: looks MAX_DEPTH folders deep and skips dependency folders; a login buried deeper is missed
    for folder, dirs, files in os.walk(home):
        depth = folder[len(home):].count(os.sep)
        dirs[:] = [] if depth >= MAX_DEPTH else [d for d in dirs if d not in SKIP and not os.path.islink(os.path.join(folder, d))]
        for fn in files:
            path = os.path.join(folder, fn)
            if any(path == r or path.startswith(r + os.sep) for r in reported):
                continue  # already reported as a tool's login folder
            if fn == ".mcp.json":
                try:
                    plugin = os.sep + os.path.join(".claude", "plugins") + os.sep in path
                    yield from mcp_findings(path, json.loads(read(path) or "{}").get("mcpServers"), prefixes, plugin)
                except (ValueError, AttributeError):
                    pass
            elif fn == ".env" or fn.startswith(".env.") or fn.endswith(".env"):
                if SECRET_LINE.search(read(path)):
                    yield path, f"login {path}: holds what looks like a key"
            elif fn in ("credentials.json", "id_rsa", "id_ed25519") or fn.endswith((".pem", ".key", ".p12")) \
                    or fn.startswith("service-account"):
                if fn.endswith((".p12",)) or fn == "credentials.json" or fn.startswith("service-account") \
                        or PRIVATE_KEY.search(read(path)):
                    yield path, f"login {path}: key or login file"


def tools(homes):
    found = set()
    for home in homes:
        for rel in TOOL_DIRS:
            folder = os.path.join(home, rel)
            if os.path.isdir(folder):
                found.update(os.path.join(folder, n) for n in os.listdir(folder))
    return found


def sweep(homes, desk="", allow=(), state="/var/lib/bellhop-sweep", accept=False, mcp_prefixes=()):
    """Lines to report. The known tool list lives in `state`; --accept rewrites it."""
    prefixes = [p.rstrip("/") + "/" for p in mcp_prefixes if p.strip()]
    if desk:
        prefixes.append(desk.rstrip("/") + "/mcp/")
    def allowed(key):
        return any(fnmatch.fnmatch(key, pattern) for pattern in allow)

    lines, seen = [], set()
    for home in homes:
        for key, line in scan_home(home, prefixes):
            if line not in seen and not allowed(key):
                seen.add(line)
                lines.append(line)
    os.makedirs(state, mode=0o700, exist_ok=True)
    known_file, now = os.path.join(state, "tools"), tools(homes)
    if accept or not os.path.exists(known_file):
        with open(known_file, "w") as f:
            f.write("\n".join(sorted(now)) + "\n")
    else:
        with open(known_file) as f:
            known = set(f.read().split("\n"))
        lines += [f"tool {p}: installed since the known list was taken" for p in sorted(now - known)
                  if not allowed("tool:" + os.path.basename(p))]
    return lines


def notify(url_file, count):
    try:
        with open(url_file) as f:
            base = f.readline().strip().split("?")[0]
    except OSError:
        return
    query = urllib.parse.urlencode({"status": "down" if count else "up", "msg": f"{count} finding(s)" if count else "OK"})
    try:
        urllib.request.urlopen(base + "?" + query, timeout=15).close()
    except OSError:
        print("sweep: could not reach the alert address", file=sys.stderr)


def lines_of(path):
    try:
        with open(path) as f:
            return [line.strip() for line in f if line.strip() and not line.startswith("#")]
    except OSError:
        return []


def main(argv=None):
    ap = argparse.ArgumentParser(prog="sweep.py")
    ap.add_argument("--accept", action="store_true", help="take the tools installed now as the known list")
    ap.add_argument("--homes", nargs="*", help="home folders to check (default: every folder in /home)")
    args = ap.parse_args(argv)
    homes = args.homes or [os.path.join("/home", n) for n in sorted(os.listdir("/home"))]
    desk = (lines_of("/etc/bellhop/url") or [""])[0]
    found = sweep(homes, desk, lines_of("/etc/bellhop/sweep-allow"), accept=args.accept,
                  mcp_prefixes=lines_of("/etc/bellhop/mcp-prefixes"))
    for line in found:
        print(line)
    print(f"sweep: {len(found)} finding(s) in {len(homes)} home folder(s)")
    notify("/etc/bellhop/alert-url", len(found))
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
