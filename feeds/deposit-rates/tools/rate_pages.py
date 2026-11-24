#!/usr/bin/env python3
"""Rate-page checker for the deposit-rates feed.

    python3 feeds/deposit-rates/tools/rate_pages.py fetch [slug ...]   read every page (or these institutions' pages)
    python3 feeds/deposit-rates/tools/rate_pages.py mark slug ... | --all
    python3 feeds/deposit-rates/tools/rate_pages.py list

fetch reads each institution's own rate pages listed in sites.json (method "curl"),
keeps the deposit-rate lines, saves them to /tmp/rates/<slug>.txt (the whole page text
to /tmp/rates/<slug>.full.txt) and compares a fingerprint of the rate lines with the one
recorded the last time Claude reviewed that page (page_state.json). It prints, per
institution:
    CHANGED   the rate lines differ from the last reviewed version: read and update rows
    NEW       never reviewed: read and add rows
    SAME      nothing changed since the last review
    NO-RATES  the page answered but shows no rates (script-loaded page or moved): fix sites.json
    FAILED    the page did not answer (HTTP code given): try WebFetch, fix sites.json
    WEBFETCH  sites.json says this site must be read with WebFetch (bot check or no readable page)
    NONE      the institution publishes no rates online
Methods in sites.json: "curl" (plain page), "pdf" (a rate-sheet PDF; text via pdftotext,
image-only pages show as a change whenever the file changes; render them with
pdftoppm and read the image), "browser" (pages that only show rates after a script
runs: rendered in headless Chrome; "zip_input" names the ZIP box to fill with 06385; needs `pip install --break-system-packages
playwright` and Chrome at /usr/bin/google-chrome), "webfetch" and "none".
mark records the fingerprints from the last fetch as reviewed. Run it only after the
change file covers what the page shows (or for SAME pages, nothing to do).
"""
import datetime as dt
import hashlib
import html
import json
import re
import subprocess
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
SITES = HERE / "sites.json"
STATE = HERE / "page_state.json"
OUT = Path("/tmp/rates")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
RATE = re.compile(r"\d?\.\d{1,3}\s?%|\bAPY\b|Annual Percentage Yield|Dividend Rate|effective|as of", re.I)
LOAN = re.compile(r"\bAPR\b|mortgage|auto loan|home equity|HELOC|credit card|personal loan|\bARM\b|"
                  r"payment|per \$1,?000|points", re.I)


def today():
    return dt.datetime.now(ZoneInfo("America/New_York")).date().isoformat()


def load(path, default):
    return json.loads(path.read_text()) if path.exists() else default


def save(path, data):
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def fetch(url):
    r = subprocess.run(["curl", "-sS", "-L", "--compressed", "-m", "45", "-A", UA,
                        "-H", "Accept-Language: en-US,en;q=0.9", "-w", "\n__CODE__%{http_code}", url],
                       capture_output=True)
    out = r.stdout.decode("utf-8", "replace")
    body, _, code = out.rpartition("\n__CODE__")
    return (code or "000").strip(), body


def fetch_pdf(url):
    r = subprocess.run(["curl", "-sS", "-L", "-m", "60", "-A", UA, "-o", "/tmp/rates/_sheet.pdf",
                        "-w", "%{http_code}", url], capture_output=True, text=True)
    code = (r.stdout or "000").strip()
    if not code.startswith("2"):
        return code, [], ""
    raw = Path("/tmp/rates/_sheet.pdf").read_bytes()
    txt = subprocess.run(["pdftotext", "-layout", "/tmp/rates/_sheet.pdf", "-"], capture_output=True,
                         text=True).stdout
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in txt.splitlines() if ln.strip()]
    return code, lines, hashlib.sha1(raw).hexdigest()[:16]


_BROWSER = None


def fetch_browser(url, zip_input=None):
    global _BROWSER
    from playwright.sync_api import sync_playwright
    if _BROWSER is None:
        pw = sync_playwright().start()
        _BROWSER = pw.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
    pg = _BROWSER.new_page(user_agent=UA)
    try:
        r = pg.goto(url, wait_until="load", timeout=60000)
        pg.wait_for_timeout(5000)
        if zip_input:  # banks that price by ZIP: Waterford, CT
            box = pg.locator(zip_input).first
            box.fill("06385", timeout=10000)
            box.press("Enter")
            pg.wait_for_timeout(7000)
        code = str(r.status) if r else "000"
        rows = pg.evaluate("""() => Array.from(document.querySelectorAll('tr')).map(tr =>
            Array.from(tr.children).map(td => (td.innerText || '').replace(/\\s+/g, ' ').trim()).join(' | '))""")
        text = pg.inner_text("body")
    except Exception as e:  # timeout or navigation error
        return "000 " + str(e)[:60], []
    finally:
        pg.close()
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.splitlines() if ln.strip()]
    return code, lines + ["[table rows]"] + [r for r in rows if r.strip(" |")]


def to_lines(body):
    body = re.sub(r"(?is)<(script|style|noscript|svg|nav|footer|header)[^>]*>.*?</\1>", " ", body)
    body = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|li|h\d|table|section|caption|thead|tbody|dt|dd|ul|ol)>", "\n", body)
    body = re.sub(r"(?i)</t[dh]>", " |", body)
    body = re.sub(r"<[^>]+>", " ", body)
    body = html.unescape(body).replace("\xa0", " ")
    raw = [re.sub(r"\s+", " ", ln).strip() for ln in body.split("\n")]
    raw = [ln for ln in raw if ln and ln != "|"]
    out, row = [], []
    for ln in raw:
        if ln.endswith("|"):
            row.append(ln.rstrip("| ").strip())
            continue
        if row:
            out.append(" | ".join(row))
            row = []
        out.append(ln)
    if row:
        out.append(" | ".join(row))
    return out


def rate_lines(lines):
    return [ln for ln in lines if RATE.search(ln) and not LOAN.search(ln)]


def block(lines, before=4, after=2):
    keep = set()
    for i, ln in enumerate(lines):
        if RATE.search(ln) and not LOAN.search(ln):
            keep.update(range(max(0, i - before), min(len(lines), i + after + 1)))
    out, last = [], -2
    for i in sorted(keep):
        if i != last + 1:
            out.append("...")
        out.append(lines[i][:3000])
        last = i
    return out


def fingerprint(lines):
    norm = [re.sub(r"\s+", " ", ln).strip().lower() for ln in rate_lines(lines)]
    return hashlib.sha1("\n".join(norm).encode()).hexdigest()[:16]


def cmd_fetch(slugs):
    sites, state = load(SITES, {}), load(STATE, {})
    OUT.mkdir(exist_ok=True)
    pending = load(OUT / "pending.json", {})
    counts = {}
    for slug in sorted(slugs or sites):
        site = sites.get(slug)
        if not site:
            print(f"{slug}: not in sites.json")
            continue
        method = site.get("method", "curl")
        if method not in ("curl", "pdf", "browser"):
            status = "WEBFETCH" if method == "webfetch" else "NONE"
            counts[status] = counts.get(status, 0) + 1
            print(f"{status:9} {slug}  {' '.join(site.get('pages', []))}  {site.get('note', '')}")
            continue
        texts, full, statuses = [], [], []
        for url in site.get("pages", []):
            pdf_fp = None
            if method == "pdf" or url.lower().split("?")[0].endswith(".pdf"):
                code, lines, pdf_fp = fetch_pdf(url)
            elif method == "browser":
                code, lines = fetch_browser(url, site.get("zip_input"))
            else:
                code, body = fetch(url)
                lines = to_lines(body) if code.startswith("2") else []
            rl = rate_lines(lines)
            if not code.startswith("2"):
                st = f"FAILED {code}"
            elif not pdf_fp and not [ln for ln in rl if re.search(r"\d?\.\d{1,3}\s?%", ln)]:
                st = "NO-RATES"
            else:
                fp = pdf_fp or fingerprint(lines)
                pending[url] = {"fingerprint": fp, "fetched": today()}
                old = (state.get(url) or {}).get("fingerprint")
                st = "NEW" if not old else ("SAME" if old == fp else "CHANGED")
            statuses.append(st)
            texts.append(f"===== {url}\n{st}\n" + "\n".join(block(lines)))
            full.append(f"===== {url}\n" + "\n".join(lines))
        (OUT / f"{slug}.txt").write_text("\n\n".join(texts) + "\n")
        (OUT / f"{slug}.full.txt").write_text("\n\n".join(full) + "\n")
        order = ["FAILED", "NO-RATES", "CHANGED", "NEW", "SAME"]
        worst = min(statuses, key=lambda s: next(i for i, o in enumerate(order) if s.startswith(o))) \
            if statuses else "NO-PAGES"
        counts[worst.split()[0]] = counts.get(worst.split()[0], 0) + 1
        print(f"{worst:9} {slug}  ({', '.join(statuses)})  -> /tmp/rates/{slug}.txt")
        if site.get("webfetch_pages"):
            print(f"          also WebFetch: {' '.join(site['webfetch_pages'])}  {site.get('note', '')}")
    save(OUT / "pending.json", pending)
    wf = [u for s in sorted(slugs or sites) for u in (sites.get(s) or {}).get("pages", []) + (sites.get(s) or {}).get("webfetch_pages", [])
          if (sites.get(s) or {}).get("method") == "webfetch" or u in (sites.get(s) or {}).get("webfetch_pages", [])]
    if wf:
        print("WebFetch URLs for this run (use only these, or URLs a WebSearch returns):")
        for u in wf:
            print("  " + u)
    print("summary:", ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))


def cmd_mark(slugs, all_):
    sites, state = load(SITES, {}), load(STATE, {})
    pending = load(OUT / "pending.json", {})
    targets = sites if all_ else slugs
    n = 0
    for slug in targets:
        for url in (sites.get(slug) or {}).get("pages", []):
            if url in pending:
                state[url] = {"fingerprint": pending[url]["fingerprint"], "reviewed": today()}
                n += 1
    save(STATE, state)
    print(f"marked {n} pages reviewed")


def cmd_list():
    sites = load(SITES, {})
    by = {}
    for slug, s in sorted(sites.items()):
        by.setdefault(s.get("method", "curl"), []).append(slug)
    for m, xs in by.items():
        print(f"{m} ({len(xs)}): {', '.join(xs)}")


def main():
    a = sys.argv[1:]
    if not a or a[0] not in ("fetch", "mark", "list"):
        sys.exit(__doc__)
    if a[0] == "fetch":
        cmd_fetch(a[1:])
    elif a[0] == "mark":
        cmd_mark([x for x in a[1:] if x != "--all"], "--all" in a)
    else:
        cmd_list()


if __name__ == "__main__":
    main()
