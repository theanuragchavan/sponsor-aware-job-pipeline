"""Things worth knowing about a posting before you spend an evening on it.

Display only. Nothing here changes a score, and that is deliberate rather than
cautious: `shortlist.score()`'s weights are frozen because there is no outcome
data to tune them against, and a signal computed from the same data the ranker
already sees would be tuning by the back door.

The cautions here are **signals, not accusations**. A long-open requisition is
sometimes a real role with a slow panel, and a company that reposts monthly is
sometimes a company that genuinely hires monthly. The output says what was
observed and leaves the inference to him.

The distinction that makes this worth having
--------------------------------------------
A naive duplicate count conflates two opposite things, and the real tracker has
a clean example of each:

- **Graphcore, "AI Research Engineer"** -- three Greenhouse ids, consecutive
  (8632581002/2002/3002), *the same posted date*, and locations London, Bristol
  and Cambridge. The same role open in three cities. Whether that is one
  requisition split by location or three real openings is not knowable from
  outside, and for someone who will take any of the three it does not matter:
  it is three chances at the same team. Reported as an opportunity.
- **Faculty, "Machine Learning Engineer"** -- six Ashby ids with six *different*
  posted dates spread from 2025-12 to 2026-07. That is the same role advertised
  again and again over seven months, which is the evergreen/hard-to-fill/ghost
  pattern.

Both show up as "6 copies" to anything counting rows. The posted date separates
them, and they mean opposite things: the first is more ways in, the second is a
reason to look twice.

The 632 reposted title groups in the store are dominated by agencies -- Noir
posting the same ".NET Developer" ad 90 times, ITOL Recruit 89 -- but those are
already dropped by the agency filter. What survives every filter is 131 of 344
rows, which is where this actually earns its place.
"""
from __future__ import annotations

import datetime as dt
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipeline import shortlist  # noqa: E402

#: Distinct posting dates before a role reads as repeatedly advertised. Two is
#: a re-list, which is ordinary; three separate dates is a pattern. Stated as a
#: judgement rather than a measurement -- there is no outcome data to fit it to,
#: and pretending otherwise is how a guess acquires false authority.
REPOST_DATES = 3

#: Days the re-postings must span before they read as *repeated* advertising.
#: Without it the signal fired on Archangel Lightworks -- three ids over three
#: days, which is one hiring push churning through aggregator ids, not a role
#: being re-advertised. Faculty's six postings span seven months, which is the
#: pattern actually worth knowing about.
REPOST_SPAN_DAYS = 30

#: Days open before age is worth mentioning. `disqualify()` uses 30 as a
#: *drop* threshold when asked; this is a higher bar for a remark, because
#: mentioning something is much cheaper than binning it.
LONG_OPEN_DAYS = 90


def title_key(row: dict) -> tuple[str, str]:
    """Company and title, normalised, as the grouping key."""
    def norm(s: str) -> str:
        return re.sub(r"[^a-z0-9 ]", "", (s or "").lower()).strip()
    return (norm(row.get("company", "")), norm(row.get("title", "")))


def index(rows: list[dict]) -> dict:
    """Group rows by company and title so copies can be counted."""
    out = defaultdict(list)
    for row in rows:
        out[title_key(row)].append(row)
    return dict(out)


def signals(row: dict, idx: dict, *, include_age: bool = True) -> list[str]:
    """Everything worth remarking on about this posting, or an empty list.

    Empty is the common and correct answer. A signal generator that always
    finds something teaches people to ignore it.
    """
    out: list[str] = []

    copies = idx.get(title_key(row), [])
    if len(copies) > 1:
        dates = {(r.get("posted_date") or "").strip()[:10]
                 for r in copies if (r.get("posted_date") or "").strip()}
        locations = {(r.get("location") or "").strip()
                     for r in copies if (r.get("location") or "").strip()}

        if len(dates) <= 1 and len(locations) > 1:
            # The same role open in several cities. Graphcore's London /
            # Bristol / Cambridge trio.
            #
            # The first version called this "one role, not 3" and told him not
            # to apply three times. That was wrong twice over. From outside you
            # cannot tell a single requisition split by city from three real
            # openings -- Greenhouse mints one id per location either way -- so
            # asserting "one role" states a guess as a fact. And he is
            # location-flexible: whichever city answers is a city he will work
            # in, which makes three postings three chances at the same team
            # rather than a duplicate to tidy away.
            #
            # So this is the one entry here that is an opportunity rather than
            # a caution, and it is phrased as one.
            # "also open in" should not list the city you are already reading.
            here = (row.get("location") or "").strip()
            others = sorted(loc for loc in locations if loc != here)
            if others:
                out.append(
                    f"same role also open in {', '.join(others[:4])}"
                    f"{' and more' if len(others) > 4 else ''} — "
                    f"{len(copies)} separate postings you can each apply to")
        elif len(dates) >= REPOST_DATES:
            try:
                real = sorted(d for d in dates if d)
                first = dt.date.fromisoformat(real[0])
                last = dt.date.fromisoformat(real[-1])
                span = (last - first).days
            except (ValueError, IndexError):
                span = None
            if span is not None and span >= REPOST_SPAN_DAYS:
                out.append(
                    f"advertised {len(dates)} separate times over {span} days "
                    f"— often an evergreen pipeline ad or a role that keeps "
                    f"not being filled")

    # `include_age=False` for callers that already report it. shortlist.py's
    # score reasons carry "386d old" of their own, and printing "open 386 days"
    # underneath restates it -- which is the noise this module's own docstring
    # warns about. The repost and multi-location signals are the ones the
    # shortlist has no other way to know.
    if include_age:
        age = shortlist.age_days(row)
        if age is not None and age >= LONG_OPEN_DAYS:
            out.append(f"open {age} days")
    return out


def main(argv=None) -> int:
    import argparse

    import config
    import tracker

    ap = argparse.ArgumentParser(
        description="Signals worth knowing before applying: extra cities to apply to, and requisitions that keep being re-advertised.")
    ap.add_argument("--all", action="store_true",
                    help="include rows the shortlist already drops")
    args = ap.parse_args(argv)

    rows = list(tracker.load_tracker(str(ROOT / config.JOBS_XLSX)).values())
    idx = index(rows)

    if not args.all:
        rows = [r for r in rows
                if shortlist.disqualify(r) is None
                and not shortlist.is_agency(r.get("company", ""))]

    flagged = [(r, s) for r in rows if (s := signals(r, idx))]
    flagged.sort(key=lambda p: (p[0].get("company", ""), p[0].get("title", "")))

    for row, sigs in flagged:
        print(f"{row.get('company','?')[:30]:<30} "
              f"{(row.get('title') or '?')[:44]:<44} {row.get('id')}")
        for s in sigs:
            print(f"      {s}")

    print()
    print(f"{len(flagged)} of {len(rows)} rows carry a signal. Nothing here "
          f"changes a score, hides a row, or stops you applying.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
