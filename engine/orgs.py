#!/usr/bin/env python3
"""The shared organization list (shared/organizations.json), used by every
feed for short names, charters and logos.

    python3 engine/orgs.py add --name "Full Name" [--short "Short name"] [--kind credit_union]
                               [--charter N] [--fdic-cert N] [--website URL] [--state CT] [--alias NAME]...
                               [--slug S]
    python3 engine/orgs.py find "Any name"
    python3 engine/orgs.py recheck-logo --slug S      (after a rebrand or completed merger)

add creates the organization or fills in what's new, and prints its slug.
Without --slug it finds the organization by name and refuses to change its
charter, FDIC cert or state: a different value means a different organization
with the same name, which needs its own entry (--slug <new-slug>). With --slug
it creates or updates exactly that entry.
"""
import argparse
import sys

from apply import apply_org
from common import load_orgs, orgs_named, orgs_path, save_json, today_et


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
        found = orgs_named(a.name_arg or a.name, orgs)
        if not found:
            sys.exit("not in the list")
        if len(found) > 1:
            print(f"{len(found)} organizations share this name; records must say which (org_slugs):")
        for slug in found:
            print(slug, orgs["organizations"][slug])
        return
    if a.cmd == "recheck-logo":
        o = orgs["organizations"].get(a.slug)
        if not o:
            sys.exit(f"unknown slug {a.slug!r}")
        o.setdefault("logo", {})["check_requested"] = today_et().isoformat()
        save_json(orgs_path(), orgs)
        print(f"{a.slug}: logo check requested")
        return
    if not a.name and not a.slug:
        sys.exit("--name is required")
    try:
        slug = apply_org(orgs, {"name": a.name, "short_name": a.short, "kind": a.kind, "charter": a.charter,
                                "fdic_cert": a.fdic_cert, "website": a.website, "state": a.state,
                                "aliases": a.alias, "slug": a.slug}, today_et().isoformat())
    except ValueError as e:
        sys.exit(f"refused: {e}")
    save_json(orgs_path(), orgs)
    print(slug)
    o = orgs["organizations"][slug]
    for n in sorted({o["name"], o.get("short_name") or "", *o.get("aliases", [])} - {""}):
        others = [x for x in orgs_named(n, orgs) if x != slug]
        if others:
            print(f"note: {n!r} is also {', '.join(others)}; change files must name this one with "
                  f'"org_slugs" (e.g. {{"acquirer": "{slug}"}})')


if __name__ == "__main__":
    main()
