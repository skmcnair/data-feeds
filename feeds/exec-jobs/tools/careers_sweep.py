#!/usr/bin/env python3
"""Read the public careers boards of every credit union at $500M and up, and keep VP-and-up jobs.

    python3 feeds/exec-jobs/tools/careers_sweep.py                 # sweep every mapped credit union
    python3 feeds/exec-jobs/tools/careers_sweep.py --charter 68433 --charter 24941
    python3 feeds/exec-jobs/tools/careers_sweep.py --map 24941 --url https://<job board URL>
    python3 feeds/exec-jobs/tools/careers_sweep.py --unmap 24941 --status blocked --note "Cloudflare check"
    python3 feeds/exec-jobs/tools/careers_sweep.py --list fallback    # credit unions we can't read directly

The list of credit unions and their boards is tools/careers_sites.json (public information only:
NCUA charter, name, state, website, the public careers board and which job platform it runs on).
Statuses: mapped (read every run), blocked (the site refuses automated reading: a bot check or
403), unsupported (a platform whose job list only loads through a signed-in or token-based call,
e.g. Paycom), unmapped (no board found yet).

Writes /tmp/exec-jobs-careers.json and prints a summary:
  listings  every VP-and-up job on a mapped board (charter, cu, platform, title, location, posted,
            url, pay)
  new       listings whose url (or credit union + title) no tracked item has
  gone      Open tracked items from a board read fine today that no longer lists them
  errors    boards that failed today (their items are not reported as gone)
  fallback  today's share of blocked/unsupported/unmapped credit unions to look up on job sites
            (rotation: every one comes round about every two weeks; $5B+ ones every day)

Only public pages and the same unauthenticated calls a visitor's browser makes are used. Never
log in, never use tokens or keys taken from a page, never get around a bot check or a 403.
"""
import argparse, concurrent.futures as cf, datetime as dt, html, json, os, re, subprocess, sys, time, urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
FEED = os.path.join(REPO, "feeds", "exec-jobs")
SITES = os.path.join(HERE, "careers_sites.json")
OUT = "/tmp/exec-jobs-careers.json"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
BLOCK = re.compile(r"<title>Just a moment|Attention Required|cf-browser-verification|Request unsuccessful|Incapsula incident", re.I)

EXEC = re.compile(r"\b(vice[\s-]*president|v\.?p\.?|svp|evp|chief\b.*\bofficer|president|ceo|cfo|coo|cio|cto|clo|cro|cmo|cxo|"
                  r"chro|cdo|ciso|general\s+manager)\b", re.I)
NOT_EXEC = re.compile(r"\b(assistant\s+vice[\s-]*president|associate\s+vice[\s-]*president|avp|asst\.?\s*v\.?p|"
                      r"executive\s+assistant|administrative\s+assistant|assistant\s+to|admin\w*\s+to|ea\s+to|"
                      r"coordinator|intern(ship)?|scheduler|analyst|specialist|representative|teller)\b", re.I)


def is_exec(title):
    t = title or ""
    return bool(EXEC.search(t)) and not NOT_EXEC.search(t) and len(t) <= 140


def curl(url, method="GET", data=None, headers=None, t=30):
    cmd = ["curl", "-sS", "-L", "--compressed", "--max-time", str(t), "-A", UA, "-w", "\n%{http_code}"]
    for h in headers or []:
        cmd += ["-H", h]
    if method == "POST":
        cmd += ["-X", "POST", "--data-binary", data or ""]
    p = subprocess.run(cmd + [url], capture_output=True)
    body = p.stdout.decode("utf-8", "replace")
    body, _, code = body.rpartition("\n")
    if p.returncode != 0 or not code.startswith("2"):
        raise RuntimeError(f"HTTP {code or '?'} {p.stderr.decode()[:100].strip()}")
    if BLOCK.search(body[:4000]):
        raise RuntimeError("bot check (blocked; never get around it)")
    return body


def text(s):
    s = re.sub(r"<script.*?</script>|<style.*?</style>", " ", s or "", flags=re.S | re.I)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def iso(s):
    s = (s or "").strip()
    m = re.match(r"(\d{4}-\d\d-\d\d)", s)
    if m:
        return m.group(1)
    for f in ("%m/%d/%Y", "%b %d, %Y", "%B %d, %Y"):
        try:
            return dt.datetime.strptime(s, f).date().isoformat()
        except ValueError:
            pass
    return ""


def rel_days(s):
    """Workday's "Posted 10 Days Ago" -> a date; "30+ Days Ago" -> blank."""
    s = (s or "").lower()
    today = dt.date.today()
    if "today" in s:
        return today.isoformat()
    if "yesterday" in s:
        return (today - dt.timedelta(days=1)).isoformat()
    m = re.search(r"posted (\d+) days? ago", s)
    return (today - dt.timedelta(days=int(m.group(1)))).isoformat() if m else ""


# ---------- platform readers: each returns [{title, url, location, posted, pay}] ----------

def r_workday(url):
    m = re.match(r"(https?://([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com)/(?:[a-z]{2}-[A-Z]{2}/)?([A-Za-z0-9_\-]+)", url)
    base, tenant, _, site = m.groups()
    api = f"{base}/wday/cxs/{tenant}/{site}/jobs"
    out, seen = [], set()
    for q in ("vice president", "VP", "chief", "president", "SVP", "EVP"):
        for off in range(0, 100, 20):
            d = json.loads(curl(api, "POST", json.dumps({"appliedFacets": {}, "limit": 20, "offset": off, "searchText": q}),
                                ["Content-Type: application/json", "Accept: application/json"]))
            posts = d.get("jobPostings") or []
            for p in posts:
                u = f"{base}/{site}{p.get('externalPath', '')}"
                if u not in seen:
                    seen.add(u)
                    out.append({"title": p.get("title", ""), "url": u, "location": p.get("locationsText", ""),
                                "posted": rel_days(p.get("postedOn"))})
            if len(posts) < 20 or off + 20 >= (d.get("total") or 0):
                break
    return out


def r_ultipro(url):
    base = re.match(r"(https?://recruiting2?\.ultipro\.com/[A-Za-z0-9]+/JobBoard/[0-9a-fA-F-]{36})", url).group(1)
    out, skip = [], 0
    while True:
        body = {"opportunitySearch": {"Top": 100, "Skip": skip, "QueryString": "",
                "OrderBy": [{"Value": "postedDateDesc", "PropertyName": "PostedDate", "Ascending": False}],
                "Filters": [{"t": "TermsSearchFilterDto", "fieldName": n, "extra": None, "values": []} for n in (4, 5, 6)]},
                "matchCriteria": {"PreferredJobs": [], "Educations": [], "LicenseAndCertifications": [], "Skills": [],
                                  "hasNoLicenses": False, "SkippedSkills": []}}
        d = json.loads(curl(base + "/JobBoardView/LoadSearchResults", "POST", json.dumps(body),
                            ["Content-Type: application/json; charset=utf-8"]))
        ops = d.get("opportunities") or []
        for o in ops:
            loc = ""
            if o.get("Locations"):
                a = (o["Locations"][0].get("Address") or {})
                loc = ", ".join(x for x in [a.get("City"), (a.get("State") or {}).get("Code")] if x)
            out.append({"title": o.get("Title", ""), "url": f"{base}/OpportunityDetail?opportunityId={o.get('Id')}",
                        "location": loc, "posted": iso(o.get("PostedDate"))})
        skip += 100
        if len(ops) < 100 or skip >= (d.get("totalCount") or 0) or skip > 1000:
            break
    return out


def r_adp(url):
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    cid = q["cid"][0]
    cc = q.get("ccId", ["19000101_000001"])[0]
    out, skip = [], 0
    while True:
        api = (f"https://workforcenow.adp.com/mascsr/default/careercenter/public/events/staffing/v1/job-requisitions"
               f"?cid={cid}&timeStamp={int(time.time() * 1000)}&lang=en_US&iccFlag=yes&eccFlag=yes&locale=en_US&$top=100&$skip={skip}")
        d = json.loads(curl(api))
        reqs = d.get("jobRequisitions") or []
        for r in reqs:
            posted = ""
            for f in (r.get("customFieldGroup") or {}).get("dateFields", []):
                if (f.get("nameCode") or {}).get("codeValue") == "PostingDate":
                    posted = iso(f.get("dateValue"))
            loc = ""
            for l in r.get("requisitionLocations") or []:
                a = ((l.get("address") or {}))
                loc = ", ".join(x for x in [a.get("cityName"), (a.get("countrySubdivisionLevel1") or {}).get("codeValue")] if x)
                break
            pay = ""
            pr = (r.get("payGradeRange") or {})
            lo, hi = (pr.get("minimumRate") or {}).get("amountValue"), (pr.get("maximumRate") or {}).get("amountValue")
            if lo and hi and float(lo) > 1000:
                pay = f"${round(float(lo) / 1000)}K-${round(float(hi) / 1000)}K"
            out.append({"title": r.get("requisitionTitle", ""), "location": loc, "posted": posted, "pay": pay,
                        "url": f"https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?cid={cid}&ccId={cc}&jobId={r.get('itemID')}&lang=en_US"})
        skip += 100
        if len(reqs) < 100 or skip > 1000:
            break
    return out


def r_paylocity(url):
    s = curl(url)
    i = s.find("window.pageData = ")
    if i < 0:
        return []
    raw = s[i + len("window.pageData = "):]
    d = json.JSONDecoder().raw_decode(raw)[0]
    return [{"title": j.get("JobTitle", ""), "url": f"https://recruiting.paylocity.com/Recruiting/Jobs/Details/{j.get('JobId')}",
             "location": j.get("LocationName", ""), "posted": iso(j.get("PublishedDate"))} for j in d.get("Jobs") or []]


def r_applicantpro(url):
    host = urllib.parse.urlsplit(url).netloc
    s = curl(f"https://{host}/jobs/")
    dom = re.search(r"domain_id['\"]?\s*[:=]\s*['\"]?(\d+)", s)
    if not dom:
        return []
    d = json.loads(curl(f"https://{host}/core/jobs/{dom.group(1)}?getParams=%7B%22isInternal%22%3A0%7D"))
    return [{"title": j.get("title", ""), "url": f"https://{host}/jobs/{j.get('id')}.html",
             "location": ", ".join(x for x in [j.get("city"), j.get("abbreviation")] if x), "posted": iso(j.get("startDateRef"))}
            for j in (d.get("data") or {}).get("jobs") or []]


def r_saashr(url):
    m = re.match(r"(https?://secure\d*\.saashr\.com)/ta/(\d+)\.", url)
    base, cid = m.groups()
    d = json.loads(curl(f"{base}/ta/rest/ui/recruitment/companies/%7C{cid}/job-requisitions?offset=0&size=200&sort=desc&ein_id=&lang=en-US"))
    out = []
    for j in d.get("job_requisitions") or []:
        loc = j.get("location") or {}
        out.append({"title": j.get("job_title", ""), "url": f"{base}/ta/{cid}.careers?ShowJob={j.get('id')}",
                    "location": ", ".join(x for x in [loc.get("city"), loc.get("state")] if x), "posted": ""})
    return out


def r_oracle(url):
    m = re.match(r"(https?://[a-z0-9.-]+)/hcmUI/CandidateExperience/([a-z]{2})/sites/([A-Za-z0-9_]+)", url)
    base, lang, site = m.groups()
    out, seen = [], set()
    for q in ("vice president", "chief", "president", "VP"):
        api = (f"{base}/hcmRestApi/resources/latest/recruitingCEJobRequisitions?onlyData=true&expand=requisitionList"
               f"&finder=findReqs;siteNumber={site},keyword={urllib.parse.quote(q)},limit=100")
        d = json.loads(curl(api))
        for it in d.get("items") or []:
            for r in it.get("requisitionList") or []:
                u = f"{base}/hcmUI/CandidateExperience/{lang}/sites/{site}/job/{r.get('Id')}"
                if u not in seen:
                    seen.add(u)
                    out.append({"title": r.get("Title", ""), "url": u, "location": r.get("PrimaryLocation", ""),
                                "posted": iso(r.get("PostedDate"))})
    return out


def r_icims(url):
    host = urllib.parse.urlsplit(url).netloc
    if not host.startswith("careers"):
        return []
    out, seen = [], set()
    for pr in range(0, 8):
        s = curl(f"https://{host}/jobs/search?ss=1&in_iframe=1&pr={pr}")
        found = 0
        for href, inner in re.findall(r'<a[^>]*href="(https://[^"]*/jobs/\d+/[^"]*/job[^"]*)"[^>]*>(.*?)</a>', s, re.S):
            t = text(inner)
            t = re.sub(r"^Title\s*", "", t)
            u = href.replace("?in_iframe=1", "")
            if t and u not in seen:
                seen.add(u)
                found += 1
                out.append({"title": t, "url": u, "location": "", "posted": ""})
        if not found:
            break
    return out


def r_greenhouse(url):
    tok = re.search(r"greenhouse\.io/(?:embed/job_board\?for=)?([a-z0-9]+)", url).group(1)
    d = json.loads(curl(f"https://boards-api.greenhouse.io/v1/boards/{tok}/jobs"))
    return [{"title": j.get("title", ""), "url": j.get("absolute_url", ""), "location": (j.get("location") or {}).get("name", ""),
             "posted": iso(j.get("updated_at"))} for j in d.get("jobs") or []]


def r_lever(url):
    co = re.search(r"jobs\.lever\.co/([a-z0-9-]+)", url).group(1)
    d = json.loads(curl(f"https://api.lever.co/v0/postings/{co}?mode=json"))
    return [{"title": j.get("text", ""), "url": j.get("hostedUrl", ""), "location": (j.get("categories") or {}).get("location", ""),
             "posted": dt.datetime.fromtimestamp(j["createdAt"] / 1000, dt.timezone.utc).date().isoformat() if j.get("createdAt") else ""} for j in d]


def r_bamboohr(url):
    host = urllib.parse.urlsplit(url).netloc
    d = json.loads(curl(f"https://{host}/careers/list"))
    return [{"title": j.get("jobOpeningName", ""), "url": f"https://{host}/careers/{j.get('id')}",
             "location": ", ".join(x for x in [(j.get("location") or {}).get("city"), (j.get("location") or {}).get("state")] if x),
             "posted": ""} for j in d.get("result") or []]


def r_xmlfeed(url):
    """A careers site's own XML job feed (e.g. careers.macu.com/jobs/xml/?rss=true)."""
    s = curl(url)
    out = []
    for blk in re.findall(r"<job>(.*?)</job>|<item>(.*?)</item>", s, re.S):
        b = blk[0] or blk[1]
        g = lambda tag: text(re.sub(r"<!\[CDATA\[|\]\]>", "", (re.search(rf"<{tag}>(.*?)</{tag}>", b, re.S) or [None, ""])[1]))
        out.append({"title": g("title"), "url": g("url") or g("link"), "location": ", ".join(x for x in [g("city"), g("state")] if x),
                    "posted": iso(g("date")) or iso(g("pubDate"))})
    return out


def r_phenom(url):
    """Phenom careers sites (jobs.<cu>.com/us/en): the search page carries its results as JSON."""
    base = re.match(r"(https?://[^/]+/[a-z]{2}/[a-z]{2})", url.rstrip("/") + "/").group(1)
    out, seen = [], set()
    for q in ("vice president", "chief", "president", "VP"):
        s = curl(f"{base}/search-results?keywords={urllib.parse.quote(q)}")
        for m in re.finditer(r'"jobSeqNo"\s*:\s*"([^"]+)"', s):
            seq = m.group(1)
            if seq in seen:
                continue
            lo = max(0, m.start() - 2500)
            win, mid = s[lo:m.end() + 2500], m.start() - lo
            titles = [(abs(t.start() - mid), t.group(1)) for t in re.finditer(r'"title"\s*:\s*"([^"]{4,140})"', win)]
            if not titles:
                continue
            seen.add(seq)
            d = re.search(r'"postedDate"\s*:\s*"([^"]+)"', win)
            c = re.search(r'"cityState"\s*:\s*"([^"]+)"', win)
            out.append({"title": html.unescape(min(titles)[1]), "url": f"{base}/job/{seq}", "location": c.group(1) if c else "",
                        "posted": iso(d.group(1)) if d else ""})
    return out


SKIP_HREF = re.compile(r"mailto:|tel:|javascript:|linkedin\.com|facebook\.com|/leadership|/about-us/(?:our-)?(?:team|leaders)|"
                       r"/board|/executive|/bio|/staff|/news|/blog|press|/team/", re.I)


ANNOUNCE = re.compile(r"welcome|message from|meet (?:our|the)|\bnames\b|announce|appoint|\bjoins\b|promot|letter|"
                      r"from (?:our|the|your) (?:president|ceo)|board of directors", re.I)


def r_generic(url):
    """Any page that lists jobs as links (Paycor, iCIMS iframes, JazzHR, isolved, HRM Direct, simple careers pages)."""
    s = curl(url)
    out, seen = [], set()
    for href, inner in re.findall(r'<a\b[^>]*href=["\']([^"\'#]+)["\'][^>]*>(.*?)</a>', s, re.S | re.I):
        t = text(inner)
        if not t or not is_exec(t) or SKIP_HREF.search(href) or ANNOUNCE.search(t):
            continue
        u = urllib.parse.urljoin(url, html.unescape(href))
        if u not in seen:
            seen.add(u)
            out.append({"title": t, "url": u, "location": "", "posted": "", "generic": True})
    for blob in re.findall(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', s, re.S | re.I):
        try:
            d = json.loads(blob)
        except Exception:
            continue
        for x in (d if isinstance(d, list) else [d]):
            if isinstance(x, dict) and x.get("@type") == "JobPosting" and x.get("title"):
                u = x.get("url") or url
                if u not in seen:
                    seen.add(u)
                    out.append({"title": x["title"], "url": u, "location": "", "posted": iso(x.get("datePosted")), "generic": True})
    return out


READERS = {"workday": r_workday, "ultipro": r_ultipro, "adp": r_adp, "paylocity": r_paylocity, "applicantpro": r_applicantpro,
           "saashr": r_saashr, "oracle": r_oracle, "icims": r_icims, "greenhouse": r_greenhouse, "lever": r_lever,
           "bamboohr": r_bamboohr, "xmlfeed": r_xmlfeed, "phenom": r_phenom}
GENERIC_OK = {"paycor", "jazzhr", "isolved", "hrmdirect", "applicantpool", "easyapply", "careerplug", "hiringthing",
              "breezy", "jobvite", "workable", "rippling", "csod", "clearcompany", "smartrecruiters", "teamtailor",
              "applicantstack", "careers_page"}
UNSUPPORTED = {"paycom", "dayforce", "taleo", "successfactors", "adp_myjobs", "ukgready", "paychex", "hirebridge", "ceipal"}


def read_site(site):
    p, u = site["platform"], site["board_url"]
    fn = READERS.get(p) or (r_generic if p in GENERIC_OK else None)
    if not fn:
        raise RuntimeError(f"no reader for {p}")
    return fn(u)


# ---------- registry ----------

def load_sites():
    return json.load(open(SITES))


def save_sites(d):
    d["sites"].sort(key=lambda x: -x.get("assets_m", 0))
    json.dump(d, open(SITES, "w"), indent=1)


PRIORITY = ["workday", "oracle", "ultipro", "adp", "paylocity", "applicantpro", "saashr", "icims", "greenhouse", "lever",
            "bamboohr", "paycor", "jazzhr", "isolved", "hrmdirect", "applicantpool", "easyapply", "careerplug", "jobvite",
            "workable", "rippling", "csod", "clearcompany", "smartrecruiters", "breezy", "paycom", "dayforce", "taleo",
            "successfactors", "adp_myjobs", "ukgready"]


NOT_CU_BOARD = re.compile(r"lplfinancial|raymondjames|ameriprise|osaic|cetera|trustage|cunamutual|investfinancial|"
                          r"cusonational|lpl\.com|edwardjones|icims\.com/legal|/login", re.I)


def pick_board(ats):
    good = []
    for name, u in ats:
        if NOT_CU_BOARD.search(u):
            continue
        if re.search(r"\.(png|jpe?g|svg|gif|css|js|php)(\?|$)|/images/|/style/|logo", u, re.I):
            continue
        if name == "icims" and not re.match(r"https?://careers", u):
            continue
        if name == "paycor" and "clientId=" in u:
            cid = re.search(r"clientId=([A-Za-z0-9]+)", u).group(1)
            u = f"https://recruitingbypaycor.com/career/CareerHome.action?clientId={cid}"
        good.append((name, u))
    good.sort(key=lambda x: PRIORITY.index(x[0]) if x[0] in PRIORITY else 99)
    return good[0] if good else None


def build_registry(discovery, cu_list):
    meta = {c["charter"]: c for c in json.load(open(cu_list))}
    sites = []
    for r in json.load(open(discovery)):
        m = meta.get(r["charter"], {})
        if m.get("corporate"):
            continue
        b = pick_board(r["ats"])
        careers = next((c.get("final") or c["url"] for c in r["careers"] if not c.get("blocked")), "")
        s = {"charter": r["charter"], "name": r["name"], "state": r["state"], "assets_m": m.get("assets_q2_m", 0),
             "website": r["website"], "careers_url": careers, "platform": "", "board_url": "", "status": "unmapped", "note": ""}
        if b:
            s["platform"], s["board_url"] = b
            s["status"] = "unsupported" if b[0] in UNSUPPORTED else "mapped"
        elif careers:
            s["platform"], s["board_url"], s["status"] = "careers_page", careers, "mapped"
        elif r["blocked"]:
            s["status"], s["note"] = "blocked", "; ".join(r["blocked"])[:160]
        sites.append(s)
    d = {"updated": dt.date.today().isoformat(), "min_assets_m": 500, "sites": sites}
    save_sites(d)
    return d


# ---------- sweep ----------

def tracked():
    try:
        return json.load(open(os.path.join(FEED, "records.json")))["records"]
    except Exception:
        return []


def norm(u):
    p = urllib.parse.urlsplit((u or "").strip())
    return urllib.parse.urlunsplit((p.scheme, p.netloc.lower().replace("www.", ""), p.path.rstrip("/"), p.query, ""))


def fallback_today(sites, n=30):
    # Credit unions we can't read job by job: blocked, unsupported and unmapped boards, and careers
    # pages that load their job list with scripts (read as a plain page they often show nothing).
    pool = [s for s in sites if s["status"] != "mapped" or s["platform"] == "careers_page"]
    big = [s for s in pool if s.get("assets_m", 0) >= 5000]
    rest = [s for s in pool if s.get("assets_m", 0) < 5000]
    if not rest:
        return big
    day = dt.date.today().toordinal()
    start = (day * n) % len(rest)
    pick = (rest + rest)[start:start + min(n, len(rest))]
    return big + pick


def sweep(sites, only=None):
    targets = [s for s in sites if s["status"] == "mapped" and (not only or s["charter"] in only)]
    listings, errors, read_ok = [], [], set()

    def one(s):
        try:
            return s, read_site(s), None
        except Exception as e:
            return s, [], str(e)[:160]

    with cf.ThreadPoolExecutor(12) as ex:
        for s, rows, err in ex.map(one, targets):
            if err:
                errors.append(f"{s['charter']} {s['name']} [{s['platform']}]: {err}")
                continue
            read_ok.add(s["charter"])
            for r in rows:
                if is_exec(r["title"]):
                    r.update({"charter": s["charter"], "cu": s["name"], "state": s["state"], "platform": s["platform"]})
                    listings.append(r)
    recs = tracked()
    by_url = {}
    for r in recs:
        for u in [r["fields"].get("job_url", "")] + [x.get("url", "") for x in r.get("sources", [])]:
            if u:
                by_url[norm(u)] = r
    board_urls = {norm(x["url"]) for x in listings}
    new = [x for x in listings if norm(x["url"]) not in by_url]
    charter_of = {}
    try:
        orgs = json.load(open(os.path.join(REPO, "shared", "organizations.json")))["organizations"]
    except Exception:
        orgs = {}
    for r in recs:
        slug = (r.get("org_slugs") or {}).get("organization")
        if slug and slug in orgs:
            charter_of[r["id"]] = orgs[slug].get("charter")
    gone = []
    for r in recs:
        f = r["fields"]
        if f.get("status") != "Open":
            continue
        ch = charter_of.get(r["id"])
        site = next((s for s in sites if s["charter"] == ch and s["status"] == "mapped"), None)
        if not site or ch not in read_ok or site.get("platform") in GENERIC_OK:
            continue
        host = urllib.parse.urlsplit(site["board_url"]).netloc.lower().replace("www.", "")
        if host and host in norm(f.get("job_url", "")) and norm(f["job_url"]) not in board_urls:
            gone.append({"id": r["id"], "organization": f.get("organization"), "title": f.get("title"), "job_url": f["job_url"]})
    return listings, new, gone, errors, read_ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--charter", action="append")
    ap.add_argument("--build-registry", nargs=2, metavar=("DISCOVERY_JSON", "CU_LIST_JSON"))
    ap.add_argument("--map", metavar="CHARTER")
    ap.add_argument("--url")
    ap.add_argument("--platform")
    ap.add_argument("--unmap", metavar="CHARTER")
    ap.add_argument("--status", choices=["blocked", "unsupported", "unmapped"])
    ap.add_argument("--note", default="")
    ap.add_argument("--list", choices=["fallback", "mapped", "blocked", "unsupported", "unmapped"])
    a = ap.parse_args()
    if a.build_registry:
        d = build_registry(*a.build_registry)
        from collections import Counter
        print(Counter(s["status"] for s in d["sites"]), len(d["sites"]))
        return
    d = load_sites()
    sites = d["sites"]
    if a.map:
        s = next(x for x in sites if x["charter"] == a.map)
        plat = a.platform
        if not plat:
            for name, pat in [("workday", r"myworkdayjobs\.com"), ("ultipro", r"recruiting2?\.ultipro\.com/.*/JobBoard/"),
                              ("adp", r"workforcenow\.adp\.com.*cid="), ("paylocity", r"recruiting\.paylocity\.com"),
                              ("applicantpro", r"applicantpro\.com"), ("saashr", r"saashr\.com/ta/\d+"),
                              ("oracle", r"/hcmUI/CandidateExperience/"), ("icims", r"careers[^.]*\.icims\.com"),
                              ("greenhouse", r"greenhouse\.io"), ("lever", r"jobs\.lever\.co"), ("bamboohr", r"bamboohr\.com"),
                              ("paycor", r"recruitingbypaycor\.com"), ("paycom", r"paycomonline\.net"), ("dayforce", r"dayforcehcm\.com"),
                              ("xmlfeed", r"/xml/|rss=true|\.xml$"), ("phenom", r"/us/en/?$")]:
                if re.search(pat, a.url):
                    plat = name
                    break
            plat = plat or "careers_page"
        s.update({"platform": plat, "board_url": a.url, "status": "unsupported" if plat in UNSUPPORTED else "mapped", "note": a.note})
        save_sites(d)
        rows = read_site(s) if s["status"] == "mapped" else []
        print(f"{s['name']}: {s['status']} {plat} - {len(rows)} jobs, {sum(is_exec(r['title']) for r in rows)} VP-and-up")
        return
    if a.unmap:
        s = next(x for x in sites if x["charter"] == a.unmap)
        s.update({"status": a.status or "unmapped", "note": a.note})
        save_sites(d)
        print(f"{s['name']}: {s['status']}")
        return
    if a.list:
        rows = fallback_today(sites) if a.list == "fallback" else [s for s in sites if s["status"] == a.list]
        for s in rows:
            print(f"{s['charter']}\t{s['name']}\t{s['state']}\t${s.get('assets_m', 0):,.0f}M\t{s['status']}\t{s['platform']}\t{s.get('note', '')}")
        print(len(rows))
        return
    listings, new, gone, errors, read_ok = sweep(sites, set(a.charter or []))
    fb = fallback_today(sites)
    json.dump({"read": dt.datetime.now().isoformat(timespec="seconds"), "boards_read": len(read_ok), "listings": listings,
               "new": new, "gone": gone, "errors": errors,
               "fallback": [{k: s[k] for k in ("charter", "name", "state", "assets_m", "status", "platform", "careers_url")} for s in fb]},
              open(OUT, "w"), indent=1)
    mapped = sum(1 for s in sites if s["status"] == "mapped")
    print(f"Read {len(read_ok)}/{mapped} credit union boards: {len(listings)} VP-and-up jobs, {len(new)} not tracked, "
          f"{len(gone)} tracked postings gone, {len(errors)} boards failed. Job-site lookups due today: {len(fb)}.")
    print(f"Details: {OUT}")
    for x in new:
        print(f"  NEW  {x['cu']} ({x['state']}) | {x['title']} | {x.get('location', '')} | {x.get('posted') or 'no date'}"
              f"{' | lead: check it' if x.get('generic') else ''} | {x['url']}")
    for g in gone:
        print(f"  GONE {g['id']} | {g['organization']} | {g['title']} | {g['job_url']}")
    for e in errors[:40]:
        print("  ERROR", e)


if __name__ == "__main__":
    main()
