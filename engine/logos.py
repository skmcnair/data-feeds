#!/usr/bin/env python3
"""Logos for the shared organization list (shared/organizations.json).

    python3 engine/logos.py add --slug S --file PATH [--source-url URL] [--style color|white] [--pin]
    python3 engine/logos.py check [--slug S] [--force]
    python3 engine/logos.py accept --slug S [--style color|white]
    python3 engine/logos.py keep --slug S

add     standardizes a logo (trimmed, transparent PNG, at most 360 px tall)
        and saves it as a new version, shared/logos/<slug>-v<N>.png. The
        original goes in shared/logos/source/. --source-url is where the image
        lives on the organization's site; the weekly check re-reads it.
        --pin marks a logo the brand gave Sean: it is never replaced.
check   (weekly, on GitHub) re-downloads every logo from its source and
        compares the picture itself. A change has to show on two checks at
        least 6 days apart before it is flagged, so one-off holiday logos are
        skipped; then Claude reviews it. --force flags a change at once
        (used after a rebrand or completed merger).
accept  promotes the pending new logo after Claude has confirmed it.
keep    keeps the current logo and clears the flag (the same new picture
        won't be flagged again).

A new version gets a new file name, so boards show it straight away instead
of a cached copy, and the old file stays for rollback.
"""
import argparse
import datetime as dt
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

from PIL import Image

from common import ROOT, load_orgs, logo_url, orgs_path, save_json, today_et

LOGOS = ROOT / "shared" / "logos"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"


def load_image(path):
    path = Path(path)
    data = path.read_bytes()[:400].lstrip()
    if path.suffix.lower() == ".svg" or data.startswith(b"<svg") or data.startswith(b"<?xml"):
        out = Path(tempfile.mkdtemp()) / "logo.png"
        subprocess.run(["rsvg-convert", "-h", "400", "-o", str(out), str(path)], check=True)
        path = out
    img = Image.open(path)
    img.load()
    return img.convert("RGBA")


def standardize(img):
    alpha = img.getchannel("A")
    if alpha.getextrema()[0] < 250:  # has transparency: trim to what's visible
        box = alpha.point(lambda a: 255 if a > 8 else 0).getbbox()
    else:  # no transparency: trim near-white margins
        rgb = img.convert("RGB")
        mask = rgb.point(lambda v: 255 if v < 245 else 0).convert("L")
        box = mask.getbbox()
    if box:
        img = img.crop(box)
    if img.height > 360:
        w = round(img.width * 360 / img.height)
        img = img.resize((w, 360), Image.LANCZOS)
    return img


def fingerprint(img):
    bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
    g = Image.alpha_composite(bg, img).convert("L").resize((17, 16), Image.LANCZOS)
    px = list(g.getdata())
    bits = 0
    for y in range(16):
        for x in range(16):
            bits = (bits << 1) | (px[y * 17 + x] > px[y * 17 + x + 1])
    return f"{bits:064x}", round(img.width / max(img.height, 1), 3)


def different(fp_a, ar_a, fp_b, ar_b):
    dist = bin(int(fp_a, 16) ^ int(fp_b, 16)).count("1")
    return dist > 30 or abs(ar_a - ar_b) > 0.15 * max(ar_a, ar_b)


def download(url, dest):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "image/*,*/*"})
    with urllib.request.urlopen(req, timeout=40) as r:
        dest.write_bytes(r.read())
    return dest


def save_version(orgs, slug, src_path, source_url=None, style=None, pin=None):
    o = orgs["organizations"][slug]
    old = o.get("logo") or {}
    n = int(old.get("version", 0)) + 1
    img = standardize(load_image(src_path))
    name = f"{slug}-v{n}.png"
    LOGOS.mkdir(parents=True, exist_ok=True)
    img.save(LOGOS / name, "PNG", optimize=True)
    ext = Path(src_path).suffix.lower() or ".img"
    (LOGOS / "source").mkdir(exist_ok=True)
    shutil.copyfile(src_path, LOGOS / "source" / f"{slug}-v{n}{ext}")
    fp, ar = fingerprint(img)
    o["logo"] = {
        "file": name, "version": n,
        "style": style or old.get("style") or "color",
        "source_url": source_url if source_url is not None else old.get("source_url", ""),
        "fingerprint": fp, "aspect": ar,
        "pinned": bool(pin) if pin is not None else bool(old.get("pinned")),
        "added": today_et().isoformat(), "checked": today_et().isoformat(),
        "previous": ([old["file"]] + old.get("previous", []))[:5] if old.get("file") else old.get("previous", []),
        "ignored": old.get("ignored", []),
    }
    pend = LOGOS / "pending" / f"{slug}.png"
    if pend.exists():
        pend.unlink()
    return o["logo"]


def check(orgs, only=None, force=False):
    today = today_et()
    report = []
    for slug, o in orgs["organizations"].items():
        if only and slug != only:
            continue
        lg = o.get("logo") or {}
        if not lg.get("file") or not lg.get("source_url"):
            continue
        tmp = Path(tempfile.mkdtemp()) / ("source" + Path(lg["source_url"].split("?")[0]).suffix)
        try:
            img = standardize(load_image(download(lg["source_url"], tmp)))
        except Exception as e:
            lg["check_errors"] = int(lg.get("check_errors", 0)) + 1
            if lg["check_errors"] >= 2:
                lg["review"] = {"reason": "the logo's web address no longer works; find its new source",
                                "since": today.isoformat(), "error": str(e)[:200]}
            report.append(f"{slug}: could not read logo ({e})")
            continue
        lg["check_errors"] = 0
        fp, ar = fingerprint(img)
        lg["checked"] = today.isoformat()
        if not different(lg["fingerprint"], lg["aspect"], fp, ar) or fp in lg.get("ignored", []):
            lg.pop("pending", None)
            continue
        (LOGOS / "pending").mkdir(parents=True, exist_ok=True)
        pend_file = f"pending/{slug}.png"
        img.save(LOGOS / pend_file, "PNG")
        src_copy = LOGOS / "pending" / f"{slug}-source{tmp.suffix}"
        shutil.copyfile(tmp, src_copy)
        pend = lg.get("pending")
        if lg.get("pinned"):
            lg["review"] = {"reason": "their site shows a new logo; yours is pinned, so it was not replaced",
                            "pending_file": pend_file, "since": today.isoformat(), "fingerprint": fp}
        elif force or (pend and not different(pend["fingerprint"], pend["aspect"], fp, ar)
                       and (today - dt.date.fromisoformat(pend["first_seen"])).days >= 6):
            lg["review"] = {"reason": "a new logo has been on their site for a week" if not force
                            else "logo changed after a rebrand or merger",
                            "pending_file": pend_file, "since": today.isoformat(), "fingerprint": fp}
        elif not pend or different(pend["fingerprint"], pend["aspect"], fp, ar):
            lg["pending"] = {"fingerprint": fp, "aspect": ar, "first_seen": today.isoformat(),
                             "file": pend_file}
        report.append(f"{slug}: logo looks different" + (" - flagged for review" if lg.get("review") else
                                                         " - will confirm next week"))
    for slug, o in orgs["organizations"].items():
        if only and slug != only:
            continue
        if (o.get("logo") or {}).get("check_requested") and o["logo"].get("file") and not o["logo"].get("review"):
            o["logo"]["review"] = {"reason": "logo check requested after news (rebrand or merger)",
                                   "since": today.isoformat()}
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["add", "check", "accept", "keep"])
    ap.add_argument("--slug")
    ap.add_argument("--file")
    ap.add_argument("--source-url")
    ap.add_argument("--style", choices=["color", "white"])
    ap.add_argument("--pin", action="store_true")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    orgs = load_orgs()
    if a.cmd != "check" and (not a.slug or a.slug not in orgs["organizations"]):
        sys.exit(f"unknown organization slug {a.slug!r}; add it to the change file's organizations first")
    if a.cmd == "add":
        if not a.file:
            sys.exit("--file is required")
        lg = save_version(orgs, a.slug, a.file, a.source_url, a.style, a.pin if a.pin else None)
        print(f"{a.slug}: saved {lg['file']} ({lg['style']}) -> {logo_url(orgs['organizations'][a.slug])}")
    elif a.cmd == "check":
        for line in check(orgs, a.slug, a.force):
            print(line)
    elif a.cmd == "accept":
        lg = orgs["organizations"][a.slug].get("logo") or {}
        pend = LOGOS / "pending" / f"{a.slug}.png"
        srcs = sorted((LOGOS / "pending").glob(f"{a.slug}-source*"))
        if not pend.exists():
            sys.exit("no pending logo for this organization")
        path = srcs[0] if srcs else pend
        lg2 = save_version(orgs, a.slug, path, lg.get("source_url"), a.style)
        for x in srcs:
            x.unlink()
        lg2.pop("review", None)
        print(f"{a.slug}: now using {lg2['file']}")
    elif a.cmd == "keep":
        lg = orgs["organizations"][a.slug].get("logo") or {}
        for fp in ((lg.get("pending") or {}).get("fingerprint"), (lg.get("review") or {}).get("fingerprint")):
            if fp and fp not in lg.setdefault("ignored", []):
                lg["ignored"].append(fp)
        for k in ("pending", "review", "check_requested"):
            lg.pop(k, None)
        for x in (LOGOS / "pending").glob(f"{a.slug}*"):
            x.unlink()
        print(f"{a.slug}: keeping {lg.get('file')}")
    save_json(orgs_path(), orgs)


if __name__ == "__main__":
    main()
