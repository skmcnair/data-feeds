#!/usr/bin/env python3
"""Build each feed's published files from its master records.

    python3 engine/build.py <feed>|--all

Writes, in feeds/<feed>/:
  feed.json      the published feed (wallboards and the tracker read this)
  feed.csv       the same rows, for Excel
  columns.json   column list for the tracker page
  worklist.json  what Claude's next review should look at
  status.json    health: last collection, last review, last publish, counts
and feeds/index.json, the list of feeds.

Your corrections (corrections.json) are laid over the records every time,
so they always win. If the new feed fails any check, the old feed.json is
kept and status.json says why.
"""
import argparse
import csv
import io
import sys

from checks import check_items
from common import (
    REPO, TITLE_AMBIGUOUS, ascii_text, feed_dir, feed_names, field_def, load_feed, load_json,
    load_orgs, now_et, org_index, resolve_org, save_json, today_et,
)
from fields import (
    accuracy, derived_value, history_text, is_open, normalize_value, output_field, summary,
)

PAGES = f"https://{REPO.split('/')[0]}.github.io/{REPO.split('/')[1]}"
RAW = f"https://raw.githubusercontent.com/{REPO}/main"


def overlay(s, rec, corr, errors):
    """Record fields with Sean's corrections laid on top."""
    fields = dict(rec["fields"])
    corrected = set()
    for c in corr.get("corrections", []):
        if c.get("id") != rec["id"]:
            continue
        for k, v in (c.get("set") or {}).items():
            f = field_def(s, k)
            if not f:
                errors.append(f"correction for {rec['id']}: {k} is not a field")
                continue
            try:
                fields[k] = normalize_value(f, v)
                corrected.add(k)
            except ValueError as e:
                errors.append(f"correction for {rec['id']}: {e}")
    return fields, corrected


def build_item(s, rec, fields, corrected, orgs, idx, today):
    out = {"id": rec["id"]}
    df = s["date_field"]
    d = fields.get(df)
    out["sort_date"] = d if d and len(str(d)) == 10 and str(d)[4] == "-" else ""
    for f in s["fields"]:
        out.update(output_field(f, fields.get(f["name"]), orgs, idx))
    for dv in s["derived"]:
        out[dv["name"]] = derived_value(dv, out)
    srcs = rec.get("sources", [])
    out["source_url"] = srcs[0]["url"] if srcs else ""
    out["sources"] = " | ".join(f"{x['label']}: {x['url']}" for x in srcs)
    out["updates"] = history_text(rec)
    out["added"] = rec.get("added", "")
    out["updated"] = rec.get("updated", "")
    out["last_checked"] = rec.get("last_checked", "")
    out["open"] = "yes" if is_open(s, rec, fields, today) else "no"
    if s["accuracy"]["enabled"]:
        out["accuracy"], out["accuracy_notes"] = accuracy(s, rec, fields, corrected)
    return {k: ascii_text(v) if isinstance(v, str) else str(v) for k, v in out.items()}


def columns(s):
    cols = []
    for f in s["fields"]:
        if f.get("tracker") is False:
            continue
        c = {"name": f["name"], "label": f.get("label", f["name"].replace("_", " ").title()),
             "kind": f["type"]}
        if f["type"] in ("date", "datetext"):
            c["sort"] = f"{f['name']}_iso"
        if f["type"] == "money":
            c["sort"] = f"{f['name']}_m"
        if f["type"] == "org":
            c.update(short=f"{f['name']}_short", logo=f"{f['name']}_logo_url",
                     style=f"{f['name']}_logo_style")
        if f["type"] == "choice":
            c["choices"] = f["choices"]
        cols.append(c)
    for dv in s["derived"]:
        if dv.get("tracker", True):
            cols.append({"name": dv["name"], "label": dv.get("label", dv["name"].replace("_", " ").title()),
                         "kind": "choice" if dv.get("groups") or dv.get("map") else "text"})
    if s["accuracy"]["enabled"]:
        cols.append({"name": "accuracy", "label": "Accuracy", "kind": "percent", "note": "accuracy_notes"})
    cols.append({"name": "updates", "label": "Updates", "kind": "text"})
    cols.append({"name": "source_url", "label": "Source", "kind": "url"})
    return cols


def build(name):
    s = load_feed(name)
    d = feed_dir(name)
    today = today_et()
    records = load_json(d / "records.json", {"records": []})
    corr = load_json(d / "corrections.json", {"corrections": [], "removed": []})
    orgs = load_orgs()
    idx = org_index(orgs)
    state = load_json(d / "state.json", {})
    inbox = load_json(d / "inbox.json", {"pending": []})
    removed = {x.get("id") for x in corr.get("removed", [])}
    errors, warnings, items, open_recs = [], [], [], []
    for rec in records["records"]:
        if rec["id"] in removed:
            continue
        fields, corrected = overlay(s, rec, corr, errors)
        it = build_item(s, rec, fields, corrected, orgs, idx, today)
        max_age = s["publish"].get("max_age_days")
        if max_age and it["open"] == "no" and it["sort_date"]:
            import datetime as _dt
            if (today - _dt.date.fromisoformat(it["sort_date"])).days > max_age:
                continue
        items.append(it)
        if it["open"] == "yes":
            open_recs.append((rec, fields))
        for f in s["fields"]:
            if f["type"] == "title" and fields.get(f["name"]) and TITLE_AMBIGUOUS.search(fields[f["name"]]):
                warnings.append(f"{rec['id']}: {f['name']} {fields[f['name']]!r} has an abbreviated "
                                "chief title; spell it out")
    items.sort(key=lambda x: (x["sort_date"], x["added"]), reverse=True)
    errors += check_items(s, items)

    published = d / "feed.json"
    if not errors:
        save_json(published, items)
        cols = list(items[0].keys()) if items else ["id"]
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        w.writerows(items)
        (d / "feed.csv").write_text(buf.getvalue(), encoding="ascii")
        save_json(d / "columns.json", {"title": s["title"], "item": s["item"], "columns": columns(s)})

    # what Claude's next review should look at
    needs_org, missing_logo, logo_reviews = set(), {}, []
    for rec, fields in open_recs + [(r, r["fields"]) for r in records["records"]]:
        for f in s["fields"]:
            if f["type"] == "org" and fields.get(f["name"]):
                slug, org = resolve_org(fields[f["name"]], orgs, idx)
                if not org:
                    needs_org.add(fields[f["name"]])
                elif not (org.get("logo") or {}).get("file"):
                    missing_logo[slug] = {"slug": slug, "name": org["name"], "website": org.get("website", "")}
    for slug, o in orgs["organizations"].items():
        lg = o.get("logo") or {}
        if lg.get("review"):
            logo_reviews.append({"slug": slug, "name": o["name"], **lg["review"]})
        if lg.get("check_requested"):
            logo_reviews.append({"slug": slug, "name": o["name"], "reason": "logo check requested",
                                 "requested": lg["check_requested"]})
    open_recs.sort(key=lambda rf: rf[0].get("last_checked") or "")
    rv = s["review"]
    lookback = rv["monday_lookback_days"] if today.weekday() == 0 else rv["lookback_days"]
    worklist = {
        "feed": name, "generated": now_et().isoformat(), "trial": bool(s.get("trial")),
        "lookback_days": lookback,
        "pending_inbox": inbox["pending"],
        "recheck": [{"id": r["id"], "summary": summary(s, f), "last_checked": r.get("last_checked", ""),
                     "fields": f} for r, f in open_recs[: rv["recheck_max"]]],
        "open_items": len(open_recs),
        "organizations_needed": sorted(needs_org),
        "logos_missing": sorted(missing_logo.values(), key=lambda x: x["slug"]),
        "logo_reviews": logo_reviews,
        "warnings": warnings,
        "last_review": state.get("last_review_report", {}),
        "tracked": [{"id": r["id"], "summary": summary(s, r["fields"])} for r in records["records"]],
    }
    save_json(d / "worklist.json", worklist)

    status = {
        "feed": name, "title": s["title"],
        "result": "failed" if errors else "ok",
        "last_build": now_et().isoformat(),
        "last_collect": state.get("last_collect", ""),
        "last_collect_result": state.get("last_collect_result", ""),
        "collect_errors": state.get("collect_errors", []),
        "last_review": state.get("last_review", ""),
        "last_review_result": state.get("last_review_result", ""),
        "review_errors": state.get("last_review_errors", []),
        "items": len(items) if not errors else len(load_json(published, []) or []),
        "open_items": len(open_recs),
        "pending_inbox": len(inbox["pending"]),
        "organizations_needed": len(needs_org),
        "logos_missing": len(missing_logo),
        "warnings": warnings[:20],
        "errors": errors[:50],
        "trial": bool(s.get("trial")),
    }
    save_json(d / "status.json", status)
    return status


def write_index():
    feeds = []
    for n in feed_names():
        st = load_json(feed_dir(n) / "status.json", {}) or {}
        feeds.append({"name": n, "title": st.get("title", n), "items": st.get("items", 0),
                      "result": st.get("result", ""), "last_review": st.get("last_review", ""),
                      "feed_url": f"{RAW}/feeds/{n}/feed.json",
                      "csv_url": f"{RAW}/feeds/{n}/feed.csv",
                      "tracker_url": f"{PAGES}/tracker/?feed={n}"})
    from common import ROOT
    save_json(ROOT / "feeds" / "index.json", {"feeds": feeds})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("feed", nargs="?")
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()
    names = feed_names() if a.all else [a.feed]
    failed = False
    for n in names:
        st = build(n)
        print(f"{n}: {st['result']} - {st['items']} items, {st['open_items']} open, "
              f"{st['pending_inbox']} waiting for review")
        for e in st["errors"]:
            print("  ERROR:", e)
        for w in st["warnings"]:
            print("  warning:", w)
        failed = failed or st["result"] == "failed"
    write_index()
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
