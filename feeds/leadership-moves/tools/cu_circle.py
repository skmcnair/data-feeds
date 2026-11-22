#!/usr/bin/env python3
"""Read Sean's CU Circle feed (public CU Circle LinkedIn posts, via RSS.app) with each post's full text.

    python3 feeds/leadership-moves/tools/cu_circle.py            # posts from the last 4 days
    python3 feeds/leadership-moves/tools/cu_circle.py --days 30  # everything the feed still holds

RSS.app keeps only the newest ~16 posts (about 11 days), and the collector stores just a 240-character
summary in the inbox. The daily review runs this every day so no post falls off unread. Nothing is
saved: the post text stays on LinkedIn/RSS.app; rows cite the post link (type social) plus any release
or story that confirms the move.
"""
import datetime as dt, email.utils, html, re, sys, urllib.request, xml.etree.ElementTree as ET

FEED = "https://rss.app/feeds/jXHvxXgW9PzMNEUr.xml"


def text(s):
    s = re.sub(r"<br\s*/?>|</p>|</li>", "\n", s or "", flags=re.I)
    s = html.unescape(re.sub(r"<[^>]+>", " ", s))
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", s)).strip()


def main(argv):
    days = 4
    if "--days" in argv:
        days = int(argv[argv.index("--days") + 1])
    req = urllib.request.Request(FEED, headers={"User-Agent": "data-feeds leadership-moves review"})
    root = ET.fromstring(urllib.request.urlopen(req, timeout=60).read())
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    items = root.findall(".//item")
    shown = 0
    for it in items:
        pub = it.findtext("pubDate") or ""
        try:
            when = email.utils.parsedate_to_datetime(pub)
        except Exception:
            when = None
        if when and when < cutoff:
            continue
        shown += 1
        print("=" * 100)
        print((when.date().isoformat() if when else pub), "|", text(it.findtext("title")))
        print(it.findtext("link"))
        print(text(it.findtext("description")))
    print("=" * 100)
    oldest = min((email.utils.parsedate_to_datetime(i.findtext("pubDate")) for i in items if i.findtext("pubDate")), default=None)
    print(f"{shown} of {len(items)} posts shown (last {days} days); the feed's oldest post is from "
          f"{oldest.date().isoformat() if oldest else 'unknown'}")


if __name__ == "__main__":
    main(sys.argv[1:])
