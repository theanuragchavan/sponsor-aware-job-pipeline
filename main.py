"""
CLI entrypoint — run every saved search, merge new jobs into the tracker CSV,
and print a summary that lists sponsor-confirmed matches first.

    python main.py

Respects Adzuna's free-tier budget: sleeps between calls and keeps a per-day
call counter that refuses to run past DAILY_CALL_CAP (~240) in a single day.
"""

import datetime as dt
import json
import logging
import os
import sys
import time

import adzuna_client
import config
import sponsor_check
import tracker

# Force UTF-8 on the console before anything can print. The run summary carries
# "£" and "—", and a plain `cmd` window on cp437/cp850 can encode neither, so an
# unhandled UnicodeEncodeError would kill the run *after* the API budget had
# already been spent. errors="replace" degrades a glyph rather than crashing.
# Task Scheduler supplies UTF-8, which is why this has never been seen to fail.
# Same guard as pipeline/shortlist.py; each entry point sets up its own console.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):   # not a reconfigurable text stream
        pass

# Log to BOTH console and a file, so a headless / scheduled run (Task Scheduler
# discards console output) still captures the summary and any errors. See
# Section 9 of the brief.
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "tracker.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)
logger = logging.getLogger("main")

CALL_COUNTER = os.path.join(sponsor_check.DATA_DIR, "call_counter.json")


# --- Daily call budget ------------------------------------------------------
def _load_call_count():
    today = dt.date.today().isoformat()
    try:
        with open(CALL_COUNTER, encoding="utf-8") as f:
            data = json.load(f)
        if data.get("date") == today:
            return data["count"]
    except (OSError, json.JSONDecodeError, KeyError):
        pass
    return 0


def _save_call_count(count):
    os.makedirs(sponsor_check.DATA_DIR, exist_ok=True)
    with open(CALL_COUNTER, "w", encoding="utf-8") as f:
        json.dump({"date": dt.date.today().isoformat(), "count": count}, f)


# --- Run --------------------------------------------------------------------
def run():
    calls_today = _load_call_count()
    if calls_today >= config.DAILY_CALL_CAP:
        logger.warning("Already made %d calls today (cap %d) — not running.",
                       calls_today, config.DAILY_CALL_CAP)
        return

    lookup = sponsor_check.load_sponsor_lookup()  # None -> matches 'unconfirmed'
    if lookup is None:
        logger.warning("Sponsor register unavailable — jobs marked 'unconfirmed'.")

    tracked = tracker.load_tracker(config.JOBS_XLSX)
    new_rows = []
    skipped_noise = 0
    budget_hit = False

    for search in config.SAVED_SEARCHES:
        if budget_hit:
            break
        for page in range(1, config.MAX_PAGES_PER_SEARCH + 1):
            if calls_today >= config.DAILY_CALL_CAP:
                logger.warning("Hit daily call cap (%d) — stopping early.",
                               config.DAILY_CALL_CAP)
                budget_hit = True
                break

            jobs = adzuna_client.search_jobs(
                what=search["keywords"],
                where=search["location"],
                distance=search["distance"],
                contract_type=search["contract_type"],
                salary_include_unknown=search["salary_include_unknown"],
                max_days_old=search["max_days_old"],
                category=search["category"],
                page=page,
            )
            calls_today += 1
            _save_call_count(calls_today)

            if not jobs:
                break
            # Drop unsuitable titles before merging — trades/construction noise
            # AND roles above the target seniority (logged, never silent).
            # New jobs only — existing rows are untouched.
            clean = []
            for job in jobs:
                reason = tracker.unsuitable_reason(job.get("title", ""))
                if reason:
                    if str(job.get("id", "")) not in tracked:
                        logger.info("  filtered (%s): %s @ %s", reason,
                                    job.get("title", ""),
                                    (job.get("company") or {}).get("display_name", ""))
                        skipped_noise += 1
                else:
                    clean.append(job)
            found = tracker.merge_new_jobs(tracked, clean, search["tier"], lookup)
            new_rows.extend(found)
            logger.info("T%d [%s] '%s' p%d: %d results, %d new",
                        search["tier"], search["category"] or "-",
                        search["keywords"], page, len(jobs), len(found))

            if page < config.MAX_PAGES_PER_SEARCH:
                time.sleep(config.SLEEP_SECONDS)

    tracker.save_tracker(config.JOBS_XLSX, tracked)
    _print_summary(new_rows, tracked, calls_today, skipped_noise)


# --- Summary ----------------------------------------------------------------
def _print_summary(new_rows, tracked, calls_today, skipped_noise=0):
    """Build the run summary, print it, AND log it (so headless runs keep it)."""
    confirmed = [r for r in new_rows if r["sponsor_match"] == "yes"]
    others = [r for r in new_rows if r["sponsor_match"] != "yes"]

    lines = [
        "=" * 70,
        "RUN SUMMARY",
        "=" * 70,
        f"API calls used today : {calls_today} / {config.DAILY_CALL_CAP}",
        f"New jobs this run     : {len(new_rows)}  "
        f"({len(confirmed)} sponsor-confirmed, {len(others)} not)",
        f"Filtered (unsuitable) : {skipped_noise}  (trades noise + too-senior)",
        f"Total jobs tracked    : {len(tracked)}",
    ]

    # The shortlist is the whole point: genuine, deduped, sponsor-confirmed
    # DIRECT-employer targets across the entire store (not just this run's new
    # rows) — the list to actually apply to. Recruiter/agency postings and
    # cleared/apprentice roles are stripped out.
    shortlist = tracker.apply_shortlist(list(tracked.values()))
    if shortlist:
        lines.append(f"\n--- APPLY SHORTLIST ({len(shortlist)} genuine targets) — "
                     f"see 'Apply Shortlist' sheet ---")
        lines += [_fmt(r) for r in shortlist]

    if confirmed:
        lines.append("\n--- NEW sponsor-confirmed (Skilled Worker) — apply first ---")
        lines += [_fmt(r) for r in
                  sorted(confirmed, key=lambda r: -tracker._as_int(r["salary_max"]))]

    if others:
        lines.append("\n--- Other new jobs ---")
        lines += [_fmt(r) for r in others]

    top = sorted(tracked.values(),
                 key=lambda r: -tracker._as_int(r["salary_max"]))[:5]
    if top:
        lines.append("\n--- Top 5 tracked by salary ---")
        lines += [_fmt(r) for r in top]
    lines.append("=" * 70)

    text = "\n".join(lines)
    print("\n" + text)
    logger.info("Run summary:\n%s", text)


def _fmt(r):
    sal = tracker._as_int(r["salary_max"])
    sal_str = f"£{sal:,}" if sal else "salary n/a"
    flag = {"yes": f"[SPONSOR {r['sponsor_rating'] or '?'}]",
            "no": "", "unconfirmed": "[?sponsor]"}.get(r["sponsor_match"], "")
    return (f"  {flag:13} T{r['tier']} {sal_str:12} {r['title']} "
            f"@ {r['company']} ({r['location']})")


if __name__ == "__main__":
    # Unattended-safe: log any unhandled exception with its full traceback so a
    # scheduled run never fails silently, then exit non-zero so Task Scheduler's
    # "Last Run Result" reflects the failure.
    try:
        run()
    except Exception:
        logger.exception("Tracker run failed with an unhandled exception")
        sys.exit(1)
