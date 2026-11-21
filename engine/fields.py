"""Field types: how a value is cleaned when it comes in, and how it is
written out in the published feed. Also derived fields, open/closed items
and the accuracy score."""
import datetime as dt
import re

from common import (
    US_STATES, ascii_text, field_def, fmt_date, fmt_money_m, logo_url, norm_org,
    normalize_title, parse_date, parse_int, parse_money, resolve_org, slugify,
)


def normalize_value(f, value):
    """Clean an incoming value for field definition f. Returns the stored
    value (None for blank). Raises ValueError with a plain reason."""
    t = f["type"]
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if t == "date":
        d = parse_date(value)
        if not d:
            raise ValueError(f"{f['name']}: {value!r} is not a full date (use YYYY-MM-DD)")
        out = d.isoformat()
    elif t == "datetext":
        d = parse_date(value)
        out = d.isoformat() if d else ascii_text(value)
    elif t == "choice":
        s = ascii_text(value)
        match = [c for c in f["choices"] if c.lower() == s.lower()]
        if not match:
            raise ValueError(f"{f['name']}: {s!r} is not one of {f['choices']}")
        out = match[0]
    elif t == "int":
        out = parse_int(value)
    elif t == "money":
        out = round(parse_money(value), 1)
    elif t == "percent":
        s = ascii_text(value).rstrip("%").strip()
        n = parse_int(s) if s else None
        if n is None or not 0 <= n <= 100:
            raise ValueError(f"{f['name']}: {value!r} is not a percent from 0 to 100")
        out = n
    elif t == "title":
        out = normalize_title(value)
    elif t == "url":
        out = ascii_text(value)
        if not re.match(r"^https?://\S+$", out):
            raise ValueError(f"{f['name']}: {value!r} is not a web address")
    elif t == "state":
        s = ascii_text(value)
        if s.lower() == "national":
            out = "National"
        elif s.upper() in US_STATES:
            out = s.upper()
        else:
            raise ValueError(f"{f['name']}: {value!r} is not a two-letter state or National")
    elif t == "yesno":
        s = str(value).strip().lower()
        if s in ("yes", "y", "true", "1"):
            out = "yes"
        elif s in ("no", "n", "false", "0"):
            out = "no"
        else:
            raise ValueError(f"{f['name']}: {value!r} must be yes or no")
    else:  # text, org
        out = ascii_text(value)
    if f.get("pattern") and isinstance(out, str) and t not in ("date",):
        if not re.fullmatch(f["pattern"], out):
            hint = f.get("pattern_hint") or f"must match {f['pattern']}"
            raise ValueError(f"{f['name']}: {out!r} {hint}")
    return out


def key_tuple(s, fields, orgs, idx):
    parts = []
    for k in s["key"]:
        f = field_def(s, k)
        v = fields.get(k)
        if v is None:
            parts.append("")
        elif f["type"] == "org":
            slug, _ = resolve_org(v, orgs, idx)
            parts.append(slug or norm_org(v))
        elif f["type"] in ("date", "int", "money", "percent"):
            parts.append(str(v))
        else:
            parts.append(norm_org(v))
    return tuple(parts)


def make_id(s, fields, existing_ids):
    d = fields.get(s["date_field"])
    d = parse_date(d)
    base = (d.isoformat() if d else "undated") + "-" + "-".join(
        slugify(fields.get(k) or "") for k in s["key"])
    base = re.sub(r"-+", "-", base).strip("-")[:90]
    out, n = base, 2
    while out in existing_ids:
        out, n = f"{base}-{n}", n + 1
    return out


def output_field(f, v, orgs, idx):
    """The published form: every value a string."""
    n, t = f["name"], f["type"]
    if t == "date":
        d = parse_date(v) if v else None
        return {n: fmt_date(d) if d else "", f"{n}_iso": d.isoformat() if d else ""}
    if t == "datetext":
        d = parse_date(v) if v else None
        return {n: fmt_date(d) if d else (v or ""), f"{n}_iso": d.isoformat() if d else ""}
    if t == "int":
        return {n: f"{v:,}" if v is not None else ""}
    if t == "money":
        return {n: fmt_money_m(v) if v is not None else "", f"{n}_m": f"{v:.1f}" if v is not None else ""}
    if t == "percent":
        return {n: f"{v}%" if v is not None else ""}
    if t == "org":
        slug, org = resolve_org(v, orgs, idx) if v else (None, None)
        logo = (org or {}).get("logo") or {}
        return {
            n: v or "",
            f"{n}_short": (org or {}).get("short_name") or (v or ""),
            f"{n}_logo_url": logo_url(org),
            f"{n}_logo_style": logo.get("style", "") if logo.get("file") else "",
        }
    return {n: "" if v is None else str(v)}


def derived_value(d, ctx):
    if d.get("map_from"):
        v = ctx.get(d["map_from"], "")
        for group, members in (d.get("groups") or {}).items():
            if v in members:
                return group
        if v in (d.get("map") or {}):
            return d["map"][v]
        return d.get("default", "")
    for case in d.get("cases", []):
        ok = True
        for k, want in (case.get("when") or {}).items():
            have = ctx.get(k, "")
            if isinstance(want, list):
                ok = ok and have in want
            else:
                ok = ok and have == str(want)
        if ok:
            return ascii_text(case.get("template", "").format_map(_Blank(ctx)))
    return d.get("default", "")


class _Blank(dict):
    def __missing__(self, key):
        return ""


def is_open(s, rec, fields, today):
    ow = s.get("open_when")
    open_ = True
    if ow:
        v = fields.get(ow["field"])
        v = "" if v is None else str(v)
        if "in" in ow:
            open_ = v in ow["in"]
        if "not_in" in ow:
            open_ = v not in ow["not_in"]
    days = s.get("close_after_days")
    if open_ and days:
        last = max(filter(None, [parse_date(fields.get(s["date_field"])),
                                 parse_date(rec.get("updated"))]), default=None)
        if last and (today - last).days > days:
            open_ = False
    return open_


def accuracy(s, rec, fields, corrected):
    """Score from how each fact was sourced, capped at 99%."""
    weights = s["accuracy"]["weights"]
    scored = [f for f in s["fields"]
              if f.get("scored", f.get("required", False)) and f["type"] != "url"]
    if not scored:
        return "", ""
    srcs = rec.get("sources", [])
    best = max((weights.get(x.get("type"), 0.5) for x in srcs), default=0.5)
    best_type = max(srcs, key=lambda x: weights.get(x.get("type"), 0.5))["type"] if srcs else ""
    fs = rec.get("field_sources", {})
    total, blank, weak = 0.0, [], {}
    for f in scored:
        n = f["name"]
        if n in corrected:
            total += 1.0
            continue
        if fields.get(n) in (None, ""):
            blank.append(f.get("label", n))
            continue
        i = fs.get(n)
        if isinstance(i, int) and 0 <= i < len(srcs):
            st = srcs[i].get("type")
            w = weights.get(st, 0.5)
        else:
            st, w = best_type, best
        total += w
        if w < 1.0:
            weak.setdefault(st or "unknown", []).append(f.get("label", n))
    score = min(99, round(99 * total / len(scored)))
    notes = []
    names = {"trade_press": "trade press only", "aggregator": "an aggregator site only",
             "social": "social media only", "unknown": "an unlabeled source"}
    for st, labels in weak.items():
        notes.append(f"From {names.get(st, st)}: {', '.join(labels)}.")
    if blank:
        notes.append(f"Not found yet: {', '.join(blank)}.")
    if not notes:
        notes.append("Every scored fact comes from a primary or regulator source.")
    return f"{score}%", " ".join(notes)


def history_text(rec):
    parts = []
    for h in reversed(rec.get("history", [])):
        if h.get("note") and h.get("note") != "Added":
            d = parse_date(h.get("date"))
            parts.append(f"{fmt_date(d) if d else h.get('date')}: {ascii_text(h['note'])}")
    return " | ".join(parts)


def summary(s, fields):
    return " / ".join(str(fields.get(k) or "") for k in s["key"])


def age_days(fields, s, today):
    d = parse_date(fields.get(s["date_field"]))
    return (today - d).days if d else 0


def _today():
    return dt.date.today()
