"""Cross-reference his own LinkedIn connections export against the tracker.

The question this answers is the one that changes what he does today: **of the
jobs worth applying to, which are at companies where he already knows
someone?** A cold application and a warm intro are not the same act, and until
now nothing in the system could tell them apart.

Why this does not cross the LinkedIn line
-----------------------------------------
The rule is: no scraping, no automation, no dummy accounts. `referrals/
linkedin.py` builds search URLs and never opens them — he runs them himself.

This is a different thing again. `Connections.csv` is a file **LinkedIn gives
him**, about people who chose to connect with him, downloaded through the
export button in his own settings. Reading a file off his disk is not
automation against a service. Nothing here logs in, fetches, or opens a
browser, and a test asserts the module makes no network calls at all.

The email column follows the same consent logic the outreach doctrine already
uses for GitHub: LinkedIn includes an address only where that person allowed
their connections to see it. Absent is the default and absent means no route.

What it will not do
-------------------
It does not rank people, score relationships, or draft anything. It reports an
overlap and stops. `web/api/outreach.py` handles drafting, and it already
refuses to ask for a referral in a first message — that doctrine is unchanged
and this feeds it, rather than working around it.

    python pipeline/connections.py import ~/Downloads/Connections.csv
    python pipeline/connections.py

The store it writes is personal by definition: real names and addresses of
people who did not consent to appear in a repo. It is gitignored, and
`data_contract.py` files it under PERSONAL_PATHS.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
import tracker  # noqa: E402
from pipeline import shortlist  # noqa: E402

EXIT_OK = 0
EXIT_NOTHING = 1
EXIT_CANNOT = 2

STORE = ROOT / "data" / "connections.json"

#: LinkedIn's own column names. It has renamed these before, so a missing
#: header is reported rather than silently yielding zero connections.
REQUIRED = ("First Name", "Last Name", "Company", "Position")


def _norm(name: str) -> str:
    """Company name reduced to something two spellings can agree on.

    The suffix list matches `ats_prober._SUFFIX` deliberately, including
    "technologies" — which a first version left out, and which cost the single
    most valuable match in the first real run. A LinkedIn profile says
    "Palantir Technologies UK Ltd" while the tracker says "Palantir", so his #1
    target, holding the one contact whose title is literally Forward Deployed
    Engineer, was the row that silently failed to join.
    """
    s = re.sub(r"[^a-z0-9 ]", " ", (name or "").lower())
    s = re.sub(
        r"\b(ltd|limited|plc|llp|llc|inc|corp|corporation|group|holdings?|"
        r"uk|gb|international|global|services|technologies|technology|the)\b",
        " ", s)
    return re.sub(r"\s+", " ", s).strip()


def read_export(path: Path) -> list[dict]:
    """Parse a LinkedIn Connections.csv into rows.

    LinkedIn prepends a three-line "Notes:" preamble to recent exports, before
    the real header. Handing that straight to DictReader makes the preamble the
    header and every row unreadable, so the header is located rather than
    assumed.
    """
    raw = path.read_text(encoding="utf-8-sig", errors="replace")
    lines = raw.splitlines()

    start = 0
    for i, line in enumerate(lines[:12]):
        if "First Name" in line and "Last Name" in line:
            start = i
            break
    else:
        raise ValueError(
            "no 'First Name,Last Name,...' header in the first 12 lines — is "
            "this a LinkedIn Connections export?")

    reader = csv.DictReader(io.StringIO("\n".join(lines[start:])))
    missing = [c for c in REQUIRED if c not in (reader.fieldnames or [])]
    if missing:
        raise ValueError(f"export is missing column(s): {', '.join(missing)}")

    out = []
    for row in reader:
        company = (row.get("Company") or "").strip()
        first = (row.get("First Name") or "").strip()
        last = (row.get("Last Name") or "").strip()
        if not company or not (first or last):
            continue
        out.append({
            "name": f"{first} {last}".strip(),
            "company": company,
            "company_key": _norm(company),
            "position": (row.get("Position") or "").strip(),
            # Present only where that person allowed connections to see it.
            # Absent is the default, and absent means there is no route.
            "email": (row.get("Email Address") or "").strip(),
            "url": (row.get("URL") or "").strip(),
            "connected_on": (row.get("Connected On") or "").strip(),
        })
    return out


def load_store() -> list[dict]:
    try:
        return json.loads(STORE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def save_store(rows: list[dict]) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STORE.with_suffix(".tmp")
    tmp.write_text(json.dumps(rows, indent=2, ensure_ascii=False),
                   encoding="utf-8")
    tmp.replace(STORE)


def overlap(connections: list[dict], rows: list[dict]) -> list[dict]:
    """Shortlist-worthy jobs at companies where he already knows someone.

    Filtered through `shortlist.disqualify` rather than run over the whole
    tracker: a connection at a company whose only opening he would never apply
    to is not a lead, and a list padded with those stops being read.
    """
    by_company: dict[str, list[dict]] = defaultdict(list)
    for person in connections:
        if person.get("company_key"):
            by_company[person["company_key"]].append(person)

    hits: dict[str, dict] = {}
    for row in rows:
        if shortlist.disqualify(row) is not None:
            continue
        if shortlist.is_agency(row.get("company", "")):
            continue
        key = _norm(row.get("company", ""))
        people = by_company.get(key)
        if not people:
            continue
        entry = hits.setdefault(key, {"company": row.get("company", ""),
                                      "people": people, "jobs": []})
        entry["jobs"].append(row)

    return sorted(hits.values(),
                  key=lambda h: (-len(h["jobs"]), -len(h["people"])))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Which shortlisted jobs are at companies you already know "
                    "someone at?")
    sub = ap.add_subparsers(dest="cmd")
    imp = sub.add_parser("import", help="load a LinkedIn Connections.csv")
    imp.add_argument("path")
    args = ap.parse_args(argv)

    if args.cmd == "import":
        src = Path(args.path).expanduser()
        if not src.exists():
            print(f"CANNOT IMPORT: {src} does not exist.")
            return EXIT_CANNOT
        try:
            rows = read_export(src)
        except (OSError, ValueError) as exc:
            print(f"CANNOT IMPORT: {exc}")
            return EXIT_CANNOT
        save_store(rows)
        firms = len({r["company_key"] for r in rows if r["company_key"]})
        with_email = sum(1 for r in rows if r["email"])
        print(f"Imported {len(rows)} connections across {firms} companies "
              f"into {STORE.relative_to(ROOT)}.")
        print(f"{with_email} shared an email address; the rest have no route "
              f"from this file, which is the default and not a gap to fill.")
        return EXIT_OK

    connections = load_store()
    if not connections:
        print("No connections imported yet.")
        print()
        print("On LinkedIn: Settings & Privacy -> Data Privacy -> Get a copy "
              "of your data -> Connections. It arrives as Connections.csv.")
        print("Then: python pipeline/connections.py import <path>")
        print()
        print("This reads a file LinkedIn gives you. Nothing logs in or "
              "fetches anything.")
        return EXIT_CANNOT

    store = ROOT / config.JOBS_XLSX
    if not store.exists():
        print(f"CANNOT REPORT: no tracker at {store}.")
        return EXIT_CANNOT
    rows = list(tracker.load_tracker(str(store)).values())

    found = overlap(connections, rows)
    for h in found:
        print(f"{h['company']} — {len(h['jobs'])} open role"
              f"{'s' if len(h['jobs']) != 1 else ''}, "
              f"{len(h['people'])} connection"
              f"{'s' if len(h['people']) != 1 else ''}")
        for job in h["jobs"][:4]:
            print(f"      {(job.get('title') or '?')[:58]}  {job.get('id')}")
        for p in h["people"][:4]:
            route = p["email"] or p["url"] or "no route in the export"
            print(f"      - {p['name']}, {p['position'][:38] or '?'}  "
                  f"({route})")
        print()

    print(f"{len(found)} company(ies) where a warm intro is possible, out of "
          f"{len({c['company_key'] for c in connections})} in your network.")
    if found:
        print("Draft with the outreach endpoint, which will not ask for a "
              "referral in a first message. That rule is not negotiable here "
              "either.")
    return EXIT_OK if found else EXIT_NOTHING


if __name__ == "__main__":
    sys.exit(main())
