"""Bitwarden CLI wrapper and in-memory item cache. The only module that runs `bw`."""
import json
import os
import subprocess
import threading
import time


class BwError(Exception):
    """A bw call failed. Never carries bw output."""


def _label(args):
    n = 2 if args[0] in ("list", "get", "create", "edit", "delete") else 1
    return "bw " + " ".join(args[:n]) + " failed"


class Bw:
    def __init__(self, env=None, exe="bw", timeout=60):
        self._env = {k: os.environ[k] for k in ("PATH", "HOME") if k in os.environ}
        self._env.update(env or {})
        self._exe = exe
        self._timeout = timeout

    def _run(self, args, stdin=None):
        what = _label(args)
        try:
            r = subprocess.run([self._exe, *args], env=self._env, input=stdin, capture_output=True,
                               text=True, check=False, timeout=self._timeout)
        except (subprocess.TimeoutExpired, OSError):
            raise BwError(what) from None
        if r.returncode != 0:
            raise BwError(what)
        return r.stdout

    def _json(self, args, stdin=None):
        out = self._run(args, stdin)
        try:
            return json.loads(out)
        except ValueError:
            raise BwError(_label(args)) from None

    def list_items(self, org_id, collection_id=None):
        args = ["list", "items", "--organizationid", org_id]
        if collection_id:
            args += ["--collectionid", collection_id]
        return self._json(args)

    def _object(self, args, stdin=None):
        """JSON object with an "id" from bw, else BwError (a reply of the wrong shape)."""
        obj = self._json(args, stdin)
        if not isinstance(obj, dict) or not isinstance(obj.get("id"), str):
            raise BwError(_label(args))
        return obj

    def get_item(self, item_id):
        return self._object(["get", "item", item_id])

    def _encode(self, obj):
        return self._run(["encode"], json.dumps(obj)).strip()

    def create_item(self, name, org_id, collection_id, value, field, notes):
        item = {"type": 1, "name": name, "notes": None, "favorite": False, "reprompt": 0,
                "login": {"username": None, "password": None, "uris": []},
                "organizationId": org_id or None,
                "collectionIds": [collection_id] if collection_id else []}
        if field == "notes":
            item["notes"] = value
        else:
            item["login"]["password"] = value
            item["notes"] = notes
        # Encoded item (value inside) goes on stdin, never into argv (/proc/<pid>/cmdline).
        return self._object(["create", "item"], self._encode(item))["id"]

    def rename_item(self, item_id, new_name):
        item = self.get_item(item_id)
        item["name"] = new_name
        self._run(["edit", "item", item_id], self._encode(item))

    def delete_item(self, item_id):
        self._run(["delete", "item", item_id])

    def unlock(self, password_file):
        return self._run(["unlock", "--raw", "--passwordfile", password_file]).strip()

    def lock(self):
        self._run(["lock"])

    def sync(self):
        self._run(["sync"])

    def list_collections(self, org_id):
        return list_collections(self, org_id)


def list_collections(bw, org_id):
    cols = bw._json(["list", "collections", "--organizationid", org_id])
    return [{"id": c["id"], "name": c["name"]} for c in cols]


def collection_ids_under(collections, parent_name):
    return {c["name"]: c["id"] for c in collections
            if c["name"] == parent_name or c["name"].startswith(parent_name + "/")}


def split_ref(ref):
    if ref.endswith("/notes"):
        return ref[:-len("/notes")], "notes"
    return ref, "password"


def value_of(item, field):
    if field == "notes":
        v = item.get("notes")
    else:
        v = (item.get("login") or {}).get("password")
    return v or None


class ItemCache:
    """Item list of an org's collections, refreshed every `ttl` seconds. Thread-safe.

    Every refresh runs `bw sync` first: `bw list items` reads the CLI's local copy, which
    writes through other accounts (relay-entry, the web vault) never reach otherwise. A failed
    sync still lists the local copy and counts in `sync_failures`.
    """

    def __init__(self, bw, org_id, collection_ids, ttl=300, clock=time.monotonic):
        self._bw, self._org, self._cols = bw, org_id, list(collection_ids)
        self._ttl, self._clock = ttl, clock
        self._items = {}
        self._loaded = None
        self._lock = threading.RLock()
        self.sync_failures = 0

    def refresh(self):
        with self._lock:
            self._refresh()

    def _refresh(self):
        try:
            self._bw.sync()
        except BwError:
            self.sync_failures += 1
        merged = {}
        for cid in self._cols:
            for it in self._bw.list_items(self._org, cid):
                merged[it["id"]] = it
        self._items = {it["name"]: it for it in merged.values()}
        self._loaded = self._clock()

    def invalidate(self):
        with self._lock:
            self._loaded = None

    def _fresh(self):
        """Current item map, refreshed first if stale. Holding the lock, one thread refreshes."""
        with self._lock:
            if self._loaded is None or self._clock() - self._loaded >= self._ttl:
                self._refresh()
            return self._items

    def names(self):
        return list(self._fresh())

    def exists(self, name):
        return name in self._fresh()

    def get(self, ref):
        name, field = split_ref(ref)
        it = self._fresh().get(name)
        return value_of(it, field) if it else None
