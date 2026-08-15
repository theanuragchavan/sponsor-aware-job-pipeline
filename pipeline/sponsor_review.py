"""
sponsor_review.py — find companies the sponsor matcher is probably wrong about.

The register is matched on an exact normalised name, and that has a real
false-negative rate, because the register records the *legal entity* while a job
ad names the *brand*:

    Monzo      -> register says MONZO BANK
    Deliveroo  -> register says ROOFOODS LTD T A DELIVEROO
    Amentum    -> register says AMENTUM UK

Every one of those scored `sponsor_match=no`, and `shortlist.disqualify` drops a
`no` row outright. So a licensed sponsor's roles were invisible.

This script proposes candidates. It never decides. Suggestions include obvious
rubbish by design — `Wise` proposes WISE BEST, WISE CLUBS AND ENTERTAINMENT,
WISE COMPUTER — and a human rejecting those in one pass is cheaper than a
clever matcher quietly promoting one of them.

Usage:
    python pipeline\\sponsor_review.py                 # write the review file
    python pipeline\\sponsor_review.py --apply         # fold confirmations in
    python pipeline\\sponsor_review.py --rescore       # restamp affected rows

Workflow: run it, open pipeline\\sponsor_review.md, put an `x` in the box next to
any correct match, then run --apply --rescore.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

ADZUNA_HOME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ADZUNA_HOME))

sys.path.insert(0, str(ADZUNA_HOME / "pipeline"))

import shortlist  # noqa: E402
import sponsor_check  # noqa: E402
import tracker  # noqa: E402

TRACKER = ADZUNA_HOME / "jobs_tracker_beautified.xlsx"
REVIEW_MD = ADZUNA_HOME / "pipeline" / "sponsor_review.md"

# Matches a ticked line:  - [x] `MONZO BANK` (A, prefix)
TICK_RE = re.compile(r"^\s*-\s*\[[xX]\]\s*`([^`]+)`")
COMPANY_RE = re.compile(r"^###\s+(.+?)\s*$")


def candidates(rows, lookup, aliases):
    """Companies currently scored 'no' that have a plausible register entry.

    Sorted by how much confirming one is actually worth: a company with live
    tier-1/2/3 roles in the tracker earns a decision, a facilities-management
    firm with one warehouse vacancy does not. Without this the list is 80-odd
    names in alphabetical order and every one costs the same attention.
    """
    index = sponsor_check.build_name_index(lookup)
    by_company = {}
    for row in rows:
        company = (row.get("company") or "").strip()
        if company:
            by_company.setdefault(company, []).append(row)

    seen, out = set(), []
    for row in rows:
        company = (row.get("company") or "").strip()
        if not company or company in seen:
            continue
        if (row.get("sponsor_match") or "").strip().lower() != "no":
            continue
        if tracker.is_agency(company):
            continue        # an agency's own licence tells us nothing
        if sponsor_check.normalize_name(company) in aliases:
            continue        # already decided
        seen.add(company)
        hits = sponsor_check.suggest_matches(company, lookup, index)
        if not hits:
            continue

        company_rows = by_company.get(company, [])
        titles = [r.get("title", "") for r in company_rows]
        tiers = {(r.get("tier") or "").strip() for r in company_rows}
        good = sorted({t for t in tiers if t in ("1", "2", "3")})
        # A title-level tech signal is a better guide than the tracker's tier,
        # which is assigned by the search that found the row, not by the role.
        techy = [t for t in titles if shortlist.TECH_SIGNAL.search(t)
                 and not shortlist.NON_TECH.search(t)]
        out.append({
            "company": company,
            "hits": hits,
            "rows": len(company_rows),
            "tiers": good,
            "tech_titles": techy[:3],
            "score": (len(techy) > 0, len(good), len(techy)),
        })

    out.sort(key=lambda c: (not c["score"][0], -c["score"][1],
                            -c["score"][2], c["company"].lower()))
    return out


def write_review(entries):
    worth_it = [e for e in entries if e["score"][0]]
    rest = [e for e in entries if not e["score"][0]]

    lines = [
        "# Sponsor review",
        "",
        f"Generated {dt.date.today().isoformat()}. "
        f"{len(entries)} companies scored `no` that may actually be sponsors.",
        "",
        "These are companies whose roles the shortlist is **dropping right now**",
        "for 'no sponsor-register match', where the register probably lists them",
        "under a different legal name (Monzo is on there as MONZO BANK).",
        "",
        "Tick a box **only** if the register entry is genuinely the same employer.",
        "Then run `python pipeline\\sponsor_review.py --apply --rescore`.",
        "",
        "Expect rubbish — the suggester favours precision-you-can-check over",
        "cleverness, so it offers AMAZON CHARITABLE TRUST next to AMAZON UK",
        "SERVICES and lets you pick. Leaving everything unticked is a valid",
        "outcome.",
        "",
        "---",
        "",
        f"## Worth deciding ({len(worth_it)})",
        "",
        "These have software/data/engineering roles in your tracker, so",
        "confirming them puts real jobs back on the shortlist.",
        "",
    ]

    def block(entry):
        out = [f"### {entry['company']}"]
        detail = f"{entry['rows']} role(s) tracked"
        if entry["tiers"]:
            detail += f", tier {'/'.join(entry['tiers'])}"
        out.append(f"*{detail}*")
        for title in entry["tech_titles"]:
            out.append(f"  - {title}")
        for key, rating, why in entry["hits"]:
            out.append(f"- [ ] `{key}` ({rating or 'no rating'}, {why})")
        out.append("")
        return out

    for entry in worth_it:
        lines += block(entry)

    lines += [
        "---",
        "",
        f"## Probably not worth it ({len(rest)})",
        "",
        "No software or data roles tracked at these — mostly construction,",
        "facilities, care and logistics. Skim, ignore, or tick if you know",
        "better.",
        "",
    ]
    for entry in rest:
        lines += block(entry)

    REVIEW_MD.write_text("\n".join(lines), encoding="utf-8")
    return REVIEW_MD


def read_confirmations():
    """Parse the ticked boxes back out of the review file."""
    if not REVIEW_MD.exists():
        sys.exit(f"no review file at {REVIEW_MD} — run without --apply first")
    confirmed, company = {}, None
    for line in REVIEW_MD.read_text(encoding="utf-8").splitlines():
        m = COMPANY_RE.match(line)
        if m:
            company = m.group(1).strip()
            continue
        m = TICK_RE.match(line)
        if m and company:
            confirmed[company] = m.group(1).strip()
            company = None      # first tick wins; ignore extra ticks
    return confirmed


def rescore(lookup, aliases):
    """Restamp rows whose company now resolves through an alias.

    Touches ONLY the three sponsor columns. status / notes / date_applied /
    applied_via are hand-maintained and must survive untouched — that is the
    whole reason the tracker merge is insert-only.
    """
    rows_by_id = tracker.load_tracker(str(TRACKER))
    changed = 0
    for row in rows_by_id.values():
        if (row.get("sponsor_match") or "").strip().lower() != "no":
            continue
        if sponsor_check.normalize_name(row.get("company", "")) not in aliases:
            continue
        before = (row.get("sponsor_match"), row.get("sponsor_rating"),
                  row.get("sponsor_route"))
        match, rating, route = sponsor_check.match_company(
            row["company"], lookup, aliases)
        row["sponsor_match"], row["sponsor_rating"], row["sponsor_route"] = (
            match, rating, route)
        if (match, rating, route) != before:
            changed += 1
    if changed:
        tracker.save_tracker(str(TRACKER), rows_by_id)
    return changed, len(rows_by_id)


def main() -> int:
    ap = argparse.ArgumentParser(description="Sponsor-match review queue")
    ap.add_argument("--apply", action="store_true",
                    help="fold ticked confirmations into sponsor_aliases.json")
    ap.add_argument("--rescore", action="store_true",
                    help="restamp tracker rows that an alias now resolves")
    args = ap.parse_args()

    lookup = sponsor_check.load_sponsor_lookup()
    if lookup is None:
        sys.exit("sponsor register unavailable — try again when online")
    aliases = sponsor_check.load_aliases()

    if args.apply:
        confirmed = read_confirmations()
        if not confirmed:
            print("No boxes ticked — nothing to apply.")
        else:
            raw = {}
            path = Path(sponsor_check.ALIASES_PATH)
            if path.exists():
                import json
                raw = json.loads(path.read_text(encoding="utf-8"))
            today = dt.date.today().isoformat()
            for company, key in confirmed.items():
                raw[company] = {"register_name": key,
                                "rating": lookup.get(key, ""),
                                "confirmed": today}
            sponsor_check.save_aliases(raw)
            aliases = sponsor_check.load_aliases()
            print(f"Confirmed {len(confirmed)} aliases -> "
                  f"{sponsor_check.ALIASES_PATH}")
            for company, key in sorted(confirmed.items()):
                print(f"    {company}  ->  {key}")

    if args.rescore:
        changed, total = rescore(lookup, aliases)
        print(f"Rescored {changed} of {total} rows to sponsor_match=yes.")

    if not args.apply and not args.rescore:
        rows = list(tracker.load_tracker(str(TRACKER)).values())
        entries = candidates(rows, lookup, aliases)
        path = write_review(entries)
        worth = sum(1 for e in entries if e["score"][0])
        print(f"{len(entries)} companies need a decision "
              f"— {worth} of them have tech roles and are worth your time.")
        print(f"Review file: {path}")
        print("Tick the correct ones, then: "
              "python pipeline\\sponsor_review.py --apply --rescore")
    return 0


if __name__ == "__main__":
    sys.exit(main())
