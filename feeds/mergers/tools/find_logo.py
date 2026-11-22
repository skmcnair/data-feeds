#!/usr/bin/env python3
"""Find candidate logos for organizations in shared/organizations.json.

    python3 feeds/mergers/tools/find_logo.py [options] <slug> [<slug> ...]
      --site slug=https://...   use this homepage (when NCUA has no website or the wrong one)
      --page slug=https://...   also look on this page (e.g. a WebSearch result for "<name> logo")
      --pdf slug=https://...    also take the page-1 images of this PDF (a press release the organization
                                issued, often posted on a chamber, city or partner site): its letterhead
      --before YYYY-MM-DD       archive cutoff for every slug (default: per slug, the day before its
                                earliest announced deal in feeds/mergers/feed.json, else 180 days ago)

For each organization it collects every plausible logo, from:
  live    the homepage (following meta-refresh stubs): header logo/brand containers (img, inline svg,
          SVG sprites), img tags naming the logo or the organization, schema.org "logo", og:image /
          twitter:image, large site icons, and CSS background images on logo/brand selectors
  archive the Internet Archive's last copy of the homepage before the cutoff (for organizations whose
          site now shows the acquirer, a "powered by" lockup or an anniversary mark). Skipped when the
          live site refused us (401/403/429): never get around a site that blocks automated requests.
  wiki    the Wikipedia infobox image, when the article title matches
  page    any --page given
  pdf     any --pdf given (how Suncoast's and Gesa's logos were found: their sites block cloud servers
          with Cloudflare's bot check, but their own press-release PDFs are posted elsewhere)
Candidates go to /tmp/logo-candidates/<slug>/<n>-<origin>.<ext>, listed in results.json, and every one
is drawn on /tmp/logo-candidates/sheet-N.png (ten organizations per sheet, one row each, flagged ones marked "!").
View the sheet with the Read tool, pick the right one, then run engine/logos.py add with its file and
source. Never accept: anniversary/award marks, "powered by"/"a division of" lockups, the acquirer's logo,
online-banking product marks, promos, NCUA/Equal Housing marks, signatures or photos.
"""
import json, re, sys, os, html, hashlib, subprocess, datetime as dt, urllib.request, urllib.parse, concurrent.futures as cf

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"}
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
OUT = "/tmp/logo-candidates"
orgs = json.load(open(f"{REPO}/shared/organizations.json"))["organizations"]
SKIP = re.compile(r"ncua|equal|housing|award|badge|cdfi|facebook|instagram|linkedin|twitter-logo|x-logo|youtube|tiktok|"
                  r"app-?store|google-?play|bbb|newsweek|forbes|best-of|loader|spinner|chat|pixel|1x1|spacer|visa|mastercard|"
                  r"co-?op-?(?:atm|network|shared)|allpoint|shared-?branch|zelle|fdic|lender", re.I)
FLAG = re.compile(r"powered|division|lockup|anniversar|\d+-?years|\d+th|celebrat|247|online-?banking|banner|hero|promo", re.I)
MAX_PER_SOURCE = 4
ARCHIVE_OK = True

def get(url, n=3_000_000):
    r = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=25)
    return r.read(n), r.geturl()

def ncua_web(ch):
    try:
        d = json.load(urllib.request.urlopen(urllib.request.Request(
            f"https://mapping.ncua.gov/api/CreditUnionDetails/GetCreditUnionDetails/{ch}", headers=UA), timeout=25))
        return d.get("creditUnionWebsite")
    except Exception:
        return None

def fdic_web(cert):
    try:
        d = json.load(urllib.request.urlopen(urllib.request.Request(
            f"https://api.fdic.gov/banks/institutions?filters=CERT:{cert}&fields=WEBADDR,NAME", headers=UA), timeout=25))
        return (d.get("data") or [{}])[0].get("data", {}).get("WEBADDR") or None
    except Exception:
        return None

def element_by_id(t, eid):
    m = re.search(r"<(symbol|g)\b[^>]*\bid=[\"']" + re.escape(eid) + r"[\"'][^>]*>", t)
    if not m:
        return None
    tag, i, depth = m.group(1), m.end(), 1
    for x in re.finditer(r"<(/?)" + tag + r"\b[^>]*?(/?)>", t[i:]):
        if x.group(2):
            continue
        depth += -1 if x.group(1) else 1
        if depth == 0:
            return t[m.start(): i + x.end()]
    return None

def svg_doc(val, t):
    u = re.search(r"<use\b[^>]*href=[\"']#([^\"']+)[\"']", val)
    if u:
        body = element_by_id(t, u.group(1))
        if not body:
            return None
        vb = re.search(r"viewBox=[\"']([^\"']+)", body[:300]) or re.search(r"viewBox=[\"']([^\"']+)", val)
        inner = re.sub(r"^<symbol\b[^>]*>|</symbol>$", "", body)
        val = (f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
               f'viewBox="{vb.group(1).strip() if vb else "0 0 400 150"}">{inner}</svg>')
    return val if "xmlns" in val[:300] else val.replace("<svg", '<svg xmlns="http://www.w3.org/2000/svg"', 1)

def find(t, base, name):
    """All candidates on one page: (score, kind, value, hint)."""
    out = []
    words = [w.lower() for w in re.findall(r"[A-Za-z]{3,}", name)
             if w.lower() not in ("credit", "union", "federal", "the", "community", "bank", "employees")]
    def img_src(tag):
        s = re.search(r"\b(?:data-orig-src|data-src|data-lazy-src|src)\s*=\s*[\"']([^\"']+)[\"']", tag, re.I)
        return None if (not s or s.group(1).startswith("data:")) else urllib.parse.urljoin(base, html.unescape(s.group(1)))
    for m in re.finditer(r"<(a|div|span|figure|header|h1|p|picture)\b([^>]*(?:logo|brand)[^>]*)>(.{0,80000}?)</\1>", t, re.I | re.S):
        inner = m.group(3)
        im = re.search(r"<img\b[^>]*>", inner, re.I)
        if im and img_src(im.group(0)):
            out.append((9, "url", img_src(im.group(0)), im.group(0)))
        sv = re.search(r"<svg\b.*?</svg>", inner, re.I | re.S)
        if sv and (len(sv.group(0)) > 400 or "<use" in sv.group(0)):
            out.append((8, "svg", sv.group(0), m.group(2)))
    for m in re.finditer(r'"logo"\s*:\s*(?:\{[^}]*?"url"\s*:\s*)?"([^"]+\.(?:png|svg|jpe?g|webp)[^"]*)"', t, re.I):
        out.append((8, "url", urllib.parse.urljoin(base, m.group(1).replace("\\/", "/")), "schema.org logo"))
    for m in re.finditer(r"<img\b[^>]*>", t, re.I):
        tag, low = m.group(0), m.group(0).lower()
        src = img_src(tag)
        if not src:
            continue
        score = (5 if "logo" in low else 0) + (2 if any(w in low for w in words) else 0) + (1 if ".svg" in src.lower() else 0)
        if score >= 5:
            out.append((score, "url", src, tag))
    for m in re.finditer(r"<svg\b[^>]*>.*?</svg>", t, re.I | re.S):
        head = m.group(0)[:600].lower()
        if ("logo" in head or any(w in head for w in words)) and len(m.group(0)) > 400:
            out.append((6, "svg", m.group(0), head[:200]))
    for prop in ("og:image", "twitter:image"):
        for m in re.finditer(r"<meta[^>]+(?:property|name)=[\"']" + prop + r"[\"'][^>]*>", t, re.I):
            c = re.search(r"content=[\"']([^\"']+)", m.group(0))
            if c:
                out.append((7 if prop == "og:image" else 5, "url", urllib.parse.urljoin(base, html.unescape(c.group(1))), prop))
    for m in re.finditer(r"<link[^>]+rel=[\"'][^\"']*(?:apple-touch-icon|icon)[^\"']*[\"'][^>]*>", t, re.I):
        h, sz = re.search(r"href=[\"']([^\"']+)", m.group(0)), re.search(r"sizes=[\"'](\d+)", m.group(0))
        if h and sz and int(sz.group(1)) >= 150:
            out.append((2, "url", urllib.parse.urljoin(base, html.unescape(h.group(1))), "site icon"))
    seen, res = set(), []
    for c in sorted(out, key=lambda x: -x[0]):
        key = c[2][:300]
        if key in seen or SKIP.search(c[2][:300] if c[1] == "url" else c[3][:300]):
            continue
        seen.add(key)
        res.append(c)
    return res

def css_logos(t, base):
    """Logos drawn with CSS: background images in rules whose selector mentions logo or brand."""
    sheets = [(t, base)]
    for m in re.finditer(r"<link\b[^>]*rel=[\"']?stylesheet[^>]*>", t, re.I):
        h = re.search(r"href=[\"']([^\"']+)", m.group(0))
        if h and len(sheets) <= 30:
            u = urllib.parse.urljoin(base, html.unescape(h.group(1)))
            if urllib.parse.urlparse(u).netloc.split(".")[-2:] == urllib.parse.urlparse(base).netloc.split(".")[-2:]:
                try:
                    sheets.append((get(u, 2_000_000)[0].decode("utf-8", "ignore"), u))
                except Exception:
                    pass
    out = []
    for css, b in sheets:
        for m in re.finditer(r"([^{}]*(?:logo|brand)[^{}]*)\{([^}]*)\}", css, re.I):
            u = re.search(r"background(?:-image)?\s*:[^;]*url\(\s*[\"']?([^\"')]+)", m.group(2), re.I)
            if u and not u.group(1).startswith("data:"):
                out.append((7, "url", urllib.parse.urljoin(b, u.group(1)), "css " + m.group(1).strip()[:80]))
    return out

def archive_copy(site, before):
    host = re.sub(r"^https?://", "", site).rstrip("/")
    q = f"https://web.archive.org/cdx/search/cdx?url={urllib.parse.quote(host)}&to={before.replace('-', '')}&limit=-1&filter=statuscode:200&fl=timestamp,original"
    rows = get(q, 50_000)[0].decode().split()
    if len(rows) < 2:
        return None
    ts, orig = rows[-2], rows[-1]
    page, _ = get(f"https://web.archive.org/web/{ts}id_/{orig}")
    return ts, orig, page.decode("utf-8", "ignore")

def wiki(name):
    q = "https://en.wikipedia.org/w/api.php?" + urllib.parse.urlencode({"action": "query", "format": "json", "generator": "search",
        "gsrsearch": name, "gsrlimit": 1, "prop": "pageimages", "piprop": "original"})
    d = json.loads(get(q, 200_000)[0])
    for p in d.get("query", {}).get("pages", {}).values():
        key = [w.lower() for w in re.findall(r"[A-Za-z]{4,}", name) if w.lower() not in ("credit", "union", "federal", "bank")]
        if p.get("original") and key and all(w in p["title"].lower() for w in key):
            return p["original"]["source"], p["title"]
    return None

def default_before(slug):
    try:
        feed = json.load(open(f"{REPO}/feeds/mergers/feed.json"))
        name = orgs[slug]["name"]
        ds = [r.get("announced_iso") for r in feed if name in (r.get("acquirer"), r.get("partner")) and r.get("announced_iso")]
        if ds:
            return (dt.date.fromisoformat(min(ds)) - dt.timedelta(days=1)).isoformat()
    except Exception:
        pass
    return (dt.date.today() - dt.timedelta(days=180)).isoformat()

def save(slug, n, origin, kind, val, page_text, fetch_prefix=""):
    d = f"{OUT}/{slug}"
    if kind == "svg":
        svg = svg_doc(val, page_text)
        if not svg:
            return None
        data, ext = svg.encode(), "svg"
    else:
        data, _ = get(fetch_prefix + val, 8_000_000)
        if len(data) < 300:
            return None
        ext = "svg" if (val.lower().split("?")[0].endswith(".svg") or data.lstrip()[:5] in (b"<?xml", b"<svg ")) else \
            (re.search(r"\.(png|webp|jpe?g|gif)", val, re.I) or [None, "png"])[1].lower().replace("jpeg", "jpg")
    p = f"{d}/{n}-{origin}.{ext}"
    open(p, "wb").write(data)
    return p, hashlib.md5(data).hexdigest()

def pdf_images(slug, url, first_n):
    """Page-1 images of a PDF (press-release letterhead), with their transparency masks applied."""
    from PIL import Image
    d = f"{OUT}/{slug}"
    pdf = f"{d}/src-{first_n}.pdf"
    open(pdf, "wb").write(get(url, 40_000_000)[0])
    rows = [r.split() for r in subprocess.run(["pdfimages", "-list", "-f", "1", "-l", "1", pdf],
            capture_output=True, text=True).stdout.splitlines()[2:]]
    subprocess.run(["pdfimages", "-png", "-f", "1", "-l", "1", pdf, f"{d}/pdf{first_n}"], capture_output=True)
    out = []
    for i, r in enumerate(rows):
        if len(r) < 5 or r[2] != "image" or int(r[3]) < 80:
            continue
        im = Image.open(f"{d}/pdf{first_n}-{i:03d}.png").convert("RGB")
        if i + 1 < len(rows) and rows[i + 1][2] == "smask":
            im.putalpha(Image.open(f"{d}/pdf{first_n}-{i + 1:03d}.png").convert("L").resize(im.size))
        p = f"{d}/pdf{first_n}-{i:03d}-logo.png"
        im.save(p)
        out.append(p)
    return out

def one(slug, site=None, pages=(), before=None, pdfs=()):
    o = orgs[slug]
    os.makedirs(f"{OUT}/{slug}", exist_ok=True)
    cands, notes, hashes = [], [], set()
    def take(origin, items, page_text, base_url, prefix=""):
        k = 0
        for score, kind, val, hint in items:
            if k >= MAX_PER_SOURCE:
                break
            try:
                got = save(slug, len(cands) + 1, origin, kind, val, page_text, prefix)
            except Exception:
                continue
            if not got or got[1] in hashes:
                continue
            hashes.add(got[1]); k += 1
            flag = bool(FLAG.search((val if kind == "url" else "") + " " + hint))
            cands.append({"n": len(cands) + 1, "origin": origin, "file": got[0], "flag": flag,
                          "source": (prefix + val) if kind == "url" else base_url, "hint": hint[:120]})
    web = site or o.get("website") or (ncua_web(o["charter"]) if o.get("charter") else None) or \
        (fdic_web(o.get("fdic_cert") or o.get("cert")) if (o.get("fdic_cert") or o.get("cert")) else None)
    blocked = False
    if web:
        web = web if web.startswith("http") else "https://" + web
        try:
            page, final = get(web)
            t = page.decode("utf-8", "ignore")
            for _ in range(2):  # follow <meta http-equiv="refresh"> on stub pages
                mr = re.search(r"<meta[^>]+http-equiv=[\"']?refresh[^>]+url=([^\"'>]+)", t, re.I)
                if not mr or len(t) > 5000:
                    break
                page, final = get(urllib.parse.urljoin(final, mr.group(1).strip()))
                t = page.decode("utf-8", "ignore")
            found = find(t, final, o["name"])
            if not [c for c in found if c[0] >= 5]:
                found += css_logos(t, final)
            take("live", sorted(found, key=lambda c: -c[0]), t, final)
        except urllib.error.HTTPError as e:
            blocked = e.code in (401, 403, 429)
            cf_ = "cloudflare" in (e.headers.get("server") or "").lower()
            notes.append(f"live site {e.code}" + (" from Cloudflare's bot check" if cf_ else "") + (
                f": don't retry it. WebSearch '{o['name']} press release pdf' and '{o['name']} logo', then rerun with "
                f"--pdf {slug}=<pdf url> / --page {slug}=<page url>" if blocked else ""))
        except Exception as e:
            notes.append(f"live site error {str(e)[:60]}")
        if not blocked and ARCHIVE_OK:
            b = before or default_before(slug)
            try:
                got = archive_copy(web, b)
                if got:
                    ts, orig, t = got
                    take("archive", find(t, orig, o["name"]), t, f"https://web.archive.org/web/{ts}/{orig}",
                         prefix=f"https://web.archive.org/web/{ts}im_/")
                else:
                    notes.append(f"no archive copy before {b}")
            except Exception as e:
                notes.append(f"archive error {str(e)[:60]}")
    else:
        notes.append("no website: WebSearch for it and rerun with --site")
    try:
        w = wiki(o["name"])
        if w:
            take("wiki", [(5, "url", w[0], w[1])], "", w[0])
    except Exception:
        pass
    for url in pdfs:
        try:
            for f in pdf_images(slug, url, len(cands) + 1):
                cands.append({"n": len(cands) + 1, "origin": "pdf", "file": f, "flag": False, "source": url, "hint": "PDF letterhead"})
        except Exception as e:
            notes.append(f"pdf error {str(e)[:60]}")
    for pg in pages:
        try:
            page, final = get(pg)
            t = page.decode("utf-8", "ignore")
            take("page", [c for c in find(t, final, o["name"]) if c[0] >= 5], t, final)
        except Exception as e:
            notes.append(f"page error {str(e)[:60]}")
    return slug, {"candidates": cands, "notes": notes}

def sheet(res):
    rows = [(s, v["candidates"]) for s, v in sorted(res.items())]
    names = []
    for i in range(0, max(1, len(rows)), 10):
        names.append(f"{OUT}/sheet-{i // 10 + 1}.png")
        sheet_page(rows[i:i + 10], names[-1])
    return names

def sheet_page(rows, path):
    from PIL import Image, ImageDraw
    W, H, per = 210, 74, 6
    rows = [(s, cs[:per]) for s, cs in rows]
    img = Image.new("RGB", (W * per + 170, H * max(1, len(rows))), (165, 165, 165))
    d = ImageDraw.Draw(img)
    for r, (slug, cs) in enumerate(rows):
        y = r * H
        d.text((4, y + 4), slug[:26], fill="black")
        d.line([(0, y), (img.width, y)], fill=(120, 120, 120))
        if not cs:
            d.text((4, y + 20), "no candidates", fill=(120, 0, 0))
        for i, c in enumerate(cs):
            f = c["file"]
            if f.endswith(".svg"):
                subprocess.run(["rsvg-convert", "-h", "120", "-o", f + ".png", f], capture_output=True, timeout=30)
                f += ".png"
            try:
                im = Image.open(f).convert("RGBA")
                im.thumbnail((W - 14, H - 20))
                img.paste(im, (170 + i * W + 6, y + 3), im)
            except Exception:
                pass
            d.text((170 + i * W + 6, y + H - 14), f'{"! " if c["flag"] else ""}{c["n"]} {c["origin"]}', fill=(150, 0, 0) if c["flag"] else "black")
    img.save(path)

def main(argv):
    sites, pages, pdfs, before, slugs = {}, {}, {}, None, []
    it = iter(argv)
    for a in it:
        if a == "--site":
            k, v = next(it).split("=", 1); sites[k] = v
        elif a == "--page":
            k, v = next(it).split("=", 1); pages.setdefault(k, []).append(v)
        elif a == "--pdf":
            k, v = next(it).split("=", 1); pdfs.setdefault(k, []).append(v)
        elif a == "--before":
            before = next(it)
        else:
            slugs.append(a)
    slugs += [s for s in list(sites) + list(pages) + list(pdfs) if s not in slugs]
    os.makedirs(OUT, exist_ok=True)
    global ARCHIVE_OK
    try:
        get("https://web.archive.org/web/2020id_/https://www.ncua.gov/", 2000)
    except Exception:
        ARCHIVE_OK = False
        print("Internet Archive not reachable from here: archive copies skipped")
    res = {}
    with cf.ThreadPoolExecutor(8) as ex:
        for slug, r in ex.map(lambda s: one(s, sites.get(s), pages.get(s, ()), before, pdfs.get(s, ())), slugs):
            res[slug] = r
    json.dump(res, open(f"{OUT}/results.json", "w"), indent=1)
    try:
        print("contact sheets:", " ".join(sheet(res)))
    except Exception as e:
        print("no contact sheet:", e)
    for s, r in sorted(res.items()):
        print(f"{s}: {len(r['candidates'])} candidates" + (f" ({'; '.join(r['notes'])})" if r["notes"] else ""))

if __name__ == "__main__":
    main(sys.argv[1:])
