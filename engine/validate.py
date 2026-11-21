#!/usr/bin/env python3
"""Check published feeds, and with --health, that they are fresh.

    python3 engine/validate.py <feed>|--all [--health]

Exits 1 and lists every problem if anything is wrong. The publish workflow
checks each feed before it goes out; the daily health workflow runs this
with --health and emails Sean through a GitHub issue if it fails.
"""
import argparse
import sys

from checks import check_health, check_items
from common import SettingsError, feed_dir, feed_names, load_feed, load_json


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("feed", nargs="?")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--health", action="store_true")
    a = ap.parse_args()
    names = feed_names() if a.all else [a.feed]
    problems = 0
    for n in names:
        try:
            s = load_feed(n)
        except SettingsError as e:
            print(f"{n}: FAILED - feed.yml: {e}")
            problems += 1
            continue
        items = load_json(feed_dir(n) / "feed.json", None)
        status = load_json(feed_dir(n) / "status.json", None)
        errs = [] if items is None and s.get("paused") else check_items(s, items if items is not None else [])
        if items is None and not s.get("paused"):
            errs.insert(0, "feed.json is missing")
        if status and items is not None and status.get("items") != len(items):
            errs.append(f"status.json says {status.get('items')} items but feed.json has {len(items)}")
        if a.health:
            errs += check_health(s, status)
        if errs:
            problems += len(errs)
            print(f"{n}: FAILED - {len(errs)} problem(s)")
            for e in errs:
                print("  -", e)
        else:
            print(f"{n}: OK - {len(items or [])} items")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
