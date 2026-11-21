"""Checks on a published feed, used before every publish (build.py) and by
the daily health check (validate.py)."""
import datetime as dt
import re

from common import ET, ROOT, RAW_BASE, now_et, parse_date

MONEY = re.compile(r"^\$(?:(\d+\.\d{2})B|([\d,]+)M|(\d\.\d)M)$")
ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def check_items(s, items):
    errs = []
    if not isinstance(items, list):
        return ["the feed is not a list"]
    ids = set()
    logo_base = f"{RAW_BASE}/shared/logos/"
    for i, it in enumerate(items):
        tag = f"item {i + 1} ({it.get('id', '?')})"
        for k, v in it.items():
            if not isinstance(v, str):
                errs.append(f"{tag}: {k} is not a string")
            elif not v.isascii():
                errs.append(f"{tag}: {k} has non-ASCII characters")
        if it.get("id") in ids:
            errs.append(f"{tag}: duplicate id")
        ids.add(it.get("id"))
        if it.get("sort_date") and not ISO.match(it["sort_date"]):
            errs.append(f"{tag}: sort_date is not YYYY-MM-DD")
        for f in s["fields"]:
            n, t = f["name"], f["type"]
            v = it.get(n)
            if v is None:
                errs.append(f"{tag}: missing {n}")
                continue
            if f.get("required") and not v:
                errs.append(f"{tag}: {n} is empty")
            if not v:
                continue
            if t == "choice" and v not in f["choices"]:
                errs.append(f"{tag}: {n} {v!r} not allowed")
            if t == "money":
                m = MONEY.match(v)
                if not m:
                    errs.append(f"{tag}: {n} {v!r} is not $X.XXB or $NNNM")
                elif m.group(1) and float(m.group(1)) < 1:
                    errs.append(f"{tag}: {n} {v!r} is under $1B but written in billions")
                elif m.group(2) and int(m.group(2).replace(",", "")) >= 1000:
                    errs.append(f"{tag}: {n} {v!r} is $1B or more but written in millions")
            if t in ("date", "datetext") and it.get(f"{n}_iso") and not ISO.match(it[f"{n}_iso"]):
                errs.append(f"{tag}: {n}_iso is not YYYY-MM-DD")
            if t == "date" and not it.get(f"{n}_iso"):
                errs.append(f"{tag}: {n}_iso missing")
            if t == "percent" and not re.fullmatch(r"\d{1,3}%", v):
                errs.append(f"{tag}: {n} {v!r} is not a percent")
            if t == "org":
                u = it.get(f"{n}_logo_url", "")
                if u:
                    if not u.startswith(logo_base):
                        errs.append(f"{tag}: {n}_logo_url is not a hosted logo")
                    elif not (ROOT / "shared" / "logos" / u[len(logo_base):]).exists():
                        errs.append(f"{tag}: {n} logo file is missing from shared/logos")
                    if it.get(f"{n}_logo_style") not in ("color", "white"):
                        errs.append(f"{tag}: {n}_logo_style must be color or white")
            if f.get("pattern") and t not in ("date", "money", "int", "percent", "org") \
                    and not re.fullmatch(f["pattern"], v):
                errs.append(f"{tag}: {n} {v!r} doesn't match its pattern")
        if s["accuracy"]["enabled"] and it.get("accuracy") and \
                not re.fullmatch(r"\d{1,2}%", it["accuracy"]):
            errs.append(f"{tag}: accuracy {it['accuracy']!r} must be 1-99%")
    return errs


def scheduled_today(s, now=None):
    now = now or now_et()
    hh, mm = (int(x) for x in str(s["review"]["time"]).split(":"))
    return now.replace(hour=hh, minute=mm, second=0, microsecond=0)


def check_health(s, status, now=None):
    """Is the feed fresh? Returns a list of problems."""
    now = now or now_et()
    errs = []
    if s.get("paused"):
        return errs
    if not status:
        return ["status.json is missing: the feed has never been built"]
    if status.get("result") == "failed":
        errs.append("last publish failed: " + "; ".join(status.get("errors", [])[:5]))
    if s["sources"]:
        lc = status.get("last_collect")
        if not lc:
            errs.append("sources have never been collected")
        else:
            age = (now - dt.datetime.fromisoformat(lc).astimezone(ET)).total_seconds() / 3600
            if age > s["health"]["collect_max_hours"]:
                errs.append(f"last collection was {age:.0f} hours ago ({lc})")
        if status.get("last_collect_result") == "failed":
            errs.append("last collection failed: " + "; ".join(status.get("collect_errors", [])[:5]))
    due = scheduled_today(s, now)
    grace = dt.timedelta(minutes=s["review"]["grace_minutes"])
    lr = status.get("last_review")
    last = dt.datetime.fromisoformat(lr).astimezone(ET) if lr else None
    if now >= due + grace:
        if not last or last < due - dt.timedelta(minutes=15):
            errs.append(f"no Claude review since today's {s['review']['time']} run "
                        f"(last review: {lr or 'never'})")
    elif last and (now - last) > dt.timedelta(hours=26) + grace:
        errs.append(f"no Claude review for over a day (last review: {lr})")
    if status.get("last_review_result") == "failed":
        errs.append("last Claude review reported a failure")
    return errs
