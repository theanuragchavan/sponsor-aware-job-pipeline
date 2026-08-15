"""
Track C — UK Skilled Worker sponsorship cross-check.

Fetches the Home Office "Register of licensed sponsors: workers", caches it
locally, and (later) matches a job's company name against it.

The register CSV is re-published under a NEW dated filename almost daily, so we
never hardcode a download URL. Instead we resolve the current link from gov.uk's
structured content API (falling back to scraping the publication page), then
cache the CSV and only refresh when the local copy is older than ~7 days.

See Section 4 of adzuna_tracker_brief.md.

NOTE: matching logic is intentionally NOT written yet — first we download the
real file and inspect its true column headers (run `python sponsor_check.py`).
"""

import csv
import json
import logging
import os
import re
import time

import requests

logger = logging.getLogger(__name__)

PUBLICATION_PAGE = (
    "https://www.gov.uk/government/publications/"
    "register-of-licensed-sponsors-workers"
)
CONTENT_API = (
    "https://www.gov.uk/api/content/government/publications/"
    "register-of-licensed-sponsors-workers"
)

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
REGISTER_CSV = os.path.join(DATA_DIR, "sponsors_register.csv")
REGISTER_META = os.path.join(DATA_DIR, "sponsors_register_meta.json")

CACHE_MAX_AGE_DAYS = 7
HEADERS = {"User-Agent": "adzuna-job-tracker/1.0 (personal use)"}


def _find_csv_url_via_api():
    """Resolve the current register CSV link from gov.uk's content API."""
    resp = requests.get(CONTENT_API, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    found = []

    def walk(node):
        if isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, str) and node.lower().endswith(".csv"):
            if node.startswith("http"):
                found.append(node)

    walk(data)
    return found[0] if found else None


def _find_csv_url_via_html():
    """Fallback: scrape the publication page for the CSV asset link."""
    resp = requests.get(PUBLICATION_PAGE, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    matches = re.findall(
        r'https://assets\.publishing\.service\.gov\.uk/media/[^"\']+?\.csv',
        resp.text,
    )
    return matches[0] if matches else None


def get_current_csv_url():
    """Return the current dated register CSV URL, API first then HTML fallback."""
    url = _find_csv_url_via_api()
    if url:
        logger.info("Resolved register CSV via content API.")
        return url
    logger.warning("Content API yielded no CSV link — falling back to HTML scrape.")
    url = _find_csv_url_via_html()
    if url:
        return url
    raise RuntimeError(
        "Could not find the sponsor-register CSV link on gov.uk "
        "(neither the content API nor the publication page returned one)."
    )


def _cache_is_fresh():
    """True if a cached CSV exists and its recorded fetch time is < max age."""
    if not (os.path.exists(REGISTER_CSV) and os.path.exists(REGISTER_META)):
        return False
    try:
        with open(REGISTER_META, encoding="utf-8") as f:
            meta = json.load(f)
        age_days = (time.time() - meta["fetched_at"]) / 86400
        return age_days < CACHE_MAX_AGE_DAYS
    except (json.JSONDecodeError, KeyError, OSError):
        return False


def ensure_register(force=False):
    """Download + cache the register if missing/stale. Returns the CSV path."""
    os.makedirs(DATA_DIR, exist_ok=True)
    if not force and _cache_is_fresh():
        logger.info("Sponsor register cache is fresh — skipping download.")
        return REGISTER_CSV

    url = get_current_csv_url()
    logger.info("Downloading sponsor register: %s", url)
    resp = requests.get(url, headers=HEADERS, timeout=120)
    resp.raise_for_status()

    with open(REGISTER_CSV, "wb") as f:
        f.write(resp.content)
    with open(REGISTER_META, "w", encoding="utf-8") as f:
        json.dump(
            {"fetched_at": time.time(), "source_url": url,
             "size_bytes": len(resp.content)},
            f, indent=2,
        )
    logger.info("Cached %d bytes to %s", len(resp.content), REGISTER_CSV)
    return REGISTER_CSV


# ---------------------------------------------------------------------------
# Matching logic — written against the REAL register schema:
#   [0] Organisation Name   [1] Town/City   [2] County
#   [3] Type & Rating  (e.g. "Worker (A rating)")   [4] Route (e.g. "Skilled Worker")
#
# Decision: a job counts as sponsor_match="yes" ONLY when its company matches an
# organisation licensed on the "Skilled Worker" route — the route that actually
# enables the visa. The A/B rating is parsed out of the "Type & Rating" text
# (there is no dedicated rating column).
# ---------------------------------------------------------------------------

SKILLED_WORKER_ROUTE = "Skilled Worker"

# Legal-form tokens stripped from the END of a name during normalisation.
_LEGAL_SUFFIXES = {
    "LTD", "LIMITED", "PLC", "LLP", "LLC", "LP", "CIC", "CIO",
    "INC", "CORP", "CO", "COMPANY",
}
# Real register uses several rating formats, e.g.
#   "Worker (A rating)", "Worker (B rating)",
#   "Worker (A (Premium))", "Worker (A (SME+))".
# Provisional entries (e.g. "UK Expansion Worker: Provisional") carry no A/B.
_RATING_RE = re.compile(r"\(([AB])(?:\s+rating\b|\s*\()", re.IGNORECASE)


def _parse_rating(type_and_rating):
    """Pull 'A' / 'B' out of e.g. 'Worker (A rating)'. Returns '' if absent."""
    m = _RATING_RE.search(type_and_rating or "")
    return m.group(1).upper() if m else ""


def normalize_name(name):
    """Normalise a company/organisation name for matching.

    Uppercase, expand '&' to 'AND', strip punctuation, drop a leading 'THE',
    collapse whitespace, and remove trailing legal-form tokens (LTD/LIMITED/...).
    """
    if not name:
        return ""
    text = name.upper().replace("&", " AND ")
    text = re.sub(r"[^A-Z0-9 ]", " ", text)      # drop punctuation
    text = re.sub(r"\s+", " ", text).strip()
    if text.startswith("THE "):
        text = text[4:]
    tokens = text.split()
    while tokens and tokens[-1] in _LEGAL_SUFFIXES:   # peel trailing suffixes
        tokens.pop()
    return " ".join(tokens)


def load_sponsor_lookup(force=False):
    """Return {normalised_name: rating} for Skilled Worker sponsors only.

    Ensures the register is cached/fresh first. When an org has several Skilled
    Worker rows, an 'A' rating wins over 'B'. Returns None if the register can't
    be obtained, so callers can fall back to 'unconfirmed' rather than 'no'.
    """
    try:
        path = ensure_register(force=force)
    except Exception as exc:  # network/parse failure — don't claim 'no'
        logger.warning("Could not obtain sponsor register: %s", exc)
        return None

    lookup = {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if (row.get("Route") or "").strip() != SKILLED_WORKER_ROUTE:
                continue
            key = normalize_name(row.get("Organisation Name", ""))
            if not key:
                continue
            rating = _parse_rating(row.get("Type & Rating", ""))
            existing = lookup.get(key)
            # 'A' beats 'B' beats ''; otherwise keep the first seen.
            if existing is None or (existing != "A" and rating == "A"):
                lookup[key] = rating
    logger.info("Loaded %d Skilled Worker sponsors into lookup.", len(lookup))
    return lookup


def match_company(company, lookup, aliases=None):
    """Match a job's company against the Skilled Worker sponsor lookup.

    Returns (sponsor_match, sponsor_rating, sponsor_route):
      - lookup is None         -> ("unconfirmed", "", "")   register unavailable
      - empty company          -> ("unconfirmed", "", "")
      - found on register      -> ("yes", "A"/"B"/"", "Skilled Worker")
      - not found              -> ("no", "", "")
    Per the brief, 'no' must only ever sort/highlight — never filter a job out.

    `aliases` is the human-confirmed overlay from load_aliases(). It is consulted
    FIRST, because exact matching has a real false-negative rate: the register
    lists Monzo as "MONZO BANK" and Deliveroo as "ROOFOODS LTD T A DELIVEROO",
    so both scored "no" until someone confirmed the mapping once.
    """
    if lookup is None:
        return ("unconfirmed", "", "")
    key = normalize_name(company)
    if not key:
        return ("unconfirmed", "", "")

    if aliases:
        entry = aliases.get(key)
        if entry:
            register_key = normalize_name(entry.get("register_name", ""))
            rating = lookup.get(register_key, entry.get("rating", ""))
            return ("yes", rating, SKILLED_WORKER_ROUTE)

    if key in lookup:
        return ("yes", lookup[key], SKILLED_WORKER_ROUTE)
    return ("no", "", "")


# --------------------------------------------------------------------------- #
# Suggestion layer — proposes matches for human review. Never decides.
# --------------------------------------------------------------------------- #

def build_name_index(lookup):
    """Index register keys by first token: {'MONZO': ['MONZO BANK', ...]}.

    A linear scan of 121k keys per company is far too slow across ~900 companies.
    """
    index = {}
    for key in lookup:
        first, _, _ = key.partition(" ")
        index.setdefault(first, []).append(key)
    return index


def suggest_matches(company, lookup, index=None, limit=3):
    """Register entries that plausibly ARE this company.

    Returns [(register_key, rating, why)] — candidates for a human to confirm,
    never an automatic verdict. Two deterministic rules, both validated against
    the real register:

      1. prefix at a word boundary — "AMENTUM" -> "AMENTUM UK", but NOT
         "AMENTUMX". The boundary is what stops a prefix rule becoming a
         substring rule.
      2. trading-as — "DELIVEROO" -> "ROOFOODS LTD T A DELIVEROO". The register
         records the legal entity; the job ad names the brand.

    Deliberately no fuzzy/difflib matching. Measured on this register, loosening
    further mostly adds false positives of exactly the shape a human then has to
    reject one by one (AMAZON -> AMAZON CHARITABLE TRUST, CATALYST -> CATALYST
    CAPITAL, LEONARDO -> LEONARDO BELGIUM SA). Precision matters more than recall
    here because every suggestion costs him a decision.
    """
    if not lookup:
        return []
    key = normalize_name(company)
    if len(key) < 4:            # "BT", "EY" — too short to match safely
        return []
    if key in lookup:
        return []               # already an exact hit; nothing to suggest

    if index is None:
        index = build_name_index(lookup)

    out = []
    prefix_re = re.compile(r"^%s\b" % re.escape(key))
    for candidate in index.get(key.split(" ")[0], []):
        if prefix_re.match(candidate):
            out.append((candidate, lookup[candidate], "prefix"))
            if len(out) >= limit:
                return out

    trading_re = re.compile(r"\bT A %s\b" % re.escape(key))
    for candidate in lookup:
        if " T A " in candidate and trading_re.search(candidate):
            out.append((candidate, lookup[candidate], "trading-as"))
            if len(out) >= limit:
                break
    return out[:limit]


# --------------------------------------------------------------------------- #
# Alias overlay — human-confirmed mappings, keyed on the normalised company.
# Lives outside the tracker so a tracker rewrite can never wipe it (the same
# reason pipeline/decisions.json exists).
# --------------------------------------------------------------------------- #

ALIASES_PATH = os.path.join(DATA_DIR, "sponsor_aliases.json")


def load_aliases(path=None):
    """Return {normalised_company: {register_name, rating, confirmed}}."""
    path = path or ALIASES_PATH
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Ignoring malformed %s: %s", path, exc)
        return {}
    return {normalize_name(k): v for k, v in raw.items()}


def save_aliases(aliases, path=None):
    """Write the overlay atomically, keyed on the ORIGINAL company spelling."""
    path = path or ALIASES_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.tmp" % path
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(aliases, f, indent=2, sort_keys=True, ensure_ascii=False)
    os.replace(tmp, path)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    path = ensure_register()

    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        headers = next(reader)
        sample = [next(reader, None) for _ in range(3)]

    print("\n=== REAL column headers from the downloaded register ===")
    for i, h in enumerate(headers):
        print(f"  [{i}] {h!r}")
    print("\n=== First few data rows ===")
    for row in sample:
        if row:
            print("  ", row)

    print("\n=== Matching smoke test ===")
    lookup = load_sponsor_lookup()
    for company in ["Mortgage Brain", "Bluetown", "Google UK Ltd",
                    "Deloitte LLP", "Totally Made Up Co Ltd"]:
        print(f"  {company!r:35} -> {match_company(company, lookup)}")
