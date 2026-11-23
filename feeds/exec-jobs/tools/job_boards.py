#!/usr/bin/env python3
"""Read the executive-search and job-board pages this feed watches, and compare them with the feed.

    python3 feeds/exec-jobs/tools/job_boards.py            # every board
    python3 feeds/exec-jobs/tools/job_boards.py --board shanley --board ddj-myers
    python3 feeds/exec-jobs/tools/job_boards.py --all-titles   # keep listings below VP too (for checking)

Prints a summary and writes /tmp/exec-jobs-boards.json:
  listings: every VP-and-up listing on each board (board, firm, org_text, title, location,
            assets_text, posted (YYYY-MM-DD or ""), posted_text, url, filled, snippet)
  new:      listings whose url no tracked item has as job_url or a source
  gone:     open tracked items whose job_url is on a board that was read fine today,
            but which is no longer listed there (the posting came down: check it)
  filled:   listings the board marks as filled (Shanley's "JUST FILLED")
  errors:   boards that could not be read today (their items are not reported as gone)

The listings are leads, not facts: org_text is how the board names the employer (it can be a
brand name, an abbreviation or a typo), and a listing can be a CUSO, league or vendor. The
review decides each one. Pages are only read, never logged in to; a board that refuses is
reported in errors and skipped.
"""
import argparse, datetime as dt, html, json, os, re, subprocess, sys, time, urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
FEED = os.path.join(REPO, "feeds", "exec-jobs")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
OUT = "/tmp/exec-jobs-boards.json"

EXEC = re.compile(r"\b(vice\s*president|v\.?p\.?|svp|evp|chief|president|ceo|cfo|coo|cio|cto|clo|cro|cmo|cxo|cpo|"
                  r"general\s+manager)\b", re.I)
BELOW = re.compile(r"\b(assistant\s+vice\s+president|avp|asst\.?\s*vp)\b", re.I)


def fetch(url, tries=2):
    """Plain GET. A refusal (403, 429, a bot check) is reported, never worked around."""
    last = ""
    for i in range(tries):
        p = subprocess.run(["curl", "-sS", "-L", "--max-time", "40", "-A", UA, "-w", "\n%{http_code}", url],
                           capture_output=True)
        body = p.stdout.decode("utf-8", "replace")
        body, _, code = body.rpartition("\n")
        if p.returncode == 0 and code.startswith("2"):
            return body
        last = f"HTTP {code or '?'} {p.stderr.decode()[:120].strip()}"
        time.sleep(3)
    raise RuntimeError(f"{url}: {last}")


def text(s):
    s = re.sub(r"<script.*?</script>|<style.*?</style>", " ", s or "", flags=re.S)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def is_exec(title):
    return bool(EXEC.search(title)) and not BELOW.search(title)


def iso(d, fmts):
    for f in fmts:
        try:
            return dt.datetime.strptime(d.strip(), f).date().isoformat()
        except ValueError:
            pass
    return ""


# ---------- boards ----------

def dhilton():
    out, seen = [], set()
    for page in range(1, 6):
        url = "https://www.dhilton.com/find-a-career" + (f"?page={page}&prop_ModuleId=2135" if page > 1 else "")
        s = fetch(url)
        cards = re.findall(r'<a href="(/find-a-career/[^"]+)" class="card[^"]*">(.*?)</a>', s, re.S)
        fresh = [c for c in cards if c[0] not in seen]
        if not fresh:
            break
        for href, body in fresh:
            seen.add(href)
            ps = [text(p) for p in re.findall(r"<p>(.*?)</p>", body, re.S)]
            loc = text(" ".join(re.findall(r"<li>(.*?)</li>", body, re.S)))
            out.append({"title": ps[0] if ps else "", "org_text": ps[1] if len(ps) > 1 else "",
                        "assets_text": ps[2] if len(ps) > 2 else "", "location": loc,
                        "url": "https://www.dhilton.com" + href})
    return out


def ddj_myers():
    s = fetch("https://ddjmyers.com/open-positions")
    out = []
    for href, body in re.findall(r'<div class="page-block[^"]*">\s*<a href="(https://ddjmyers\.com/open-positions/[^"]+)">(.*?)</a>\s*</div>', s, re.S):
        date = text((re.search(r'resource-date.*?<span>(.*?)</span>', body, re.S) or [None, ""])[1])
        title = text((re.search(r'<h4 class="resource-title">(.*?)</h4>', body, re.S) or [None, ""])[1])
        org = text((re.search(r"<p><strong>(.*?)</strong></p>", body, re.S) or [None, ""])[1])
        blurb = text((re.findall(r"<p>(.*?)</p>", body, re.S) or [""])[-1])
        assets = (re.search(r"\$[\d.,]+\s*[BMK]\w*", blurb) or [""])[0]
        out.append({"title": title, "org_text": org, "assets_text": assets, "location": "",
                    "posted": iso(date, ["%B %d, %Y"]), "posted_text": date, "url": href,
                    "snippet": blurb[:200]})
    return out


def shanley():
    base = "https://shanleysearch.com/available/open-positions/"
    s = fetch(base)
    out = []
    for m in re.finditer(r'<div id="(tab-[^"]+)" class="tab-pane[^"]*"([^>]*)>', s):
        attrs = dict(re.findall(r'data-([a-z]+)="([^"]*)"', m.group(2)))
        title = html.unescape(attrs.get("title", ""))
        body = s[m.end(): m.end() + 6000]
        filled = "JUSTFILLED" in m.group(1) or "just filled" in title.lower()
        title = re.sub(r"\s*[-(]*\s*just filled\s*\)?", "", title, flags=re.I).strip()
        site = (re.search(r'class="company-link" href="([^"]+)"', body) or [None, ""])[1]
        snippet = text((re.search(r'class="richtext">(.*?)</div>', body, re.S) or [None, ""])[1])
        assets = (re.search(r"\$[\d.,]+\s*(?:billion|million|B|M)\b", snippet, re.I) or [""])[0]
        pay = (re.search(r"\$[\d,]{6,}\s*(?:-|to|–)\s*\$[\d,]{6,}", body) or [""])[0]
        out.append({"title": title, "org_text": html.unescape(attrs.get("company", "")),
                    "location": html.unescape(attrs.get("location", "")), "assets_text": assets,
                    "posted": iso(attrs.get("dateposted", ""), ["%m-%d-%Y"]),
                    "posted_text": attrs.get("dateposted", ""), "url": base + "#" + m.group(1),
                    "filled": filled, "org_site": site, "pay_text": pay, "snippet": snippet[:200]})
    return out


def jspire():
    root = "https://www.theapplicantmanager.com/"
    s = fetch(root + "careers?co=js")
    out = []
    for href, label in re.findall(r'<a class="pos_title_list" href="([^"]+)">(.*?)</a>', s, re.S):
        label = text(label)
        title, _, loc = label.rpartition(" (")
        title, loc = (title or label).strip(), loc.rstrip(")").strip()
        item = {"title": title, "location": loc, "org_text": "", "assets_text": "",
                "url": root + href.replace("&fs=1.0em", "")}
        if is_exec(title):  # the detail page names the credit union ("Truity- Chief Financial Officer")
            try:
                d = fetch(item["url"])
                head = text((re.search(r"<title>(.*?)</title>", d, re.S) or [None, ""])[1])
                item["org_text"] = head.split("- ")[0].strip() if "- " in head else ""
                body = text(d)
                item["assets_text"] = (re.search(r"\$[\d.,]+\s*(?:billion|million)", body, re.I) or [""])[0]
                item["snippet"] = body[:200]
            except Exception as e:
                item["detail_error"] = str(e)[:120]
        out.append(item)
    return out


def zealyst():
    root = "https://zealyst.app.loxo.co"
    s = fetch(root + "/zealyst")
    out = []
    for card in s.split("class='jobs-listing-card'")[1:]:
        a = re.search(r"class='job-title'[^>]*href=\"([^\"]+)\"\s*>(.*?)</a>", card, re.S)
        if not a:
            continue
        label = text(a.group(2))
        org, _, title = label.partition(" - ")
        when = text((re.search(r"class='job-date'[^>]*>(.*?)</div>", card, re.S) or [None, ""])[1])
        loc = text((re.search(r"class='job-location'[^>]*>(.*?)</div>", card, re.S) or [None, ""])[1])
        loc = re.sub(r"^location_on\s*", "", loc).replace(", United States", "")
        out.append({"title": (title or label).strip(), "org_text": org.strip() if title else "",
                    "location": loc, "assets_text": "", "posted_text": when, "url": root + a.group(1)})
    return out


def cuinsight():
    # Page 1 only (the newest ~24 posts, read daily). Later pages answer with Cloudflare's bot
    # check; never try to get past it.
    out, seen = [], set()
    for page in (1,):
        s = fetch("https://www.cuinsight.com/jobs/")
        if "<title>Just a moment" in s:
            raise RuntimeError("Cloudflare bot check (skipped; never get around it)")
        arts = re.findall(r'<article id="\d+" class="column-job relative">(.*?)</article>', s, re.S)
        if not arts:
            break
        for a in arts:
            date = (re.search(r'<time datetime="(\d{4}-\d\d-\d\d)', a) or [None, ""])[1]
            link = re.search(r'<h3[^>]*>\s*<a href="([^"]+)"[^>]*>(.*?)</a>', a, re.S)
            if not link:
                continue
            label = text(link.group(2))
            if " at " not in label or re.match(r"^\d+ positions? at", label, re.I):
                continue  # "17 positions at AmeriCU": a careers-page link, not one job
            title, _, org = label.partition(" at ")
            loc = text((re.search(r"<h4[^>]*>(.*?)</h4>", a, re.S) or [None, ""])[1])
            firm = text((re.search(r"Featured by\s*(.*?)</", a, re.S) or [None, ""])[1])
            key = (title, org, link.group(1))
            if key in seen:
                continue
            seen.add(key)
            out.append({"title": title.strip(), "org_text": org.strip(), "location": loc, "assets_text": "",
                        "posted": date, "posted_text": date, "url": link.group(1), "featured_by": firm})
    return out


BOARDS = {
    "d-hilton": ("D. Hilton Associates", dhilton),
    "ddj-myers": ("DDJ Myers", ddj_myers),
    "shanley": ("Shanley Search Partners", shanley),
    "jspire": ("JSpire Recruiting", jspire),
    "zealyst": ("Zealyst", zealyst),
    "cuinsight": ("CUInsight Job Board", cuinsight),
}
# Read by the review with WebFetch (they refuse plain requests, or list jobs in a way this
# script can't read reliably): see feed.yml instructions.
WEBFETCH_BOARDS = [
    "https://www.cues.org/networking/credit-union-job-board (CUES; Executive positions)",
]


def tracked():
    try:
        recs = json.load(open(os.path.join(FEED, "records.json")))["records"]
    except Exception:
        recs = []
    return recs


def norm_url(u):
    u = (u or "").strip()
    p = urllib.parse.urlsplit(u)
    return urllib.parse.urlunsplit((p.scheme, p.netloc.lower().replace("www.", ""), p.path.rstrip("/"), p.query, p.fragment))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--board", action="append", choices=sorted(BOARDS))
    ap.add_argument("--all-titles", action="store_true")
    a = ap.parse_args()
    names = a.board or list(BOARDS)
    listings, errors, read_ok = [], [], set()
    for b in names:
        firm, fn = BOARDS[b]
        try:
            rows = fn()
            read_ok.add(b)
        except Exception as e:
            errors.append(f"{b}: {str(e)[:200]}")
            continue
        for r in rows:
            r.setdefault("posted", "")
            r.setdefault("posted_text", "")
            r.setdefault("filled", False)
            r["board"] = b
            r["firm"] = r.pop("featured_by", "") or (firm if b != "cuinsight" else "")
            if a.all_titles or is_exec(r["title"]):
                listings.append(r)
        if not rows:
            errors.append(f"{b}: no listings found (page layout may have changed)")
            read_ok.discard(b)

    recs = tracked()
    known = {}
    for r in recs:
        urls = [r["fields"].get("job_url", "")] + [x.get("url", "") for x in r.get("sources", [])]
        for u in urls:
            if u:
                known[norm_url(u)] = r
    on_board = {norm_url(x["url"]) for x in listings}
    new = [x for x in listings if norm_url(x["url"]) not in known]
    gone = []
    for r in recs:
        f = r["fields"]
        if f.get("status") != "Open" or not f.get("job_url"):
            continue
        u = norm_url(f["job_url"])
        b = next((k for k, host in [("d-hilton", "dhilton.com"), ("ddj-myers", "ddjmyers.com"),
                                     ("shanley", "shanleysearch.com"), ("jspire", "theapplicantmanager.com"),
                                     ("zealyst", "zealyst.app.loxo.co")] if host in u), None)
        if b and b in read_ok and u not in on_board:
            gone.append({"id": r["id"], "organization": f.get("organization"), "title": f.get("title"),
                         "job_url": f["job_url"]})
    filled = [x for x in listings if x.get("filled")]
    result = {"read": dt.datetime.now().isoformat(timespec="seconds"), "boards_read": sorted(read_ok),
              "errors": errors, "webfetch_boards": WEBFETCH_BOARDS, "listings": listings,
              "new": new, "gone": gone, "filled": filled}
    json.dump(result, open(OUT, "w"), indent=1)
    print(f"Read {len(read_ok)}/{len(names)} boards: {len(listings)} VP-and-up listings, "
          f"{len(new)} not tracked yet, {len(gone)} tracked postings no longer listed, {len(filled)} marked filled.")
    for e in errors:
        print("  ERROR", e)
    print(f"Also read with WebFetch: {'; '.join(WEBFETCH_BOARDS)}")
    print(f"Details: {OUT}")
    for x in new:
        print(f"  NEW  [{x['board']}] {x['org_text'] or '?'} | {x['title']} | {x['location']} | "
              f"{x.get('posted') or x.get('posted_text') or 'no date'}{' | FILLED' if x.get('filled') else ''} | {x['url']}")
    for g in gone:
        print(f"  GONE {g['id']} | {g['organization']} | {g['title']} | {g['job_url']}")


if __name__ == "__main__":
    main()
