#!/usr/bin/env python3
"""Find a credit union's NCUA charter, headquarters and current assets.

    python3 feeds/leadership-moves/tools/cu_lookup.py "Jeanne D'Arc Credit Union|MA" "Lake Trust" cu:67352

Each argument is a name (optionally "|ST" to limit to a state) or cu:<charter>.
Prints one JSON object keyed by argument. For a name it lists up to 5
candidates, best first; pick the one whose city/state matches the story.
Each candidate has: charter, ncua_name, legal_name_guess (NCUA name + FCU/CU
type, title case; confirm the exact legal name on the credit union's site),
city, state, assets_m (NCUA's latest call report, $ millions), members,
quarter (call report date), ceo (as NCUA lists it: legal name), website,
status (Active/Inactive), source_url.

Names come from NCUA's quarterly call report files (FOICU.txt and
TradeNames.txt, cached in /tmp/ncua-cr); figures come from NCUA's live
credit union record, which has the newest quarter. Credit unions with no live
record (privately insured ones such as Interra or Plus) fall back to the
quarterly file's figures (FS220.txt), with source_url pointing at that file.
"""
import datetime as dt, io, json, os, re, subprocess, sys, zipfile

UA = "Mozilla/5.0 (data-feeds leadership-moves)"
CACHE = "/tmp/ncua-cr"
STOP = {"credit", "union", "federal", "fcu", "cu", "the", "of", "inc", "community", "financial"}


def curl(url, out=None, tries=3):
    cmd = ["curl", "-sS", "--fail", "-L", "-m", "180", "--retry", str(tries), "--retry-delay", "5",
           "--retry-all-errors", "-A", UA, url]
    if out:
        subprocess.run(cmd + ["-o", out], check=True)
        return None
    return subprocess.run(cmd, check=True, capture_output=True).stdout


def latest_zip():
    os.makedirs(CACHE, exist_ok=True)
    for f in sorted(os.listdir(CACHE), reverse=True):
        if f.endswith(".zip"):
            return os.path.join(CACHE, f)
    today = dt.date.today()
    y, q = today.year, (today.month - 1) // 3  # last finished quarter
    for _ in range(6):
        if q == 0:
            y, q = y - 1, 4
        mm = {1: "03", 2: "06", 3: "09", 4: "12"}[q]
        path = os.path.join(CACHE, f"call-report-data-{y}-{mm}.zip")
        try:
            curl(f"https://ncua.gov/files/publications/analysis/call-report-data-{y}-{mm}.zip", path, tries=1)
            if zipfile.is_zipfile(path):
                return path
        except subprocess.CalledProcessError:
            pass
        if os.path.exists(path):
            os.remove(path)
        q -= 1
    raise SystemExit("could not download any NCUA call report file")


def rows(z, name):
    import csv
    with z.open(name) as fh:
        return list(csv.DictReader(io.TextIOWrapper(fh, encoding="latin-1")))


def norm(s):
    s = re.sub(r"[^a-z0-9 ]", " ", (s or "").lower().replace("&", " and ").replace("'", ""))
    return [w for w in s.split() if w not in STOP]


def title_case(s):
    small = {"of", "and", "the", "for"}
    out = []
    for i, w in enumerate((s or "").split()):
        lw = w.lower()
        out.append(lw if lw in small and i else (w if len(w) <= 4 and w.isupper() and not w.isalpha() else lw.capitalize()))
    return " ".join(out)


def details(charter):
    try:
        d = json.loads(curl(f"https://mapping.ncua.gov/api/CreditUnionDetails/GetCreditUnionDetails/{charter}"))
    except Exception as e:  # report and carry on
        return {"error": str(e)}
    cycles = [dt.datetime.strptime(v, "%m/%d/%Y").date() for v in (d.get("callReportCycleDates") or {}).values()]
    assets = d.get("creditUnionAssets")
    return {
        "assets_m": round(int(assets) / 1e6, 1) if assets and str(assets).isdigit() else None,
        "members": d.get("creditUnionNom"),
        "quarter": max(cycles).isoformat() if cycles else "",
        "city": d.get("creditUnionCity"), "state": d.get("creditUnionState"),
        "ceo": d.get("creditUnionCeo"), "website": d.get("creditUnionWebsite"),
        "status": d.get("creditUnionStatus"),
        "source_url": f"https://mapping.ncua.gov/api/CreditUnionDetails/GetCreditUnionDetails/{charter}",
    }


def main(args):
    z = zipfile.ZipFile(latest_zip())
    foicu = rows(z, "FOICU.txt")
    trade = {}
    for r in rows(z, "TradeNames.txt"):
        trade.setdefault(r["CU_NUMBER"], []).append(r["TradeName"])
    by_charter = {r["CU_NUMBER"]: r for r in foicu}
    fs220 = {r["CU_NUMBER"]: r for r in rows(z, "FS220.txt")}
    zip_name = os.path.basename(z.filename)
    zip_url = "https://ncua.gov/files/publications/analysis/" + zip_name
    out = {}
    for arg in args:
        if arg.startswith("cu:"):
            ch = arg[3:]
            r = by_charter.get(ch)
            cands = [r] if r else []
            if not r:
                cands = [{"CU_NUMBER": ch, "CU_NAME": "", "CU_TYPE": "", "CITY": "", "STATE": ""}]
        else:
            name, _, st = arg.partition("|")
            want = norm(name)
            scored = []
            for r in foicu:
                if st and r["STATE"].upper() != st.strip().upper():
                    continue
                best = 0.0
                for cand in [r["CU_NAME"]] + trade.get(r["CU_NUMBER"], []):
                    have = norm(cand)
                    if not have or not want:
                        continue
                    if have == want:
                        best = max(best, 1.0)
                        continue
                    common = len(set(have) & set(want))
                    best = max(best, common / max(len(set(have) | set(want)), 1))
                if best >= 0.5:
                    scored.append((best, r))
            scored.sort(key=lambda x: -x[0])
            cands = [r for _, r in scored[:5]]
        res = []
        for r in cands:
            ch = r["CU_NUMBER"]
            kind = {"1": "Federal Credit Union", "2": "Credit Union"}.get(str(r.get("CU_TYPE", "")).strip(), "Credit Union")
            item = {"charter": ch, "ncua_name": r.get("CU_NAME", ""),
                    "legal_name_guess": title_case(r.get("CU_NAME", "")) if "CREDIT UNION" in r.get("CU_NAME", "").upper()
                    else (title_case(r.get("CU_NAME", "")) + " " + kind).strip(),
                    "trade_names": trade.get(ch, [])}
            item.update(details(ch))
            if item.get("assets_m") is None and ch in fs220:
                # Not in NCUA's live record (e.g. privately insured): use the quarterly file.
                f = fs220[ch]
                cyc = dt.datetime.strptime(f["CYCLE_DATE"].split()[0], "%m/%d/%Y").date()
                item.update({"assets_m": round(int(f["ACCT_010"]) / 1e6, 1) if f.get("ACCT_010") else None,
                             "members": f.get("ACCT_083"), "quarter": cyc.isoformat(),
                             "city": r.get("CITY"), "state": r.get("STATE"), "source_url": zip_url,
                             "note": "from NCUA's quarterly call report file (no live NCUA record)"})
                item.pop("error", None)
            res.append(item)
        out[arg] = res
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
