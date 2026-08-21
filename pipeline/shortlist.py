"""
shortlist.py — deterministic bulk scan of the job tracker.

Turns hundreds of tracker rows into a short, ranked candidate list for the day.
This script FILTERS AND RANKS. It never judges fit, never writes cover letters,
never touches the outside world, and never marks anything applied. Judgment
happens afterwards, per-role, via the job-triage skill.

Why deterministic: the hard constraints (seniority, clearance, sponsorship,
already-actioned) are checkable facts. Facts belong in code — scripts don't
hallucinate. An LLM scanning a couple of thousand rows would be slower,
costlier, and less reliable than these rules.

Usage:
    python D:\\Adzuna\\pipeline\\shortlist.py                 # top 10, table
    python D:\\Adzuna\\pipeline\\shortlist.py --top 5
    python D:\\Adzuna\\pipeline\\shortlist.py --write         # also write .md
    python D:\\Adzuna\\pipeline\\shortlist.py --explain       # show drop reasons
    python D:\\Adzuna\\pipeline\\shortlist.py --include-agency

Reads the store through `tracker.load_tracker` (read-only) rather than parsing
it here, so id normalisation and the schema live in exactly one place. That
pulls in openpyxl via tracker — the one dependency beyond the stdlib, and one
the daily run already loads.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# Windows consoles default to a legacy codepage — cp1252 can't encode the "→"
# below, and cp437/cp850 (a plain `cmd` window) can't encode "—" either. Task
# Scheduler happens to hand us UTF-8, which is why the daily run survives and
# running it by hand does not. Fix it at the stream so no output character can
# ever take the run down: errors="replace" degrades a glyph, never crashes.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):   # not a reconfigurable text stream
        pass

ADZUNA_HOME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ADZUNA_HOME))
import config  # noqa: E402 — needs ADZUNA_HOME on the path first
import tracker  # noqa: E402

# The store, not the legacy CSV. `jobs_tracker.csv` froze on 2026-07-01 at 286
# rows; reading it meant every morning briefing re-scanned a stale snapshot and
# never saw a job found since.
TRACKER = ADZUNA_HOME / "jobs_tracker_beautified.xlsx"
SHORTLIST_DIR = ADZUNA_HOME / "pipeline" / "shortlists"
DECISIONS = ADZUNA_HOME / "pipeline" / "decisions.json"

# --------------------------------------------------------------------------- #
# Hard disqualifiers. Each returns a reason string, or None if the row passes.
# These encode the candidate profile's hard constraints. The profile itself
# (pipeline/profile.md) is personal and is not committed.
# --------------------------------------------------------------------------- #

SENIORITY = re.compile(
    r"\b(senior|snr|sr\.?|staff|principal|lead|leader|head\s+of|director|"
    r"architect|manager|managing|vp|vice\s+president|chief|cto|cio|"
    r"expert|specialist\s+iv|iii\b|\biv\b|10\+|[5-9]\+\s*years)\b",
    re.IGNORECASE,
)
# Clearance markers that appear in a TITLE. These are hard blocks: UK SC
# clearance normally requires five continuous years of UK residency and DV
# considerably more, so a role gated on either is closed to a recent arrival
# regardless of how good the fit is. "UK Government" and "NATO" are included
# because, on this data, every such posting turned out to carry a clearance
# requirement in the body even when the stored description was too short to
# show it.
CLEARANCE = re.compile(
    r"\b(sc[\s-]?cleared|dv[\s-]?cleared|e?dv\b|security\s+clearance|"
    r"security[\s-]?cleared|developed\s+vetting|"
    r"must\s+be\s+a?\s*british|uk\s+national(s)?\s+only|nppv|mod\b|"
    r"uk\s+government|nato)\b",
    re.IGNORECASE,
)

# Softer signal, matched against the DESCRIPTION rather than the title. Phrases
# like "you may need to be eligible for DV" describe some of a company's work,
# not this specific role: Faculty's Forward Deployed Engineer carries exactly
# that wording and is otherwise a strong fit. 187 rows mention clearance
# somewhere in their text, so dropping on this would bin most of the board.
# Flag it instead and let a human read the posting.
CLEARANCE_HINT = re.compile(
    r"(may\s+need\s+to\s+be\s+eligible|eligible\s+for\s+(uk\s+)?(sc|dv|"
    r"developed\s+vetting|security\s+clearance)|willing\s+to\s+undergo)",
    re.IGNORECASE,
)

# Internships, placements and apprenticeships. A 2026 graduate is not eligible:
# Palantir's London internships require graduating in 2028, and UK
# apprenticeships require existing right to work. Reuses the list already in
# config so this rule has one home.
EARLY_BLOCK = re.compile(
    r"\b(" + "|".join(re.escape(t) for t in getattr(config, "APPLY_BLOCK_TERMS", [])) + r")\b",
    re.IGNORECASE,
) if getattr(config, "APPLY_BLOCK_TERMS", None) else None


def is_agency(company: str) -> bool:
    """Delegates to tracker so there is exactly one definition of an agency.

    This module used to keep its own KNOWN_AGENCIES set, which disagreed with
    the config-driven one: Salt was an agency here and not there, so the sponsor
    review queue kept asking for human decisions on recruitment firms. Both
    lists are now merged into config.RECRUITER_COMPANIES.
    """
    return tracker.is_agency(company)


# The tracker's `tier` column is assigned by keyword and is demonstrably
# unreliable — a building-maintenance "Multi-Skilled Engineer" and a
# "Talent Acquisition Partner" both landed in software tiers. So a row must
# earn its place with a positive tech signal in the title, and must not match
# a known non-software trade. Verified against real tracker data 2026-07-20.
TECH_SIGNAL = re.compile(
    r"\b(software|developer|dev\b|devops|sre\b|programm\w+|coding|"
    r"data\s+(scientist|engineer|analyst)|analytics|machine\s+learning|\bml\b|"
    r"\bai\b|nlp|llm|genai|python|java\b|javascript|typescript|golang|\bc\+\+|"
    r"\.net|react|node|backend|back[\s-]end|frontend|front[\s-]end|full[\s-]?stack|"
    r"platform|cloud|aws|azure|gcp|kubernetes|infrastructure|database|\bsql\b|"
    r"\bqa\b|test\s+automation|\bsdet\b|cyber|information\s+security|appsec|"
    r"\bit\b|technology|technical|systems?\s+engineer|solutions?\s+engineer|"
    r"forward[\s-]?deployed|pre[\s-]?sales|customer\s+engineer|"
    r"integration|api\b|robotics|computer\s+vision)\b",
    re.IGNORECASE,
)
NON_TECH = re.compile(
    r"\b(multi[\s-]?skilled|maintenance|mechanical|electrical|electrician|"
    r"hvac|plumb\w*|gas\b|boiler|refrigeration|lift\s+engineer|welder|"
    r"fabricat\w+|civil|structural|site\s+engineer|quantity\s+surveyor|"
    r"estimator|building\s+services|groundwork|carpenter|"
    r"talent\s+acquisition|recruit\w*|human\s+resources|\bhr\b|payroll|"
    r"nurse|nursing|care\s+(assistant|worker)|healthcare\s+assistant|"
    r"teacher|tutor|lecturer|chef|driver|warehouse|cleaner|"
    r"security\s+officer|door\s+supervisor|retail|cashier|"
    r"account\s+manager|business\s+development|marketing|copywriter)\b",
    re.IGNORECASE,
)


def normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def age_days(row: dict) -> int | None:
    """How long ago this ad was posted, in days. None if undateable."""
    seen = parse_date(row.get("posted_date", "") or row.get("date_first_seen", ""))
    if not seen:
        return None
    return (datetime.now(timezone.utc).replace(tzinfo=None) - seen).days


def disqualify(row: dict, max_age: int | None = None) -> str | None:
    """Return the reason this row is out, or None to keep it."""
    title = row.get("title", "") or ""
    status = (row.get("status", "") or "").strip().lower()

    if status and status != "new":
        return f"already actioned (status={status})"
    # The age gate is a proxy for "this ad has probably closed", and it only
    # makes sense where nothing better is available. A row from an employer's
    # own board was in that board's feed on the last successful fetch, which is
    # direct evidence it is still open — so ageing it out would be discarding a
    # fact in favour of a guess. Rows that HAVE closed are removed by their
    # absence from the feed instead (see ats_main.record_closures).
    from_own_board = row.get("source", tracker.SOURCE_ADZUNA) != \
        tracker.SOURCE_ADZUNA
    if max_age is not None and not from_own_board:
        age = age_days(row)
        # Undateable rows survive — we drop on evidence, never on absence of it.
        if age is not None and age > max_age:
            return f"older than {max_age}d (likely closed)"
    if SENIORITY.search(title):
        return "seniority title — early-career profile"
    if CLEARANCE.search(title):
        return "security clearance / nationality requirement"
    if EARLY_BLOCK and EARLY_BLOCK.search(title):
        return "internship/apprenticeship — needs a later graduation year"
    if (row.get("sponsor_match", "") or "").strip().lower() != "yes":
        return "no sponsor-register match"
    if NON_TECH.search(title):
        return "non-software trade/role (tracker tier unreliable)"
    if not TECH_SIGNAL.search(title):
        return "no tech signal in title"
    return None


# --------------------------------------------------------------------------- #
# Ranking. Higher score = look at it sooner. Signals only — not a fit verdict.
# --------------------------------------------------------------------------- #

TIER_SCORE = {"1": 50, "2": 30, "3": 35, "4": 15}
FDE_TITLE = re.compile(
    r"\b(forward[\s-]?deployed|solutions?\s+engineer|solution\s+architect|"
    r"customer\s+engineer|field\s+engineer|implementation\s+engineer|"
    r"sales\s+engineer|pre[\s-]?sales)\b",
    re.IGNORECASE,
)
GRAD_TITLE = re.compile(
    r"\b(graduate|junior|jnr|entry[\s-]?level|associate|trainee|"
    r"early\s+careers?|new\s+grad)\b",
    re.IGNORECASE,
)
AI_TITLE = re.compile(
    r"\b(ai|ml|machine\s+learning|nlp|llm|genai|generative)\b", re.IGNORECASE
)


def parse_date(value: str) -> datetime | None:
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S",
                "%d/%m/%Y", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime((value or "").strip()[:19], fmt)
        except (ValueError, TypeError):
            continue
    return None


def score(row: dict) -> tuple[int, list[str]]:
    title = row.get("title", "") or ""
    company = row.get("company", "") or ""
    location = row.get("location", "") or ""
    points, why = 0, []

    tier = (row.get("tier", "") or "").strip()
    if tier in TIER_SCORE:
        points += TIER_SCORE[tier]
        why.append(f"tier {tier}")

    if FDE_TITLE.search(title):
        points += 30
        why.append("forward-deployed/solutions title")
    if GRAD_TITLE.search(title):
        points += 20
        why.append("early-career title")
    if AI_TITLE.search(title):
        points += 10
        why.append("AI/ML title")

    if (row.get("sponsor_rating", "") or "").strip().upper() == "A":
        points += 10
        why.append("A-rated sponsor")

    if re.search(r"london", location, re.IGNORECASE):
        points += 10
        why.append("London")

    if is_agency(company):
        points -= 25
        why.append("agency listing (real employer hidden)")

    # A high stated floor on a "junior" ad usually means it isn't junior.
    try:
        smin = int(float(row.get("salary_min") or 0))
    except (TypeError, ValueError):
        smin = 0
    if smin >= 90000:
        points -= 20
        why.append(f"salary floor £{smin:,} suggests senior")

    # Through age_days() so the ranking and the line printed above it cannot
    # disagree. They did: this read date_first_seen first while age_days reads
    # posted_date first, so the top two candidates on 2026-08-20 were Palantir
    # requisitions displayed as "384d ago" and "833d ago" while being scored
    # +10 for "posted this week" -- the briefing telling the truth and the
    # ranking rewarding the opposite, in the same block of output.
    #
    # posted_date is the right one to prefer: it is when the employer posted
    # the ad. date_first_seen is when this pipeline noticed, which for the
    # 2026-08-14 ATS backfill was the same day for 235 rows regardless of how
    # old the postings were, so preferring it scores an ingest event as
    # freshness.
    age = age_days(row)
    if age is not None:
        if age <= 7:
            points += 10
            why.append("posted this week")
        elif age > 30:
            points -= 10
            why.append(f"{age}d old")

    return points, why


# --------------------------------------------------------------------------- #

def load_decisions() -> dict:
    """Persistent triage rulings that survive tracker rewrites."""
    if not DECISIONS.exists():
        return {"companies": {}, "roles": {}}
    try:
        data = json.loads(DECISIONS.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"WARNING: decisions.json is malformed ({e}) — ignoring it.",
              file=sys.stderr)
        return {"companies": {}, "roles": {}}
    return {"companies": data.get("companies", {}),
            "roles": data.get("roles", {})}


def ruling_for(row: dict, decisions: dict) -> dict | None:
    """Most specific ruling wins: role-level before company-level."""
    company = normalise(row.get("company", ""))
    title = normalise(row.get("title", ""))
    for key, ruling in decisions["roles"].items():
        dc, _, dt = key.partition("::")
        if normalise(dc) in company and normalise(dt) in title:
            return ruling
    for key, ruling in decisions["companies"].items():
        if normalise(key) in company:
            return ruling
    return None


def load_rows() -> list[dict]:
    """Read-only load of the store. Values come back as strings, as before."""
    if not TRACKER.exists():
        sys.exit(f"tracker not found: {TRACKER}")
    return list(tracker.load_tracker(str(TRACKER)).values())


def main() -> int:
    ap = argparse.ArgumentParser(description="Deterministic tracker shortlist")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--write", action="store_true",
                    help="write the shortlist to pipeline/shortlists/")
    ap.add_argument("--explain", action="store_true",
                    help="print a breakdown of why rows were dropped")
    ap.add_argument("--include-agency", action="store_true",
                    help="keep agency listings (penalised, not dropped)")
    ap.add_argument("--max-age", type=int, default=None, metavar="DAYS",
                    help="drop ads older than this (0 disables). Adzuna "
                         "publishes no deadline, so age is only a proxy. "
                         "Defaults to 30 on its own, but to OFF when "
                         "--verify-live can establish the truth instead.")
    ap.add_argument("--verify-live", action="store_true",
                    help="probe the surviving candidates over the network and "
                         "drop the ones Adzuna has delisted (410 Gone). "
                         "Costs a Firecrawl scrape per unseen id; cached.")
    args = ap.parse_args()

    # Age is a guess; a 410 is a fact. Letting the guess run first would bin
    # live ads before the fact could save them — which is exactly what happened
    # to Syntasso's still-open Solutions Engineer ad at 47 days. So when we are
    # going to verify over the network, the age gate stands down unless it was
    # asked for explicitly.
    if args.max_age is None:
        max_age = None if args.verify_live else 30
    else:
        max_age = args.max_age if args.max_age > 0 else None

    rows = load_rows()
    decisions = load_decisions()
    kept, dropped, seen = [], {}, set()
    for row in rows:
        reason = disqualify(row, max_age=max_age)
        if reason:
            dropped[reason] = dropped.get(reason, 0) + 1
            continue
        ruling = ruling_for(row, decisions)
        if ruling and ruling.get("verdict") == "CUT":
            dropped["previously CUT (see decisions.json)"] = (
                dropped.get("previously CUT (see decisions.json)", 0) + 1)
            continue
        if not args.include_agency and is_agency(row.get("company", "")):
            dropped["agency listing"] = dropped.get("agency listing", 0) + 1
            continue
        # Boards relist the same role repeatedly; one entry per role+employer.
        key = (normalise(row.get("title", "")), normalise(row.get("company", "")))
        if key in seen:
            dropped["duplicate listing"] = dropped.get("duplicate listing", 0) + 1
            continue
        seen.add(key)
        pts, why = score(row)
        kept.append((pts, why, row, ruling))

    kept.sort(key=lambda t: -t[0])

    # Network check, last and on the survivors only. Probing all 3,000 rows
    # would be absurd; probing the handful he might actually apply to is cheap.
    # We over-fetch (3x) so that dropping dead ads still fills the top N.
    dead_ids: set[str] = set()
    unknown_ids: set[str] = set()
    if args.verify_live:
        import liveness
        pool = kept[: max(args.top * 3, args.top)]
        # Only Adzuna rows are probed. Other sources report their own liveness
        # through their board feed, and probing an Adzuna URL for them would
        # cache a permanent false DEAD.
        adzuna_ids = [str(r.get("id", "")) for _, _, r, _ in pool
                      if r.get("source", tracker.SOURCE_ADZUNA)
                      == tracker.SOURCE_ADZUNA]
        print(f"\nVerifying {len(adzuna_ids)} of {len(pool)} candidates "
              f"are still listed...")
        verdicts = liveness.check(adzuna_ids, verbose=args.explain)
        for jid, (verdict, _note) in verdicts.items():
            if verdict == liveness.DEAD:
                dead_ids.add(str(jid))
            elif verdict == liveness.UNKNOWN:
                unknown_ids.add(str(jid))
        if dead_ids:
            dropped[f"delisted (410 Gone)"] = len(dead_ids)
        kept = [t for t in kept if str(t[2].get("id", "")) not in dead_ids]

    top = kept[: args.top]

    print(f"\nTracker: {len(rows)} rows → {len(kept)} candidates "
          f"→ showing top {len(top)}\n")
    if args.explain:
        print("Dropped:")
        for reason, n in sorted(dropped.items(), key=lambda kv: -kv[1]):
            print(f"  {n:>4}  {reason}")
        print()

    if not top:
        print("Nothing qualifies. Widen the tracker search or relax a filter.")
        return 0

    for i, (pts, why, row, ruling) in enumerate(top, 1):
        # A row from the employer's own board carries a direct apply link rather
        # than an aggregator redirect, which is worth seeing at a glance.
        direct = "" if row.get("source", tracker.SOURCE_ADZUNA) == \
            tracker.SOURCE_ADZUNA else "  [direct]"
        print(f"{i:>2}. [{pts:>3}] {row.get('title','')[:62]}{direct}")
        print(f"       {row.get('company','')} — {row.get('location','')[:40]}"
              f"  | tier {row.get('tier','?')}"
              f" | sponsor {row.get('sponsor_rating','-')}")
        print(f"       {', '.join(why)}")
        if CLEARANCE_HINT.search(row.get("description", "") or ""):
            print("       ! posting mentions clearance eligibility — read it before applying")
        if str(row.get("id", "")) in unknown_ids:
            print("       ? could not verify it is still listed — check the link")
        if ruling:
            print(f"       ! PRIOR RESEARCH ({ruling.get('verified','?')}): "
                  f"{ruling.get('reason','')}")
        print(f"       {row.get('redirect_url','')[:100]}")
        print()

    if args.write:
        SHORTLIST_DIR.mkdir(parents=True, exist_ok=True)
        today = datetime.now().strftime("%Y-%m-%d")
        out = SHORTLIST_DIR / f"{today}-shortlist.md"
        freshness = (f"Ads older than {max_age}d excluded."
                     if max_age else "No age cut-off — liveness decides.")
        checked = ("Every candidate below was confirmed still listed."
                   if args.verify_live
                   else "Listings NOT network-verified — run with "
                        "`--verify-live` to drop delisted ads.")
        lines = [
            f"# Shortlist — {today}", "",
            f"{len(rows)} tracker rows → {len(kept)} candidates → top {len(top)}.",
            f"{freshness} {checked}",
            "Deterministic filter only. Run `/triage <url>` on each before packing.",
            "",
        ]
        for i, (pts, why, row, ruling) in enumerate(top, 1):
            age = age_days(row)
            direct = "" if row.get("source", tracker.SOURCE_ADZUNA) == \
                tracker.SOURCE_ADZUNA else " **[direct employer board]**"
            lines += [
                f"## {i}. {row.get('title','')} — {row.get('company','')}{direct}",
                f"- Score: {pts} ({', '.join(why)})",
                f"- Location: {row.get('location','')} | Tier: "
                f"{row.get('tier','?')} | Sponsor: {row.get('sponsor_rating','-')}"
                + (f" | Posted: {age}d ago" if age is not None else ""),
            ]
            if CLEARANCE_HINT.search(row.get("description", "") or ""):
                lines.append("- ⚠️ The posting mentions clearance eligibility. "
                             "Read it before applying: SC normally needs five "
                             "years' UK residency and DV considerably more.")
            if str(row.get("id", "")) in unknown_ids:
                lines.append("- ⚠️ Could not verify this ad is still listed.")
            if ruling:
                lines.append(f"- **Prior research ({ruling.get('verified','?')}):** "
                             f"{ruling.get('reason','')}")
            lines += [
                f"- URL: {row.get('redirect_url','')}",
                f"- Tracker id: `{row.get('id','')}`",
                "- [ ] triaged   - [ ] packed   - [ ] submitted",
                "",
            ]
        out.write_text("\n".join(lines), encoding="utf-8")
        print(f"written: {out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
