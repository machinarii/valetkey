"""Broker-side TOTP pairing and immutable, expiring route proposals: a new bearer route, or a machine
joining an existing route (nothing else about that route changes)."""
import base64
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import tempfile
import time
import urllib.parse

from .routes import (BINDING, join_binding, load_entry_config, load_routes, proposed_api, ROUTE_NAME_RE,
                     RoutesError)


class Refused(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def totp(seed, counter):
    mac = hmac.digest(seed, counter.to_bytes(8, "big"), "sha1")
    offset = mac[-1] & 15
    number = int.from_bytes(mac[offset:offset + 4], "big") & 0x7fffffff
    return f"{number % 1_000_000:06d}"


class Approvals:
    """Single serialized worker. SQLite holds identifiers and policy metadata, never TOTP seeds."""
    def __init__(self, database, key, routes, owners, install, clock=time.time):
        self.db = sqlite3.connect(database, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS owner (
            name TEXT PRIMARY KEY, active TEXT, ticket TEXT, expires INTEGER,
            last_step INTEGER DEFAULT -1, failures INTEGER DEFAULT 0, until_time INTEGER DEFAULT 0
          );
          CREATE TABLE IF NOT EXISTS proposal (
            id TEXT PRIMARY KEY, node TEXT, spec TEXT, expires INTEGER, state TEXT, approver TEXT
          );
        """)
        if len(key) != 32:
            raise ValueError("invalid approval master key")
        self.key, self.routes, self.owners = key, Path(routes), Path(owners)
        self.install, self.clock = install, clock
        self.grants = self.routes.parent / "approved-routes.d"

    def policy(self):
        routes = load_routes(self.routes)
        owners = load_entry_config(self.owners)
        routes.validate_owners(owners.owner_nodes)
        return routes, owners

    def open_pairing(self, who):
        _, conf = self.policy()
        if who not in conf.owner_nodes or not ROUTE_NAME_RE.fullmatch(who):
            raise Refused(403, "select a configured approver device")
        ticket = secrets.token_hex(20)
        self.db.execute("""INSERT INTO owner(name,ticket,expires) VALUES(?,?,?)
            ON CONFLICT(name) DO UPDATE SET ticket=excluded.ticket, expires=excluded.expires""",
                        (who, ticket, int(self.clock()) + 600))
        return ticket

    def pairing_expires(self, who, ticket):
        row = self.db.execute("SELECT expires FROM owner WHERE name=? AND ticket=?", (who, ticket)).fetchone()
        return row["expires"] if row else 0

    def seed(self, who, ticket):
        return hmac.digest(self.key, f"valetkey-totp-v1/{who}/{ticket}".encode(), "sha256")[:20]

    def authenticate(self, who, seed, code):
        now = int(self.clock())
        owner = self.db.execute("SELECT * FROM owner WHERE name=?", (who,)).fetchone()
        if owner["failures"] >= 5 and now < owner["until_time"]:
            raise Refused(429, "too many attempts; try again after five minutes")
        step = now // 30
        for counter in (step, step - 1, step + 1):
            if (counter >= 0 and counter > owner["last_step"] and isinstance(code, str)
                    and re.fullmatch("[0-9]{6}", code) and hmac.compare_digest(totp(seed, counter), code)):
                self.db.execute("UPDATE owner SET last_step=?, failures=0, until_time=0 WHERE name=?",
                                (counter, who))
                return
        failures = owner["failures"] if now < owner["until_time"] else 0
        self.db.execute("UPDATE owner SET failures=?, until_time=? WHERE name=?",
                        (failures + 1, now + 300, who))
        raise Refused(403, "invalid or already used code; enter a fresh code")

    def pair(self, who, rid, code=None):
        _, conf = self.policy()
        row = self.db.execute("SELECT * FROM owner WHERE name=? AND ticket=?", (who, rid)).fetchone()
        if who not in conf.owner_nodes or not row or row["expires"] <= self.clock():
            raise Refused(404, "pairing link expired or unavailable")
        seed = self.seed(who, rid)
        if code is None:
            label = urllib.parse.quote(f"Valetkey:{who}@{conf.tailnet_name}", safe="")
            secret = base64.b32encode(seed).decode().rstrip("=")
            return f"otpauth://totp/{label}?secret={secret}&issuer=Valetkey&algorithm=SHA1&digits=6&period=30"
        self.authenticate(who, seed, code)
        self.db.execute("UPDATE owner SET active=ticket, ticket=NULL WHERE name=?", (who,))
        return "Authenticator paired"

    def propose(self, who, spec):
        routes, _ = self.policy()
        if who not in routes.agent_nodes:
            raise Refused(403, "only configured agent machines can propose routes")
        if isinstance(spec, dict) and set(spec) == {"join"}:
            spec = self.join_spec(routes, who, spec["join"])
        else:
            try:
                route = proposed_api(spec, who)
            except RoutesError as exc:
                raise Refused(400, str(exc)) from None
            if route.name in routes.api or (self.grants / (route.name + ".json")).exists():
                raise Refused(409, "route name already exists")
            spec = dict(spec, methods=route.methods, paths=route.paths)
        now = int(self.clock())
        self.db.execute("DELETE FROM proposal WHERE expires<=? AND state NOT IN ('installing','recovery')",
                        (now,))
        encoded = json.dumps(spec, sort_keys=True)
        row = self.db.execute("SELECT id FROM proposal WHERE node=? AND spec=? AND state='pending'",
                              (who, encoded)).fetchone()
        if row:
            return row["id"]
        if self.db.execute("SELECT count(*) FROM proposal WHERE node=?", (who,)).fetchone()[0] >= 20:
            raise Refused(429, "too many recent proposals")
        rid = secrets.token_hex(20)
        self.db.execute("INSERT INTO proposal VALUES(?,?,?,?,?,NULL)", (rid, who, encoded, now + 900, "pending"))
        return rid

    def join_spec(self, routes, who, name):
        """What the owner approves when a machine joins an existing route: the route as it is now."""
        route = routes.api.get(name) if isinstance(name, str) and ROUTE_NAME_RE.fullmatch(name) else None
        if not route or route.model:
            raise Refused(404, "no API route with that name")
        if who in route.nodes or (self.grants / f"{name}+{who}.json").exists():
            raise Refused(409, "this machine already uses that route")
        return dict(join_binding(route), join=name, methods=list(route.methods), paths=list(route.paths),
                    nodes=list(route.nodes))

    def review(self, who, rid):
        _, conf = self.policy()
        if who not in conf.owner_nodes:
            raise Refused(403, "approver device required")
        row = self.db.execute("SELECT * FROM proposal WHERE id=?", (rid,)).fetchone()
        if not row or row["expires"] <= self.clock():
            raise Refused(404, "approval link expired or unavailable")
        return dict(node=row["node"], spec=json.loads(row["spec"]), state=row["state"], expires=row["expires"])

    def grant_file(self, rid, node, spec):
        if "join" in spec:
            obj = {"request": rid, "node": node, "join": spec["join"], "bind": {k: spec[k] for k in BINDING}}
            return self.grants / f"{spec['join']}+{node}.json", json.dumps(obj, sort_keys=True).encode()
        obj = {"request": rid, "node": node, "spec": spec}
        return self.grants / (spec["name"] + ".json"), json.dumps(obj, sort_keys=True).encode()

    def sync_grants(self):
        fd = os.open(self.grants, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def restore(self, rid, node, spec):
        path, data = self.grant_file(rid, node, spec)
        if path.exists():
            if path.read_bytes() != data:
                raise Refused(409, "grant changed; broker administrator recovery required")
            path.unlink()
        self.sync_grants()
        if not self.install(spec["org"], None):
            raise Refused(503, "restoring prior configuration requires broker administrator attention")
        self.db.execute("UPDATE proposal SET state='failed' WHERE id=?", (rid,))

    def recover(self):
        for row in self.db.execute("SELECT * FROM proposal WHERE state IN ('installing','recovery')").fetchall():
            self.restore(row["id"], row["node"], json.loads(row["spec"]))

    def approve(self, who, rid, code):
        proposal = self.review(who, rid)
        if proposal["state"] == "active":
            return "Route already active"
        if proposal["state"] != "pending":
            raise Refused(409, "proposal can no longer be approved")
        spec, node = proposal["spec"], proposal["node"]
        routes, _ = self.policy()
        path, data = self.grant_file(rid, node, spec)
        if "join" in spec:
            route = routes.api.get(spec["join"])
            if (node not in routes.agent_nodes or not route or route.model or node in route.nodes
                    or join_binding(route) != {k: spec[k] for k in BINDING} or path.exists()):
                raise Refused(409, "agent or route policy changed; proposal cannot be applied")
        else:
            route = proposed_api(spec, node)
            if node not in routes.agent_nodes or route.name in routes.api or path.exists():
                raise Refused(409, "agent or route policy changed; proposal cannot be applied")
        owner = self.db.execute("SELECT active FROM owner WHERE name=?", (who,)).fetchone()
        if not owner or not owner["active"]:
            raise Refused(403, "pair this approver device from the broker console first")
        if self.db.execute("SELECT 1 FROM proposal WHERE state IN ('installing','recovery')").fetchone():
            raise Refused(503, "previous activation requires recovery")
        self.authenticate(who, self.seed(who, owner["active"]), code)
        self.db.execute("UPDATE proposal SET state='installing', approver=? WHERE id=?", (who, rid))
        try:
            with tempfile.NamedTemporaryFile(dir=self.grants, delete=False) as f:
                tmp = Path(f.name)
                try:
                    f.write(data)
                    f.flush()
                    os.fchmod(f.fileno(), 0o644)
                    os.fsync(f.fileno())
                    os.link(tmp, path)
                finally:
                    tmp.unlink(missing_ok=True)
            self.sync_grants()
            if not self.install(spec["org"], route):
                raise Refused(503, "route installation failed; previous configuration restored")
        except Exception:
            self.db.execute("UPDATE proposal SET state='recovery' WHERE id=?", (rid,))
            self.restore(rid, node, spec)
            raise
        self.db.execute("UPDATE proposal SET state='active' WHERE id=?", (rid,))
        return "Route active"
