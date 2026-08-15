"""
liveness.py — is this job ad still up?

Adzuna has no deadline or expiry field, so "has the deadline passed" has to be
answered by asking the listing itself. When an ad is delisted Adzuna serves
**HTTP 410 Gone** on `/jobs/details/<id>` ("Page not found"); a live ad serves
200 with the real job title. That 200/410 split is the whole signal, and it is
a checkable fact rather than a guess.

Two things make this awkward, both verified by experiment on 2026-08-14:

1. **Direct requests get IP-blocked.** Five quick fetches from this machine and
   Adzuna returned 403 to everything afterwards, including URLs that had just
   answered 200 — and it stayed 403 at 12-second spacing. So a plain urllib
   prober cannot be trusted: its 403s are indistinguishable from real failures.
   We go through Firecrawl, which gets clean 200/410 through the block.
2. **A 410 does NOT mean the job is gone.** It means the *aggregator listing*
   is gone. Yoti's ad 410s while the role itself is still open on the company's
   own board. So a dead row means "stop trusting this URL, go look at the
   employer's careers page", never "this employer isn't hiring".

Cost control: results are cached in `liveness_cache.json` and DEAD is treated
as permanent (a delisted ad never comes back under the same id), so each id is
paid for at most once. LIVE re-checks after LIVE_TTL_DAYS because a live ad can
die later. Requests are throttled to stay inside the Firecrawl free tier.

Fails OPEN by design: a probe that errors returns UNKNOWN, and UNKNOWN never
drops a row. We would rather show a dead ad than silently bin a live one.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:                                    # pragma: no cover
    load_dotenv = None

PIPELINE = Path(__file__).resolve().parent
ADZUNA_HOME = PIPELINE.parent
CACHE_PATH = PIPELINE / "liveness_cache.json"

LIVE = "live"
DEAD = "dead"
UNKNOWN = "unknown"

# Free tier measured at ~10 scrapes/min, so 7s spacing leaves headroom.
THROTTLE_SECONDS = 7.0
LIVE_TTL_DAYS = 3
FIRECRAWL_URL = "https://api.firecrawl.dev/v2/scrape"

# One key, deliberately. Rotating between the two keys in .env to get around a
# rate limit would be evading the free-tier terms, not respecting them.
KEY_VAR = "FIRECRAWL_API_KEY_1"


def _api_key() -> str | None:
    if load_dotenv is not None:
        load_dotenv(ADZUNA_HOME / ".env")
    return os.getenv(KEY_VAR)


def detail_url(job_id: str) -> str:
    """Adzuna's canonical per-job page.

    The tracker stores two URL shapes — `/jobs/details/<id>` and
    `/jobs/land/ad/<id>?se=...`. Only the first gives a clean 200/410, so we
    rebuild from the id rather than probing whatever shape was stored.

    Refuses a namespaced id. Rebuilding an Adzuna URL around, say, a Greenhouse
    id produces a page that was never going to exist, and Adzuna answers 410 for
    an unknown id on this route — which this module treats as DEAD and caches
    permanently. The row would then be dropped from every future shortlist for a
    reason that was pure fiction. Raising here means no caller can reintroduce
    that quietly.
    """
    if ":" in str(job_id):
        raise ValueError(
            f"{job_id!r} is not an Adzuna id — its liveness comes from its own "
            "source feed, not from probing an Adzuna URL")
    return f"https://www.adzuna.co.uk/jobs/details/{job_id}"


# --------------------------------------------------------------------------- #
# Cache
# --------------------------------------------------------------------------- #

def load_cache() -> dict:
    if not CACHE_PATH.exists():
        return {}
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}          # a corrupt cache costs a re-probe, not a crash


def save_cache(cache: dict) -> None:
    tmp = CACHE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cache, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, CACHE_PATH)          # atomic; never leaves a half-file


def cached_verdict(cache: dict, job_id: str) -> str | None:
    """Return a still-valid cached verdict, or None if we must re-probe."""
    entry = cache.get(str(job_id))
    if not entry:
        return None
    verdict = entry.get("verdict")
    if verdict == DEAD:
        return DEAD                       # delisted ids do not come back
    if verdict == LIVE:
        try:
            checked = datetime.fromisoformat(entry["checked"])
        except (KeyError, ValueError):
            return None
        if datetime.now() - checked < timedelta(days=LIVE_TTL_DAYS):
            return LIVE
    return None                           # stale LIVE, or a previous UNKNOWN


# --------------------------------------------------------------------------- #
# Probe
# --------------------------------------------------------------------------- #

def _probe(job_id: str, key: str, timeout: int = 90) -> tuple[str, str]:
    """One Firecrawl scrape. Returns (verdict, note)."""
    payload = json.dumps({
        "url": detail_url(job_id),
        "formats": ["markdown"],
        "onlyMainContent": True,
        "timeout": 30000,
    }).encode()
    req = urllib.request.Request(
        FIRECRAWL_URL,
        data=payload,
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return UNKNOWN, f"firecrawl http {exc.code}"
    except Exception as exc:                           # network, timeout, JSON
        return UNKNOWN, f"probe failed: {type(exc).__name__}"

    meta = (body.get("data") or {}).get("metadata") or {}
    status = meta.get("statusCode")
    title = (meta.get("title") or "").strip()

    if status == 410:
        return DEAD, "410 Gone — delisted from Adzuna"
    if status == 200:
        # Adzuna has served 200 with a not-found shell before; trust the title.
        if "page not found" in title.lower():
            return DEAD, "200 but 'Page not found' shell"
        return LIVE, title[:70]
    return UNKNOWN, f"unexpected status {status}"


def check(job_ids, *, cache=None, throttle=THROTTLE_SECONDS, verbose=False):
    """Resolve LIVE/DEAD/UNKNOWN for each id, using and updating the cache.

    Returns {job_id: (verdict, note)}. Never raises for a single bad id.
    """
    cache = load_cache() if cache is None else cache
    key = _api_key()
    results: dict[str, tuple[str, str]] = {}
    to_probe: list[str] = []

    for jid in (str(j) for j in job_ids):
        if ":" in jid:
            # Not an Adzuna row. Probing an Adzuna URL for it cannot produce a
            # true answer in either direction, and a false DEAD would be cached
            # forever. Its own source feed knows whether it is still open: a job
            # that stops appearing in a successful board fetch is closed.
            results[jid] = (UNKNOWN, "non-adzuna id — liveness comes from the feed")
            continue
        hit = cached_verdict(cache, jid)
        if hit:
            results[jid] = (hit, "cached")
        else:
            to_probe.append(jid)

    if to_probe and not key:
        if verbose:
            print(f"  no {KEY_VAR} in .env — cannot verify, treating as unknown")
        for jid in to_probe:
            results[jid] = (UNKNOWN, "no api key")
        return results

    for i, jid in enumerate(to_probe):
        if i:
            time.sleep(throttle)
        verdict, note = _probe(jid, key)
        results[jid] = (verdict, note)
        if verdict != UNKNOWN:            # never cache a transient failure
            cache[jid] = {"verdict": verdict,
                          "checked": datetime.now().isoformat(timespec="seconds"),
                          "note": note}
        if verbose:
            print(f"  {verdict.upper():<7} {jid}  {note}")

    if to_probe:
        save_cache(cache)
    return results


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Check whether Adzuna ads are live")
    ap.add_argument("ids", nargs="+", help="Adzuna job ids")
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args()

    out = check(args.ids, cache={} if args.no_cache else None, verbose=True)
    for jid, (verdict, note) in out.items():
        print(f"{jid:>12}  {verdict.upper():<7}  {note}")
