#!/usr/bin/python3 -I
"""Vault lookups for valetkey-broker login and valetkey-broker check. Names and counts only, never ids or values.

Runs as valetkey-broker-<name> with BW_SESSION and BITWARDENCLI_APPDATA_DIR in its environment:

  vault-info.py org-json <local|infra|entry>     org.json on stdout, "ok ..." lines on stderr
  vault-info.py check <local|infra|entry> --routes R --org-ids F [--lock]

org-json: the org's broker collection and every collection nested under it (`<parent>/<child>`).
Names come from LOCAL_ORG, INFRA_ORG, LOCAL_COLLECTION, INFRA_COLLECTION (defaults below).
check: vault status; for read services ok/FAIL per item in each collection and per routes entry
of the org; the collections the account sees, for the owner to compare with the web vault.
"""
import argparse
import json
import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
for _d in (_here, os.path.dirname(_here)):
    if os.path.isdir(os.path.join(_d, "brokerlib")):
        sys.path.insert(0, _d)
        break
from brokerlib import routes as routes_mod  # noqa: E402
from brokerlib.bwclient import (Bw, BwError, collection_ids_under, list_collections,  # noqa: E402
                               split_ref, value_of)

DEFAULTS = {"LOCAL_ORG": "local-org", "INFRA_ORG": "infra-org",
            "LOCAL_COLLECTION": "local-broker-collection",
            "INFRA_COLLECTION": "infra-broker-collection"}


class Fail(Exception):
    pass


def names_for(org):
    o = org.upper()
    return (os.environ.get(f"{o}_ORG") or DEFAULTS[f"{o}_ORG"],
            os.environ.get(f"{o}_COLLECTION") or DEFAULTS[f"{o}_COLLECTION"])


def bw_from_env():
    # A short-lived helper run by root's tools as the broker user: bw gets our whole environment
    # (BW_SESSION, BITWARDENCLI_APPDATA_DIR), as valetkey-broker-check's bw did.
    return Bw(env=dict(os.environ))


def org_block(bw, org):
    org_name, coll = names_for(org)
    found = [o for o in bw._json(["list", "organizations"]) if o.get("name") == org_name]
    if len(found) != 1:
        raise Fail(f"organization {org_name} not visible (accept and confirm the invite)"
                   if not found else f"{len(found)} organizations named {org_name}")
    org_id = found[0]["id"]
    cols = collection_ids_under(list_collections(bw, org_id), coll)
    if coll not in cols:
        raise Fail(f"collection {coll} not visible in {org_name} (grant the account access)")
    return {"org_id": org_id, "collection": coll, "collections": dict(sorted(cols.items()))}


def cmd_org_json(bw, name):
    orgs = ("local", "infra") if name == "entry" else (name,)
    blocks = {org: org_block(bw, org) for org in orgs}
    for org, b in blocks.items():
        label = f"{name}/{org}" if name == "entry" else name
        print(f"ok    {label}: {len(b['collections'])} collections", file=sys.stderr)
    out = blocks if name == "entry" else blocks[name]
    json.dump(out, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


class Report:
    def __init__(self):
        self.failed = False

    def ok(self, text):
        print(f"ok    {text}")

    def fail(self, text):
        self.failed = True
        print(f"FAIL  {text}")


def project_of(coll_name, parent):
    """Nested collection `<parent>/<project>` -> project name; the parent itself -> None."""
    return coll_name[len(parent) + 1:] if coll_name.startswith(parent + "/") else None


def check_items(bw, rep, org, block):
    """ok/FAIL per item in each collection of org.json. Returns {name: item}."""
    _, parent = names_for(org)
    seen = {}
    for coll, cid in sorted(block["collections"].items()):
        items = sorted(bw.list_items(block["org_id"], cid), key=lambda i: i.get("name", ""))
        print(f"collection {coll}: {len(items)} items")
        project = project_of(coll, parent)
        for it in items:
            name = it.get("name", "")
            if name in seen and seen[name].get("id") != it.get("id"):
                rep.fail(f"{name}: duplicate name in the org")
                continue
            seen[name] = it
            if routes_mod.BACKUP_RE.search(name):
                rep.ok(f"{name} (backup)")
                continue
            err = routes_mod.validate_item_name(name, project)
            if err:
                rep.fail(f"{name}: {err}")
            elif not (value_of(it, "password") or value_of(it, "notes")):
                rep.fail(f"{name}: no password or notes value")
            else:
                rep.ok(name)
    return seen


def _also_matching(name, items):
    """Other item names that contain `name`. fnox finds an [api.*] item with `bw get item <name>`,
    which matches every item whose name contains it (a `--replace` backup does) and fails on more
    than one match; fnox then refuses to start and the whole read service is down."""
    return sorted(n for n in items if n != name and name.lower() in n.lower())


def check_routes(rep, routes, items):
    for kind, table in (("api", routes.api), ("value", routes.values)):
        for rname, r in sorted(table.items()):
            name, field = split_ref(r.item)
            it = items.get(name)
            if it is None:
                rep.fail(f"[{kind}.{rname}] item {name} missing")
            elif not value_of(it, field):
                rep.fail(f"[{kind}.{rname}] item {name} has no {field} value")
            elif kind == "api" and _also_matching(name, items):
                rep.fail(f"[{kind}.{rname}] item {name} is ambiguous for fnox: delete or rename "
                         + ", ".join(_also_matching(name, items)))
            else:
                rep.ok(f"[{kind}.{rname}] {r.item}")
    for pname, p in sorted(routes.projects.items()):
        n = sum(1 for name in items if routes_mod.match_item(name, p.items))
        (rep.ok if n else rep.fail)(f"[project.{pname}] {n} items match {', '.join(p.items)}")


def print_visible(bw, rep, label, block, org):
    _, parent = names_for(org)
    visible = sorted(c["name"] for c in list_collections(bw, block["org_id"]))
    print(f"collections {label} sees: {', '.join(visible) or '(none)'}")
    for c in sorted(block["collections"]):
        if c not in visible:
            rep.fail(f"collection {c} in org.json is no longer visible")
    for c in visible:
        if (c == parent or c.startswith(parent + "/")) and c not in block["collections"]:
            rep.fail(f"collection {c} is new: re-run valetkey-broker login to add it to org.json")


def check_api_items(bw, name, routes_path, org_ids_path):
    """Positive readiness check for a web grant; fail closed without printing vault records."""
    with open(org_ids_path) as f:
        block = json.load(f)
    bw.sync()
    items = bw.list_items(block["org_id"])
    collections = set(block["collections"].values())
    for route in routes_mod.load_routes(routes_path).for_org(name).api.values():
        ref, field = split_ref(route.item)
        matches = [it for it in items if ref.lower() in it.get("name", "").lower()]
        if (len(matches) != 1 or matches[0]["name"] != ref
                or not collections.intersection(matches[0].get("collectionIds") or [])
                or not value_of(matches[0], field)):
            return 1
    return 0


def cmd_check(bw, name, routes_path, org_ids_path):
    rep = Report()
    status = json.loads(bw._run(["status"])).get("status", "unknown")
    print(f"valetkey-broker-{name}: {status}")
    if status != "unlocked":
        return 1
    try:
        bw.sync()  # list what the server has, not the CLI's local copy
    except BwError:
        rep.fail("bw sync failed: the lists below are the local copy and may be stale")
    with open(org_ids_path) as f:
        ids = json.load(f)
    if name == "entry":
        for org in ("local", "infra"):
            print_visible(bw, rep, f"valetkey-broker-entry ({org})", ids[org], org)
    else:
        items = check_items(bw, rep, name, ids)
        check_routes(rep, routes_mod.load_routes(routes_path).for_org(name), items)
        print_visible(bw, rep, f"valetkey-broker-{name}", ids, name)
    return 1 if rep.failed else 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="vault-info.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    oj = sub.add_parser("org-json")
    oj.add_argument("name", choices=("local", "infra", "entry"))
    ck = sub.add_parser("check")
    ck.add_argument("name", choices=("local", "infra", "entry"))
    ck.add_argument("--routes", required=True)
    ck.add_argument("--org-ids", required=True)
    ck.add_argument("--api-only", action="store_true")
    ck.add_argument("--lock", action="store_true", help="bw lock when done")
    args = ap.parse_args(argv)
    bw = bw_from_env()
    try:
        if args.cmd == "org-json":
            cmd_org_json(bw, args.name)
            return 0
        if args.api_only:
            return check_api_items(bw, args.name, args.routes, args.org_ids)
        return cmd_check(bw, args.name, args.routes, args.org_ids)
    except (Fail, BwError, routes_mod.RoutesError, OSError, ValueError, KeyError) as e:
        print(f"FAIL  {e}", file=sys.stderr)
        return 1
    finally:
        if getattr(args, "lock", False):
            try:
                bw.lock()
            except BwError:
                pass


if __name__ == "__main__":
    sys.exit(main())
