"""
Minimal Adzuna Search API client — starter skeleton.

Build the rest of the tracker project around this in Claude Code
(see adzuna_tracker_brief.md for the full spec).

Setup:
    pip install requests python-dotenv

    Create a .env file in the same folder (DO NOT commit it):
        ADZUNA_APP_ID=your_app_id
        ADZUNA_APP_KEY=your_app_key

Get free credentials: https://developer.adzuna.com/signup

Free-tier limits (personal research use): 25 calls/minute, 250/day,
1,000/week, 2,500/month. The pagination helper below sleeps between
calls to stay well inside the per-minute limit.
"""

import logging
import os
import time

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

APP_ID = os.getenv("ADZUNA_APP_ID")
APP_KEY = os.getenv("ADZUNA_APP_KEY")
BASE_URL = "https://api.adzuna.com/v1/api/jobs/gb/search"

if not APP_ID or not APP_KEY:
    raise RuntimeError(
        "Missing ADZUNA_APP_ID / ADZUNA_APP_KEY. Add them to a local .env "
        "file (never commit it) — register free at "
        "https://developer.adzuna.com/signup"
    )

# Reuse one connection across the many calls per run.
_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": "adzuna-job-tracker/1.0 (personal use)"})

# Retry policy for transient failures (rate limits + server errors).
MAX_RETRIES = 3
RETRY_STATUSES = {429, 500, 502, 503, 504}


def _get_with_retry(url, params, timeout=15):
    """GET with exponential backoff on 429/5xx, honoring Retry-After.

    Raises RuntimeError with a clear message on auth failure (401), and
    re-raises the last error if all retries are exhausted.
    """
    backoff = 2.0
    last_exc = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = _SESSION.get(url, params=params, timeout=timeout)
        except requests.RequestException as exc:
            last_exc = exc
            logger.warning("Request error (attempt %d/%d): %s",
                           attempt, MAX_RETRIES, exc)
            if attempt == MAX_RETRIES:
                raise
            time.sleep(backoff)
            backoff *= 2
            continue

        if resp.status_code == 401:
            raise RuntimeError(
                "Adzuna rejected the credentials (401). Check ADZUNA_APP_ID / "
                "ADZUNA_APP_KEY in your .env file."
            )
        if resp.status_code in RETRY_STATUSES:
            retry_after = resp.headers.get("Retry-After", "")
            wait = float(retry_after) if retry_after.isdigit() else backoff
            logger.warning("HTTP %d from Adzuna (attempt %d/%d) — retrying in %.1fs",
                           resp.status_code, attempt, MAX_RETRIES, wait)
            last_exc = requests.HTTPError(
                f"{resp.status_code} from Adzuna", response=resp)
            if attempt == MAX_RETRIES:
                break
            time.sleep(wait)
            backoff *= 2
            continue

        resp.raise_for_status()
        return resp

    # Retries exhausted on a retryable status.
    raise last_exc


def search_jobs(what, where="UK", salary_min=None, max_days_old=7,
                 results_per_page=20, page=1, sort_by="date",
                 category=None, distance=None, contract_type=None,
                 salary_include_unknown=False):
    """
    Fetch one page of Adzuna job search results.

    what                    -- keywords, e.g. "graduate software engineer"
    where                   -- location string, e.g. "London" or "UK"
    salary_min              -- int, optional
    max_days_old            -- only ads posted in the last N days
    results_per_page        -- up to 50
    page                    -- 1-indexed
    sort_by                 -- "date", "salary", or "relevance"
    category                -- Adzuna category tag, e.g. "it-jobs" or
                               "graduate-jobs". Leave None to search all
                               categories (only one tag per call is
                               possible — Adzuna doesn't support combining
                               categories in a single request).
    distance                -- search radius in miles from `where`
    contract_type           -- "permanent", "contract", etc.
    salary_include_unknown  -- if True, also include ads with no salary
                               listed (most grad-scheme posts have none)

    Returns a list of job dicts as given by the Adzuna API.
    """
    params = {
        "app_id": APP_ID,
        "app_key": APP_KEY,
        "what": what,
        "max_days_old": max_days_old,
        "results_per_page": results_per_page,
        "sort_by": sort_by,
        "content-type": "application/json",
    }
    if where:
        # Empty `where` = no location filter = nationwide (the GB endpoint
        # already restricts to Great Britain). We search UK-wide and prioritise
        # London at sort time instead of narrowing the query here.
        params["where"] = where
    if salary_min:
        params["salary_min"] = salary_min
    if category:
        params["category"] = category
    if distance is not None:
        params["distance"] = distance
    if contract_type:
        # Adzuna has no `contract_type` param — it uses boolean flags
        # (permanent=1, contract=1, full_time=1, part_time=1). Map to those.
        if contract_type in ("permanent", "contract", "full_time", "part_time"):
            params[contract_type] = 1
        else:
            logger.warning("Unknown contract_type %r — ignoring.", contract_type)
    if salary_include_unknown:
        params["salary_include_unknown"] = 1

    response = _get_with_retry(f"{BASE_URL}/{page}", params)
    return response.json().get("results", [])


def search_all_pages(what, max_pages=3, sleep_seconds=2.5, **kwargs):
    """
    Pull several pages back-to-back for one search, sleeping between
    calls to stay well inside Adzuna's free-tier limit of 25 calls/minute.
    """
    all_jobs = []
    for page in range(1, max_pages + 1):
        jobs = search_jobs(what, page=page, **kwargs)
        if not jobs:
            break  # ran out of results
        all_jobs.extend(jobs)
        logger.info("'%s' page %d: %d jobs", what, page, len(jobs))
        if page < max_pages:
            time.sleep(sleep_seconds)  # only between calls, not after the last
    return all_jobs


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    # Quick smoke test — confirms credentials work before building the rest.
    sample = search_jobs("graduate software engineer", where="London", max_days_old=7)
    print(f"Found {len(sample)} jobs on page 1.")
    for job in sample[:5]:
        print(f"- {job['title']} @ {job['company']['display_name']} "
              f"({job['location']['display_name']})")
