"""Why did this job not surface? Or: why is that one at the top?

Nine filters stand between a posting existing and it reaching the shortlist,
spread across `tracker.py`, `pipeline/shortlist.py` and `decisions.json`. Each
is defensible on its own. Together they are opaque: a role can look like an
obvious match and never appear, and until now the only way to find out why was
to read three modules and simulate them by hand.

That opacity has a cost beyond annoyance. A filter that silently drops good
roles looks exactly like a market with no good roles in it, and the response to
those two situations is completely different -- fix the rule, or widen the
search. Nothing in the pipeline could tell them apart.

So this walks the chain for one row and reports **every stage, passes
included**. A stage that passed is as informative as the one that failed: it is
the difference between "this filter is wrong" and "this filter is the only
thing that saved you from 200 care-assistant roles".

The rules are not restated here. Every check calls the same function the
pipeline calls, because a debugger that reimplements what it debugs will
eventually disagree with it and be believed.

    python pipeline/explain.py 5340891736
    python pipeline/explain.py https://www.adzuna.co.uk/jobs/details/5340891736
    python pipeline/explain.py "palantir forward deployed"

Exit codes follow the house convention: 0 the row surfaces, 1 it is filtered
out, 2 it cannot be found at all -- which is a different fact from being
filtered, and the one that means the search never saw it.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
import tracker  # noqa: E402
from pipeline import shortlist  # noqa: E402

EXIT_SURFACES = 0
EXIT_FILTERED = 1
EXIT_NOT_FOUND = 2

PASS = "  pass"
FAIL = "  DROP"
NOTE = "      "


def find_rows(query: str, rows: list[dict]) -> list[dict]:
    """Rows matching an id, a URL, or words appearing in company and title.

    Three input shapes because there are three ways he arrives at this
    question: from the tracker (an id), from a browser (a URL he is looking
    at), or from memory ("that Palantir one"). Requiring the id would make the
    tool useless in the case that prompts it most often.
    """
    q = (query or "").strip()
    if not q:
        return []

    # A URL: pull the longest digit run, which is the posting id in Adzuna,
    # Greenhouse and Lever URLs alike.
    if "://" in q:
        ids = re.findall(r"\d{6,}", q)
        if ids:
            longest = max(ids, key=len)
            hit = [r for r in rows if longest in str(r.get("id", ""))]
            if hit:
                return hit
        # Fall through: an Ashby URL carries a uuid, so the stored link is
        # the only thing left to match on. The column is `redirect_url`;
        # `url` reads as empty on all 3,815 rows and fails silently.
        return [r for r in rows
                if q.rstrip("/") in (r.get("redirect_url") or "")]

    exact = [r for r in rows if str(r.get("id", "")).strip() == q]
    if exact:
        return exact

    words = [w for w in re.split(r"\s+", q.lower()) if w]
    return [r for r in rows
            if all(w in f"{r.get('company','')} {r.get('title','')}".lower()
                   for w in words)]


def stages(row: dict, decisions: dict, max_age: int | None) -> list[tuple]:
    """(name, ok, detail) for every gate, in the order the pipeline applies it.

    Every entry delegates. `unsuitable_reason`, `apply_ready_reason`,
    `disqualify` and `ruling_for` are the pipeline's own functions, called on
    the pipeline's own row.
    """
    out: list[tuple[str, bool, str]] = []

    title = row.get("title", "") or ""
    company = row.get("company", "") or ""
    status = (row.get("status") or "new").strip().lower()

    # 1. Ingest. Applied at fetch time, before the row was ever stored -- so a
    #    row reaching here already passed it. Shown anyway: it is the stage
    #    people suspect first, and "it passed" is the answer.
    reason = tracker.unsuitable_reason(title)
    out.append(("ingest title filter", reason is None,
                reason or "not trades, not senior"))

    # 2. Sponsorship. His #1 filter and the one that is never relaxed.
    sponsor = (row.get("sponsor_match") or "").strip().lower()
    out.append(("sponsor register", sponsor == "yes",
                f"sponsor_match={sponsor or 'empty'}"
                + (f", register name: {row['sponsor_name']}"
                   if row.get("sponsor_name") else "")))

    # 3. Agency. An agency's licence is not the employer's.
    agency = tracker.is_agency(company)
    out.append(("direct employer", not agency,
                "recruiter/agency posting" if agency else company or "unknown"))

    # 4. Status. Not a filter so much as a record that he already ruled on it.
    out.append(("not yet actioned", status in ("", "new"),
                f"status={status}"))

    # 5. The apply shortlist (tracker.py).
    ready = tracker.apply_ready_reason(row)
    out.append(("apply-shortlist rules", ready is None,
                ready or "on-target role, direct employer"))

    # 6. The scanner (pipeline/shortlist.py). Overlapping with the above by
    #    design -- two independent filters, and the answer differs when the
    #    row is old or the title carries a clearance requirement.
    #
    #    `disqualify()` is not the whole scanner. Its agency drop lives in
    #    main() at :374, so calling disqualify() alone reported Robert Walters
    #    as "kept" when the scanner drops it. A debugger that models a stage
    #    more loosely than the stage models itself is worse than no debugger,
    #    because it is believed.
    dq = shortlist.disqualify(row, max_age=max_age)
    if dq is None and shortlist.is_agency(company):
        dq = "agency listing (dropped in main, not disqualify)"
    out.append(("shortlist scanner", dq is None, dq or "kept"))

    # 7. His own recorded rulings, which outrank everything computed.
    ruling = shortlist.ruling_for(row, decisions)
    verdict = (ruling or {}).get("verdict", "")
    out.append(("your decisions.json", verdict.upper() != "CUT",
                f"{verdict or 'no ruling'}"
                + (f" — {ruling.get('note')}" if ruling and ruling.get("note")
                   else "")))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Explain why a job did or did not reach the shortlist.")
    ap.add_argument("query", help="job id, posting URL, or words from the title")
    ap.add_argument("--max-age", type=int, default=None, metavar="DAYS",
                    help="apply the scanner's age gate at this threshold")
    args = ap.parse_args(argv)

    store = ROOT / config.JOBS_XLSX
    if not store.exists():
        print(f"CANNOT EXPLAIN: no tracker at {store}. That is not the same "
              f"as the job being filtered out.")
        return EXIT_NOT_FOUND

    rows = list(tracker.load_tracker(str(store)).values())
    matches = find_rows(args.query, rows)

    if not matches:
        print(f"Not in the tracker: {args.query!r}")
        print()
        print(f"The store holds {len(rows)} rows, so this was never ingested "
              f"rather than ingested and dropped. Usually one of:")
        print("  - the search terms in config.py never asked for it")
        print("  - its title matched TITLE_EXCLUDE or SENIORITY_EXCLUDE at "
              "fetch time, which drops before storing")
        print("  - it is on a board nothing polls — check data/ats_boards.csv, "
              "and ats_prober.py can look for one")
        return EXIT_NOT_FOUND

    if len(matches) > 1:
        print(f"{len(matches)} rows match. Explaining the first; the rest:")
        for r in matches[1:6]:
            print(f"  {r.get('id')}  {r.get('company','')[:28]:<28} "
                  f"{r.get('title','')[:44]}")
        print()

    row = matches[0]
    decisions = shortlist.load_decisions()
    checks = stages(row, decisions, args.max_age)
    failed = [c for c in checks if not c[1]]

    print(f"{row.get('company','?')} — {row.get('title','?')}")
    print(f"id {row.get('id')}   source {row.get('source','?')}   "
          f"posted {row.get('posted_date') or '?'}   "
          f"first seen {row.get('date_first_seen') or '?'}")
    if row.get("redirect_url"):
        print(row["redirect_url"])
    print()

    for name, ok, detail in checks:
        print(f"{PASS if ok else FAIL}  {name:<24} {detail}")

    print()
    if failed:
        # Ordered, so the first failure is the one to fix -- later stages ran
        # anyway, because knowing a row fails three gates rather than one
        # changes whether it is worth pursuing at all.
        first = failed[0][0]
        print(f"Filtered out at: {first}"
              + (f" (and {len(failed) - 1} later stage"
                 f"{'s' if len(failed) > 2 else ''})" if len(failed) > 1
                 else ""))
        return EXIT_FILTERED

    total, reasons = shortlist.score(row)
    print(f"Surfaces. Score {total}:")
    for r in reasons:
        print(f"{NOTE}{r}")
    age = shortlist.age_days(row)
    if age is not None and age > 60:
        # A signal, not an accusation, and never a score change: shortlist.py's
        # weights are frozen because there is no outcome data to tune them on.
        print()
        print(f"Worth knowing: this posting is {age} days old. Long-open "
              f"requisitions are often filled, on hold, or evergreen pipeline "
              f"ads. Not disqualifying — just not fresh.")
    return EXIT_SURFACES


if __name__ == "__main__":
    sys.exit(main())
