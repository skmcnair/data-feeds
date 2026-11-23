#!/usr/bin/env python3
"""Check that every saved logo is the one the organization uses TODAY.

    python3 feeds/exec-jobs/tools/verify_logos.py                 # every credit union in the exec-jobs feed
    python3 feeds/exec-jobs/tools/verify_logos.py <slug> ...      # only these
    python3 feeds/exec-jobs/tools/verify_logos.py --ok <slug> --note "why"   # after reviewing a flag
    python3 feeds/exec-jobs/tools/verify_logos.py --confirm <slug> --source <url> --date YYYY-MM-DD

The weekly engine check only re-downloads the file a logo came from, so it can't notice a rebrand
(the site switches to a new file) or a stale source (a 2021 press-release PDF, an old og:image).
This compares the saved logo with the logos on the organization's live homepage right now
(header, schema.org logo, og:image, CSS logos), using a picture fingerprint drawn on grey so white
and colour logos both compare. Results:
  current    the saved logo matches the logo in the live site's header (or, when the header has
             none, its sharing image)
  pinned     a logo Sean supplied (never second-guessed)
  reviewed   didn't match, but Claude reviewed it and marked it --ok (deliberate crop, merged
             organization whose site now shows the acquirer); re-flagged if the saved logo or the
             site's logos change
  MISMATCH   the site shows something else: view /tmp/logo-verify/sheet-N.png (saved logo first,
             then the site's logos), then fix the logo or mark it --ok with a note
  STALE-RISK the site can't be compared and the logo's source is over 18 months old, an old-tracker
             import or an undated PDF (how Gesa's 2021 letterhead got in): confirm or replace it
  confirmed  the site can't be compared, but a source from the last 12 months shows this same logo
             (recorded with: --confirm <slug> --source <url> --date YYYY-MM-DD)
  BLOCKED    the site refuses us (e.g. Cloudflare's bot check): needs a source dated within the
             last 12 months, a second recent source that agrees, or a logo from Sean (--pin)
  NO-SITE    no website, or no logo found on it: same as BLOCKED
  RETRY      the site's logo files wouldn't download this time (dropped connections): run again
State is kept in feeds/exec-jobs/tools/logo_checks.json.
"""
import json, os, sys, datetime as dt, hashlib, importlib.util, concurrent.futures as cf, subprocess, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import find_logo as fl  # noqa: E402

REPO = fl.REPO
STATE = f"{HERE}/logo_checks.json"
OUT = "/tmp/logo-verify"
sys.path.insert(0, f"{REPO}/engine")
_spec = importlib.util.spec_from_file_location("eng_logos", f"{REPO}/engine/logos.py")
eng = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(eng)
from PIL import Image  # noqa: E402

def vhash(img):
    img = img.convert("RGBA")
    bg = Image.new("RGBA", img.size, (128, 128, 128, 255))
    g = Image.alpha_composite(bg, img).convert("L").resize((17, 16), Image.LANCZOS)
    px = list(g.get_flattened_data() if hasattr(g, "get_flattened_data") else g.getdata()); bits = 0
    for y in range(16):
        for x in range(16):
            bits = (bits << 1) | (px[y * 17 + x] > px[y * 17 + x + 1])
    return bits, round(img.width / max(img.height, 1), 3)

def close(a, b):
    return bin(a[0] ^ b[0]).count("1") <= 34 and abs(a[1] - b[1]) <= 0.18 * max(a[1], b[1])

def live_logos(slug, o):
    web = o.get("website") or (fl.ncua_web(o["charter"]) if o.get("charter") else None) or \
        (fl.fdic_web(o.get("fdic_cert")) if o.get("fdic_cert") else None)
    if not web:  # fall back to the site the logo itself came from, unless that's a hosting service
        host = fl.urllib.parse.urlparse((o.get("logo") or {}).get("source_url") or "").netloc
        if host and not fl.re.search(r"githubusercontent|wixstatic|squarespace|cloudfront|amazonaws|sitecorecloud|website-files|wp\.com|prnewswire|archive\.org", host):
            web = "https://" + host
    if not web:
        return "NO-SITE", [], "no website on file"
    web = web if web.startswith("http") else "https://" + web
    r_ = fl.refused_recently(web)
    if r_:
        return "BLOCKED", [], f"site refused us on {r_['date']} ({r_['code']}); not asked again for 30 days"
    try:
        page, final = fl.get(web)
        t = page.decode("utf-8", "ignore")
        for _ in range(2):
            mr = fl.re.search(r"<meta[^>]+http-equiv=[\"']?refresh[^>]+url=([^\"'>]+)", t, fl.re.I)
            if not mr or len(t) > 5000:
                break
            page, final = fl.get(fl.urllib.parse.urljoin(final, mr.group(1).strip()))
            t = page.decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        cfl = "cloudflare" in (e.headers.get("server") or "").lower()
        if e.code in (401, 403, 429):
            fl.refused_mark(web, e.code)
            return "BLOCKED", [], f"site {e.code}" + (" (Cloudflare bot check)" if cfl else "")
        return "NO-SITE", [], f"site {e.code}"
    except Exception as e:
        return "NO-SITE", [], f"site error {str(e)[:50]}"
    found = [c for c in fl.find(t, final, o["name"]) if c[0] >= 5]
    if not [c for c in found if c[3] not in ("og:image", "twitter:image", "site icon")]:
        found += [c for c in fl.css_logos(t, final) if c[0] >= 5]
    d = f"{OUT}/{slug}"; os.makedirs(d, exist_ok=True)
    out, failed = [], 0
    for i, (score, kind, val, hint) in enumerate(found[:8]):
        try:
            if kind == "svg":
                svg = fl.svg_doc(val, t)
                if not svg:
                    continue
                p = f"{d}/live{i}.svg"; open(p, "w").write(svg)
            else:
                data, _ = fl.get(val, 8_000_000)
                ext = "svg" if data.lstrip()[:5] in (b"<?xml", b"<svg ") else "img"
                if ext == "svg":  # some sites label UTF-8 SVGs as UTF-16 (Sunmark); fix the label
                    data = fl.re.sub(rb"encoding=[\"']utf-16[\"']", b'encoding="utf-8"', data, flags=fl.re.I)
                p = f"{d}/live{i}.{ext}"; open(p, "wb").write(data)
            img = eng.standardize(eng.load_image(p))
            img.save(f"{d}/live{i}.png")
            meta = hint in ("og:image", "twitter:image", "site icon")
            out.append({"file": f"{d}/live{i}.png", "source": val if kind == "url" else final, "hash": vhash(img),
                        "where": "sharing image" if meta else "header"})
        except Exception:
            failed += 1
            continue
    if not out and failed:
        return "RETRY", [], f"the site's {failed} logo file(s) wouldn't download; run again"
    if not out:
        return "NO-SITE", [], "no logo found on the live site"
    return "LIVE", out, final

def source_date(url):
    """Best guess at when a logo's source was published: a /YYYY/MM/ path or a PDF's creation date."""
    m = fl.re.search(r"/(20\d\d)/(\d\d)/", url)
    if m:
        return dt.date(int(m.group(1)), int(m.group(2)), 1)
    if url.lower().split("?")[0].endswith(".pdf"):
        try:
            p = f"{OUT}/src.pdf"; open(p, "wb").write(fl.get(url, 40_000_000)[0])
            info = subprocess.run(["pdfinfo", p], capture_output=True, text=True).stdout
            m = fl.re.search(r"CreationDate:\s+\w+ (\w+)\s+(\d+) [\d:]+ (\d{4})", info)
            if m:
                return dt.datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%b %d %Y").date()
        except Exception:
            pass
    return None

def check(slug, orgs, state):
    o = orgs[slug]; lg = o.get("logo") or {}
    if not lg.get("file"):
        return slug, {"result": "MISSING"}
    if lg.get("pinned"):
        return slug, {"result": "pinned", "file": lg["file"]}
    saved = Image.open(f"{REPO}/shared/logos/{lg['file']}")
    sh = vhash(saved)
    kind, cands, note = live_logos(slug, o)
    if kind != "LIVE":
        prev = state.get(slug, {})
        conf = prev.get("confirmed")
        if conf and prev.get("file") == lg["file"] and (dt.date.today() - dt.date.fromisoformat(conf["date"])).days <= 365:
            return slug, {"result": "confirmed", "file": lg["file"], "note": f"{note}; matches {conf['source']} ({conf['date']})",
                          "confirmed": conf}
        src = lg.get("source_url") or ""
        when = source_date(src)
        stale = (when is None and ("cu-leadership-feed" in src or src.lower().endswith(".pdf"))) or \
            (when is not None and (dt.date.today() - when).days > 548)
        return slug, {"result": "STALE-RISK" if stale else kind, "file": lg["file"], "source": src,
                      "note": note + (f"; source dated {when}" if when else "; source undated") +
                      ("; confirm against a source from the last 12 months" if stale else "")}
    header = [c for c in cands if c["where"] == "header"]
    if any(close(sh, c["hash"]) for c in header) or (not header and any(close(sh, c["hash"]) for c in cands)):
        return slug, {"result": "current", "file": lg["file"]}
    only_meta = any(close(sh, c["hash"]) for c in cands)
    # what the site's header shows now, kept so a reviewed flag stays settled until the header changes
    live_sig = [[f"{c['hash'][0]:064x}", c["hash"][1]] for c in (header or cands)]
    prev = state.get(slug, {})
    seen = [(int(x[0], 16), x[1]) for x in prev.get("live_sig") or [] if isinstance(x, (list, tuple)) and len(x) == 2]
    unchanged = seen and all(any(close(c["hash"], p) for p in seen) for c in (header or cands))
    if prev.get("ok") and prev.get("file") == lg["file"] and unchanged:
        return slug, {"result": "reviewed", "file": lg["file"], "note": prev.get("note"), "live_sig": prev["live_sig"], "ok": True}
    why = "matches only the site's sharing image, which can be years old; the header shows something else" if only_meta else note
    return slug, {"result": "MISMATCH", "file": lg["file"], "live": cands, "live_sig": live_sig, "site": why}

def sheet(rows):
    from PIL import ImageDraw
    names = []
    for k in range(0, len(rows), 8):
        part = rows[k:k + 8]
        W, H = 200, 80
        img = Image.new("RGB", (W * 7 + 180, H * len(part)), (150, 150, 150)); d = ImageDraw.Draw(img)
        for r, (slug, res) in enumerate(part):
            y = r * H
            d.line([(0, y), (img.width, y)], fill=(100, 100, 100))
            d.text((4, y + 4), slug[:28], fill="black")
            files = [(f"{REPO}/shared/logos/{res['file']}", "SAVED")] + [(c["file"], f"site {i + 1}") for i, c in enumerate(res["live"][:6])]
            for i, (f, label) in enumerate(files):
                try:
                    im = Image.open(f).convert("RGBA"); im.thumbnail((W - 14, H - 20))
                    img.paste(im, (180 + i * W + 6, y + 3), im)
                except Exception:
                    pass
                d.text((180 + i * W + 6, y + H - 14), label, fill=(120, 0, 0) if label == "SAVED" else "black")
        p = f"{OUT}/sheet-{k // 8 + 1}.png"; img.save(p); names.append(p)
    return names

def main(argv):
    orgs = json.load(open(f"{REPO}/shared/organizations.json"))["organizations"]
    state = json.load(open(STATE)) if os.path.exists(STATE) else {}
    if argv[:1] == ["--confirm"]:
        # the site can't be compared (blocked, no website): record a recent source that shows this same logo
        slug = argv[1]; src = argv[argv.index("--source") + 1]; date = argv[argv.index("--date") + 1]
        lg = orgs[slug]["logo"]
        state[slug] = {**state.get(slug, {}), "file": lg["file"], "confirmed": {"source": src, "date": date},
                       "result": "confirmed", "checked": dt.date.today().isoformat()}
        json.dump(state, open(STATE, "w"), indent=1, sort_keys=True)
        print(f"{slug}: confirmed current by {src} ({date})"); return
    if argv[:1] == ["--ok"]:
        slug = argv[1]; note = argv[argv.index("--note") + 1] if "--note" in argv else ""
        _, res = check(slug, orgs, {})
        if res["result"] != "MISMATCH":
            print(f"{slug}: {res['result']} (nothing to mark)"); return
        state[slug] = {"ok": True, "file": res["file"], "live_sig": res["live_sig"], "note": note,
                       "reviewed": dt.date.today().isoformat()}
        json.dump(state, open(STATE, "w"), indent=1, sort_keys=True)
        print(f"{slug}: marked reviewed ({note})"); return
    if argv:
        slugs = argv
    else:
        feed = json.load(open(f"{REPO}/feeds/exec-jobs/feed.json"))
        names = {r["organization"] for r in feed}
        slugs = sorted(s for s, o in orgs.items() if o["name"] in names)
    os.makedirs(OUT, exist_ok=True)
    with cf.ThreadPoolExecutor(8) as ex:
        res = dict(ex.map(lambda s: check(s, orgs, state), slugs))
    today = dt.date.today().isoformat()
    for s, r in res.items():
        keep = {k: v for k, v in r.items() if k in ("result", "file", "note", "live_sig", "ok", "confirmed")}
        state[s] = {**state.get(s, {}), **keep, "checked": today}
        if r["result"] == "MISMATCH":
            state[s].pop("ok", None)
    json.dump(state, open(STATE, "w"), indent=1, sort_keys=True)
    from collections import Counter
    print(dict(Counter(r["result"] for r in res.values())))
    bad = [(s, r) for s, r in sorted(res.items()) if r["result"] == "MISMATCH"]
    if bad:
        print("review:", " ".join(sheet(bad)))
    for s, r in sorted(res.items()):
        if r["result"] in ("MISMATCH", "STALE-RISK", "RETRY", "BLOCKED", "NO-SITE", "MISSING"):
            print(f"  {r['result']:8} {s}: {r.get('note') or r.get('site') or ''}  source={str(r.get('source') or '')[:80]}")

if __name__ == "__main__":
    main(sys.argv[1:])
