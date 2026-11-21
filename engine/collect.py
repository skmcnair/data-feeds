#!/usr/bin/env python3
"""Collect new candidate items from each feed's sources into its waiting list.

    python3 engine/collect.py <feed>|--all

Sources are listed in feed.yml: `rss` (any RSS or Atom feed, including
RSS.app feeds of LinkedIn pages) and `google_news` (a Google News search).
Each new article goes into feeds/<feed>/inbox.json once; seen.json remembers
what was already collected so nothing is queued twice. Claude's daily review
works through the waiting list. Runs every few hours on GitHub, so nothing
is missed between reviews and a failed review loses nothing.
"""
import argparse
import datetime as dt
import email.utils
import html
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from common import (
    ascii_text, feed_dir, feed_names, load_feed, load_json, now_et, save_json, short_hash,
)

UA = "Mozilla/5.0 (compatible; data-feeds collector; +https://github.com/skmcnair/data-feeds)"
DROP_PARAMS = re.compile(r"^(utm_|fbclid|gclid|mc_|ref$|oc$)")


def source_url(src):
    if src["type"] == "google_news":
        q = urllib.parse.quote(src["query"])
        return f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
    return src["url"]


def fetch(url, tries=3):
    if url.startswith("file://"):
        return open(url[7:], "rb").read()
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
            with urllib.request.urlopen(req, timeout=40) as r:
                return r.read()
        except Exception as e:  # network errors: retry, then report
            last = e
            time.sleep(3 * (i + 1))
    raise RuntimeError(f"{url}: {last}")


def canonical(url):
    u = urllib.parse.urlsplit(url.strip())
    q = [(k, v) for k, v in urllib.parse.parse_qsl(u.query) if not DROP_PARAMS.match(k)]
    return urllib.parse.urlunsplit((u.scheme, u.netloc.lower(), u.path, urllib.parse.urlencode(q), ""))


def strip_html(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def parse_when(s):
    if not s:
        return None
    when = None
    try:
        when = email.utils.parsedate_to_datetime(s)
    except (TypeError, ValueError):
        try:
            when = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.timezone.utc)
    return when


def parse_feed(data):
    """RSS 2.0 or Atom -> [{title, url, published, summary}]"""
    root = ET.fromstring(data)
    out = []
    def local(t):
        return t.rsplit("}", 1)[-1]
    for el in root.iter():
        tag = local(el.tag)
        if tag not in ("item", "entry"):
            continue
        rec = {"title": "", "url": "", "published": None, "summary": ""}
        for c in el:
            ct = local(c.tag)
            if ct == "title":
                rec["title"] = strip_html(c.text or "")
            elif ct == "link":
                href = c.get("href")
                if href and c.get("rel", "alternate") == "alternate":
                    rec["url"] = href
                elif not href and (c.text or "").strip():
                    rec["url"] = c.text.strip()
            elif ct in ("pubDate", "published", "updated", "date") and not rec["published"]:
                rec["published"] = parse_when((c.text or "").strip())
            elif ct in ("description", "summary", "content") and not rec["summary"]:
                rec["summary"] = strip_html(c.text or "")
        if rec["url"]:
            out.append(rec)
    return out


def wanted(s, title, summary):
    text = f"{title} {summary}".lower()
    inc = [w.lower() for w in s["collect"].get("include_any") or []]
    exc = [w.lower() for w in s["collect"].get("exclude_any") or []]
    if inc and not any(w in text for w in inc):
        return False
    return not any(w in text for w in exc)


def collect(name):
    s = load_feed(name)
    d = feed_dir(name)
    now = now_et()
    inbox = load_json(d / "inbox.json", {"pending": []})
    seen = load_json(d / "seen.json", {})
    cutoff = now - dt.timedelta(days=s["collect"]["max_age_days"])
    added, errors = 0, []
    for src in s["sources"]:
        try:
            entries = parse_feed(fetch(source_url(src)))
        except Exception as e:
            errors.append(f"{src.get('name', src['type'])}: {e}")
            continue
        for e in entries:
            url = canonical(e["url"])
            iid = short_hash(url)
            if iid in seen:
                continue
            if e["published"] and e["published"] < cutoff:
                continue
            if not wanted(s, e["title"], e["summary"]):
                seen[iid] = now.date().isoformat()
                continue
            seen[iid] = now.date().isoformat()
            inbox["pending"].append({
                "id": iid, "title": ascii_text(e["title"])[:200], "url": url,
                "source": ascii_text(src.get("name", src["type"])),
                "published": e["published"].astimezone(now.tzinfo).isoformat() if e["published"] else "",
                "found": now.isoformat(),
                "summary": ascii_text(e["summary"])[:240],
            })
            added += 1
    inbox["pending"].sort(key=lambda x: x.get("published") or x["found"], reverse=True)
    cap = s["collect"]["max_pending"]
    if len(inbox["pending"]) > cap:
        errors.append(f"waiting list over {cap}; dropped {len(inbox['pending']) - cap} oldest")
        inbox["pending"] = inbox["pending"][:cap]
    keep_after = (now - dt.timedelta(days=120)).date().isoformat()
    seen = {k: v for k, v in seen.items() if v >= keep_after}
    save_json(d / "inbox.json", inbox)
    save_json(d / "seen.json", seen)
    state = load_json(d / "state.json", {})
    state["last_collect"] = now.isoformat()
    ok_sources = len(s["sources"]) - len([e for e in errors if not e.startswith("waiting list")])
    state["last_collect_result"] = "ok" if not errors else ("failed" if s["sources"] and ok_sources == 0 else "partial")
    state["collect_errors"] = errors
    save_json(d / "state.json", state)
    return added, errors


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("feed", nargs="?")
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()
    names = feed_names() if a.all else [a.feed]
    for n in names:
        s = load_feed(n)
        if s.get("paused") or not s["sources"]:
            print(f"{n}: no sources to collect")
            continue
        added, errors = collect(n)
        print(f"{n}: {added} new items waiting for review")
        for e in errors:
            print("  problem:", e)
    sys.exit(0)


if __name__ == "__main__":
    main()
