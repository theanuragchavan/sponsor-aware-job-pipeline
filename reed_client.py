"""
reed_client.py — Reed.co.uk Jobseeker API client.

The sibling of adzuna_client.py. Same job, different aggregator, and it fills a
gap Adzuna cannot: Reed has a real server-side contract-type filter, so
"permanent only" is enforced by the API instead of by post-filtering rows we
already paid a call for.

Setup:
    Add to .env (never commit it):
        REED_API_KEY=your_key

    Free key: https://www.reed.co.uk/developers/jobseeker

Auth is the part people get wrong: HTTP Basic with the key as the USERNAME and
an EMPTY password. Not a bearer token, not an X-API-Key header.

Two shape traps, both confirmed live on 2026-08-21:

  * Dates are DD/MM/YYYY, not ISO. A naive `[:10]` slice yields "06/08/2026"
    and every date comparison downstream silently misreads August as June.
  * Descriptions come back double-encoded: UTF-8 bytes served as cp1252, so an
    em dash arrives as "â€"". _fix_mojibake repairs it.

Reed documents no rate limit beyond resultsToTake <= 100. That is not licence
to hammer it; SLEEP_SECONDS below keeps the pace civil.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import time

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

API_KEY = os.getenv("REED_API_KEY")
BASE_URL = "https://www.reed.co.uk/api/1.0/search"

MAX_PER_PAGE = 100          # Reed's own ceiling on resultsToTake
SLEEP_SECONDS = 1.5
TIMEOUT = 30
MAX_RETRIES = 3
RETRY_STATUSES = {429, 500, 502, 503, 504}

SOURCE = "reed"             # row-id prefix; tracker.source_of splits on ":"

_SESSION = requests.Session()
_SESSION.headers.update(
    {"User-Agent": "adzuna-job-tracker/1.0 (personal job search)"})


class ReedAuthError(RuntimeError):
    """The key is missing or rejected. Not retryable."""


def _require_key():
    if not API_KEY:
        raise ReedAuthError(
            "Missing REED_API_KEY. Add it to a local .env file (never commit "
            "it) — free key at https://www.reed.co.uk/developers/jobseeker")
    return API_KEY


def _fix_mojibake(text):
    """Repair UTF-8 that Reed served as cp1252 ("â€"" -> "—").

    Only applied when it round-trips cleanly; text that is already correct is
    returned untouched rather than mangled by a speculative re-decode.
    """
    if not text or "Â" not in text and "â" not in text:
        return text
    try:
        return text.encode("cp1252").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


def to_date(value):
    """DD/MM/YYYY -> ISO. Returns "" rather than raising on anything else."""
    if not value:
        return ""
    try:
        return dt.datetime.strptime(str(value).strip(),
                                    "%d/%m/%Y").date().isoformat()
    except (ValueError, TypeError):
        logger.debug("unparseable Reed date %r", value)
        return ""


def _get(params):
    backoff = 2.0
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = _SESSION.get(BASE_URL, params=params,
                                auth=(_require_key(), ""), timeout=TIMEOUT)
        except requests.RequestException as exc:
            if attempt < MAX_RETRIES:
                time.sleep(backoff)
                backoff *= 2
                continue
            raise
        if resp.status_code in (401, 403):
            raise ReedAuthError(
                f"Reed rejected the key (HTTP {resp.status_code}). Check "
                "REED_API_KEY, and that it is sent as the Basic-auth username "
                "with a blank password.")
        if resp.status_code in RETRY_STATUSES and attempt < MAX_RETRIES:
            logger.warning("HTTP %d from Reed — retrying in %.1fs",
                           resp.status_code, backoff)
            time.sleep(backoff)
            backoff *= 2
            continue
        resp.raise_for_status()
        return resp.json()
    raise RuntimeError("Reed retries exhausted")


def search_jobs(keywords, location="", distance=None, contract_type="permanent",
                salary_min=None, max_pages=1, per_page=MAX_PER_PAGE):
    """Return a list of raw Reed postings.

    contract_type maps onto Reed's own boolean filters, which is the whole
    reason this client exists. Anything unrecognised means "no filter".
    """
    flags = {"permanent": {"permanent": "true"},
             "contract": {"contract": "true"},
             "temporary": {"temp": "true"},
             "part_time": {"partTime": "true"},
             "full_time": {"fullTime": "true"}}.get(contract_type or "", {})

    out, skip = [], 0
    for page in range(max_pages):
        params = {"keywords": keywords,
                  "resultsToTake": min(per_page, MAX_PER_PAGE),
                  "resultsToSkip": skip}
        if location:
            params["locationName"] = location
            if distance:
                params["distanceFromLocation"] = distance
        if salary_min:
            params["minimumSalary"] = salary_min
        params.update(flags)

        data = _get(params)
        results = data.get("results") or []
        out.extend(results)
        total = data.get("totalResults") or 0
        skip += len(results)
        if not results or skip >= total:
            break
        if page + 1 < max_pages:
            time.sleep(SLEEP_SECONDS)
    return out


def to_row(raw, tier=""):
    """Map one Reed posting onto the tracker's row contract.

    Salary arrives as a float and is normalised to a plain int string, because
    the store writes every cell as text and "35000.0" sorts and reads badly.
    """
    def money(val):
        return "" if val in (None, "") else str(int(float(val)))

    return {
        "id": f"{SOURCE}:{raw.get('jobId', '')}",
        "title": _fix_mojibake(raw.get("jobTitle", "") or "").strip(),
        "company": _fix_mojibake(raw.get("employerName", "") or "").strip(),
        "location": (raw.get("locationName") or "").strip(),
        "salary_min": money(raw.get("minimumSalary")),
        "salary_max": money(raw.get("maximumSalary")),
        "contract_type": "",
        "category": "",
        "tier": tier,
        "posted_date": to_date(raw.get("date")),
        "redirect_url": raw.get("jobUrl", ""),
        "date_first_seen": dt.date.today().isoformat(),
        "status": "new",
        "sponsor_match": "",
        "sponsor_rating": "",
        "sponsor_route": "",
        "applied_via": "",
        "date_applied": "",
        "notes": "",
        "description": _fix_mojibake(raw.get("jobDescription", "") or "")[:2000],
        "source": SOURCE,
        "apply_deadline": to_date(raw.get("expirationDate")),
    }
