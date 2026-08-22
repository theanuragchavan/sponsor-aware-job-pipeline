"""
hn_hiring.py -- harvest UK roles from Hacker News "Ask HN: Who is hiring?".

Once a month @whoishiring posts a thread and founders reply with one job each.
These are small startups that never buy a job-board listing, so nothing in
Adzuna, Reed or the ATS registry will ever surface them. That is the entire
argument for this module; the volume is not.

Measured on the August 2026 thread (2026-08-21):
    243 postings, 16 mentioning a UK location, 4 of those mentioning a visa.

So this is a low-yield, high-relevance source. It runs MONTHLY, by hand:

    python hn_hiring.py --dry-run          # show what it would add
    python hn_hiring.py                    # merge into the tracker

Deliberately NOT in run_tracker.bat. The thread changes once a month, so a
daily run would re-scan the same 243 comments thirty times for nothing.

Two free, keyless Algolia endpoints, no account:
    /search_by_date?tags=story,author_whoishiring
    /items/{story_id}                       (the whole comment tree)

On parsing: the convention is "Company | Role | Location | REMOTE | ...", but
field ORDER is not fixed. Real examples from one thread:

    Snout https://snout.com/ | Multiple Engineering Roles | Remote US | Full Time
    Flywheel Motion ( url ) | REMOTE (worldwide) | Contract        <- no role
    PostHog | Full-Time | Technical CSMs, AI Research Engineer | REMOTE

Only the FIRST field is reliably the company. Everything after it is guesswork,
so this module does not pretend otherwise: it fills `company` confidently,
takes a best-effort `title`, and leaves the raw posting in `description` for a
human to read. `redirect_url` is the HN permalink, which always resolves, and
never a scraped apply link, which often does not.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import logging
import re
import sys
import urllib.request

import config
import sponsor_check
import tracker

logger = logging.getLogger("hn_hiring")

SOURCE = "hn"
SEARCH_URL = ("https://hn.algolia.com/api/v1/search_by_date"
              "?tags=story,author_whoishiring&query=hiring&hitsPerPage=10")
ITEM_URL = "https://hn.algolia.com/api/v1/items/%s"
PERMALINK = "https://news.ycombinator.com/item?id=%s"
USER_AGENT = "adzuna-job-tracker/1.0 (personal job search)"
TIMEOUT = 45

# Word-boundary anchored so "UK" does not match "Ukraine".
UK_PATTERNS = [
    r"\bUnited Kingdom\b", r"\bU\.?K\.?\b", r"\bLondon\b", r"\bManchester\b",
    r"\bEdinburgh\b", r"\bGlasgow\b", r"\bBristol\b", r"\bLeeds\b",
    r"\bOxford\b", r"\bCambridge\b", r"\bBirmingham\b", r"\bSheffield\b",
    r"\bScotland\b", r"\bEngland\b", r"\bWales\b",
]
_UK_RE = re.compile("|".join(UK_PATTERNS), re.I)

# "Cambridge, MA" and "Manchester, NH" are not the ones we mean. Same trap the
# ATS mapper hit with New York vs York -- see ats.mapping.uk_location_reason.
_US_STATE_RE = re.compile(
    r"\b(?:Cambridge|Manchester|Birmingham|Bristol|Oxford)\s*,\s*"
    r"(?:MA|NH|AL|CT|MS|TN|NY|NJ|PA|CA|TX|OH|Massachusetts|New Hampshire)\b",
    re.I)

_TAG_RE = re.compile(r"<[^>]+>")
_URL_RE = re.compile(r"https?://\S+")


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def clean_text(raw):
    """HN comments are HTML fragments with <p> separators and entities."""
    if not raw:
        return ""
    text = _TAG_RE.sub(" ", raw)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def find_latest_thread():
    """Return (story_id, title) of the most recent "Who is hiring" thread.

    @whoishiring also posts "Who wants to be hired?" and "Freelancer?" in the
    same window, so the title has to be checked rather than taking hits[0].
    """
    data = _get(SEARCH_URL)
    for hit in data.get("hits") or []:
        if "who is hiring" in (hit.get("title") or "").lower():
            return hit["objectID"], hit["title"]
    raise RuntimeError("no 'Who is hiring' thread found in the last 10 posts")


def fetch_postings(story_id):
    """Top-level comments only. Replies are discussion, not job ads."""
    data = _get(ITEM_URL % story_id)
    out = []
    for child in data.get("children") or []:
        text = clean_text(child.get("text"))
        if text:
            out.append({"id": str(child.get("id")), "text": text,
                        "created_at": child.get("created_at") or ""})
    return out


def looks_uk(text):
    """True if the posting plausibly offers a UK-based role."""
    if _US_STATE_RE.search(text):
        return False
    return bool(_UK_RE.search(text))


def mentions_visa(text):
    return bool(re.search(r"\bvisa\b|\bsponsor", text, re.I))


def parse_company(text):
    """The first pipe-delimited field, which is the one reliable convention.

    Strips a trailing URL and any parenthesised domain, both common:
        "Snout https://snout.com/"       -> "Snout"
        "Amodo Design (amododesign.com)" -> "Amodo Design"
    """
    head = text.split("|", 1)[0]
    head = _URL_RE.sub("", head)
    head = re.sub(r"\([^)]*\.[a-z]{2,}[^)]*\)", "", head, flags=re.I)
    head = head.strip(" -,:;")
    # Some postings use no pipes at all, so `head` is the entire comment. A
    # company name is a few words; anything longer is prose that must not be
    # written into the company column, where sponsor matching reads it.
    # With a pipe, field one is the company and is already short. Without one,
    # the posting does not follow the convention and `head` is the whole
    # comment, so it must be cut. Length is the wrong trigger for that -- a
    # 48-character sentence slips under any sane threshold -- so key it on the
    # missing pipe instead.
    if "|" not in text:
        head = re.split(r"[.!?—–]| - ", head)[0].strip()
        if len(head) > 40:
            # No delimiter either. Any multi-word guess is prose, so take the
            # first token: a short wrong name beats a paragraph in the column
            # sponsor matching reads.
            head = head.split()[0] if head.split() else ""
    return head[:80]


def parse_title(text):
    """Best effort. Returns "" rather than guessing when no field looks like a
    role, because a wrong title is worse than a blank one: it feeds both the
    seniority filter and the tier classifier."""
    fields = [f.strip() for f in text.split("|")[1:5]]
    role_hint = re.compile(
        r"engineer|developer|scientist|designer|manager|analyst|research|"
        r"founding|architect|lead\b|intern|graduate|sre|devops|full.?stack|"
        r"front.?end|back.?end|data\b|ml\b|ai\b", re.I)
    noise = re.compile(
        r"^(remote|onsite|hybrid|full.?time|part.?time|contract|visa|"
        r"relocation)", re.I)
    for field in fields:
        if not field or noise.match(field):
            continue
        # A posting with no trailing "|" puts its whole body in the last field,
        # so a bare role_hint match will happily return a paragraph of prose.
        # A link is the reliable tell; the length cap is generous on purpose
        # because a real field often lists four roles at once, and cutting it
        # short would hide those titles from the seniority filter entirely.
        if "http" in field or len(field) > 140:
            continue
        if role_hint.search(field):
            return field[:120]
    return ""


def to_row(posting, tier=""):
    """Map one comment onto the tracker's row contract.

    `redirect_url` is the HN permalink on purpose: the apply link inside a
    posting is inconsistent and often dead, while the permalink always resolves
    and carries the full text the human actually needs to read.
    """
    text = posting["text"]
    return {
        "id": f"{SOURCE}:{posting['id']}",
        "title": parse_title(text),
        "company": parse_company(text),
        "location": "UK (see posting)",
        "salary_min": "",
        "salary_max": "",
        "contract_type": "",
        "category": "",
        "tier": tier,
        "posted_date": (posting.get("created_at") or "")[:10],
        "redirect_url": PERMALINK % posting["id"],
        "date_first_seen": dt.date.today().isoformat(),
        "status": tracker.DEFAULT_STATUS,
        "sponsor_match": "",
        "sponsor_rating": "",
        "sponsor_route": "",
        "applied_via": "",
        "date_applied": "",
        "notes": "visa mentioned" if mentions_visa(text) else "",
        "description": text[:2000],
        "source": SOURCE,
        "apply_deadline": "",
    }


def run(dry_run=False):
    story_id, title = find_latest_thread()
    logger.info("Thread: %s (id %s)", title, story_id)

    postings = fetch_postings(story_id)
    uk = [p for p in postings if looks_uk(p["text"])]
    logger.info("%d postings, %d UK-relevant", len(postings), len(uk))

    lookup = sponsor_check.load_sponsor_lookup()
    tracked = tracker.load_tracker(config.JOBS_XLSX)

    rows, skipped = [], 0
    for posting in uk:
        row = to_row(posting)
        if row["id"] in tracked:
            continue
        if row["title"]:
            reason = tracker.unsuitable_reason(row["title"])
            if reason:
                logger.info("  filtered (%s): %s @ %s", reason,
                            row["title"], row["company"])
                skipped += 1
                continue
        rows.append(tracker.attach_sponsorship(row, lookup))

    if dry_run:
        logger.info("DRY RUN -- nothing written. %d would be added (%d filtered)",
                    len(rows), skipped)
        for row in rows:
            flag = " [visa]" if row["notes"] else ""
            logger.info("  %-32s | %-44s | %s%s",
                        row["company"][:32], (row["title"] or "(no title)")[:44],
                        row["sponsor_match"], flag)
        return rows

    new_rows = tracker.merge_rows(tracked, rows)
    tracker.save_tracker(config.JOBS_XLSX, tracked)
    logger.info("Added %d new rows (%d filtered)", len(new_rows), skipped)
    return new_rows


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description="Harvest UK roles from HN")
    parser.add_argument("--dry-run", action="store_true",
                        help="show what would be added, write nothing")
    args = parser.parse_args()
    try:
        run(dry_run=args.dry_run)
    except Exception:
        logger.exception("HN hiring run failed")
        sys.exit(1)
