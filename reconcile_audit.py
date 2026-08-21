"""Replay the decision log over the tracker and report where they disagree.

The alias overlay already works this way: `audit.replay_aliases` rebuilds it
from the log and `audit.verify` reports drift between the two. Job status had
no equivalent, and on 2026-08-16 that cost a real application.

The incident: `application.logged` seq 7 recorded Snowflake -- Associate
Solution Engineer, applied 2026-07-20. The tracker has that row back at
`status: new` with no `date_applied`. Because `shortlist.disqualify` only drops
rows whose status is set and not "new", the role returned to the shortlist and
sat at #2 for five days -- the system recommending a job already applied to.

Nothing detected it. The log was right the whole time and nobody asked it.

What this does NOT do is guess a cause. Every automated write path was tested
against a copy of the real workbook -- a plain load/save round trip, three of
them, `merge_new_jobs` (the 09:00 Adzuna run) and `merge_rows` (the ATS run) --
and all four preserve an applied status. The remaining suspects are outside the
code: a save from Excel holding a view older than the write, or an interactive
action that left no trace. `store.py` guards against two *programmatic* writers
racing; it cannot guard against Excel as a third one, and this workbook is
opened in Excel by design.

So the fix is not a patch to a broken function. It is making the class loud:
the log is the tamper-evident record, so anything it asserts that the store
denies is drift, whoever caused it.

Exit codes follow the loop-engineering convention, for the same reason:
    0  no drift -- the store agrees with the log
    1  drift -- the store contradicts the log; go look
    2  cannot verify -- the log or store is unreadable, or the hash chain is
       broken. "The checker could not run" and "the thing is broken" are
       different facts and collapsing them is how a gate starts lying.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import config
import tracker
from web.api.audit import (APPLICATION, FAILED_SUFFIX, UNDO_SUFFIX,
                           DecisionLog)

EXIT_CLEAN = 0
EXIT_DRIFT = 1
EXIT_CANNOT_VERIFY = 2

# Fields the log can speak about. Anything else in a row is none of its
# business -- the log records decisions, not the whole row.
TRACKED_FIELDS = ("status", "date_applied", "applied_via")


def replay_statuses(entries) -> dict[str, dict]:
    """Rebuild expected per-job field state from the log. Last write wins.

    Folds each entry's `after` block, which both `log_application` and
    `set_status` already write in the shape {job_id: {field: value}}. Reading
    `after` rather than special-casing each action type means a new action that
    writes to a job row is covered the day it ships, without editing this file.

    `application.logged` additionally carries `date_applied` and `applied_via`
    in its input but not in `after`, so those are lifted across -- they are the
    fields that make an application record worth anything.

    Skips, mirroring `replay_aliases`:
      - `.failed` entries, which state outright that nothing was written
      - entries reversed by a later `.undone`
    """
    rows = list(entries)
    undone = {r.get("input", {}).get("target_log_entry_id")
              for r in rows if r.get("type", "").endswith(UNDO_SUFFIX)}

    out: dict[str, dict] = {}
    for r in rows:
        if r.get("id") in undone or r.get("type", "").endswith(FAILED_SUFFIX):
            continue

        after = r.get("after") or {}
        if not isinstance(after, dict):
            continue

        for job_id, fields in after.items():
            if not isinstance(fields, dict):
                continue
            rec = out.setdefault(str(job_id), {})
            for field, value in fields.items():
                if field in TRACKED_FIELDS:
                    rec[field] = value

            if r.get("type") == APPLICATION:
                src = r.get("input", {})
                for field in ("date_applied", "applied_via"):
                    if src.get(field):
                        rec[field] = src[field]

            rec["_seq"] = r.get("seq")
            rec["_ts"] = r.get("ts", "")
            rec["_entry"] = r.get("id", "")
            rec["_type"] = r.get("type", "")
    return out


def find_drift(expected: dict[str, dict], rows: dict[str, dict]) -> list[dict]:
    """Every field the log asserts and the store contradicts.

    A missing row counts as drift too: the log recorded a decision about a job
    that is no longer in the store at all, which is strictly worse than a
    wrong value.
    """
    out: list[dict] = []
    for job_id, rec in sorted(expected.items(), key=lambda kv: kv[1].get("_seq") or 0):
        row = rows.get(job_id)
        if row is None:
            out.append({
                "job_id": job_id, "field": "*", "expected": "row present",
                "actual": "row missing", "company": "", "title": "",
                "seq": rec.get("_seq"), "ts": rec.get("_ts"),
                "entry": rec.get("_entry"), "type": rec.get("_type"),
            })
            continue
        for field in TRACKED_FIELDS:
            if field not in rec:
                continue
            want = str(rec[field] or "").strip()
            got = str(row.get(field) or "").strip()
            if want != got:
                out.append({
                    "job_id": job_id, "field": field,
                    "expected": want, "actual": got,
                    "company": row.get("company", ""),
                    "title": row.get("title", ""),
                    "seq": rec.get("_seq"), "ts": rec.get("_ts"),
                    "entry": rec.get("_entry"), "type": rec.get("_type"),
                })
    return out


REPAIR = "reconcile.repaired"


def repair(store_path: Path, log_path: Path, drift: list[dict]) -> dict:
    """Re-apply what the log asserts to the store, and record having done it.

    This is a reconciliation, not a new decision. The log already says the
    application happened; the store is the thing that is wrong. Logging a
    fresh `application.logged` would claim a second application to a job
    applied to once, which is a different and worse lie than the one being
    fixed.

    Writes through `TrackerStore.apply`, so it inherits the field-level,
    re-read-under-the-lock discipline rather than stamping a whole snapshot
    back over whatever else has landed since.

    The repair itself goes into the chain. A record that can be silently
    corrected is not a record.
    """
    from web.api.store import TrackerStore  # local: keeps the CLI import-light

    mutations: dict[str, dict[str, str]] = {}
    for d in drift:
        if d["field"] == "*":
            continue                        # a vanished row cannot be patched
        mutations.setdefault(d["job_id"], {})[d["field"]] = d["expected"]

    if not mutations:
        return {"repaired": 0, "rows": 0, "skipped": len(drift)}

    store = TrackerStore(store_path)
    changed = store.apply(mutations)

    log = DecisionLog(log_path)
    log.record(
        type=REPAIR,
        entity={"kind": "reconciliation", "id": ",".join(sorted(mutations))},
        actor={"id": "reconcile_audit", "display_name": "reconcile_audit",
               "via": "cli"},
        input={"fields": [f"{d['job_id']}:{d['field']}" for d in drift
                          if d["field"] != "*"]},
        validations=[{"rule": "chain_ok", "result": "pass", "detail": ""}],
        rationale=("Store disagreed with the log; re-applied the log's "
                   "asserted values."),
        effects={"rows": changed, "fields": len(mutations)},
        before={d["job_id"]: {d["field"]: d["actual"]} for d in drift
                if d["field"] != "*"},
        after=mutations,
        reversible=False)

    return {"repaired": sum(len(v) for v in mutations.values()),
            "rows": changed,
            "skipped": sum(1 for d in drift if d["field"] == "*")}


def reconcile(store_path: Path, log_path: Path) -> tuple[int, dict]:
    """Returns (exit_code, report). Never raises on a missing input."""
    if not log_path.exists():
        return EXIT_CANNOT_VERIFY, {"error": f"no decision log at {log_path}"}
    if not store_path.exists():
        return EXIT_CANNOT_VERIFY, {"error": f"no tracker at {store_path}"}

    log = DecisionLog(log_path)
    try:
        entries = log.entries()
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return EXIT_CANNOT_VERIFY, {"error": f"log unreadable: {exc}"}

    chain = log.verify()
    if not chain.get("chain_ok"):
        # A broken chain means the log itself is not trustworthy, so a
        # comparison against it proves nothing either way.
        return EXIT_CANNOT_VERIFY, {
            "error": "hash chain is broken -- the log cannot be trusted as a "
                     "reference",
            "broken_at_seq": chain.get("broken_at_seq"),
            "entries": chain.get("count"),
        }

    try:
        rows = tracker.load_tracker(str(store_path))
    except (OSError, ValueError) as exc:
        return EXIT_CANNOT_VERIFY, {"error": f"tracker unreadable: {exc}"}

    expected = replay_statuses(entries)
    drift = find_drift(expected, rows)

    report = {
        "entries": len(entries),
        "chain_ok": True,
        "jobs_asserted_by_log": len(expected),
        "tracker_rows": len(rows),
        "drift_count": len(drift),
        "drift": drift,
    }
    return (EXIT_DRIFT if drift else EXIT_CLEAN), report


def _render(report: dict, code: int) -> None:
    if "error" in report:
        print(f"CANNOT VERIFY: {report['error']}")
        for key in ("broken_at_seq", "entries"):
            if report.get(key) is not None:
                print(f"  {key}: {report[key]}")
        return

    print(f"log entries        : {report['entries']}")
    print(f"hash chain         : ok")
    print(f"jobs the log claims: {report['jobs_asserted_by_log']}")
    print(f"tracker rows       : {report['tracker_rows']}")
    print()

    if code == EXIT_CLEAN:
        print("CLEAN: the tracker agrees with every decision in the log.")
        return

    print(f"DRIFT: {report['drift_count']} field(s) the log asserts and the "
          f"tracker denies.\n")
    for d in report["drift"]:
        who = f"{d['company']} — {d['title']}" if d["company"] else d["job_id"]
        print(f"  {who}")
        print(f"    id       : {d['job_id']}")
        print(f"    field    : {d['field']}")
        print(f"    log says : {d['expected']!r}   (seq {d['seq']}, "
              f"{d['type']}, {d['ts']})")
        print(f"    store has: {d['actual']!r}")
        print()
    print("The log is append-only and hash-chained, so it is the reference.")
    print("Re-apply the decision, or record why it was reversed.")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Replay the decision log over the tracker and report drift.")
    ap.add_argument("--store", default=None,
                    help=f"tracker workbook (default: {config.JOBS_XLSX})")
    ap.add_argument("--log", default=None,
                    help="decision log jsonl (default: data/decision_log.jsonl)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--quiet", action="store_true",
                    help="print nothing; use the exit code")
    ap.add_argument("--repair", action="store_true",
                    help="re-apply what the log asserts to the store, and "
                         "record the repair in the chain")
    args = ap.parse_args(argv)

    root = Path(__file__).resolve().parent
    store_path = Path(args.store) if args.store else root / config.JOBS_XLSX
    log_path = Path(args.log) if args.log else root / "data" / "decision_log.jsonl"

    code, report = reconcile(store_path, log_path)

    if args.repair and code == EXIT_DRIFT:
        if not args.quiet:
            _render(report, code)
            print("--- repairing ---\n")
        outcome = repair(store_path, log_path, report["drift"])
        report["repair"] = outcome
        # Prove it took, rather than reporting the write we just asked for.
        code, report_after = reconcile(store_path, log_path)
        report["drift_after_repair"] = report_after.get("drift_count",
                                                        report_after.get("error"))
        if not args.quiet and not args.json:
            print(f"repaired {outcome['repaired']} field(s) across "
                  f"{outcome['rows']} row(s)")
            if outcome["skipped"]:
                print(f"skipped {outcome['skipped']} vanished row(s) — a "
                      f"missing row cannot be patched")
            print(f"re-checked: {report['drift_after_repair']} drift remaining")
            return code
    if args.quiet:
        return code
    if args.json:
        print(json.dumps(report, indent=2, default=str))
        return code
    _render(report, code)
    return code


if __name__ == "__main__":
    sys.exit(main())
