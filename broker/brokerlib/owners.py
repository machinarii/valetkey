"""Approver devices: find one of the owner's own devices on the tailnet and list it in entry.toml."""
import ipaddress
import os
import re
import tempfile

from .routes import ROUTE_NAME_RE, load_entry_config

EXAMPLE = ["laptop", "tablet", "phone"]  # config/entry.example.toml: placeholders, not devices


class OwnerError(Exception):
    """Why a device cannot be added. The text is for the person at the broker console."""


def devices(status):
    """{name: (system, [addresses])}: the untagged machines of the broker's own tailnet, from
    `tailscale status --json`. A tagged machine is a server or an agent machine, never an approver
    device, and a machine shared from another tailnet picks its own name, so neither is listed."""
    own = status["Self"]["DNSName"].rstrip(".").lower().partition(".")[2]
    found = {}
    for peer in (status.get("Peer") or {}).values():
        name, _, suffix = (peer.get("DNSName") or "").rstrip(".").lower().partition(".")
        if name and own and suffix == own and not peer.get("Tags"):
            found[name] = (str(peer.get("OS") or ""), [str(a) for a in peer.get("TailscaleIPs") or []])
    return found


def resolve(status, text):
    """The machine name for what the owner typed: its name, its full tailnet name or an address."""
    text = text.strip().lower().rstrip(".")
    own = status["Self"]["DNSName"].rstrip(".").lower().partition(".")[2]
    try:
        address = str(ipaddress.ip_address(text))
    except ValueError:
        address = None
    found = devices(status)
    for name, (_, addresses) in found.items():
        if text in (name, f"{name}.{own}") or (address and address in addresses):
            return name
    listed = ", ".join(sorted(found)) or "none"
    raise OwnerError(f"no device of yours on this tailnet matches {text!r}. An approver device is one of your "
                     f"own devices, signed in to Tailscale and without a tag. Found: {listed}")


def add_owner(path, name, agent_nodes=()):
    """List `name` under owner_nodes in entry.toml. Returns what was done, in words."""
    if not ROUTE_NAME_RE.fullmatch(name):
        raise OwnerError("not a Tailscale machine name")
    if name in agent_nodes:
        raise OwnerError(f"{name} is an agent machine (routes.toml agent_nodes); an approver device must not run agents")
    owners = load_entry_config(path).owner_nodes
    if name in owners:
        return f"{name} is already an approver device"
    with open(path) as f:
        text = f.read()
    line = re.search(r'^(owner_nodes[ \t]*=[ \t]*)\[[^\]\n]*\]', text, re.M)
    if not line:
        raise OwnerError(f"owner_nodes in {path} is not on one line; add \"{name}\" to it by hand")
    # The example's names are placeholders: left in, any device that happened to be called
    # "laptop" or "phone" would be an approver device.
    kept = [] if owners == EXAMPLE else owners
    new = line.group(1) + "[" + ", ".join(f'"{n}"' for n in [*kept, name]) + "]"
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(path)), prefix=".entry-")
    try:
        os.fchmod(fd, os.stat(path).st_mode & 0o777)
        with os.fdopen(fd, "w") as f:
            f.write(text[:line.start()] + new + text[line.end():])
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return f"{name} is now an approver device" + ("" if kept else " (the example names were removed)")
