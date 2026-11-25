#!/usr/bin/python3 -I
"""Private broker service. Console-only enrollment; entry can forward bounded operations."""
import ipaddress
import json
import os
from pathlib import Path
import pwd
import re
import socket
import socketserver
import struct
import subprocess
import sys

if __package__:
    from .brokerlib.approval_rpc import SOCKET, LIMIT
    from .brokerlib.approvals import Approvals, Refused
    from .brokerlib.identity import Whois
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from brokerlib.approval_rpc import SOCKET, LIMIT
    from brokerlib.approvals import Approvals, Refused
    from brokerlib.identity import Whois


def install(org, route):
    if org not in ("local", "infra"):
        return False
    action = "restore" if route is None else "activate"
    unit = f"valetkey-broker-{action}@{org}.service"
    return subprocess.run(["/usr/bin/systemctl", "start", unit], capture_output=True).returncode == 0


def dispatch(store, identity, msg, admin):
    op = msg["op"]
    if op == "open-pair":
        if not admin:
            raise Refused(403, "broker console required")
        return store.open_pairing(msg["owner"])
    peer = str(ipaddress.ip_address(msg["peer"]))
    who, tagged = identity.identity(peer)
    if not who:
        raise Refused(403, "unknown device")
    if op == "propose":
        return store.propose(who, msg["spec"])
    if tagged or who not in store.policy()[1].owner_nodes:
        raise Refused(403, "untagged approver device required")
    rid = msg["id"]
    if not isinstance(rid, str) or not re.fullmatch("[0-9a-f]{40}", rid):
        raise Refused(404, "unknown link")
    if op == "pair-view":
        return {"uri": store.pair(who, rid), "expires": store.pairing_expires(who, rid)}
    if op == "review":
        return store.review(who, rid)
    if not isinstance(msg.get("code"), str):
        raise Refused(400, "code required")
    if op == "pair-confirm":
        return store.pair(who, rid, msg["code"])
    if op == "approve":
        return store.approve(who, rid, msg["code"])
    raise Refused(400, "unknown operation")


class Handler(socketserver.StreamRequestHandler):
    timeout = 5

    def handle(self):
        uid = struct.unpack("3i", self.request.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))[1]
        if uid not in (0, self.server.entry_uid):
            return
        try:
            line = self.rfile.readline(LIMIT + 1)
            if len(line) > LIMIT or not line.endswith(b"\n"):
                raise ValueError
            msg = json.loads(line)
            data = dispatch(self.server.store, self.server.identity, msg, uid == 0)
            result = [200, data]
        except Refused as exc:
            result = [exc.status, str(exc)]
        except (KeyError, ValueError, TypeError, RecursionError):
            result = [400, "invalid request"]
        except Exception:
            result = [503, "approval operation unavailable; check broker services"]
        self.wfile.write(json.dumps(result).encode() + b"\n")


def main():
    key = (Path(os.environ["CREDENTIALS_DIRECTORY"]) / "valetkey-broker-approval-key").read_bytes()
    store = Approvals("/var/lib/valetkey-broker-approval/state.sqlite", key, "/etc/valetkey-broker/routes.toml",
                      "/etc/valetkey-broker/entry.toml", install)
    store.recover()
    Path(SOCKET).unlink(missing_ok=True)
    with socketserver.UnixStreamServer(SOCKET, Handler) as server:
        server.store = store
        server.entry_uid = pwd.getpwnam("valetkey-broker-entry").pw_uid
        server.identity = Whois(ttl=0)
        os.chmod(SOCKET, 0o660)
        server.serve_forever()


if __name__ == "__main__":
    main()
