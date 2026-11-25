"""Bounded local protocol between the entry page and the approval worker."""
import hmac
import json
import os
from pathlib import Path
import socket

SOCKET = "/run/valetkey-broker-approval/worker.sock"
LIMIT = 16384


HEADER = "X-Broker-Approval-Front"
# How long an approval can last, in seconds; 0 is until the owner removes it.
LASTS = {0: "until I remove it", 3600: "1 hour", 28800: "8 hours", 86400: "1 day",
         604800: "7 days", 2592000: "30 days"}


def front_key():
    directory = os.environ.get("CREDENTIALS_DIRECTORY")
    if directory:
        try:
            key = (Path(directory) / "valetkey-broker-approval-front").read_bytes()
        except FileNotFoundError:
            return None
        if len(key) == 64:
            return key
    return None


def sensitive(path):
    return path == "/v1/route-requests" or path.startswith(("/pair/", "/approve/"))


def proof(key, method, path, peer):
    return hmac.digest(key, "\0".join((method, path, peer)).encode(), "sha256").hex()


def request(message):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(450)
        sock.connect(SOCKET)
        sock.sendall(json.dumps(message).encode() + b"\n")
        with sock.makefile("rb") as stream:
            line = stream.readline(LIMIT + 1)
        if len(line) > LIMIT or not line.endswith(b"\n"):
            raise OSError("invalid approval response")
        return json.loads(line)
