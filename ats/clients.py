"""
clients.py — fetch job lists from public ATS board APIs.

These endpoints need no key, no login, no browser and no scraping. Verified
live on 2026-08-14 (Greenhouse `monzo` returned 81 jobs, 60 of them London).

HTTP and parsing only. This module knows nothing about the tracker, so it can be
imported and tested without touching the store.

**Do not import adzuna_client here.** It raises RuntimeError at import time when
the Adzuna credentials are missing (adzuna_client.py:36), which would make ATS
ingestion depend on an unrelated API's keys. The retry shape below is
deliberately a copy of its `_get_with_retry`, not a reuse of it.

Failure vocabulary matters, because conflating these two silently shrinks the
registry:
    BoardNotFound   404 — the slug is wrong. Try another candidate.
    BoardError      5xx / timeout — their problem, not ours. Retry later, and
                    never demote a board because of an outage.
A 200 carrying zero jobs is NEITHER: it is a valid board that happens to be
empty today (Yoti's Workable account does exactly this), and deleting it would
throw away a good registry entry.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

# An honest User-Agent. These are free endpoints someone else pays to serve.
USER_AGENT = "adzuna-job-tracker/1.0 (personal job search; contact via GitHub)"
TIMEOUT = 20
MAX_RETRIES = 3
RETRY_STATUSES = {429, 500, 502, 503, 504}


class BoardNotFound(Exception):
    """404 — no board at this slug."""


class BoardError(Exception):
    """Transient failure. The board may be perfectly fine."""


def _get_json(url):
    backoff = 2.0
    last = None
    for attempt in range(1, MAX_RETRIES + 1):
        req = urllib.request.Request(url, headers={
            "User-Agent": USER_AGENT, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise BoardNotFound(f"404 at {url}") from exc
            if exc.code in RETRY_STATUSES and attempt < MAX_RETRIES:
                retry_after = exc.headers.get("Retry-After", "") if exc.headers else ""
                wait = float(retry_after) if retry_after.isdigit() else backoff
                logger.warning("HTTP %d from %s — retrying in %.1fs",
                               exc.code, url, wait)
                time.sleep(wait)
                backoff *= 2
                last = exc
                continue
            raise BoardError(f"HTTP {exc.code} at {url}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last = exc
            if attempt < MAX_RETRIES:
                time.sleep(backoff)
                backoff *= 2
                continue
            raise BoardError(f"{type(exc).__name__} at {url}") from exc
    raise BoardError(f"retries exhausted at {url}: {last}")


def fetch_greenhouse(slug):
    """Return (company_name, jobs) from a Greenhouse board.

    `content=true` asks for the full HTML job description in the same call,
    which saves one request per job and gives a far better `description` than
    Adzuna's truncated snippet. Greenhouse also returns `application_deadline`,
    which nothing else in this project can provide.
    """
    url = ("https://boards-api.greenhouse.io/v1/boards/%s/jobs?content=true"
           % urllib.parse.quote(slug, safe=""))
    data = _get_json(url)
    jobs = data.get("jobs") or []
    # Greenhouse has no top-level company field; it rides on each job.
    company = (jobs[0].get("company_name") or "") if jobs else ""
    return company, jobs


def fetch_ashby(slug):
    """Return (company_name, jobs) from an Ashby job board.

    Shape verified live: {"jobs": [...], "apiVersion": ...} with a flat
    `location` string and `jobUrl` per posting. Ashby has no company field, so
    the registry name is the only one available.
    """
    url = ("https://api.ashbyhq.com/posting-api/job-board/%s"
           % urllib.parse.quote(slug, safe=""))
    data = _get_json(url)
    if not isinstance(data, dict) or "jobs" not in data:
        # Ashby answers 200 with an error body for an unknown board rather
        # than 404, so an unexpected shape means "no such board", not "empty".
        raise BoardNotFound(f"no Ashby board at {slug}")
    return "", data.get("jobs") or []


def fetch_lever(slug):
    """Return (company_name, jobs) from a Lever board.

    Lever returns a bare JSON list and stamps `createdAt` as epoch
    MILLISECONDS as an int — see ats.mapping.to_date, which exists because a
    naive `[:10]` slice raises TypeError on that.
    """
    url = ("https://api.lever.co/v0/postings/%s?mode=json"
           % urllib.parse.quote(slug, safe=""))
    data = _get_json(url)
    if not isinstance(data, list):
        raise BoardNotFound(f"no Lever board at {slug}")
    return "", data


def fetch_smartrecruiters(slug):
    """Return (company_name, jobs) from a SmartRecruiters posting board.

    Two traps here, both confirmed live on 2026-08-21, and both invisible if
    you only look at the status code:

    1. There is no 404. An unknown slug answers 200 with
       {"totalFound": 0, "content": []} — byte-identical to a real board with
       nothing open today. So this fetcher CANNOT distinguish "no such board"
       from "empty board", and deliberately does not try: it returns an empty
       list and lets boards.py keep the registry entry. Probing for discovery
       against this endpoint is therefore worthless; add slugs by hand.
    2. The slug is not a company. `palantir` here is a C# consultancy in
       Westhill, Aberdeen, not Palantir Technologies. Every posting carries
       company.name, so the caller can check it against register_name.

    Postings carry no description; that needs one extra call per job via `ref`.
    Following fetch_greenhouse's one-call rule, we skip it and leave the field
    empty rather than multiplying requests against someone else's free endpoint.
    """
    quoted = urllib.parse.quote(slug, safe="")
    jobs, offset, limit = [], 0, 100
    while True:
        url = ("https://api.smartrecruiters.com/v1/companies/%s/postings"
               "?limit=%d&offset=%d" % (quoted, limit, offset))
        data = _get_json(url)
        if not isinstance(data, dict) or "content" not in data:
            raise BoardNotFound(f"no SmartRecruiters board at {slug}")
        page = data.get("content") or []
        jobs.extend(page)
        offset += limit
        total = data.get("totalFound") or 0
        if len(jobs) >= total or not page or offset > 2000:
            break
        time.sleep(0.5)          # their endpoint, their bandwidth
    company = ""
    if jobs:
        company = (jobs[0].get("company") or {}).get("name") or ""
    return company, jobs


FETCHERS = {
    "greenhouse": fetch_greenhouse,
    "ashby": fetch_ashby,
    "lever": fetch_lever,
    "smartrecruiters": fetch_smartrecruiters,
}


def fetch(family, slug):
    """Dispatch to the right board API. Raises KeyError for an unknown family."""
    try:
        fetcher = FETCHERS[family]
    except KeyError:
        raise KeyError(
            f"no client for ATS family {family!r}; known: {sorted(FETCHERS)}"
        ) from None
    return fetcher(slug)
