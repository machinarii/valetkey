"""Who is calling: Tailscale machine name of a peer address."""
import json
import subprocess
import threading
import time


class Whois:
    """`tailscale whois` lookups, cached for `ttl` seconds (failures too)."""

    def __init__(self, ttl=300, runner=subprocess.run):
        self.ttl = ttl
        self.runner = runner
        self._cache = {}
        self._lock = threading.Lock()

    def machine(self, ip):
        """Lowercase first DNS label of the peer's Tailscale name, or "" if unknown."""
        return self.identity(ip)[0]

    def owner_machine(self, ip):
        """Tagged nodes carry service identity and must never gain owner entry privileges."""
        name, tagged = self.identity(ip)
        return "" if tagged else name

    def identity(self, ip):
        now = time.monotonic()
        with self._lock:
            hit = self._cache.get(ip)
            if hit and now - hit[1] < self.ttl:
                return hit[0]
        try:
            out = self.runner(["tailscale", "whois", "--json", ip],
                              capture_output=True, timeout=5, check=True)
            node = json.loads(out.stdout).get("Node", {})
            # Use the control-plane DNS name, not the display/OS-derived ComputedName.
            name, _, suffix = (node.get("Name") or "").rstrip(".").lower().partition(".")
            # A device of another tailnet (node sharing) picks its own short name, so a short
            # name only counts under this broker's own DNS suffix. Asked on every cache miss:
            # a renamed tailnet must not need a restart.
            own = self.runner(["tailscale", "status", "--json"],
                              capture_output=True, timeout=5, check=True)
            own = json.loads(own.stdout)["Self"]["DNSName"].rstrip(".").lower().partition(".")[2]
            if not own or suffix != own:
                name = ""
            tagged = bool(node.get("Tags"))
        except Exception:
            name, tagged = "", True
        identity = (name, tagged)
        with self._lock:
            self._cache[ip] = (identity, now)
        return identity


def peer_ip(handler):
    """Client address; behind the loopback TLS front end, the address it reports in X-Relay-Peer."""
    addr = handler.client_address[0]
    if addr == "127.0.0.1":
        forwarded = handler.headers.get("X-Relay-Peer")
        if forwarded:
            return forwarded
    return addr
