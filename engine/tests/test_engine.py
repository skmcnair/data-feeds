#!/usr/bin/env python3
"""End-to-end test of the engine on a throwaway sample feed.

    python3 engine/tests/test_engine.py

Builds a temporary repo, then runs collect, apply (adds, updates, rejects,
duplicates), corrections, build, validate, health and the logo commands
exactly as the workflows and Claude's review do. Exits 1 on any failure.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ENGINE = Path(__file__).resolve().parent.parent
FAILS = []

FEED_YML = """
title: Sample Mergers
item: deal
date_field: announced
key: [acquirer, partner]
fields:
  - {name: announced, type: date, required: true, label: Announced}
  - {name: acquirer, type: org, required: true, label: Acquirer}
  - {name: partner, type: org, required: true, label: Partner}
  - {name: state, type: state, label: State}
  - {name: status, type: choice, choices: [Announced, Approved, Completed, Called off], required: true}
  - {name: combined_assets, type: money, scored: true, label: Combined assets}
  - {name: members, type: int, label: Members}
  - {name: ceo_title, type: title, label: CEO title}
  - name: expected_close
    type: text
    pattern: '(Q[1-4] \\d{4}|H[12] \\d{4}|\\d{4}|Not Disclosed|Completed|Called off)'
    pattern_hint: "must be a quarter (Q2 2027), half (H1 2027), year, Not Disclosed, Completed or Called off"
    scored: true
  - {name: effective, type: datetext}
  - {name: notes, type: text}
derived:
  - name: region
    map_from: state
    groups: {New England: [CT, ME, MA, NH, RI, VT]}
    default: Elsewhere
  - name: headline
    cases:
      - when: {status: Completed}
        template: "{acquirer_short} completes merger with {partner_short}"
      - template: "{acquirer_short} and {partner_short} to merge"
open_when: {field: status, not_in: [Completed, Called off]}
sources:
  - {name: Test RSS, type: rss, url: "file://FIXTURE"}
collect: {include_any: [merger, merge]}
review: {time: "06:37"}
"""

RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
<item><title>Alpha CU and Beta FCU to merge</title><link>https://example.com/a?utm_source=x</link>
<pubDate>{now}</pubDate><description>&lt;p&gt;A merger &#8212; big news&lt;/p&gt;</description></item>
<item><title>Unrelated bake sale</title><link>https://example.com/b</link><pubDate>{now}</pubDate></item>
</channel></rss>"""


def run(args, root, ok=True):
    env = dict(os.environ, FEEDS_ROOT=str(root), PYTHONDONTWRITEBYTECODE="1")
    r = subprocess.run([sys.executable, *args], cwd=root, env=env, capture_output=True, text=True)
    if ok is not None and (r.returncode == 0) != ok:
        FAILS.append(f"{' '.join(args)} exited {r.returncode}:\n{r.stdout}{r.stderr}")
    return r


def expect(cond, msg):
    if not cond:
        FAILS.append(msg)


def main():
    import email.utils
    root = Path(tempfile.mkdtemp())
    shutil.copytree(ENGINE, root / "engine", ignore=shutil.ignore_patterns("__pycache__", "tests"))
    fd = root / "feeds" / "sample"
    fd.mkdir(parents=True)
    fixture = root / "fixture.xml"
    fixture.write_text(RSS.format(now=email.utils.formatdate()))
    (fd / "feed.yml").write_text(FEED_YML.replace("FIXTURE", str(fixture)))
    (root / "shared" / "logos").mkdir(parents=True)

    # collect: the merger story is queued once; the bake sale is filtered out
    run(["engine/collect.py", "sample"], root)
    run(["engine/collect.py", "sample"], root)
    inbox = json.loads((fd / "inbox.json").read_text())["pending"]
    expect(len(inbox) == 1, f"inbox should hold 1 item, has {len(inbox)}")
    expect("utm_source" not in inbox[0]["url"], "tracking parameters should be stripped")
    expect(inbox[0]["summary"].isascii(), "summary should be ASCII")

    # organizations and a logo
    run(["engine/orgs.py", "add", "--name", "Alpha Credit Union", "--short", "Alpha CU",
         "--kind", "credit_union", "--state", "CT"], root)
    run(["engine/orgs.py", "add", "--name", "Beta Federal Credit Union", "--short", "Beta FCU"], root)
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (600, 300), (255, 255, 255, 0))
    ImageDraw.Draw(img).rectangle([100, 80, 500, 220], fill=(245, 130, 32, 255))
    logo = root / "alpha.png"
    img.save(logo)
    run(["engine/logos.py", "add", "--slug", "alpha-cu", "--file", str(logo),
         "--source-url", "file://" + str(logo)], root)
    expect((root / "shared" / "logos" / "alpha-cu-v1.png").exists(), "logo v1 should be saved")
    w, h = Image.open(root / "shared" / "logos" / "alpha-cu-v1.png").size
    expect((w, h) == (401, 141), f"logo should be trimmed to its shape, got {w}x{h}")

    # a change file with one good add, one bad add and a duplicate
    change = {
        "feed": "sample",
        "run": {"started": "2026-11-21T06:37:00-05:00", "finished": "2026-11-21T06:52:00-05:00"},
        "reviewed": [inbox[0]["id"]],
        "adds": [
            {"fields": {"announced": "Nov 19, 2026", "acquirer": "Alpha Credit Union",
                        "partner": "Beta Federal Credit Union", "state": "ct", "status": "announced",
                        "combined_assets": "$1,874,900,000", "members": "112,662",
                        "ceo_title": "President and Chief Executive Officer",
                        "expected_close": "Q2 2027", "effective": "Jan 2027",
                        "notes": "Both boards approved — needs member vote"},
             "sources": [{"label": "Alpha CU release", "url": "https://alpha.example/news", "type": "primary"},
                         {"label": "CU Today", "url": "https://cutoday.example/x", "type": "trade_press"}],
             "field_sources": {"expected_close": 1}},
            {"fields": {"announced": "someday", "acquirer": "Gamma CU", "partner": "Delta CU",
                        "status": "Pending", "expected_close": "June 2027"},
             "sources": [{"label": "x", "url": "https://x.example", "type": "trade_press"}]},
        ],
        "updates": [],
    }
    cdir = fd / "changes"
    cdir.mkdir()
    (cdir / "2026-11-21-0652.json").write_text(json.dumps(change))
    r = run(["engine/apply.py", "sample", "--dry-run", str(cdir / "2026-11-21-0652.json")], root, ok=False)
    expect("REJECTED" in r.stdout and "is not a full date" in r.stdout, "dry run should explain rejections")
    expect(not (fd / "records.json").exists(), "dry run should write nothing")
    run(["engine/apply.py", "sample"], root)
    recs = json.loads((fd / "records.json").read_text())["records"]
    expect(len(recs) == 1, f"one record expected, got {len(recs)}")
    rec = recs[0]
    expect(rec["fields"]["status"] == "Announced", "choice should be canonical")
    expect(rec["fields"]["combined_assets"] == 1874.9, "dollars should become $ millions")
    expect(rec["fields"]["ceo_title"] == "President & CEO", "title style should apply")
    expect(rec["fields"]["effective"] == "Jan 2027", "datetext keeps text")
    expect((cdir / "applied" / "2026-11-21-0652.json").exists(), "change file should move to applied/")
    expect((cdir / "rejected" / "2026-11-21-0652.json").exists(), "rejections should be kept")
    expect(json.loads((fd / "inbox.json").read_text())["pending"] == [], "reviewed item should leave inbox")

    # build and check the published feed
    run(["engine/build.py", "sample"], root)
    feed = json.loads((fd / "feed.json").read_text())
    it = feed[0]
    expect(it["combined_assets"] == "$1.87B", f"money format: {it['combined_assets']}")
    expect(it["members"] == "112,662", "int format")
    expect(it["announced"] == "Nov 19, 2026" and it["announced_iso"] == "2026-11-19", "date format")
    expect(it["acquirer_short"] == "Alpha CU" and it["partner_short"] == "Beta FCU", "short names")
    expect(it["acquirer_logo_url"].endswith("/shared/logos/alpha-cu-v1.png"), "hosted logo url")
    expect(it["partner_logo_url"] == "", "missing logo stays blank")
    expect(it["region"] == "New England", "derived groups")
    expect(it["headline"] == "Alpha CU and Beta FCU to merge", f"derived cases: {it['headline']}")
    expect(it["open"] == "yes", "open item")
    expect(it["confidence"] == "97%", f"confidence from sources: {it['confidence']} {it['confidence_notes']}")
    expect(" - needs member vote" in it["notes"], "ASCII dash")
    wl = json.loads((fd / "worklist.json").read_text())
    expect([x["slug"] for x in wl["logos_missing"]] == ["beta-fcu"], "worklist lists missing logos")
    expect(len(wl["recheck"]) == 1, "worklist lists open items to recheck")
    run(["engine/validate.py", "sample"], root)

    # an update with a dated note, then a duplicate add that only fills blanks
    change2 = {"feed": "sample", "run": {"finished": "2026-11-22T06:50:00-05:00"},
               "updates": [{"id": rec["id"], "set": {"status": "Approved", "expected_close": "Q1 2027"},
                            "note": "NCUA approved; close moved to Q1 2027",
                            "sources": [{"label": "NCUA", "url": "https://ncua.example/a", "type": "regulator"}]}],
               "adds": [{"fields": {"announced": "2026-11-19", "acquirer": "Alpha CU", "partner": "Beta FCU",
                                    "status": "Completed", "state": "MA"},
                         "sources": [{"label": "dup", "url": "https://dup.example", "type": "trade_press"}]}]}
    (cdir / "2026-11-22-0650.json").write_text(json.dumps(change2))
    run(["engine/apply.py", "sample"], root)
    rec = json.loads((fd / "records.json").read_text())["records"][0]
    expect(len(json.loads((fd / "records.json").read_text())["records"]) == 1, "duplicate should not add a row")
    expect(rec["fields"]["status"] == "Approved", "update should change status, duplicate must not")
    expect(rec["fields"]["state"] == "CT", "duplicate fills blanks only")
    run(["engine/build.py", "sample"], root)
    it = json.loads((fd / "feed.json").read_text())[0]
    expect(it["updates"].startswith("Nov 21, 2026: NCUA approved") or "NCUA approved" in it["updates"],
           f"dated update note: {it['updates']}")

    # Sean's correction wins, even over a later update
    run(["engine/correct.py", "sample", "--id", rec["id"], "--set", "expected_close=Q3 2027",
         "--note", "Per the CEO"], root)
    change3 = {"feed": "sample", "updates": [{"id": rec["id"], "set": {"expected_close": "Q4 2027"},
                                              "note": "x", "sources": [{"label": "y", "url": "https://y.example",
                                                                         "type": "trade_press"}]}]}
    (cdir / "2026-11-23-0650.json").write_text(json.dumps(change3))
    run(["engine/apply.py", "sample"], root)
    run(["engine/build.py", "sample"], root)
    it = json.loads((fd / "feed.json").read_text())[0]
    expect(it["expected_close"] == "Q3 2027", f"correction should win: {it['expected_close']}")
    bad = run(["engine/correct.py", "sample", "--id", rec["id"], "--set", "expected_close=June"], root, ok=False)
    expect("must be a quarter" in (bad.stderr + bad.stdout), "bad correction should be refused")

    # a broken feed is never published: the last good one stays
    corr = json.loads((fd / "corrections.json").read_text())
    corr["corrections"].append({"id": rec["id"], "set": {"status": "Nope"}})
    (fd / "corrections.json").write_text(json.dumps(corr))
    run(["engine/build.py", "sample"], root, ok=False)
    st = json.loads((fd / "status.json").read_text())
    expect(st["result"] == "failed" and st["items"] == 1, "failed build keeps the old feed")
    expect(json.loads((fd / "feed.json").read_text())[0]["expected_close"] == "Q3 2027", "old feed kept")
    corr["corrections"].pop()
    (fd / "corrections.json").write_text(json.dumps(corr))
    run(["engine/build.py", "sample"], root)

    # health: a missed morning review is caught
    sys.path.insert(0, str(root / "engine"))
    os.environ["FEEDS_ROOT"] = str(root)
    import datetime as dt
    from checks import check_health
    from common import ET, load_feed
    s = load_feed("sample")
    st = json.loads((fd / "status.json").read_text())
    late = dt.datetime(2026, 11, 24, 9, 30, tzinfo=ET)
    st["last_collect"] = (late - dt.timedelta(hours=1)).isoformat()
    probs = check_health(s, st, now=late)
    expect(any("no Claude review" in p for p in probs), f"missed review should be caught: {probs}")
    st["last_review"] = late.replace(hour=6, minute=55).isoformat()
    expect(check_health(s, st, now=late) == [], f"fresh feed should pass: {check_health(s, st, now=late)}")

    # logos: a changed logo must show twice, a week apart, before review
    img2 = Image.new("RGBA", (600, 600), (255, 255, 255, 0))
    ImageDraw.Draw(img2).ellipse([50, 50, 550, 550], fill=(26, 31, 36, 255))
    img2.save(logo)
    run(["engine/logos.py", "check"], root)
    o = json.loads((root / "shared" / "organizations.json").read_text())["organizations"]["alpha-cu"]["logo"]
    expect(o.get("pending") and not o.get("review"), "first sighting should only be pending")
    o_all = json.loads((root / "shared" / "organizations.json").read_text())
    o_all["organizations"]["alpha-cu"]["logo"]["pending"]["first_seen"] = "2026-01-01"
    (root / "shared" / "organizations.json").write_text(json.dumps(o_all))
    run(["engine/logos.py", "check"], root)
    o = json.loads((root / "shared" / "organizations.json").read_text())["organizations"]["alpha-cu"]["logo"]
    expect(bool(o.get("review")), "second sighting a week later should flag review")
    run(["engine/logos.py", "accept", "--slug", "alpha-cu"], root)
    o = json.loads((root / "shared" / "organizations.json").read_text())["organizations"]["alpha-cu"]["logo"]
    expect(o["file"] == "alpha-cu-v2.png" and o["previous"] == ["alpha-cu-v1.png"], "new version, old kept")
    expect((root / "shared" / "logos" / "alpha-cu-v1.png").exists(), "old logo file kept for rollback")
    run(["engine/build.py", "sample"], root)
    it = json.loads((fd / "feed.json").read_text())[0]
    expect(it["acquirer_logo_url"].endswith("alpha-cu-v2.png"), "feed points at the new logo")

    # two organizations with the same name (Consumers CU in Illinois and Michigan)
    rec = json.loads((fd / "records.json").read_text())["records"][0]
    expect(rec.get("org_slugs") == {"acquirer": "alpha-cu", "partner": "beta-fcu"},
           f"records remember which organization they mean: {rec.get('org_slugs')}")
    bad = run(["engine/orgs.py", "add", "--name", "Alpha Credit Union", "--state", "MI",
               "--website", "https://alpha-mi.example"], root, ok=False)
    expect("different organization" in bad.stderr, f"same name, other state must be refused: {bad.stderr}")
    o = json.loads((root / "shared" / "organizations.json").read_text())["organizations"]["alpha-cu"]
    expect(o["state"] == "CT" and not o.get("website"), "refused add must not touch the existing entry")
    run(["engine/orgs.py", "add", "--slug", "alpha-cu-mi", "--name", "Alpha Credit Union",
         "--short", "Alpha CU", "--state", "MI"], root)
    found = run(["engine/orgs.py", "find", "Alpha Credit Union"], root)
    expect("2 organizations share this name" in found.stdout, f"find lists both: {found.stdout}")
    run(["engine/build.py", "sample"], root)
    it = json.loads((fd / "feed.json").read_text())[0]
    expect(it["acquirer_logo_url"].endswith("alpha-cu-v2.png"), "a pinned record keeps its organization")
    st = json.loads((fd / "status.json").read_text())
    expect(not any("several organizations" in w for w in st["warnings"]), "pinned records raise no warning")
    add = {"fields": {"announced": "2026-11-20", "acquirer": "Alpha CU", "partner": "Gamma Credit Union",
                      "status": "Announced", "state": "MI"},
           "sources": [{"label": "x", "url": "https://gamma.example", "type": "primary"}]}
    (cdir / "2026-11-24-0650.json").write_text(json.dumps({"feed": "sample", "adds": [add]}))
    r = run(["engine/apply.py", "sample", "--dry-run", str(cdir / "2026-11-24-0650.json")], root, ok=False)
    expect("matches several organizations" in r.stdout, f"an unpinned shared name is refused: {r.stdout}")
    add["org_slugs"] = {"acquirer": "alpha-cu-mi"}
    (cdir / "2026-11-24-0650.json").write_text(json.dumps({"feed": "sample", "adds": [add]}))
    state_before = json.loads((fd / "state.json").read_text())["last_review"]
    run(["engine/apply.py", "sample"], root)
    expect(json.loads((fd / "state.json").read_text())["last_review"] == state_before,
           "a change file without a run section is maintenance, not a review")
    run(["engine/build.py", "sample"], root)
    feed = {x["partner"]: x for x in json.loads((fd / "feed.json").read_text())}
    expect(feed["Gamma Credit Union"]["acquirer_logo_url"] == "", "the Michigan one has no logo yet")
    expect(feed["Beta Federal Credit Union"]["acquirer_logo_url"].endswith("alpha-cu-v2.png"),
           "the Connecticut one keeps its logo")
    wl = json.loads((fd / "worklist.json").read_text())
    expect("alpha-cu-mi" in [x["slug"] for x in wl["logos_missing"]], "worklist asks for the new one's logo")
    gid = feed["Gamma Credit Union"]["id"]
    (cdir / "2026-11-24-0700.json").write_text(json.dumps({"feed": "sample", "updates": [
        {"id": gid, "org_slugs": {"acquirer": "alpha-cu"}, "note": "It was the Connecticut one"}]}))
    run(["engine/apply.py", "sample"], root)
    g = next(x for x in json.loads((fd / "records.json").read_text())["records"] if x["id"] == gid)
    expect(g["org_slugs"]["acquirer"] == "alpha-cu" and "It was the Connecticut one" in g["history"][-1]["note"],
           f"an update can re-point a record: {g.get('org_slugs')}")
    all_recs = json.loads((fd / "records.json").read_text())
    for x in all_recs["records"]:
        x.pop("org_slugs", None)
    (fd / "records.json").write_text(json.dumps(all_recs))
    run(["engine/build.py", "sample"], root)
    st = json.loads((fd / "status.json").read_text())
    expect(any("several organizations" in w for w in st["warnings"]), f"unpinned shared name warns: {st['warnings']}")

    if os.environ.get("KEEP_TEST_DIR"):
        print("kept", root)
    else:
        shutil.rmtree(root, ignore_errors=True)
    if FAILS:
        print(f"FAILED: {len(FAILS)} problem(s)")
        for f in FAILS:
            print(" -", f)
        sys.exit(1)
    print("OK: engine end-to-end test passed")


if __name__ == "__main__":
    main()
