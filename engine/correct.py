#!/usr/bin/env python3
"""Record Sean's corrections. A correction always wins over Claude's
research and is never overwritten by a daily review.

    python3 engine/correct.py <feed> --id ID --set field=value [--set ...] --note "why"
    python3 engine/correct.py <feed> --id ID --remove --note "why"
    python3 engine/correct.py <feed> --id ID --undo field     (drop a correction)

Then run build.py to check it, commit corrections.json and push.
"""
import argparse
import sys

from common import feed_dir, field_def, load_feed, load_json, save_json, today_et
from fields import normalize_value


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("feed")
    ap.add_argument("--id", required=True)
    ap.add_argument("--set", action="append", default=[], metavar="FIELD=VALUE")
    ap.add_argument("--remove", action="store_true", help="hide the item from the feed")
    ap.add_argument("--restore", action="store_true", help="show a removed item again")
    ap.add_argument("--undo", metavar="FIELD")
    ap.add_argument("--note", default="")
    a = ap.parse_args()
    s = load_feed(a.feed)
    d = feed_dir(a.feed)
    recs = load_json(d / "records.json", {"records": []})["records"]
    if a.id not in {r["id"] for r in recs}:
        sys.exit(f"no item with id {a.id!r}")
    corr = load_json(d / "corrections.json", {"corrections": [], "removed": []})
    today = today_et().isoformat()
    if a.remove:
        corr["removed"] = [x for x in corr["removed"] if x["id"] != a.id]
        corr["removed"].append({"id": a.id, "reason": a.note, "date": today})
    if a.restore:
        corr["removed"] = [x for x in corr["removed"] if x["id"] != a.id]
    if a.undo:
        for c in corr["corrections"]:
            if c["id"] == a.id:
                (c.get("set") or {}).pop(a.undo, None)
        corr["corrections"] = [c for c in corr["corrections"] if c.get("set")]
    if a.set:
        vals = {}
        for kv in a.set:
            k, _, v = kv.partition("=")
            f = field_def(s, k)
            if not f:
                sys.exit(f"{k} is not a field of {a.feed}")
            try:
                normalize_value(f, v)
            except ValueError as e:
                sys.exit(str(e))
            vals[k] = v
        corr["corrections"].append({"id": a.id, "set": vals, "note": a.note, "date": today})
    save_json(d / "corrections.json", corr)
    print(f"saved; {len(corr['corrections'])} corrections, {len(corr['removed'])} removed items in {a.feed}")


if __name__ == "__main__":
    main()
