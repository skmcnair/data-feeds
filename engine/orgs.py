#!/usr/bin/env python3
"""The shared organization list (shared/organizations.json), used by every
feed for short names, charters and logos.

    python3 engine/orgs.py add --name "Full Name" [--short "Short name"] [--kind credit_union]
                               [--charter N] [--fdic-cert N] [--website URL] [--state CT] [--alias NAME]...
    python3 engine/orgs.py find "Any name"
    python3 engine/orgs.py recheck-logo --slug S      (after a rebrand or completed merger)

add creates the organization or fills in what's new, and prints its slug.
"""
import argparse
import sys

from apply import apply_org
from common import load_orgs, orgs_path, resolve_org, save_json, today_et


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["add", "find", "recheck-logo"])
    ap.add_argument("name_arg", nargs="?")
    ap.add_argument("--name")
    ap.add_argument("--short")
    ap.add_argument("--kind", choices=["credit_union", "bank", "league", "association", "regulator", "other"])
    ap.add_argument("--charter")
    ap.add_argument("--fdic-cert")
    ap.add_argument("--website")
    ap.add_argument("--state")
    ap.add_argument("--alias", action="append", default=[])
    ap.add_argument("--slug")
    a = ap.parse_args()
    orgs = load_orgs()
    if a.cmd == "find":
        slug, o = resolve_org(a.name_arg or a.name, orgs)
        if not o:
            sys.exit("not in the list")
        print(slug, o)
        return
    if a.cmd == "recheck-logo":
        o = orgs["organizations"].get(a.slug)
        if not o:
            sys.exit(f"unknown slug {a.slug!r}")
        o.setdefault("logo", {})["check_requested"] = today_et().isoformat()
        save_json(orgs_path(), orgs)
        print(f"{a.slug}: logo check requested")
        return
    if not a.name:
        sys.exit("--name is required")
    slug = apply_org(orgs, {"name": a.name, "short_name": a.short, "kind": a.kind, "charter": a.charter,
                            "fdic_cert": a.fdic_cert, "website": a.website, "state": a.state,
                            "aliases": a.alias}, today_et().isoformat())
    save_json(orgs_path(), orgs)
    print(slug)


if __name__ == "__main__":
    main()
