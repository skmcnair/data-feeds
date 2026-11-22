#!/usr/bin/env python3
"""Latest regulator figures for the mergers feed (ported from cu-merger-feed's
scripts/regulators.py, which the old tracker used).

    python3 feeds/mergers/tools/sizes.py cu:<charter> bank:<FDIC cert> ...

Prints one JSON object keyed by each argument:
  quarter (call report date), assets_m, members, branches (member-service
  locations in the CU's current NCUA profile; FDIC offices for banks),
  states (branch states, most branches first), city, state, ceo ("First Last"),
  state_counts (branches per state), status (NCUA Active/Inactive), source_url.

Credit unions: NCUA's credit union details record plus the current profile
PDF (needs pdftotext: apt-get install -y poppler-utils). Banks: FDIC BankFind
institutions and locations. Exits 1 if any lookup failed (that entry has
"error"); retry it, then fall back to the latest NCUA quarterly data zip.
"""
import collections, json, os, re, subprocess, sys, tempfile, time

UA = "Mozilla/5.0 (data-feeds mergers)"


def fetch(url, out):
    subprocess.run(["curl", "-sS", "--fail", "-L", "-m", "180", "--retry", "3", "--retry-delay", "5",
                    "--retry-all-errors", "-A", UA, url, "-o", out], check=True)


def city_case(s):
    s = " ".join((s or "").split())
    return " ".join(w[:1].upper() + (w[1:].lower() if w.isupper() or w.islower() else w[1:]) for w in s.split())


def person(n):
    n = " ".join((n or "").split())
    if not n:
        return None
    if "," in n:
        last, first = [x.strip() for x in n.split(",", 1)]
        n = f"{first} {last}"
    def cap(w):  # O'BRIEN -> O'Brien, JR. -> Jr., SMITH-JONES -> Smith-Jones; keeps II/III/IV
        if not w.isupper() or w in ("II", "III", "IV") or len(w) < 2:
            return w
        return re.sub(r"[A-Za-z]+", lambda m: m.group(0).capitalize(), w)
    words = [cap(w) for w in n.split()]
    kept = [w for w in words if not re.fullmatch(r"[A-Za-z]\.?", w)]  # drop middle initials
    return " ".join(kept if len(kept) >= 2 else words)


STATE_CODES = {"Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR", "California": "CA", "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE", "District of Columbia": "DC", "Florida": "FL", "Georgia": "GA", "Hawaii": "HI", "Idaho": "ID", "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS", "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD", "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS", "Missouri": "MO", "Montana": "MT", "Nebraska": "NE", "Nevada": "NV", "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM", "New York": "NY", "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK", "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC", "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX", "Utah": "UT", "Vermont": "VT", "Virginia": "VA", "Washington": "WA", "West Virginia": "WV", "Wisconsin": "WI", "Wyoming": "WY", "Puerto Rico": "PR", "Guam": "GU", "Virgin Islands": "VI", "American Samoa": "AS", "Northern Mariana Islands": "MP"}


def profile_sites(text):
    """Member-service sites (Branch or Corporate Office) and their states."""
    blocks = re.split(r"\n\s*Site Type\s+Site Name\s+Physical Address\s*\n", text)[1:]
    n, states = 0, collections.Counter()
    for b in blocks:
        first = b.split("\n", 1)[0]
        m = re.match(r"\s*(Branch Office|Corporate Office|Other)\s{2,}", first)
        if not m or m.group(1) == "Other":
            continue
        f = re.search(r"Site Functions\s+(.+)", b)
        if f and "Member Services" in f.group(1):
            n += 1
            st = re.search(r"\bST\s+([A-Za-z .]+?)\s{2,}Zip", b.split("Mailing Address")[0])
            if st:
                name = st.group(1).strip()
                states[STATE_CODES.get(name, name if len(name) == 2 else "??")] += 1
    return n, states


def ncua(charter, tmp):
    det_path = os.path.join(tmp, f"cu{charter}.json")
    fetch(f"https://mapping.ncua.gov/api/CreditUnionDetails/GetCreditUnionDetails/{charter}", det_path)
    det = json.load(open(det_path))
    if det.get("isError"):
        raise RuntimeError(det.get("errorMessage") or "NCUA returned an error")
    cur = det["profileCycleDates"]["Current profile"].split()[0]
    m, d, y = (int(x) for x in cur.split("/"))
    prof = os.path.join(tmp, f"cu{charter}-profile.json")
    fetch(f"https://mapping.ncua.gov/api/CreditUnionDetails/GetDownloadProfile/{charter}"
          f"?isCorpCU=false&cycleDate={m:02d}/{d:02d}/{y}&isSnapshot=false", prof)
    pdf = os.path.join(tmp, f"cu{charter}.pdf")
    open(pdf, "wb").write(bytes(json.load(open(prof))["fileContents"]))
    subprocess.run(["pdftotext", "-layout", pdf, pdf[:-4] + ".txt"], check=True)
    n, states = profile_sites(open(pdf[:-4] + ".txt", errors="ignore").read())
    if n == 0:
        raise RuntimeError("no member-service sites parsed from the NCUA profile")
    return {"type": "cu", "id": charter, "name": det.get("creditUnionName"), "status": det.get("creditUnionStatus"),
            "quarter": f"{y:04d}-{m:02d}-{d:02d}", "assets_m": round(int(det["creditUnionAssets"]) / 1e6, 1),
            "members": int(det["creditUnionNom"]), "branches": n, "states": [s for s, _ in states.most_common()],
            "state_counts": dict(states.most_common()),
            "city": city_case(det.get("creditUnionCity")), "state": det.get("creditUnionState"),
            "ceo": person(det.get("creditUnionCeo")), "website": det.get("creditUnionWebsite"),
            "source_url": f"https://mapping.ncua.gov/api/CreditUnionDetails/GetCreditUnionDetails/{charter}"}


def fdic(cert, tmp):
    p = os.path.join(tmp, f"bank{cert}.json")
    fetch(f"https://api.fdic.gov/banks/institutions?filters=CERT:{cert}&fields=NAME,CITY,STALP,REPDTE,ASSET,OFFICES,ACTIVE", p)
    data = json.load(open(p))["data"]
    if not data:
        raise RuntimeError("FDIC returned no institution for this CERT")
    r = data[0]["data"]
    time.sleep(2)
    lp = os.path.join(tmp, f"bank{cert}-loc.json")
    fetch(f"https://api.fdic.gov/banks/locations?filters=CERT:{cert}&fields=STALP&limit=500", lp)
    states = collections.Counter(x["data"]["STALP"] for x in json.load(open(lp))["data"])
    m, d, y = (int(x) for x in r["REPDTE"].split("/"))
    return {"type": "bank", "id": cert, "name": r.get("NAME"), "active": bool(r.get("ACTIVE")),
            "quarter": f"{y:04d}-{m:02d}-{d:02d}", "assets_m": round(int(r["ASSET"]) / 1000, 1), "members": None,
            "branches": int(r["OFFICES"]), "states": [s for s, _ in states.most_common()],
            "state_counts": dict(states.most_common()),
            "city": city_case(r.get("CITY")), "state": r.get("STALP"), "ceo": None,
            "source_url": f"https://api.fdic.gov/banks/institutions?filters=CERT:{cert}"}


def main(args):
    if not args:
        print(__doc__)
        return 2
    out, failed = {}, False
    with tempfile.TemporaryDirectory() as tmp:
        for a in args:
            kind, _, ident = a.partition(":")
            try:
                out[a] = ncua(int(ident), tmp) if kind == "cu" else fdic(int(ident), tmp) if kind == "bank" else None
                if out[a] is None:
                    raise ValueError("use cu:<charter> or bank:<FDIC cert>")
            except Exception as e:
                out[a] = {"error": str(e)}
                failed = True
    print(json.dumps(out, indent=1))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
