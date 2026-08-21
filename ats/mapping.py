"""
mapping.py — turn a raw ATS payload into a tracker row.

Every row is built from `{k: "" for k in tracker.FIELDNAMES}`. That is not
tidiness: main.py indexes rows directly (`r["sponsor_match"]`, `r["tier"]`) when
printing its summary, so a row missing a key raises KeyError *after* the store
has already been written.

Tiering reuses the keyword lists already in config.py. A new taxonomy here would
drift out of step with the Adzuna searches within a week.
"""

from __future__ import annotations

import datetime as dt
import html
import re
import sys
from pathlib import Path

ADZUNA_HOME = Path(__file__).resolve().parent.parent
if str(ADZUNA_HOME) not in sys.path:
    sys.path.insert(0, str(ADZUNA_HOME))

import config  # noqa: E402
import tracker  # noqa: E402

# --------------------------------------------------------------------------- #
# Dates
# --------------------------------------------------------------------------- #

def clean_html(text):
    """Job-description HTML -> readable plain text.

    Greenhouse delivers `content` **entity-encoded**: the field literally
    contains `&lt;p&gt;&lt;span data-contrast=&quot;auto&quot;&gt;...`.
    tracker._clean_snippet strips tags and *then* unescapes, which is right for
    Adzuna's raw-HTML snippets but leaves visible `<p><span ...>` markup on a
    Greenhouse description. Unescaping first turns the entities into real tags
    so the existing stripper can do its job.
    """
    if not text:
        return ""
    return tracker._clean_snippet(html.unescape(text))


def to_date(value):
    """Normalise an ATS timestamp to YYYY-MM-DD. Returns "" if unparseable.

    Do not replace this with a `[:10]` slice. tracker.to_row does exactly that
    to Adzuna's `created` string, and it works only because Adzuna always sends
    ISO-8601 text. Lever sends `createdAt` as **epoch milliseconds, as an int** —
    slicing an int raises TypeError and takes the whole run down.
    """
    if value in (None, ""):
        return ""
    if isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)):
        seconds = float(value)
        if seconds > 1e11:            # milliseconds, not seconds
            seconds /= 1000.0
        try:
            return dt.datetime.fromtimestamp(
                seconds, dt.timezone.utc).date().isoformat()
        except (OSError, OverflowError, ValueError):
            return ""
    text = str(value).strip()
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", text)
    if m:
        return "-".join(m.groups())
    for fmt in ("%d/%m/%Y", "%m/%d/%Y", "%d %B %Y", "%B %d, %Y"):
        try:
            return dt.datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return ""


# --------------------------------------------------------------------------- #
# Location — he needs UK roles, and these boards are mostly global
# --------------------------------------------------------------------------- #

# Unambiguous: nowhere else is called this, so one mention settles it.
_UK_STRONG = re.compile(
    r"\b(uk|u\.k\.|united kingdom|england|scotland|wales|northern ireland|"
    r"london|gbr|manchester|glasgow|edinburgh|bristol|nottingham|sheffield|"
    r"liverpool|cardiff|belfast|leeds|brighton|coventry|southampton|"
    r"milton keynes|oxford)\b",
    re.IGNORECASE,
)
# UK places whose names exist abroad too. "New York" is not York, "Cambridge,
# MA" is not Cambridge — treating these as proof of a UK role let 36 of
# Anthropic's 41 surviving rows through as San Francisco and New York jobs.
# They only count when nothing foreign is also present.
_UK_WEAK = re.compile(
    r"\b(cambridge|birmingham|newcastle|reading|bath|leicester|"
    r"(?<!new )york)\b",
    re.IGNORECASE,
)
# Work-arrangement words carry no geography, so a location made only of them is
# "unspecified", not "foreign". Cloudflare sets every location to the bare word
# "Hybrid" and names the country in the title — which is handled by checking the
# title against _FOREIGN below, not by treating "Hybrid" itself as suspicious.
_ARRANGEMENT = re.compile(r"\b(remote|anywhere|global|hybrid|on[\s-]?site|"
                          r"flexible|various)\b", re.IGNORECASE)
_NOISE = re.compile(r"[^a-z]+", re.IGNORECASE)

# Explicit non-UK markers, seeded from what these feeds actually emit rather
# than from an invented world gazetteer. Deny-list, not allow-list: an unknown
# place is kept for him to judge, never silently binned.
_FOREIGN = re.compile(
    r"\b(india|mumbai|bengaluru|bangalore|delhi|korea|seoul|japan|tokyo|"
    r"singapore|thailand|bangkok|australia|melbourne|sydney|canada|toronto|"
    r"brazil|s(a|ã)o paulo|mexico|germany|berlin|munich|france|paris|spain|"
    r"madrid|barcelona|italy|milan|netherlands|amsterdam|poland|warsaw|"
    r"portugal|lisbon|ireland|dublin|sweden|stockholm|denmark|copenhagen|"
    r"norway|oslo|switzerland|zurich|austria|israel|tel aviv|hebrew|"
    r"united states|usa|u\.s\.|new york|san francisco|seattle|austin|boston|"
    r"chicago|denver|atlanta|los angeles|texas|california|emea remote|"
    r"apac|latam|china|shanghai|beijing|hong kong|taiwan|vietnam|philippines|"
    r"malaysia|indonesia|uae|dubai|saudi|qatar|egypt|nigeria|kenya|"
    r"south africa|new zealand|belfast time zone)\b",
    re.IGNORECASE,
)
# US state suffixes as they appear in these feeds ("San Francisco, CA").
_US_STATE = re.compile(
    r",\s?(AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|MD|MA|MI|"
    r"MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|"
    r"VA|WA|WV|WI|WY|DC)\b")


def uk_location_reason(location, title=""):
    """None if this role is plausibly UK-based, else why it's dropped.

    Judges the title AND the location together, because neither is reliable on
    its own. Two failure modes seen live:

      - "Remote India": an earlier rule matched /remote/ and returned keep
        before anything read the rest of the string, so a Mumbai role reached
        the shortlist.
      - Cloudflare sets every location to "Hybrid" and names the country in the
        title ("Customer Engineer (Pre-Sales), Korea (Based in Seoul)").

    Default is KEEP. An unrecognised place is unknown, not foreign, and a wrong
    drop is invisible in a way a wrong keep is not.
    """
    combined = f"{title or ''} {location or ''}".strip()
    if not combined:
        return None

    # An unambiguous UK mention settles it, even alongside other offices — a
    # role listed "London, UK; San Francisco" is genuinely open in London.
    if _UK_STRONG.search(combined):
        return None

    foreign = _FOREIGN.search(combined) or _US_STATE.search(combined)
    if foreign:
        return f"not a UK location ({foreign.group(0).strip(', ')})"

    # Only now do the ambiguous city names count, with nothing foreign present.
    if _UK_WEAK.search(combined):
        return None

    text = (location or "").strip()
    if not text:
        return None
    remainder = _NOISE.sub(" ", _ARRANGEMENT.sub(" ", text)).strip()
    if not remainder:
        return None                       # purely "Remote" / "Anywhere"
    return f"not a UK location ({text[:40]})"


# --------------------------------------------------------------------------- #
# Tier — reuse the shortlist's title classifiers
# --------------------------------------------------------------------------- #
# The obvious move is to reuse config.TIER1..TIER4, but those are *Adzuna search
# phrases* ("Forward Deployed Engineer", "Software Engineer"), not a classifier.
# Tried it: every surviving Monzo role came back untiered, because "Platform
# Engineer" and "Backend Engineer III" contain none of those exact phrases.
#
# shortlist.py already carries real classifiers — FDE_TITLE, GRAD_TITLE,
# AI_TITLE, TECH_SIGNAL — which are used to score every Adzuna row today. Using
# the same ones keeps ATS and Adzuna rows ranked on identical rules, which is
# the actual requirement.

sys.path.insert(0, str(ADZUNA_HOME / "pipeline"))
import shortlist  # noqa: E402


def tier_for_title(title):
    """Best-matching tier as a string, or "" if nothing matches.

    Mirrors the tiers in config.py: 1 forward-deployed/solutions, 2 early-career
    software, 3 AI/ML, 4 everything else with a tech signal.
    """
    text = title or ""
    if shortlist.FDE_TITLE.search(text):
        return "1"
    if shortlist.AI_TITLE.search(text):
        return "3"
    if shortlist.GRAD_TITLE.search(text) and shortlist.TECH_SIGNAL.search(text):
        return "2"
    if shortlist.TECH_SIGNAL.search(text):
        return "4"
    return ""


# --------------------------------------------------------------------------- #
# Contract type — cosmetic; nothing downstream reads it
# --------------------------------------------------------------------------- #

CONTRACT_MAP = {
    "fulltime": "permanent", "full_time": "permanent", "full-time": "permanent",
    "regular": "permanent", "permanent": "permanent",
    "parttime": "part_time", "part_time": "part_time", "part-time": "part_time",
    "contract": "contract", "temporary": "contract", "intern": "internship",
    "internship": "internship",
}


def _contract(value):
    return CONTRACT_MAP.get(str(value or "").strip().lower().replace(" ", ""), "")


# --------------------------------------------------------------------------- #
# Greenhouse
# --------------------------------------------------------------------------- #

def greenhouse_row(raw, board):
    """Map one Greenhouse job onto a tracker row.

    `board` is the registry entry; `board["company"]` wins over the feed's
    `company_name` because the registry also carries `register_name`, the exact
    Home Office string the sponsorship gate needs.
    """
    row = {name: "" for name in tracker.FIELDNAMES}

    jid = str(raw.get("id", "")).strip()
    location = ((raw.get("location") or {}).get("name") or "").strip()
    title = (raw.get("title") or "").strip()

    row.update({
        # Namespaced: Greenhouse ids are plain integers exactly like Adzuna's,
        # so a bare id could collide and the insert-only merge would silently
        # drop whichever arrived second.
        "id": f"gh:{jid}",
        "title": title,
        "company": board.get("company") or raw.get("company_name") or "",
        "location": location,
        "contract_type": _contract(raw.get("employment_type")),
        "category": ", ".join(
            d.get("name", "") for d in (raw.get("departments") or []) if d
        )[:60],
        "tier": tier_for_title(title),
        "posted_date": to_date(raw.get("first_published")
                               or raw.get("updated_at")),
        # The employer's own application page, not an aggregator redirect.
        "redirect_url": raw.get("absolute_url") or "",
        "date_first_seen": dt.date.today().isoformat(),
        "status": tracker.DEFAULT_STATUS,
        "description": clean_html(raw.get("content") or "")[:2000],
        "source": "gh",
        "apply_deadline": to_date(raw.get("application_deadline")),
    })
    return row


def _base_row(board, jid, prefix, title, location, url, posted,
              contract="", category="", description="", deadline=""):
    """Shared skeleton so every family produces an identical row shape."""
    row = {name: "" for name in tracker.FIELDNAMES}
    row.update({
        "id": f"{prefix}:{jid}",
        "title": (title or "").strip(),
        "company": board.get("company", ""),
        "location": (location or "").strip(),
        "contract_type": contract,
        "category": (category or "")[:60],
        "tier": tier_for_title(title),
        "posted_date": posted,
        "redirect_url": url or "",
        "date_first_seen": dt.date.today().isoformat(),
        "status": tracker.DEFAULT_STATUS,
        "description": clean_html(description)[:2000],
        "source": prefix,
        "apply_deadline": deadline,
    })
    return row


def ashby_row(raw, board):
    """Map one Ashby posting. `location` is a flat string, not a dict."""
    secondary = raw.get("secondaryLocations") or []
    extra = ", ".join(
        s.get("location", "") if isinstance(s, dict) else str(s)
        for s in secondary)
    location = raw.get("location") or ""
    if extra:
        location = f"{location}; {extra}" if location else extra
    return _base_row(
        board, raw.get("id", ""), "ashby",
        raw.get("title"), location,
        raw.get("jobUrl"), to_date(raw.get("publishedAt")),
        contract=_contract(raw.get("employmentType")),
        category=raw.get("department") or raw.get("team") or "",
        description=raw.get("descriptionHtml") or raw.get("descriptionPlain") or "",
    )


def lever_row(raw, board):
    """Map one Lever posting. Location lives under `categories`."""
    cats = raw.get("categories") or {}
    return _base_row(
        board, raw.get("id", ""), "lever",
        raw.get("text"), cats.get("location") or "",
        raw.get("hostedUrl") or raw.get("applyUrl"),
        to_date(raw.get("createdAt")),
        contract=_contract(cats.get("commitment")),
        category=cats.get("team") or cats.get("department") or "",
        description=raw.get("descriptionPlain") or raw.get("description") or "",
    )


def smartrecruiters_row(raw, board):
    """Map one SmartRecruiters posting.

    `location` is a dict with a prebuilt `fullLocation` that carries stray
    double commas ("London, , United Kingdom"), so it is rebuilt from parts.
    The list endpoint has no description field (see clients.fetch_smartrecruiters).
    The public apply URL is not in the payload either; `ref` is the API link, so
    the jobs.smartrecruiters.com pattern is constructed from the company
    identifier and posting id.
    """
    loc = raw.get("location") or {}
    parts = [loc.get("city"), loc.get("region"), loc.get("country", "").upper()]
    location = ", ".join(p for p in parts if p)
    if loc.get("remote"):
        location = f"{location}; Remote" if location else "Remote"
    ident = (raw.get("company") or {}).get("identifier") or ""
    jid = raw.get("id", "")
    url = (f"https://jobs.smartrecruiters.com/{ident}/{jid}" if ident and jid
           else raw.get("ref", ""))
    return _base_row(
        board, jid, "sr",
        raw.get("name"), location, url,
        to_date(raw.get("releasedDate")),
        contract=_contract((raw.get("typeOfEmployment") or {}).get("id")),
        category=((raw.get("department") or {}).get("label")
                  or (raw.get("function") or {}).get("label") or ""),
    )


MAPPERS = {
    "greenhouse": greenhouse_row,
    "ashby": ashby_row,
    "lever": lever_row,
    "smartrecruiters": smartrecruiters_row,
}

# Row-id prefix per family, so ats_main can spot a board's own rows.
PREFIXES = {"greenhouse": "gh", "ashby": "ashby", "lever": "lever",
            "smartrecruiters": "sr"}


def to_row(family, raw, board):
    try:
        mapper = MAPPERS[family]
    except KeyError:
        raise KeyError(
            f"no mapper for ATS family {family!r}; known: {sorted(MAPPERS)}"
        ) from None
    return mapper(raw, board)


# --------------------------------------------------------------------------- #
# Filters applied to a mapped row, each returning a logged reason
# --------------------------------------------------------------------------- #

def drop_reason(row, today=None):
    """Why this row should not enter the store, or None to keep it."""
    title = row.get("title", "")
    reason = tracker.unsuitable_reason(title)
    if reason:
        return reason                       # reuse, don't reimplement

    # An Adzuna row arrives pre-filtered by the search keywords that fetched it.
    # A board feed does not — Monzo's returns Benefits Specialist, Buyer, Legal
    # Counsel and Tech Recruiter alongside the engineering roles. The shortlist
    # would hide those anyway, but there is no reason to carry them in the store.
    if shortlist.NON_TECH.search(title):
        return "non-software role"
    if not shortlist.TECH_SIGNAL.search(title):
        return "no tech signal in title"

    reason = uk_location_reason(row.get("location", ""), title)
    if reason:
        return reason
    deadline = row.get("apply_deadline") or ""
    if deadline:
        today = today or dt.date.today().isoformat()
        if deadline < today:
            return f"deadline passed ({deadline})"
    return None
