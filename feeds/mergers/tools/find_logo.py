#!/usr/bin/env python3
"""Find candidate logos for organizations in shared/organizations.json.

    python3 feeds/mergers/tools/find_logo.py <slug> [<slug> ...]

Fetches each organization's homepage (plain browser User-Agent) and looks for the header logo:
img/svg inside a logo/brand container, img tags mentioning logo or the name, inline svg, and SVG
sprites (<use href="#id"> resolved to the <g>/<symbol> it points to). Saves candidates to
/tmp/logo-candidates/<slug>.<ext> and a contact sheet /tmp/logo-candidates/sheet.png.
View the sheet with the Read tool before running engine/logos.py add for each correct one.
Never accept: anniversary or award marks, co-branded "X, a division of Y" or "powered by"
lockups, the acquirer's logo on a merged organization's site, promos, NCUA/Equal Housing marks.
"""
import json, re, sys, os, html, subprocess, urllib.request, urllib.parse, concurrent.futures as cf
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"}
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
OUT = "/tmp/logo-candidates"
os.makedirs(OUT, exist_ok=True)
orgs = json.load(open(f"{REPO}/shared/organizations.json"))["organizations"]
EXTRA_WEB = {}
def ncua_web(ch):
    try:
        d = json.load(urllib.request.urlopen(urllib.request.Request(f"https://mapping.ncua.gov/api/CreditUnionDetails/GetCreditUnionDetails/{ch}", headers=UA), timeout=25))
        return d.get("creditUnionWebsite")
    except Exception:
        return None
BAD = re.compile(r"\d+-?years|anniversar|247|online-?banking|footer|ncua|equal|housing|award|badge|cdfi|social|facebook|instagram|linkedin|twitter|youtube|app-?store|google-?play|bbb|newsweek|forbes|best-|hero|banner|promo|icon-|favicon|sprite|loader|spinner|chat", re.I)
def get(url, n=1500000):
    r = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=25)
    return r.read(n), r.geturl()
def candidates(t, base, name):
    out = []
    words = [w.lower() for w in re.findall(r"[A-Za-z]{3,}", name) if w.lower() not in ("credit", "union", "federal", "the", "community", "bank")]
    # 1) containers marked logo/brand with an img or svg inside
    for m in re.finditer(r"<(a|div|span|figure|header|h1|p)\b([^>]*(?:logo|brand)[^>]*)>(.{0,80000}?)</\1>", t, re.I | re.S):
        if BAD.search(m.group(2)): continue
        inner = m.group(3)
        im = re.search(r"<img\b[^>]*?(?:data-src|src)\s*=\s*[\"']([^\"']+)[\"'][^>]*>", inner, re.I)
        if im and not BAD.search(im.group(0)) and not im.group(1).startswith("data:"):
            out.append((9, m.start(), "url", urllib.parse.urljoin(base, html.unescape(im.group(1)))))
        sv = re.search(r"<svg\b.*?</svg>", inner, re.I | re.S)
        if sv and (len(sv.group(0)) > 400 or "<use" in sv.group(0)): out.append((8, m.start(), "svg", sv.group(0)))
    # 2) img tags mentioning logo or the org's name
    for m in re.finditer(r"<img\b[^>]*>", t, re.I):
        tag = m.group(0); low = tag.lower()
        src = re.search(r"\b(?:data-src|src)\s*=\s*[\"']([^\"']+)[\"']", tag, re.I)
        if not src or src.group(1).startswith("data:") or BAD.search(tag): continue
        score = (5 if "logo" in low else 0) + (2 if any(w in low for w in words) else 0) + (1 if re.search(r"\.svg", src.group(1), re.I) else 0)
        if score >= 5: out.append((score, m.start(), "url", urllib.parse.urljoin(base, html.unescape(src.group(1)))))
    # 3) inline svg with logo in class/id/title
    for m in re.finditer(r"<svg\b[^>]*>.*?</svg>", t, re.I | re.S):
        head = m.group(0)[:600].lower()
        if ("logo" in head or any(w in head for w in words)) and len(m.group(0)) > 400 and not BAD.search(head[:200]):
            out.append((6, m.start(), "svg", m.group(0)))
    out.sort(key=lambda x: (-x[0], x[1]))
    return out
def element_by_id(t, eid):
    m = re.search(r"<(symbol|g)\b[^>]*\bid=[\"']" + re.escape(eid) + r"[\"'][^>]*>", t)
    if not m: return None
    tag, i, depth = m.group(1), m.end(), 1
    for x in re.finditer(r"<(/?)" + tag + r"\b[^>]*?(/?)>", t[i:]):
        if x.group(2): continue
        depth += -1 if x.group(1) else 1
        if depth == 0: return t[m.start(): i + x.end()]
    return None
def one(slug):
    o = orgs[slug]
    web = EXTRA_WEB.get(slug) or o.get("website") or (ncua_web(o["charter"]) if o.get("charter") else None)
    if not web: return slug, "no website", None
    if not web.startswith("http"): web = "https://" + web
    try:
        page, final = get(web)
    except Exception as e:
        return slug, f"site error {str(e)[:50]}", None
    t = page.decode("utf-8", "ignore")
    for score, pos, kind, val in candidates(t, final, o["name"])[:3]:
        try:
            if kind == "svg":
                u = re.search(r"<use\b[^>]*href=[\"']#([^\"']+)[\"']", val)
                if u:
                    body = element_by_id(t, u.group(1))
                    if not body: continue
                    vb = re.search(r"viewBox=[\"']([^\"']+)", body[:300]) or re.search(r"viewBox=[\"']([^\"']+)", val) or re.search(r"viewbox:\s*([\d. ]+)", body[:400])
                    inner = re.sub(r"^<symbol\b[^>]*>|</symbol>$", "", body)
                    val = f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="{vb.group(1).strip() if vb else "0 0 400 150"}">{inner}</svg>'
                svg = val if "xmlns" in val[:300] else val.replace("<svg", '<svg xmlns="http://www.w3.org/2000/svg"', 1)
                p = f"/tmp/logo-candidates/{slug}.svg"; open(p, "w").write(svg); return slug, "ok", (p, final)
            data, _ = get(val, 6_000_000)
            ext = "svg" if (val.lower().split("?")[0].endswith(".svg") or data.lstrip()[:5] in (b"<?xml", b"<svg ")) else (re.search(r"\.(png|webp|jpe?g|gif)", val, re.I) or [None, "png"])[1].lower()
            p = f"/tmp/logo-candidates/{slug}.{ext}"; open(p, "wb").write(data); return slug, "ok", (p, val)
        except Exception as e:
            continue
    return slug, "no logo found", None
slugs = sys.argv[1:]
res = {}
with cf.ThreadPoolExecutor(10) as ex:
    for slug, msg, got in ex.map(one, slugs):
        res[slug] = {"msg": msg, "file": got[0] if got else None, "source": got[1] if got else None}
json.dump(res, open(f"{OUT}/results.json", "w"), indent=1)
try:
    from PIL import Image, ImageDraw
    items = [(s, v) for s, v in sorted(res.items()) if v["file"]]
    W, H, cols = 230, 78, 6; rows = max(1, (len(items) + cols - 1) // cols)
    sheet = Image.new("RGB", (W * cols, H * rows), (165, 165, 165)); d = ImageDraw.Draw(sheet)
    for i, (s, v) in enumerate(items):
        f = v["file"]
        if f.endswith(".svg"):
            subprocess.run(["rsvg-convert", "-h", "120", "-o", f + ".png", f], capture_output=True, timeout=30); f = f + ".png"
        try:
            im = Image.open(f).convert("RGBA"); im.thumbnail((W - 16, H - 20))
        except Exception:
            continue
        x, y = (i % cols) * W, (i // cols) * H
        sheet.paste(im, (x + 8, y + 3), im); d.text((x + 6, y + H - 14), s[:34], fill="black")
    sheet.save(f"{OUT}/sheet.png"); print("contact sheet:", f"{OUT}/sheet.png")
except Exception as e:
    print("no contact sheet:", e)
from collections import Counter
print(Counter(v["msg"].split(" ")[0] for v in res.values()))
print({k: v["msg"] for k, v in res.items() if v["msg"] != "ok"})
