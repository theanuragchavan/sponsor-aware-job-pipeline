"""Probe sponsor-licensed employers for a readable job board.

The inversion is the whole idea, and it comes from JOB_SOURCES.md: today the
Home Office register is a *filter*, applied to whatever an aggregator happens to
surface. Probing runs it the other way. Start from employers that already hold a
Skilled Worker licence, ask which of them publish a public ATS feed, and poll
those directly.

Two things fall out of that. Postings appear on an employer's own board days
before they reach an aggregator, and **every hit is sponsor-confirmed by
construction** -- there is no matching step to get wrong, because the company
came out of the register in the first place.

Proven on a real target before this file existed: `palantir` -> Lever, 308
postings, 38 UK, including all four roles he is actually chasing. One keyless
GET.

Why this is not a list
----------------------
Every public "which company uses which ATS" list is incomplete and stale, and
maintaining one by hand is what `data/ats_boards.csv` already does for 47
employers. Slugifying a name and asking four endpoints is cheaper than curating,
and it stays correct when a company migrates ATS.

What it will not do
-------------------
**Never treats HTTP 200 as a hit.** That rule is the difference between this
being useful and being noise, and JOB_SOURCES.md records two live traps that
prove it:

- SmartRecruiters answers `200 {"totalFound": 0, "content": []}` for *any*
  slug, including `zzqqxnotacompany999`. It is excluded from probing entirely --
  not gated, excluded -- because a family that cannot distinguish a real board
  from a nonexistent one cannot be used for discovery at all. It stays in
  `ats/clients.py` for boards added by hand, where the slug is already known.
- Workable returns a real account with an empty jobs array, and was found on
  2026-08-21 to serve no job data at all across twelve accounts and three
  endpoint variants. Not probed.

So a hit requires a parsed, non-empty list of postings. Anything else is a miss.

It also never writes to the tracker. It proposes rows for `ats_boards.csv` and a
human accepts them, which is the same rule `sponsor_review.py` follows: the
machine narrows, the person decides.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import sponsor_check  # noqa: E402
from ats import boards as boards_mod  # noqa: E402
from ats.clients import (BoardError, BoardNotFound, fetch_ashby,  # noqa: E402
                         fetch_greenhouse, fetch_lever)

EXIT_OK = 0
EXIT_NOTHING = 1
EXIT_CANNOT_PROBE = 2

#: Families that can tell a real board from a nonexistent one. See the module
#: docstring for why SmartRecruiters and Workable are absent -- that is a
#: correctness decision, not an oversight.
PROBES = (
    ("greenhouse", fetch_greenhouse),
    ("ashby", fetch_ashby),
    ("lever", fetch_lever),
)

#: Seconds between requests. The register has 121,188 rows; politeness here is
#: not optional and a prober that gets the IP blocked has cost more than it
#: found. `adzuna_client.py` uses the same posture.
THROTTLE = 1.2

#: Corporate suffixes that never appear in an ATS slug.
_SUFFIX = re.compile(
    r"\b(ltd|limited|plc|llp|llc|inc|corp|corporation|group|holdings?|"
    r"uk|gb|international|global|services|technologies|technology)\b",
    re.IGNORECASE)

CACHE = ROOT / "data" / "ats_probe_cache.json"


def slug_variants(company: str) -> list[str]:
    """The handful of spellings an ATS slug plausibly takes, best first.

    Deliberately small. Each variant is a request against someone else's
    server, and the marginal one is far more likely to be a wasted call than a
    find -- three families times six variants is eighteen requests per company,
    which at 121k companies is not a thing anyone should run.
    """
    name = (company or "").strip()
    if not name:
        return []

    base = re.sub(r"[^\w\s&-]", " ", name)
    base = base.replace("&", " and ")
    trimmed = _SUFFIX.sub(" ", base)
    words = [w for w in trimmed.lower().split() if w]
    if not words:
        words = [w for w in base.lower().split() if w]
    if not words:
        return []

    out = [
        "".join(words),          # monzobank
        "-".join(words),         # monzo-bank
        words[0],                # monzo
    ]
    # Deduplicate, preserving order: a one-word company produces the same
    # string three times and would otherwise be probed three times.
    seen, unique = set(), []
    for s in out:
        if s and s not in seen and len(s) >= 3:
            seen.add(s)
            unique.append(s)
    return unique


def match_strength(company: str, board_name: str,
                   slug: str = "") -> str:
    """How well the board's own name matches the one searched for.

    Returns "exact", "loose", or "" for a rejection.

    A bool was not enough, and a live probe proved it. Searching "Universal
    Music" hit a Greenhouse board named "Universal" carrying two postings, one
    of them titled "Open Applicatons" -- a London design agency, not Universal
    Music Group. The old prefix test accepted it because "universalmusic"
    starts with "univer".

    The trap is that the same prefix test is *right* for "Monzo Bank" -> a
    board named "Monzo". A shorter board name is genuinely ambiguous: it is
    either the company's short form or a different company that happens to
    share an opening word, and no amount of string comparison separates those.

    So this grades instead of guessing, which is the posture the rest of the
    module already takes -- it proposes rows and a human accepts them. An exact
    match is proposed; a loose one is shown with sample postings and left for
    a person to judge. When a family reports no name at all, the slug carries
    the evidence instead -- see the comment below.
    """
    a = re.sub(r"[^a-z0-9]", "", (company or "").lower())
    b = re.sub(r"[^a-z0-9]", "", (board_name or "").lower())
    if not b:
        # Ashby reports no company name at all, so a first version graded every
        # Ashby hit "loose" -- eight of eleven findings in a 150-company sweep,
        # which is an artifact of the family rather than eight doubtful results.
        # With no name to check, the slug is the evidence: one that spells the
        # company out in full ("zilch", "maya-htt", "tracebit") is as good as a
        # name match, while one that dropped a word ("amber" for Amber Labs,
        # "raspberry" for Raspberry Pi Foundation) is the case that actually
        # needs a person. That split takes the review pile from eight to three.
        if slug and slug.lower() in {v for v in slug_variants(company)[:2]}:
            return "exact"
        return "loose"
    if not a:
        return ""
    if a == b:
        return "exact"
    # Guards the failure JOB_SOURCES.md records by name: SmartRecruiters'
    # `palantir` board belongs to a C# consultancy in Aberdeen.
    if a.startswith(b[:6]) or b.startswith(a[:6]) or a in b or b in a:
        return "loose"
    return ""


def probe_company(company: str, *, throttle: float = THROTTLE,
                  verbose: bool = False) -> dict | None:
    """The first family and slug that yields real postings, or None.

    Returns on the first hit rather than probing every family: a company has one
    careers board, and continuing would spend requests to learn nothing.
    """
    for slug in slug_variants(company):
        for family, fetch in PROBES:
            try:
                board_name, jobs = fetch(slug)
            except (BoardNotFound, BoardError):
                jobs = []
                board_name = ""
            except Exception as exc:  # noqa: BLE001 - one bad slug must not stop a sweep
                if verbose:
                    print(f"    {family}/{slug}: {type(exc).__name__}: {exc}")
                jobs = []
                board_name = ""
            finally:
                time.sleep(throttle)

            # Parsed and non-empty. Never the status code.
            if not jobs:
                continue
            strength = match_strength(company, board_name, slug)
            if not strength:
                if verbose:
                    print(f"    {family}/{slug}: {board_name!r} is a different "
                          f"company - skipped")
                continue
            return {"company": company, "ats": family, "slug": slug,
                    "board_name": board_name, "jobs": len(jobs),
                    "match": strength,
                    "sample": [str(j.get("title") or "")[:60]
                               for j in jobs[:3]]}
    return None


def targets_from_tracker(limit: int) -> list[str]:
    """Sponsor-confirmed employers already posting roles he wants, most first.

    The obvious starting set is the register itself, and it is wrong. Probed
    alphabetically it yields `003`, `007 TAXI`, `0086`, `01 ACCOUNTING
    SERVICES` -- the register is 121,188 organisations and most of them are
    care homes, takeaways and two-person consultancies. `ats/boards.py` already
    recorded the same finding from the other direction: filtering it by
    tech-sounding tokens produces 4,600 entries headed by "0xA Technologies
    Ltd" and "1 WAY TECH SOLUTIONS LIMITED".

    The tracker is the better frame and it is already on disk. Every company in
    it posted something that survived the title filters, the seniority filter
    and the sponsor gate -- so it is pre-qualified as an employer that hires
    people like him AND can sponsor them. Probing those converts an aggregator
    row, which arrives late and links to a redirect, into a direct feed that
    carries the full description and the employer's own apply URL.

    Ranked by how many surviving roles each has posted, because an employer
    with eleven relevant openings is worth a board lookup more than one with a
    single stale row.
    """
    import collections

    import config
    import tracker as _tracker

    rows = _tracker.load_tracker(str(ROOT / config.JOBS_XLSX))
    counts: collections.Counter = collections.Counter()
    for row in rows.values():
        if (row.get("sponsor_match") or "").strip().lower() != "yes":
            continue
        # Already coming from a direct board; nothing to discover.
        if ":" in str(row.get("id", "")):
            continue
        company = (row.get("company") or "").strip()
        if company and not _tracker.is_agency(company):
            counts[company] += 1
    return [name for name, _n in counts.most_common(limit)]


def resolve_register_name(company: str, lookup: dict, aliases: dict) -> str:
    """The exact Home Office string for this company, or "" if nobody decided.

    `register_name` is the load-bearing column in `ats_boards.csv`: Greenhouse
    reports Monzo as "Monzo" while the register says "MONZO BANK", and without
    the mapping every job ingested from that board fails the sponsorship gate
    it was collected to pass.

    Two sources count, and neither of them is this function guessing. An exact
    register key is a fact. A `sponsor_aliases.json` entry is a decision he
    already made and dated -- five of the boards found in the first sweep
    resolved this way (Axle -> AXLE ENERGY, Quilter -> QUILTER BUSINESS
    SERVICES), all confirmed on 2026-08-15.

    A prefix or fuzzy match returns "" instead. "AXLE" starting "AXLE ENERGY"
    is exactly the reasoning that would also map "Universal Music" onto a
    design agency, and `data/sponsor_aliases.json` is documented as never
    written by a matcher, only by a decision.
    """
    key = sponsor_check.normalize_name(company)
    if not key:
        return ""

    entry = (aliases or {}).get(key)
    if entry and entry.get("register_name"):
        name = entry["register_name"]
        # Confirm the alias still points at a Skilled Worker licence rather
        # than trusting it outright. The register carries **one row per route**,
        # so an organisation can appear on it while holding nothing Anurag can
        # use -- Salesforce's first row is "Temporary Worker (A rating) | Global
        # Business Mobility: Graduate Trainee". `load_sponsor_lookup` already
        # filters on Route, so presence in it is the check; an alias that no
        # longer resolves there is stale, and stale is not decided.
        if sponsor_check.normalize_name(name) in (lookup or {}):
            return name
        return ""

    if key in (lookup or {}):
        return key
    return ""


def append_boards(rows: list[dict], path: Path) -> int:
    """Append accepted boards to ats_boards.csv, preserving what is there."""
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    nl = "\n"
    lines = [] if not existing or existing.endswith(nl) else [nl]
    for r in rows:
        lines.append(",".join([
            r["company"], r["ats"], r["slug"], r["register_name"], "active",
            "", f"{r['jobs']} jobs on first probe"]) + nl)
    with open(path, "a", encoding="utf-8", newline="") as fh:
        fh.write("".join(lines))
    return len(rows)


#: How long a "no board found" answer stays believed. A company that had no
#: public board in June may have one now, and re-probing 150 of them costs a
#: few minutes once a month.
MISS_TTL_DAYS = 30


def cached_hit(cache: dict, key: str):
    """The cached result for this company: a hit dict, {} for a known miss, or
    None meaning "probe it".

    The first version stored `hit or {}` and skipped anything already in the
    cache. That did two wrong things at once. A cached *miss* was believed
    forever, contradicting this module's own stated policy of re-probing
    unresolved companies monthly. And a cached *hit* was skipped entirely
    rather than reported, so a second run of a successful sweep found nothing
    and `--apply` had no rows to write.

    A hit is a durable fact about a company and is reused without re-probing.
    A miss is only an observation about one day, and expires.
    """
    entry = cache.get(key)
    if entry is None:
        return None

    # Entries written before this format had no timestamp: a bare hit dict, or
    # {} for a miss. Honour the hits; re-probe the undated misses.
    if "hit" not in entry:
        hit = entry if entry.get("company") else None
        if hit is None:
            return None
    else:
        hit = entry.get("hit")
        if not hit:
            try:
                seen = dt.date.fromisoformat(entry.get("at", ""))
            except ValueError:
                return None
            return {} if (dt.date.today() - seen).days < MISS_TTL_DAYS else None

    # Re-grade rather than trusting the stored verdict, and do it on the way
    # out so the legacy path cannot skip it -- the first attempt returned
    # old-format entries early and five Ashby boards kept a verdict from a rule
    # that had since been fixed. `match` is a judgement; the postings are the
    # expensive part, and those are what is really being cached.
    hit["match"] = match_strength(hit.get("company", ""),
                                  hit.get("board_name", ""),
                                  hit.get("slug", ""))
    return hit


def load_cache() -> dict:
    try:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_cache(cache: dict) -> None:
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CACHE.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(CACHE)


def known_slugs(path: Path) -> set[str]:
    """Slugs already in ats_boards.csv, so a sweep does not re-probe them."""
    out: set[str] = set()
    try:
        with open(path, encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                if row.get("slug"):
                    out.add(row["slug"].strip().lower())
    except OSError:
        pass
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Find public ATS boards belonging to licensed sponsors.")
    ap.add_argument("companies", nargs="*",
                    help="company names; omit to read from the register")
    ap.add_argument("--limit", type=int, default=25,
                    help="how many tracker companies to probe (default 25)")
    ap.add_argument("--throttle", type=float, default=THROTTLE)
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--apply", action="store_true",
                    help="append confirmed boards to data/ats_boards.csv "
                         "(only those whose register_name is already decided)")
    args = ap.parse_args(argv)

    if args.companies:
        targets = args.companies
    else:
        targets = targets_from_tracker(args.limit)
        if not targets:
            print("CANNOT PROBE: no sponsor-confirmed companies in the "
                  "tracker. That is not the same as finding nothing.")
            return EXIT_CANNOT_PROBE

    cache = load_cache()
    already = known_slugs(ROOT / "data" / "ats_boards.csv")
    hits: list[dict] = []
    seen_already: list[dict] = []
    review: list[dict] = []

    for company in targets:
        key = company.strip().lower()
        hit = cached_hit(cache, key)

        if hit is None:
            if args.verbose:
                print(f"{company}")
            hit = probe_company(company, throttle=args.throttle,
                                verbose=args.verbose)
            cache[key] = {"at": dt.date.today().isoformat(), "hit": hit}
        elif args.verbose:
            print(f"{company}: cached ({hit or 'no board'})")

        if not hit:
            continue
        # "Already tracked" and "no board exists" are different findings and
        # the first version reported both as nothing. A probe that found
        # Palantir's 308-posting Lever board and printed "0 new boards" reads
        # as a broken prober.
        if hit["slug"].lower() in already:
            seen_already.append(hit)
            if args.verbose:
                print(f"  known  {hit['company'][:34]:<34} {hit['ats']} "
                      f"{hit['slug']} — already in ats_boards.csv")
            continue
        (hits if hit["match"] == "exact" else review).append(hit)
        tag = "HIT " if hit["match"] == "exact" else "?   "
        print(f"  {tag} {hit['company'][:34]:<34} {hit['ats']:<11} "
              f"{hit['slug']:<24} {hit['jobs']} jobs")

    save_cache(cache)

    if args.json:
        print(json.dumps(hits, indent=2))
        return EXIT_OK if hits else EXIT_NOTHING

    print()
    print(f"probed {len(targets)} company(ies): {len(hits)} confirmed, "
          f"{len(review)} needing a look, {len(seen_already)} already tracked, "
          f"{len(targets) - len(hits) - len(review) - len(seen_already)} "
          f"with no public board")

    if review:
        print()
        print("Name did not match exactly - check before adding. A probe for "
              '"Universal Music" once returned a two-person design agency '
              'called "Universal":')
        for h in review:
            print(f"  {h['company']}  ->  {h['ats']}/{h['slug']} "
                  f"(board says {h['board_name'] or 'nothing'}), "
                  f"{h['jobs']} jobs")
            for t in h["sample"]:
                print(f"      - {t}")
    if hits:
        lookup = sponsor_check.load_sponsor_lookup()
        aliases = sponsor_check.load_aliases()
        for h in hits:
            h["register_name"] = resolve_register_name(h["company"], lookup,
                                                       aliases)
        ready = [h for h in hits if h["register_name"]]
        undecided = [h for h in hits if not h["register_name"]]

        if ready:
            print()
            print("company,ats,slug,register_name,status,last_ok,notes")
            for h in ready:
                print(f"  {h['company']},{h['ats']},{h['slug']},"
                      f"{h['register_name']},active,,{h['jobs']} jobs on "
                      f"first probe")

        if undecided:
            print()
            print(f"{len(undecided)} board(s) have no decided register_name "
                  f"and are left out even with --apply:")
            for h in undecided:
                print(f"  {h['company']} ({h['ats']}/{h['slug']}, "
                      f"{h['jobs']} jobs)")
            print("Resolve them with pipeline/sponsor_review.py, which "
                  "proposes a register name and waits for you to tick it. A "
                  "prefix match is not a decision.")

        if args.apply and ready:
            n = append_boards(ready, ROOT / "data" / "ats_boards.csv")
            print()
            print(f"Appended {n} board(s) to data/ats_boards.csv. "
                  f"`python ats_main.py --dry-run` polls them.")
        elif ready:
            print()
            print("Proposed, not written. Re-run with --apply to append.")
    return EXIT_OK if hits else EXIT_NOTHING


if __name__ == "__main__":
    sys.exit(main())
