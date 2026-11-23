#!/usr/bin/env python3
"""Find candidate logos for the exec-jobs feed's credit unions (shared/organizations.json).

    python3 feeds/exec-jobs/tools/find_logo.py [options] <slug> [<slug> ...]
      --site slug=https://...   use this homepage (when NCUA has no website or the wrong one)
      --page slug=https://...   also look on this page (a WebSearch result: the credit union's own newsroom,
                                careers site or online-banking login page, a chamber, sponsor, partner,
                                vendor case-study or lender-directory page, a news story)
      --pdf slug=https://...    also take the page-1 images of this PDF (a release or member notice the
                                credit union issued, often reposted on a chamber, city or partner site)
      --image slug=https://...  take this image file as a candidate (an image URL a search result showed)
      --before YYYY-MM-DD       archive cutoff for every slug (default: today)

For each credit union it collects every plausible logo, from:
  live    the homepage (following meta-refresh stubs): header logo/brand containers (img, inline svg,
          SVG sprites), img tags naming the logo or the credit union, schema.org "logo", og:image /
          twitter:image, large site icons, and CSS background images on logo/brand selectors
  release the press-wire releases this feed cites for the credit union's moves (PR Newswire,
          GlobeNewswire, Business Wire, EIN Presswire), which carry the issuer's logo
  archive the Internet Archive's last copy of the homepage before the cutoff. Skipped when the live site
          refused us (401/403/429): never get around a site that blocks automated requests.
  wiki    the Wikipedia infobox image, when the article title matches
  commons Wikimedia Commons files titled "<name> logo" (larger credit unions often have one)
  page / pdf / image   anything given with --page, --pdf or --image
Candidates go to /tmp/logo-candidates/<slug>/<n>-<origin>.<ext>, listed in results.json, and every one
is drawn on /tmp/logo-candidates/sheet-N.png (ten credit unions per sheet, one row each, flagged ones marked "!").
View the sheet with the Read tool, pick the credit union's own CURRENT logo, then run engine/logos.py add with
its file and source. Never accept: anniversary/award marks, "powered by"/"a division of" lockups, another
organization's logo (a vendor's, a league's, a merger partner's), online-banking product marks, promos,
NCUA/Equal Housing marks, signatures, photos or app icons that are only part of the mark.
"""
import json, re, sys, os, html, hashlib, subprocess, datetime as dt, urllib.request, urllib.parse, concurrent.futures as cf

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"}
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
OUT = "/tmp/logo-candidates"
FEED = f"{REPO}/feeds/exec-jobs"
orgs = json.load(open(f"{REPO}/shared/organizations.json"))["organizations"]
SKIP = re.compile(r"ncua|equal|housing|award|badge|cdfi|facebook|instagram|linkedin|twitter-logo|x-logo|youtube|tiktok|"
                  r"app-?store|google-?play|bbb|newsweek|forbes|best-of|loader|spinner|chat|pixel|1x1|spacer|visa|mastercard|"
                  r"co-?op-?(?:atm|network|shared)|allpoint|shared-?branch|zelle|fdic|lender", re.I)
FLAG = re.compile(r"powered|division|lockup|anniversar|\d+-?years|\d+th|celebrat|247|online-?banking|banner|hero|promo", re.I)
MAX_PER_SOURCE = 4
ARCHIVE_OK = True
REFUSED = os.path.join(os.path.dirname(os.path.abspath(__file__)), "refused_sites.json")

def refused_load():
    try:
        return json.load(open(REFUSED))
    except Exception:
        return {}

def host_of(url):
    return urllib.parse.urlparse(url if url.startswith("http") else "https://" + url).netloc.lower().removeprefix("www.")

def refused_recently(url):
    when = refused_load().get(host_of(url))
    return when and (dt.date.today() - dt.date.fromisoformat(when["date"])).days < 30 and when

def refused_mark(url, code):
    d = refused_load()
    d[host_of(url)] = {"date": dt.date.today().isoformat(), "code": code}
    json.dump(d, open(REFUSED, "w"), indent=1, sort_keys=True)

def get(url, n=3_000_000):
    """Fetch a URL. A dropped connection or timeout is retried once; a refusal (HTTP 4xx/5xx) is not."""
    import time
    for attempt in (1, 2):
        try:
            r = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=25)
            return r.read(n), r.geturl()
        except urllib.error.HTTPError:
            raise
        except Exception:
            if attempt == 2:
                raise
            time.sleep(3)

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

def org_words(name):
    return [w.lower() for w in re.findall(r"[A-Za-z]{3,}", name)
            if w.lower() not in ("credit", "union", "federal", "the", "community", "bank", "employees", "association")]

def theirs(cands, name, org_site, page_url):
    """On someone else's page (trade press, a wire, a chamber or job board), keep only images that are the
    credit union's: the file name, alt text or tag names it (or its domain). This drops the publisher's own
    logo (schema.org logo, site icon, header logo) and headshot share images, which daily runs kept picking up."""
    org_host = host_of(org_site) if org_site else ""
    if org_host and host_of(page_url) == org_host:
        return cands
    words, keep = org_words(name), []
    for c in cands:
        low = ((c[2] + " " + c[3]) if c[1] == "url" else c[3]).lower()
        if c[3] in ("schema.org logo", "site icon") or re.search(
                r"share|twitter\.com|x\.com/intent|pinterest|facebook\.com|linkedin\.com/share|mailto:|avatar|gravatar", c[3], re.I):
            continue
        if (org_host and org_host.split(".")[0] in low) or any(w in low for w in words):
            keep.append(c)
    return keep

def find(t, base, name):
    """All candidates on one page: (score, kind, value, hint)."""
    out = []
    words = org_words(name)
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
        for m in re.finditer(r"([^{}]*)\{([^}]*)\}", css):
            u = re.search(r"background(?:-image)?\s*:[^;]*url\(\s*[\"']?([^\"')]+)", m.group(2), re.I)
            if not u or u.group(1).startswith("data:"):
                continue
            if re.search(r"logo|brand", m.group(1), re.I) or re.search(r"logo", u.group(1).split("/")[-1], re.I):
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
    return dt.date.today().isoformat()

def release_pages(slug):
    """Press-wire pages this feed cites for the credit union's moves (they carry the issuer's logo)."""
    try:
        recs = json.load(open(f"{FEED}/records.json"))["records"]
    except Exception:
        return []
    name = orgs[slug]["name"].lower()
    names = {name} | {a.lower() for a in orgs[slug].get("aliases", [])}
    out = []
    for r in recs:
        if (r["fields"].get("organization") or "").lower() not in names:
            continue
        for s_ in r.get("sources", []):
            u = s_.get("url", "")
            if re.search(r"prnewswire\.com|globenewswire\.com|businesswire\.com|einpresswire\.com|einnews\.com", u) and u not in out:
                out.append(u)
    return out[:3]

def commons(name):
    """Wikimedia Commons files whose title has the credit union's distinctive words and 'logo'."""
    key = [w.lower() for w in re.findall(r"[A-Za-z]{3,}", name) if w.lower() not in ("credit", "union", "federal", "the", "community")]
    q = "https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode({"action": "query", "format": "json",
        "generator": "search", "gsrnamespace": 6, "gsrsearch": f"{name} logo", "gsrlimit": 8,
        "prop": "imageinfo", "iiprop": "url"})
    d = json.loads(get(q, 400_000)[0])
    out = []
    for p in d.get("query", {}).get("pages", {}).values():
        t = p.get("title", "").lower()
        if key and all(w in t for w in key) and "logo" in t and p.get("imageinfo"):
            out.append((6, "url", p["imageinfo"][0]["url"], p["title"]))
    return out

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
        data = re.sub(rb"encoding=[\"']utf-16[\"']", b'encoding="utf-8"', data, flags=re.I)  # mislabeled SVGs
        head = data.lstrip()[:400].lower()
        is_svg = head[:5] in (b"<?xml", b"<svg ") and b"<svg" in data[:4000].lower()
        if not is_svg:
            if head.startswith((b"<!doctype", b"<html")) or b"<html" in head:
                return None  # an HTML page (error or login page) served at an image address
            try:
                from PIL import Image
                import io
                Image.open(io.BytesIO(data)).verify()
            except Exception:
                return None
        ext = "svg" if is_svg else \
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

def one(slug, site=None, pages=(), before=None, pdfs=(), images=()):
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
    org_site = web
    blocked = False
    if web and refused_recently(web):
        r_ = refused_recently(web)
        blocked = True
        notes.append(f"live site refused us on {r_['date']} ({r_['code']}): not asked again for 30 days. Use --page / --pdf / "
                     f"--image from WebSearch results (see the logo steps in feed.yml)")
        web = None
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
            if blocked:
                refused_mark(web, e.code)
            cf_ = "cloudflare" in (e.headers.get("server") or "").lower()
            notes.append(f"live site {e.code}" + (" from Cloudflare's bot check" if cf_ else "") + (
                f": don't retry it. WebSearch '{o['name']} press release pdf' and '{o['name']} logo', then rerun with "
                f"--pdf {slug}=<pdf url> / --page {slug}=<page url> / --image {slug}=<image url>" if blocked else ""))
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
    elif not blocked:
        notes.append("no website: WebSearch for it and rerun with --site")
    for url in release_pages(slug):
        try:
            page, final = get(url)
            t = page.decode("utf-8", "ignore")
            wire_brand = re.compile(r"/content/dam/prnewswire|cision|globenewswire\.com/(?:Content|images)/|"
                                    r"businesswire\.com/(?:images|static)|einpresswire\.com/(?:static|images/logo)|einnews\.com/(?:static|images)", re.I)
            take("release", theirs([c for c in find(t, final, o["name"]) if c[0] >= 5 and not (c[1] == "url" and wire_brand.search(c[2]))],
                                   o["name"], org_site, final), t, final)
        except Exception as e:
            notes.append(f"release page error {str(e)[:50]}")
    try:
        take("commons", commons(o["name"]), "", "https://commons.wikimedia.org/")
    except Exception:
        pass
    for url in images:
        take("image", [(9, "url", url, "given image")], "", url)
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
        if refused_recently(pg):
            notes.append(f"page skipped: {host_of(pg)} refused us recently")
            continue
        try:
            page, final = get(pg)
            t = page.decode("utf-8", "ignore")
            take("page", theirs([c for c in find(t, final, o["name"]) if c[0] >= 5], o["name"], org_site, final), t, final)
        except urllib.error.HTTPError as e:
            if e.code in (401, 403, 429):
                refused_mark(pg, e.code)
            notes.append(f"page {e.code}: {host_of(pg)}")
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
    sites, pages, pdfs, images, before, slugs = {}, {}, {}, {}, None, []
    it = iter(argv)
    for a in it:
        if a == "--site":
            k, v = next(it).split("=", 1); sites[k] = v
        elif a == "--page":
            k, v = next(it).split("=", 1); pages.setdefault(k, []).append(v)
        elif a == "--pdf":
            k, v = next(it).split("=", 1); pdfs.setdefault(k, []).append(v)
        elif a == "--image":
            k, v = next(it).split("=", 1); images.setdefault(k, []).append(v)
        elif a == "--before":
            before = next(it)
        else:
            slugs.append(a)
    slugs += [s for s in list(sites) + list(pages) + list(pdfs) + list(images) if s not in slugs]
    os.makedirs(OUT, exist_ok=True)
    global ARCHIVE_OK
    try:
        get("https://web.archive.org/web/2020id_/https://www.ncua.gov/", 2000)
    except Exception:
        ARCHIVE_OK = False
        print("Internet Archive not reachable from here: archive copies skipped")
    res = {}
    with cf.ThreadPoolExecutor(8) as ex:
        for slug, r in ex.map(lambda s: one(s, sites.get(s), pages.get(s, ()), before, pdfs.get(s, ()), images.get(s, ())), slugs):
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
