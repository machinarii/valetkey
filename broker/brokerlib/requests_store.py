"""Entry requests and suspect marks for valetkey-broker-entry, kept in one JSON file.

The file holds names, orgs, purposes, statuses and result texts only — never a value. Every
change is written atomically (temp file in the same directory, mode 600, then os.replace).
"""
import base64
import dataclasses
import json
import os
import secrets
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

EXPIRE_AFTER = 86400


@dataclass
class EntryRequest:
    id: str
    name: str
    org: str
    project: str | None
    field: str
    purpose: str
    replace: bool
    status: str
    created: float
    flags: dict = field(default_factory=dict)
    result: str = ""
    requester: str = ""  # machine that asked; empty in older state files
    access: dict = field(default_factory=dict)  # what the stored key opens; see access_check
    encode: str = ""  # "basic": the owner pastes the key, the vault gets base64("<key>:") for Basic auth


def new_id():
    return "r_" + base64.b32encode(secrets.token_bytes(10)).decode().lower()[:10]


class RequestsStore:
    def __init__(self, path: Path, clock=time.time):
        self._path = Path(path)
        self._clock = clock
        self._lock = threading.Lock()
        self._requests = {}
        self._suspects = {}  # "org/name" -> since
        self._aside = {}  # kind -> {"org/name", ...}: what the owner chose to ignore on the page
        if self._path.exists():
            data = json.loads(self._path.read_text())
            self._requests = {k: EntryRequest(**v) for k, v in data.get("requests", {}).items()}
            self._suspects = dict(data.get("suspects", {}))
            self._aside = {k: set(v) for k, v in data.get("aside", {}).items()}

    def _save(self):
        data = {"requests": {k: dataclasses.asdict(v) for k, v in self._requests.items()},
                "suspects": self._suspects, "aside": {k: sorted(v) for k, v in self._aside.items()}}
        fd, tmp = tempfile.mkstemp(dir=self._path.parent, prefix=".requests-")
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=1)
            os.replace(tmp, self._path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def _expire(self):
        """Mark pending requests older than 24 h expired. Caller holds the lock."""
        now = self._clock()
        changed = False
        for r in self._requests.values():
            if r.status == "pending" and now - r.created > EXPIRE_AFTER:
                r.status = "expired"
                changed = True
        if changed:
            self._save()

    def create(self, name, org, project, field, purpose, replace, flags=None, requester="",
               limit=None, encode="") -> EntryRequest | None:
        """New pending request; None when `requester` already has `limit` pending ones."""
        with self._lock:
            self._expire()
            if limit is not None and sum(r.status == "pending" and r.requester == requester
                                         for r in self._requests.values()) >= limit:
                return None
            rid = new_id()
            while rid in self._requests:
                rid = new_id()
            r = EntryRequest(rid, name, org, project, field, purpose, bool(replace), "pending",
                             self._clock(), dict(flags or {}), "", requester, encode=encode)
            self._requests[rid] = r
            self._save()
            return dataclasses.replace(r, flags=dict(r.flags))

    def get(self, rid) -> EntryRequest | None:
        with self._lock:
            self._expire()
            r = self._requests.get(rid)
            return dataclasses.replace(r, flags=dict(r.flags)) if r else None

    def pending(self) -> list:
        with self._lock:
            self._expire()
            rs = [r for r in self._requests.values() if r.status == "pending"]
            return [dataclasses.replace(r, flags=dict(r.flags)) for r in sorted(rs, key=lambda r: r.created)]

    def set_status(self, rid, status, result=""):
        with self._lock:
            r = self._requests[rid]
            r.status, r.result = status, result
            self._save()

    def set_access(self, rid, access):
        with self._lock:
            self._requests[rid].access = access
            self._save()

    def mark_suspect(self, name, org, on: bool):
        key = f"{org}/{name}"
        with self._lock:
            if on and key not in self._suspects:
                self._suspects[key] = self._clock()
            elif not on and key in self._suspects:
                del self._suspects[key]
            else:
                return
            self._save()

    def put_aside(self, kind, key):
        with self._lock:
            self._aside.setdefault(kind, set()).add(key)
            self._save()

    def aside(self, kind) -> set:
        with self._lock:
            return set(self._aside.get(kind, ()))

    def suspects_since(self) -> list:
        """[(name, org, since)] oldest first."""
        with self._lock:
            out = []
            for key, since in self._suspects.items():
                org, name = key.split("/", 1)
                out.append((name, org, since))
            return sorted(out, key=lambda t: t[2])

    def suspects(self) -> list:
        return [(name, org) for name, org, _ in self.suspects_since()]
