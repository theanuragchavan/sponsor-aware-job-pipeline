"""
ats_main.py — pull jobs straight from employers' own ATS job boards.

The sibling of main.py. Adzuna is keyword-first; this is employer-first, and the
data is better: full job descriptions, the real apply URL on the company's own
site, and a genuine application deadline.

    python ats_main.py --dry-run              # show everything, write nothing
    python ats_main.py --board monzo --dry-run
    python ats_main.py --max-new 300          # write, with a blast-radius cap

Deliberately NOT wired into run_tracker.bat yet. The tracker merge is
insert-only — it never revisits an id — so a mapping bug that writes several
hundred wrong rows at 09:00 has to be undone by hand in Excel. Run it manually
until the drop table has been boring for a week.

It keeps its own call counter. config.DAILY_CALL_CAP guards the *Adzuna* free
tier, and spending that budget on unauthenticated ATS endpoints would starve the
daily search run for no reason.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import logging
import os
import sys
import time
from pathlib import Path

ADZUNA_HOME = Path(__file__).resolve().parent
sys.path.insert(0, str(ADZUNA_HOME))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

import config  # noqa: E402
import sponsor_check  # noqa: E402
import tracker  # noqa: E402
from ats import boards as boards_mod  # noqa: E402
from ats import clients, mapping  # noqa: E402

LOG_DIR = ADZUNA_HOME / "logs"
DATA_DIR = ADZUNA_HOME / "data"
CALL_COUNTER = DATA_DIR / "ats_call_counter.json"
CLOSED_PATH = DATA_DIR / "ats_closed.json"

DAILY_FETCH_CAP = 400
SLEEP_SECONDS = 1.5

logger = logging.getLogger("ats")


def _setup_logging():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG_DIR / "ats.log", encoding="utf-8")],
    )


def _load_calls():
    if not CALL_COUNTER.exists():
        return 0
    try:
        data = json.loads(CALL_COUNTER.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return 0
    return data.get("count", 0) if data.get("date") == _today() else 0


def _save_calls(count):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CALL_COUNTER.write_text(
        json.dumps({"date": _today(), "count": count}), encoding="utf-8")


def _today():
    return dt.date.today().isoformat()


def _load_closed():
    if not CLOSED_PATH.exists():
        return {}
    try:
        return json.loads(CLOSED_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_closed(closed):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CLOSED_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(closed, indent=2, sort_keys=True),
                   encoding="utf-8")
    os.replace(tmp, CLOSED_PATH)


def record_closures(board, seen_ids, tracked, closed):
    """Mark rows from this board that no longer appear in its feed.

    Only ever called after a SUCCESSFUL fetch. A network failure returning zero
    jobs must never be read as "the employer closed everything" — that is why
    this is not driven off the job count.

    This is why ATS rows are not probed for liveness the way Adzuna rows are:
    feed absence is exact and free, whereas Greenhouse serves HTTP 200 on a
    redirect for a pulled posting, so probing the job URL would report it live.
    """
    prefix = f"{board['ats_prefix']}:"
    newly = 0
    for jid, row in tracked.items():
        if not jid.startswith(prefix):
            continue
        if row.get("company", "") != board.get("company", ""):
            continue
        if jid in seen_ids or jid in closed:
            continue
        closed[jid] = {"closed_on": _today(),
                       "reason": f"absent from {board['ats']} feed"}
        newly += 1
    return newly


def run(args):
    all_boards = boards_mod.load_boards()
    if not all_boards:
        print(f"No boards configured. Add rows to {boards_mod.BOARDS_CSV}")
        return 1

    targets = list(boards_mod.iter_active(all_boards, args.only, args.board))
    if not targets:
        print("No active boards match that filter.")
        return 1

    calls = _load_calls()
    if calls >= DAILY_FETCH_CAP:
        print(f"Daily ATS fetch cap reached ({calls}/{DAILY_FETCH_CAP}).")
        return 0

    lookup = sponsor_check.load_sponsor_lookup()
    if lookup is None:
        logger.warning("Sponsor register unavailable — rows will be 'unconfirmed'")
    aliases = sponsor_check.load_aliases()

    tracked = tracker.load_tracker(str(ADZUNA_HOME / config.JOBS_XLSX))
    closed = _load_closed()

    drops = collections.Counter()
    candidates, fetched, closures = [], 0, 0

    for board in targets:
        if calls >= DAILY_FETCH_CAP:
            print("Fetch cap reached mid-run; stopping.")
            break
        family, slug = board["ats"], board["slug"]
        try:
            feed_company, raw_jobs = clients.fetch(family, slug)
            calls += 1
            _save_calls(calls)
        except clients.BoardNotFound:
            boards_mod.mark(all_boards, board, "not_found",
                            f"404 on {_today()} — wrong slug?")
            logger.warning("%s/%s: 404", family, slug)
            print(f"  {board['company']:<22} 404 — wrong slug, left in registry")
            continue
        except clients.BoardError as exc:
            # Explicitly NOT a status change. An outage must never demote a board.
            logger.warning("%s/%s: %s", family, slug, exc)
            print(f"  {board['company']:<22} transient error — unchanged ({exc})")
            continue
        except KeyError as exc:
            print(f"  {board['company']:<22} {exc}")
            continue

        fetched += 1
        board["ats_prefix"] = mapping.PREFIXES.get(family, family)
        boards_mod.mark(all_boards, board,
                        "active" if raw_jobs else "empty",
                        f"{len(raw_jobs)} jobs on {_today()}")

        seen_ids = set()
        kept_here = 0
        for raw in raw_jobs:
            row = mapping.to_row(family, raw, board)
            seen_ids.add(row["id"])
            reason = mapping.drop_reason(row)
            if reason:
                # Collapse the per-city variants so the drop table stays
                # readable; the individual cities are in the log.
                drops["not a UK location" if reason.startswith("not a UK")
                      else reason] += 1
                logger.debug("DROP %s | %s | %s", row["id"], row["title"], reason)
                continue
            if row["id"] in tracked:
                drops["already tracked"] += 1
                continue
            # register_name is the exact Home Office string; the feed's company
            # name usually isn't (Greenhouse says "Monzo", register says
            # "MONZO BANK"), so the gate would fail on the collected job.
            match_name = board.get("register_name") or row["company"]
            m, rating, route = sponsor_check.match_company(
                match_name, lookup, aliases)
            row["sponsor_match"], row["sponsor_rating"], row["sponsor_route"] = (
                m, rating, route)
            candidates.append(row)
            kept_here += 1

        closures += record_closures(board, seen_ids, tracked, closed)
        print(f"  {board['company']:<22} {family:<11} "
              f"{len(raw_jobs):>4} jobs -> {kept_here:>3} new candidates"
              + (f"  (feed name: {feed_company})" if feed_company else ""))
        time.sleep(SLEEP_SECONDS)

    if args.max_new and len(candidates) > args.max_new:
        print(f"\nCapping at --max-new {args.max_new} "
              f"(had {len(candidates)}).")
        candidates = candidates[: args.max_new]

    print(f"\nFetched {fetched} board(s), {calls} calls today.")
    print(f"{len(candidates)} new rows; {closures} previously-seen rows now closed.")
    if drops:
        print("\nDropped:")
        for reason, n in drops.most_common():
            print(f"  {n:>4}  {reason}")

    if args.dry_run:
        print("\nDRY RUN — nothing written.")
        for row in candidates[:15]:
            print(f"  [{row['sponsor_match']:<11}] {row['tier'] or '-'} "
                  f"{row['title'][:52]:<52} {row['location'][:26]}")
        if len(candidates) > 15:
            print(f"  ... and {len(candidates) - 15} more")
        return 0

    added = tracker.merge_rows(tracked, candidates)
    if added:
        tracker.save_tracker(str(ADZUNA_HOME / config.JOBS_XLSX), tracked)
    boards_mod.save_boards(all_boards)
    _save_closed(closed)
    print(f"\nWrote {len(added)} new rows to {config.JOBS_XLSX}.")
    for row in added:
        logger.info("NEW %s | %s | %s | %s | sponsor=%s",
                    row["id"], row["company"], row["title"],
                    row["location"], row["sponsor_match"])
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Ingest jobs from ATS boards")
    ap.add_argument("--dry-run", action="store_true",
                    help="show what would happen; write nothing")
    ap.add_argument("--board", metavar="SLUG", help="only this board slug")
    ap.add_argument("--only", metavar="FAMILY",
                    help="only this ATS family, e.g. greenhouse")
    ap.add_argument("--max-new", type=int, default=300, metavar="N",
                    help="blast-radius cap on new rows per run (default 300)")
    args = ap.parse_args()
    _setup_logging()
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
