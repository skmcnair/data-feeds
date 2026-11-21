#!/usr/bin/env python3
"""Apply Claude's change files to a feed's master records.

    python3 engine/apply.py <feed>|--all            apply every waiting change file
    python3 engine/apply.py <feed> --dry-run FILE   check a change file, write nothing

A change file is feeds/<feed>/changes/<YYYY-MM-DD-HHMM>.json (format in
docs/REVIEW.md). Each entry is checked on its own: good entries are applied,
bad ones are set aside with the reason in changes/rejected/. The file then
moves to changes/applied/. Applying is safe to repeat: a file is applied once.
"""
import argparse
import shutil
import sys

from common import (
    SOURCE_TYPES, ascii_text, feed_dir, feed_names, field_def, load_feed, load_json,
    load_orgs, norm_org, now_et, org_index, orgs_path, resolve_org, save_json, slugify,
    today_et,
)
from fields import key_tuple, make_id, normalize_value


def load_records(name):
    return load_json(feed_dir(name) / "records.json", {"records": []})


def load_corrections(name):
    return load_json(feed_dir(name) / "corrections.json", {"corrections": [], "removed": []})


def corrected_fields(corr, rid):
    out = set()
    for c in corr.get("corrections", []):
        if c.get("id") == rid:
            out.update((c.get("set") or {}).keys())
    return out


def clean_sources(raw):
    out, errs = [], []
    for i, x in enumerate(raw or []):
        if not isinstance(x, dict):
            errs.append(f"source {i + 1} is not an object")
            continue
        url = ascii_text(x.get("url"))
        if not url.startswith(("http://", "https://")):
            errs.append(f"source {i + 1} has no web address")
            continue
        st = x.get("type")
        if st not in SOURCE_TYPES:
            errs.append(f"source {i + 1}: type must be one of {SOURCE_TYPES}")
            continue
        out.append({"label": ascii_text(x.get("label")) or url.split("/")[2], "url": url, "type": st})
    return out, errs


def merge_sources(rec, new, field_sources):
    """Add new sources to a record; return field_sources re-pointed at the
    record's own list."""
    have = rec.setdefault("sources", [])
    remap = {}
    for i, x in enumerate(new):
        for j, y in enumerate(have):
            if y["url"] == x["url"]:
                remap[i] = j
                break
        else:
            have.append(x)
            remap[i] = len(have) - 1
    out = {}
    for k, v in (field_sources or {}).items():
        if isinstance(v, int) and v in remap:
            out[k] = remap[v]
    return out


def apply_org(orgs, o, today):
    name = ascii_text(o.get("name"))
    if not name:
        raise ValueError("organization needs a name")
    idx = org_index(orgs)
    slug, existing = resolve_org(name, orgs, idx)
    if not existing and o.get("short_name"):
        slug, existing = resolve_org(o["short_name"], orgs, idx)
    if not existing:
        slug = slugify(o.get("short_name") or name)
        base, n = slug, 2
        while slug in orgs["organizations"]:
            slug, n = f"{base}-{n}", n + 1
        existing = orgs["organizations"].setdefault(slug, {"name": name, "added": today})
    for k in ("short_name", "kind", "charter", "website", "state", "fdic_cert"):
        if o.get(k):
            existing[k] = ascii_text(o[k])
    al = set(existing.get("aliases", []))
    for a in o.get("aliases", []) + ([name] if name != existing.get("name") else []):
        if a and norm_org(a) != norm_org(existing.get("name", "")):
            al.add(ascii_text(a))
    if al:
        existing["aliases"] = sorted(al)
    if o.get("recheck_logo"):
        existing.setdefault("logo", {})["check_requested"] = today
    return slug


def apply_change(s, change, records, corr, orgs, today):
    """Apply one change file in memory. Returns a report."""
    rep = {"added": [], "updated": [], "rechecked": [], "rejected": [], "reviewed": 0, "orgs": []}
    if not isinstance(change, dict):
        rep["rejected"].append({"entry": None, "reasons": ["the file is not a JSON object"]})
        return rep
    if change.get("feed") not in (None, s["name"]):
        rep["rejected"].append({"entry": None, "reasons": [f"file is for feed {change.get('feed')!r}"]})
        return rep

    for o in change.get("organizations", []) or []:
        try:
            rep["orgs"].append(apply_org(orgs, o, today))
        except ValueError as e:
            rep["rejected"].append({"entry": o, "reasons": [str(e)]})

    idx = org_index(orgs)
    recs = records["records"]
    by_id = {r["id"]: r for r in recs}
    by_key = {key_tuple(s, r["fields"], orgs, idx): r for r in recs}
    names = {f["name"] for f in s["fields"]}

    def clean_fields(raw, need_required):
        out, errs = {}, []
        if not isinstance(raw, dict):
            return {}, ["fields must be an object"]
        bad = set()
        for k, v in raw.items():
            if k not in names:
                errs.append(f"{k}: not a field of this feed")
                continue
            try:
                out[k] = normalize_value(field_def(s, k), v)
            except ValueError as e:
                errs.append(str(e))
                bad.add(k)
        if need_required:
            for f in s["fields"]:
                if f["name"] not in bad and f.get("required") and out.get(f["name"]) in (None, ""):
                    errs.append(f"{f['name']}: required")
        return out, errs

    def update_record(rec, fields, note, sources, field_sources, fill_only=False):
        locked = corrected_fields(corr, rec["id"])
        changes, kept = [], []
        for k, v in fields.items():
            if k in s["key"] and rec["fields"].get(k) not in (None, "") and v != rec["fields"].get(k):
                kept.append(f"{k} (part of the key)")
                continue
            if k in locked:
                kept.append(f"{k} (Sean's correction)")
                continue
            old = rec["fields"].get(k)
            if fill_only and old not in (None, ""):
                continue
            if v != old and not (v is None and old in (None, "")):
                rec["fields"][k] = v
                changes.append({"field": k, "old": old, "new": v})
        rec["last_checked"] = today
        if changes and sources:
            fs = merge_sources(rec, sources, field_sources)
            first = next(i for i, y in enumerate(rec["sources"]) if y["url"] == sources[0]["url"])
            for c in changes:
                rec.setdefault("field_sources", {})[c["field"]] = fs.get(c["field"], first)
        if changes:
            rec["updated"] = today
            rec.setdefault("history", []).append(
                {"date": today, "note": ascii_text(note) or "Updated",
                 "changes": changes, "sources": [x["url"] for x in sources]})
        return changes, kept

    for e in change.get("adds", []) or []:
        fields, errs = clean_fields((e or {}).get("fields"), True)
        sources, serrs = clean_sources((e or {}).get("sources"))
        errs += serrs
        if not sources:
            errs.append("every new item needs at least one source")
        if errs:
            rep["rejected"].append({"entry": e, "reasons": errs})
            continue
        k = key_tuple(s, fields, orgs, idx)
        if k in by_key:
            rec = by_key[k]
            changes, _ = update_record(rec, fields, e.get("note") or "Details added from a new report",
                                       sources, e.get("field_sources"), fill_only=True)
            rep["updated"].append({"id": rec["id"], "note": "already tracked; filled blanks",
                                   "changes": changes})
            continue
        rid = make_id(s, fields, by_id)
        rec = {"id": rid, "fields": {f["name"]: fields.get(f["name"]) for f in s["fields"]},
               "sources": [], "field_sources": {}, "history": [{"date": today, "note": "Added"}],
               "added": today, "updated": today, "last_checked": today}
        rec["field_sources"] = merge_sources(rec, sources, e.get("field_sources"))
        recs.append(rec)
        by_id[rid], by_key[k] = rec, rec
        rep["added"].append({"id": rid, "summary": " / ".join(str(fields.get(x) or "") for x in s["key"])})

    for e in change.get("updates", []) or []:
        e = e or {}
        rec = by_id.get(e.get("id"))
        if not rec and e.get("key"):
            kf, _ = clean_fields(e["key"], False)
            rec = by_key.get(key_tuple(s, kf, orgs, idx))
        if not rec:
            rep["rejected"].append({"entry": e, "reasons": ["no tracked item has that id or key"]})
            continue
        fields, errs = clean_fields(e.get("set"), False)
        sources, serrs = clean_sources(e.get("sources"))
        errs += serrs
        if fields and not sources:
            errs.append("an update needs a source for what changed")
        if fields and not ascii_text(e.get("note")):
            errs.append("an update needs a short note saying what changed")
        if errs:
            rep["rejected"].append({"entry": e, "reasons": errs})
            continue
        changes, kept = update_record(rec, fields, e.get("note"), sources, e.get("field_sources"))
        if changes:
            rep["updated"].append({"id": rec["id"], "note": ascii_text(e.get("note")),
                                   "changes": changes, "kept": kept})
        else:
            rep["rechecked"].append(rec["id"])
            if kept:
                rep["updated"].append({"id": rec["id"], "note": "no change", "changes": [], "kept": kept})

    for rid in change.get("rechecked", []) or []:
        if rid in by_id:
            by_id[rid]["last_checked"] = today
            rep["rechecked"].append(rid)
        else:
            rep["rejected"].append({"entry": rid, "reasons": ["rechecked id is not tracked"]})

    rep["reviewed"] = len(change.get("reviewed", []) or [])
    return rep


def run(name, dry_file=None):
    s = load_feed(name)
    d = feed_dir(name)
    today = today_et().isoformat()
    records, corr, orgs = load_records(name), load_corrections(name), load_orgs()
    files = [dry_file] if dry_file else sorted((d / "changes").glob("*.json"))
    reports = []
    for path in files:
        try:
            change = load_json(path)
        except Exception as e:  # unreadable JSON
            change = None
            rep = {"added": [], "updated": [], "rechecked": [], "reviewed": 0, "orgs": [],
                   "rejected": [{"entry": None, "reasons": [f"not valid JSON: {e}"]}]}
        else:
            rep = apply_change(s, change, records, corr, orgs, today)
        rep["file"] = str(getattr(path, "name", path))
        reports.append(rep)
        if dry_file:
            continue
        # inbox items Claude has looked at leave the waiting list
        inbox = load_json(d / "inbox.json", {"pending": []})
        done = set((change or {}).get("reviewed", []) or [])
        inbox["pending"] = [x for x in inbox["pending"] if x["id"] not in done]
        save_json(d / "inbox.json", inbox)
        if rep["rejected"]:
            save_json(d / "changes" / "rejected" / path.name, {"rejected": rep["rejected"]})
        (d / "changes" / "applied").mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), d / "changes" / "applied" / path.name)
        state = load_json(d / "state.json", {})
        run_info = (change or {}).get("run") or {}
        state["last_review"] = run_info.get("finished") or now_et().isoformat()
        state["last_review_file"] = path.name
        state["last_review_result"] = "partial" if rep["rejected"] or run_info.get("errors") else "ok"
        state["last_review_errors"] = list(run_info.get("errors") or [])
        state["last_review_report"] = {k: rep[k] for k in ("added", "updated", "rechecked", "reviewed")}
        state["last_review_report"]["rejected"] = [r["reasons"] for r in rep["rejected"]]
        save_json(d / "state.json", state)
    if not dry_file and files:
        records["records"].sort(key=lambda r: (r["fields"].get(s["date_field"]) or "", r["added"]), reverse=True)
        save_json(d / "records.json", records)
        save_json(orgs_path(), orgs)
    return reports


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("feed", nargs="?")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--dry-run", metavar="FILE")
    a = ap.parse_args()
    names = feed_names() if a.all else [a.feed]
    bad = False
    for n in names:
        reps = run(n, a.dry_run)
        for r in reps:
            print(f"{n} {r['file']}: {len(r['added'])} added, {len(r['updated'])} updated, "
                  f"{len(r['rechecked'])} rechecked, {r['reviewed']} inbox reviewed, "
                  f"{len(r['rejected'])} rejected")
            for x in r["rejected"]:
                bad = True
                print("  REJECTED:", "; ".join(x["reasons"]))
    if a.dry_run and bad:
        sys.exit(1)


if __name__ == "__main__":
    main()
