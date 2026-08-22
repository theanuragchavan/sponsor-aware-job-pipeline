"""Record applications made outside the pipeline.

The funnel was reporting 3 applications when 11 had gone out in three days.
Eight of them were to companies the tracker had never heard of, found on
Student Circus and employers' own sites, so there was no row to mark applied
and nowhere for an outcome to live.

That is not a small bookkeeping gap. Every question worth asking later --
does the new CV get replies, which channel converts, is silence at 10 days
normal -- divides by the number of applications. A denominator that counts a
quarter of them makes every answer wrong in the same flattering direction.

So this adds a row for an application that happened somewhere else, and marks
it applied. Two things it deliberately does not do:

**It does not judge the role.** `shortlist.disqualify` still has its say when
the row is ranked, and it will drop most of these -- "Senior Backend
Developer" is filtered as too senior, which is exactly why the pipeline never
surfaced the Rullion posting he applied to and was rejected from inside a day.
But a filter's job is to decide what to *show*, not to decide what is true.
He applied; the record says so.

**It does not invent a sponsor verdict.** Sponsorship comes from
`sponsor_check.match_company` against the register, the same call the ingest
path makes. A manually-added row that claimed "yes" without checking would
poison the one field the whole system is built on.

    python pipeline/log_manual.py --file data/manual_applications.json
    python pipeline/log_manual.py --file ... --apply

Preview is the default and prints exactly what would change. Nothing is
written without `--apply`, and every write goes through the same hash-chained
decision log the web app uses.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
import sponsor_check  # noqa: E402
import tracker  # noqa: E402

EXIT_OK = 0
EXIT_NOTHING = 1
EXIT_CANNOT = 2

#: Marks a row as hand-entered rather than fetched. Kept distinct from the
#: ingest sources so `source_of` and the liveness rules can tell them apart --
#: a manual row has no feed to be absent from, so it must never be probed for
#: liveness or aged out the way an Adzuna row is.
SOURCE_MANUAL = "manual"

REQUIRED = ("company", "title", "date_applied", "applied_via")


def slug(text: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-",
                                     (text or "").lower())).strip("-")


def row_id(entry: dict) -> str:
    """A stable synthetic id, so re-running does not duplicate the row."""
    return f"{SOURCE_MANUAL}:{slug(entry['company'])}-{slug(entry['title'])}"[:120]


def find_existing(entry: dict, rows: dict) -> dict | None:
    """A row already in the tracker for this company and title, if any.

    Checked before inventing one: Sparta Global's Junior AI Engineer was
    already ingested from Adzuna, and adding a second row for it would split
    one application across two records and double the denominator.

    `job_id` wins when given, because company+title is not always unique. That
    same Sparta role exists twice -- London and Sheffield, one requisition per
    city -- and matching on the pair would silently pick whichever came first
    and mark the wrong city applied.
    """
    if entry.get("job_id"):
        return rows.get(str(entry["job_id"]))

    want_c = sponsor_check.normalize_name(entry["company"])
    want_t = re.sub(r"[^a-z0-9]+", " ", entry["title"].lower()).strip()
    for row in rows.values():
        if sponsor_check.normalize_name(row.get("company", "")) != want_c:
            continue
        have_t = re.sub(r"[^a-z0-9]+", " ", (row.get("title") or "").lower()).strip()
        if have_t == want_t:
            return row
    return None


def differs(entry: dict, row: dict) -> bool:
    """Whether the tracker row disagrees with what the entry records.

    Status is the obvious one -- new -> applied, applied -> rejected. The less
    obvious one is a row that already says `applied` but with `applied_via`
    blank, which is how both Snowflake rows sat: the application happened, the
    channel was never written down. That blank is not cosmetic. `applied_via`
    is the axis the whole channel funnel divides by, so a row missing it is a
    row that silently drops out of the only analysis it was applied for.

    Only fields the entry actually states are compared, so an entry that omits
    a field never blanks one that is already filled.
    """
    if (row.get("status") or "new").strip().lower() != (
            entry.get("status") or "applied"):
        return True
    for field in ("applied_via", "date_applied"):
        stated = (entry.get(field) or "").strip()
        if stated and (row.get(field) or "").strip() != stated:
            return True
    return False


def build_row(entry: dict, lookup, aliases) -> dict:
    match, rating, route = sponsor_check.match_company(
        entry["company"], lookup, aliases)
    return {
        "id": row_id(entry),
        "title": entry["title"],
        "company": entry["company"],
        "location": entry.get("location", ""),
        "salary_min": "", "salary_max": "", "contract_type": "",
        "category": "", "tier": entry.get("tier", ""),
        "posted_date": entry.get("posted_date", entry["date_applied"]),
        "redirect_url": entry.get("url", ""),
        "date_first_seen": entry["date_applied"],
        "status": entry.get("status", "applied"),
        "sponsor_match": match,
        "sponsor_rating": rating,
        "sponsor_route": route,
        "applied_via": entry["applied_via"],
        "date_applied": entry["date_applied"],
        "notes": entry.get("notes", ""),
        "description": entry.get("description", ""),
        "source": SOURCE_MANUAL,
        "apply_deadline": "",
    }


def plan(entries: list[dict], rows: dict, lookup, aliases) -> list[dict]:
    """What would change, without changing it."""
    out = []
    for entry in entries:
        missing = [k for k in REQUIRED if not entry.get(k)]
        if missing:
            out.append({"action": "skip", "entry": entry,
                        "why": f"missing {', '.join(missing)}"})
            continue

        existing = find_existing(entry, rows)
        if existing:
            if differs(entry, existing):
                out.append({"action": "mark", "entry": entry, "row": existing})
            else:
                out.append({"action": "already", "entry": entry,
                            "row": existing})
        else:
            out.append({"action": "add", "entry": entry,
                        "row": build_row(entry, lookup, aliases)})
    return out


def describe(step: dict) -> str:
    e, row = step["entry"], step.get("row", {})
    who = f"{e['company']} — {e['title']}"
    if step["action"] == "skip":
        return f"  SKIP    {who}\n            {step['why']}"
    if step["action"] == "already":
        return (f"  already {who}\n"
                f"            recorded {row.get('date_applied') or '?'}")
    if step["action"] == "mark":
        return (f"  MARK    {who}\n"
                f"            existing row {row.get('id')} "
                f"({row.get('status') or 'new'} -> "
                f"{e.get('status', 'applied')}), "
                f"sponsor {row.get('sponsor_match')}")
    return (f"  ADD     {who}\n"
            f"            new row {row.get('id')}\n"
            f"            sponsor {row.get('sponsor_match')}"
            f"{' ' + row['sponsor_rating'] if row.get('sponsor_rating') else ''}"
            f", via {row.get('applied_via')}, {row.get('date_applied')}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Record applications made outside the pipeline.")
    ap.add_argument("--file", required=True,
                    help="JSON list of {company,title,date_applied,applied_via,...}")
    ap.add_argument("--apply", action="store_true",
                    help="write the changes (default is preview only)")
    args = ap.parse_args(argv)

    src = Path(args.file)
    if not src.exists():
        print(f"CANNOT RECORD: {src} does not exist.")
        return EXIT_CANNOT
    try:
        entries = json.loads(src.read_text(encoding="utf-8"))
    except ValueError as exc:
        print(f"CANNOT RECORD: {src} is not valid JSON — {exc}")
        return EXIT_CANNOT
    if not isinstance(entries, list):
        print("CANNOT RECORD: expected a JSON list of applications.")
        return EXIT_CANNOT

    store_path = ROOT / config.JOBS_XLSX
    if not store_path.exists():
        print(f"CANNOT RECORD: no tracker at {store_path}.")
        return EXIT_CANNOT

    rows = tracker.load_tracker(str(store_path))
    lookup = sponsor_check.load_sponsor_lookup()
    aliases = sponsor_check.load_aliases()
    if lookup is None:
        print("CANNOT RECORD: the sponsor register is unavailable, so every "
              "new row would claim 'unconfirmed' sponsorship without having "
              "checked. That is a different fact from 'no'.")
        return EXIT_CANNOT

    steps = plan(entries, rows, lookup, aliases)
    for step in steps:
        print(describe(step))

    adds = [s for s in steps if s["action"] == "add"]
    marks = [s for s in steps if s["action"] == "mark"]
    print()
    print(f"{len(adds)} new row(s), {len(marks)} existing row(s) to mark, "
          f"{sum(1 for s in steps if s['action'] == 'already')} already "
          f"recorded, {sum(1 for s in steps if s['action'] == 'skip')} skipped")

    if not args.apply:
        print("\nPreview only. Re-run with --apply to write.")
        return EXIT_OK if (adds or marks) else EXIT_NOTHING

    if not (adds or marks):
        print("\nNothing to write.")
        return EXIT_NOTHING

    for step in marks:
        row = step["row"]
        row["status"] = step["entry"].get("status", "applied")
        row["date_applied"] = step["entry"]["date_applied"]
        row["applied_via"] = step["entry"]["applied_via"]
    for step in adds:
        rows[step["row"]["id"]] = step["row"]

    tracker.save_tracker(str(store_path), rows)
    print(f"\nWrote {len(adds)} new and {len(marks)} updated row(s) to "
          f"{config.JOBS_XLSX}.")

    # The decision log is the record that outlives the spreadsheet. Recorded
    # after the write, with what was actually written, because a log entry
    # claiming a change that did not land is worse than no entry.
    try:
        from web.api.audit import DecisionLog
        log = DecisionLog(ROOT / "data" / "decision_log.jsonl")
        for step in adds + marks:
            e = step["entry"]
            log.record(
                type="application.logged",
                entity={"kind": "job", "id": step["row"]["id"]},
                actor={"id": "anurag", "name": "Anurag Chavan"},
                input={"job_id": step["row"]["id"], "company": e["company"],
                       "title": e["title"], "applied_via": e["applied_via"],
                       "date_applied": e["date_applied"]},
                validations=[{"rule": "sponsor_checked", "result": "pass",
                              "detail": step["row"].get("sponsor_match", "")}],
                rationale=f"applied outside the pipeline via {e['applied_via']}",
                effects={"row": step["action"]},
                after={"status": "applied",
                       "date_applied": e["date_applied"],
                       "applied_via": e["applied_via"]})
        print(f"Recorded {len(adds) + len(marks)} entries in the decision log.")
    except Exception as exc:  # noqa: BLE001 - the tracker write already landed
        print(f"WARNING: tracker updated but the decision log was not written "
              f"({type(exc).__name__}: {exc}). The rows are correct; the audit "
              f"trail for them is missing.")
        return EXIT_CANNOT
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
