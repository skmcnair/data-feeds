"""Shared helpers for the data-feeds engine.

Everything mechanical lives here so every feed formats data the same way:
paths, JSON and settings loading, ASCII cleanup, dates, money, numbers,
title style and organization-name matching.
"""
import datetime as dt
import hashlib
import json
import os
import re
import unicodedata
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

ROOT = Path(os.environ.get("FEEDS_ROOT", Path(__file__).resolve().parent.parent))
ET = ZoneInfo("America/New_York")
REPO = os.environ.get("FEEDS_REPO", "skmcnair/data-feeds")
RAW_BASE = f"https://raw.githubusercontent.com/{REPO}/main"

FIELD_TYPES = {
    "text", "date", "datetext", "choice", "int", "money", "percent",
    "title", "org", "url", "state", "yesno",
}
SOURCE_TYPES = ["primary", "regulator", "trade_press", "aggregator", "social"]
DEFAULT_WEIGHTS = {"primary": 1.0, "regulator": 1.0, "trade_press": 0.9,
                   "aggregator": 0.75, "social": 0.7}
US_STATES = set(
    "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS "
    "MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI "
    "WY PR GU VI AS MP".split()
)


# ---------------------------------------------------------------- time, files

def now_et():
    return dt.datetime.now(ET).replace(microsecond=0)


def today_et():
    return now_et().date()


def load_json(path, default=None):
    path = Path(path)
    if not path.exists():
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="ascii") as f:
        json.dump(data, f, indent=2, ensure_ascii=True)
        f.write("\n")
    os.replace(tmp, path)


def feed_dir(name):
    return ROOT / "feeds" / name


def feed_names():
    base = ROOT / "feeds"
    if not base.exists():
        return []
    return sorted(p.name for p in base.iterdir()
                  if p.is_dir() and not p.name.startswith("_") and (p / "feed.yml").exists())


def short_hash(text, n=12):
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:n]


# ---------------------------------------------------------------- text

_PUNCT = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'",
    "“": '"', "”": '"', "„": '"', "″": '"',
    "–": "-", "—": "-", "―": "-", "−": "-", "‐": "-", "‑": "-",
    "…": "...", " ": " ", " ": " ", "​": "", "®": "", "™": "",
    "©": "", "•": "-", "·": "-",
}


def ascii_text(value):
    """Plain ASCII: straight quotes, hyphens for dashes, accents removed."""
    if value is None:
        return ""
    s = str(value)
    for k, v in _PUNCT.items():
        s = s.replace(k, v)
    s = unicodedata.normalize("NFKD", s)
    s = s.encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[ \t]+", " ", s)
    return s.strip()


def slugify(value):
    s = ascii_text(value).lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "item"


def norm_org(value):
    """Matching key for an organization name: 'The Nutmeg State Financial
    Credit Union' and 'Nutmeg State Financial CU' match."""
    s = ascii_text(value).lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"\bfederal credit union\b", "fcu", s)
    s = re.sub(r"\bcredit union\b", "cu", s)
    s = re.sub(r"^the ", "", s)
    s = re.sub(r"\b(inc|incorporated|na|n a)$", "", s).strip()
    return s


# ---------------------------------------------------------------- dates

_DATE_FORMATS = ["%Y-%m-%d", "%b %d, %Y", "%B %d, %Y", "%b %d %Y", "%B %d %Y",
                 "%m/%d/%Y", "%m/%d/%y", "%d %b %Y", "%d %B %Y", "%b. %d, %Y"]


def parse_date(value):
    """Return a date, or None if the value is not a full calendar date."""
    if value is None or value == "":
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if isinstance(value, dict) and "date" in value:
        return parse_date(value["date"])
    s = ascii_text(value)
    m = re.match(r"^(\d{4}-\d{2}-\d{2})[T ]", s)
    if m:
        s = m.group(1)
    s = s.replace("Sept ", "Sep ")
    for f in _DATE_FORMATS:
        try:
            return dt.datetime.strptime(s, f).date()
        except ValueError:
            continue
    return None


def fmt_date(d):
    return f"{d:%b} {d.day}, {d.year}"


# ---------------------------------------------------------------- numbers

def parse_money(value):
    """Return $ millions as a float. Numbers are taken as $ millions already;
    strings may be '$1.85B', '$345M', '1,850' (millions) or '$1,850,000,000'."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError("not an amount")
    if isinstance(value, (int, float)):
        return float(value)
    s = ascii_text(value).replace(",", "").replace(" ", "").lower()
    m = re.fullmatch(r"\$?(\d+(?:\.\d+)?)(b|bn|billion|m|mm|million|k|thousand)?", s)
    if not m:
        raise ValueError(f"not an amount: {value!r}")
    n, unit = float(m.group(1)), m.group(2)
    if unit in ("b", "bn", "billion"):
        return n * 1000
    if unit in ("m", "mm", "million"):
        return n
    if unit in ("k", "thousand"):
        return n / 1000
    if "$" in str(value) and n >= 100000:
        return n / 1e6
    return n


def fmt_money_m(v):
    """Sean's rule: $1B and up in billions with two decimals, below that millions."""
    d = Decimal(str(v))
    if d >= Decimal("999.5"):
        b = (d / 1000).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return f"${b}B"
    if d < 1:
        return f"${d.quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)}M"
    return f"${int(d.quantize(Decimal('1'), rounding=ROUND_HALF_UP)):,}M"


def parse_int(value):
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError("not a whole number")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    s = ascii_text(value).replace(",", "")
    if re.fullmatch(r"-?\d+", s):
        return int(s)
    raise ValueError(f"not a whole number: {value!r}")


# ---------------------------------------------------------------- title style

_RANKS = [
    (r"senior executive vice[ -]president", "Senior EVP"),
    (r"executive vice[ -]president", "EVP"),
    (r"senior vice[ -]president", "SVP"),
    (r"assistant vice[ -]president", "AVP"),
    (r"regional vice[ -]president", "Regional VP"),
    (r"vice[ -]president", "VP"),
]
_CHIEF_ABBR = {
    "CFO": "Chief Financial Officer", "COO": "Chief Operating Officer",
    "CTO": "Chief Technology Officer", "CMO": "Chief Marketing Officer",
    "CHRO": "Chief Human Resources Officer", "CISO": "Chief Information Security Officer",
    "CLO": "Chief Lending Officer", "CXO": "Chief Experience Officer",
}
_RANK_RE = r"(?:Interim )?(?:Senior EVP|EVP|SVP|AVP|Regional VP|VP|Senior Director|Director)"
_SMALL = {"of", "and", "the", "for", "in", "at", "to", "on", "a", "an", "&"}
TITLE_AMBIGUOUS = re.compile(r"\b(?!CEO\b)C[A-Z]{1,3}O\b")


def _cap_word(w, first):
    core = re.match(r"^([A-Za-z][A-Za-z'.-]*)(.*)$", w)
    if not core:
        return w
    word, rest = core.groups()
    if word.isupper() and len(word) > 1:
        return w
    if any(c.isupper() for c in word[1:]):
        return w  # eBanking, McNair
    if word.lower() in _SMALL and not first:
        return word.lower() + rest
    parts = word.split("-")
    return "-".join(p[:1].upper() + p[1:] for p in parts) + rest


def normalize_title(value):
    """One title style everywhere: 'VP, Marketing', 'SVP, Chief Lending
    Officer', 'President & CEO', chief titles spelled out."""
    s = ascii_text(value)
    if not s:
        return ""
    s = re.sub(r"\s+", " ", s)
    low = s.lower()
    m = re.fullmatch(r"(interim )?president\s*(?:and|&|/|,)\s*(?:chief executive officer|ceo)", low)
    if m:
        return ("Interim " if m.group(1) else "") + "President & CEO"
    m = re.fullmatch(r"(interim )?chief executive officer", low)
    if m:
        return ("Interim " if m.group(1) else "") + "CEO"
    board = [
        (r"(?:board )?vice[ -]chair(?:man|woman|person)?(?: of the board)?", "Board Vice Chair"),
        (r"(?:board )?chair(?:man|woman|person)?(?: of the board)?", "Board Chair"),
        (r"(?:board )?treasurer(?: of the board)?", "Board Treasurer"),
        (r"(?:board )?secretary(?: of the board)?", "Board Secretary"),
    ]
    if "board" in low:
        for pat, out in board:
            if re.fullmatch(pat, low):
                return out
    for pat, out in _RANKS:
        s = re.sub(rf"\b{pat}\b", out, s, flags=re.I)
    for abbr, full in _CHIEF_ABBR.items():
        s = re.sub(rf"\b{abbr}\b", full, s)
    s = re.sub(rf"^({_RANK_RE})\s*(?:and|&|/|,)\s*(Chief .+)$", r"\1, \2", s, flags=re.I)
    s = re.sub(rf"^({_RANK_RE})\s*(?:\s(?:of|for)\s|\s?[-:]\s?|,\s*)\s*(.+)$", r"\1, \2", s, flags=re.I)
    s = re.sub(r"\s+and\s+", " & ", s)
    s = re.sub(r"\s*,\s*", ", ", s)
    words = s.split(" ")
    s = " ".join(_cap_word(w, i == 0) for i, w in enumerate(words))
    for abbr in ("Senior EVP", "EVP", "SVP", "AVP", "Regional VP", "VP"):
        s = re.sub(rf"\b{abbr}\b", abbr, s, flags=re.I)
    return s


# ---------------------------------------------------------------- settings

DEFAULTS = {
    "item": "item",
    "key": [],
    "date_field": None,
    "fields": [],
    "derived": [],
    "open_when": None,
    "close_after_days": None,
    "sources": [],
    "collect": {"max_age_days": 14, "include_any": [], "exclude_any": [], "max_pending": 300},
    "review": {"time": "06:37", "grace_minutes": 120, "lookback_days": 3,
               "monday_lookback_days": 30, "recheck_max": 25, "searches": []},
    "publish": {"max_age_days": None},
    "confidence": {"enabled": True, "weights": DEFAULT_WEIGHTS},
    "health": {"collect_max_hours": 8},
    "trial": True,
}


def _merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


class SettingsError(ValueError):
    pass


def load_feed(name):
    path = feed_dir(name) / "feed.yml"
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    if "accuracy" in raw and "confidence" not in raw:  # the setting's old name still works
        raw["confidence"] = raw.pop("accuracy")
    s = _merge(DEFAULTS, raw)
    s["name"] = name
    check_settings(s)
    return s


def check_settings(s):
    errs = []
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", s["name"]):
        errs.append("feed folder name must be lowercase letters, digits and hyphens")
    if not s.get("title"):
        errs.append("title is required")
    names = set()
    for i, f in enumerate(s["fields"]):
        n = f.get("name", "")
        if not re.fullmatch(r"[a-z][a-z0-9_]*", n):
            errs.append(f"field {i + 1}: name {n!r} must be snake_case")
        if n in names:
            errs.append(f"field {n}: listed twice")
        names.add(n)
        t = f.get("type", "text")
        f["type"] = t
        if t not in FIELD_TYPES:
            errs.append(f"field {n}: unknown type {t!r}")
        if t == "choice" and not f.get("choices"):
            errs.append(f"field {n}: a choice field needs choices")
        if f.get("pattern"):
            try:
                re.compile(f["pattern"])
            except re.error as e:
                errs.append(f"field {n}: bad pattern ({e})")
    if not s["key"]:
        errs.append("key is required: the fields that make two items the same")
    for k in s["key"]:
        if k not in names:
            errs.append(f"key field {k!r} is not one of the fields")
    if not s["date_field"] or s["date_field"] not in names:
        errs.append("date_field must name one of the fields (the item's main date)")
    else:
        df = field_def(s, s["date_field"])
        if df["type"] not in ("date", "datetext"):
            errs.append("date_field must be a date or datetext field")
    reserved = {"id", "sort_date", "sources", "source_url", "updates", "added", "updated",
                "confidence", "confidence_notes", "open", "last_checked"}
    for f in s["fields"]:
        if f.get("name") in reserved:
            errs.append(f"field {f['name']}: that name is reserved")
    for d in s["derived"]:
        if not d.get("name") or d["name"] in names or d["name"] in reserved:
            errs.append(f"derived field {d.get('name')!r} needs a new, unused name")
        if not (d.get("map_from") or d.get("cases")):
            errs.append(f"derived field {d.get('name')}: give map_from (+ map/groups) or cases")
    ow = s.get("open_when")
    if ow and ow.get("field") not in names:
        errs.append("open_when.field must be one of the fields")
    for src in s["sources"]:
        if src.get("type") not in ("rss", "google_news"):
            errs.append(f"source {src.get('name')!r}: type must be rss or google_news")
        if src.get("type") == "rss" and not src.get("url"):
            errs.append(f"source {src.get('name')!r}: rss needs url")
        if src.get("type") == "google_news" and not src.get("query"):
            errs.append(f"source {src.get('name')!r}: google_news needs query")
    if errs:
        raise SettingsError("; ".join(errs))


def field_def(s, name):
    for f in s["fields"]:
        if f["name"] == name:
            return f
    return None


# ---------------------------------------------------------------- organizations

def orgs_path():
    return ROOT / "shared" / "organizations.json"


def load_orgs():
    return load_json(orgs_path(), {"organizations": {}})


# A name two different organizations share (Consumers Credit Union in Illinois
# and in Michigan) resolves to neither: each record says which one it means in
# its "org_slugs" ({field: slug}), recorded when it is added.
AMBIGUOUS = "?ambiguous"


def org_names(o):
    return {norm_org(n) for n in [o.get("name"), o.get("short_name"), *o.get("aliases", [])] if n}


def org_index(orgs):
    """Matching name -> slug, or AMBIGUOUS when several organizations share it."""
    idx = {}
    for slug, o in orgs["organizations"].items():
        for n in org_names(o):
            idx[n] = slug if idx.get(n, slug) == slug else AMBIGUOUS
    return idx


def org_matches(org, name):
    return bool(name) and norm_org(name) in org_names(org)


def orgs_named(name, orgs):
    n = norm_org(name)
    return [s for s, o in orgs["organizations"].items() if n in org_names(o)]


def is_ambiguous(name, idx):
    return bool(name) and idx.get(norm_org(name)) == AMBIGUOUS


def resolve_org(name, orgs, idx=None, pin=None):
    """(slug, org) for a name, or (None, None) when it's unknown or shared by
    several organizations. pin: the slug a record recorded for this field; it
    wins while that organization still goes by the name."""
    if not name:
        return None, None
    if pin and pin in orgs["organizations"] and org_matches(orgs["organizations"][pin], name):
        return pin, orgs["organizations"][pin]
    idx = idx if idx is not None else org_index(orgs)
    slug = idx.get(norm_org(name))
    if not slug or slug == AMBIGUOUS:
        return None, None
    return slug, orgs["organizations"][slug]


def logo_url(org):
    logo = (org or {}).get("logo") or {}
    if not logo.get("file"):
        return ""
    return f"{RAW_BASE}/shared/logos/{logo['file']}"
