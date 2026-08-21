"""Read back what an agent did, and put it in the hash-chained log.

This is the return leg, and it is the reason auto-apply is worth building at
all. The system has never had outcome data -- 3,623 rows tracked and, until
this week, nothing marked applied. Every learning idea in the plan is gated on
data that does not exist. An agent that submits a form is standing right where
that data is created: it sees the confirmation page, and it can see the
confirmation email land in the Gmail it already has open.

The contract is one file per attempt, written by Cowork:

    runs/<job_id>/result.json
        {
          "job_id":     "lever:abc123",
          "outcome":    "submitted" | "stopped" | "failed",
          "at":         "2026-08-21T22:40:00Z",
          "blocked_by": ["ats_recorded"],        # when stopped
          "confirmation": "Application received. Ref ABC-123",
          "screenshot": "runs/lever_abc123/final.png",
          "error":      "..."                     # when failed
        }

Three outcomes rather than two, for the same reason verify has three exit
codes. "The agent stopped because a gate said so" and "the agent crashed" are
different facts about the world, and collapsing them loses the distinction
between a system working and a system broken.

Reading it does three things, in this order:

1. Verify the package it refers to still matches its manifest. A result that
   describes a package someone edited afterwards is describing something else.
2. Append to the decision log. `application.logged` for a submission, a
   `cowork.stopped` for anything else -- because a stop is evidence too, and
   the thing you most want to count later is how often the gate fired and on
   which condition.
3. Update the tracker through the same field-level path the API uses.

It never marks a job applied on the strength of the file alone. `outcome:
submitted` with no confirmation text is treated as unverified and recorded as
such: an agent reporting success is exactly the claim this whole system exists
not to take at face value.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
import tracker  # noqa: E402
from web.api.audit import APPLICATION, DecisionLog  # noqa: E402
from web.api.store import TrackerStore  # noqa: E402

EXIT_OK = 0
EXIT_REJECTED = 1
EXIT_CANNOT_READ = 2

#: A stop is recorded, not discarded. How often the gate fires and on which
#: condition is the most useful thing this system can learn early, and it is
#: available long before any employer replies.
STOPPED = "cowork.stopped"

VALID_OUTCOMES = ("submitted", "stopped", "failed")


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def package_intact(pkg_dir: Path) -> tuple[bool, str]:
    """Does the package still match the manifest it was built with?

    A result describing an edited package is describing something else. This is
    the same question `cv_gate` asks about a PDF, one level up.
    """
    manifest_path = pkg_dir / "MANIFEST.json"
    if not manifest_path.exists():
        return False, "no MANIFEST.json in the package"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        return False, f"manifest unreadable: {exc}"

    for name, rec in manifest.get("files", {}).items():
        f = pkg_dir / name
        if not f.exists():
            return False, f"{name} is missing from the package"
        if sha256_of(f) != rec["sha256"]:
            return False, f"{name} changed after the package was built"
    return True, f"{len(manifest.get('files', {}))} file(s) match"


def validate(result: dict) -> list[str]:
    """Everything wrong with the file, as a list. Empty means usable."""
    problems: list[str] = []
    if not result.get("job_id"):
        problems.append("no job_id")
    outcome = result.get("outcome")
    if outcome not in VALID_OUTCOMES:
        problems.append(f"outcome {outcome!r} is not one of {VALID_OUTCOMES}")
    if not result.get("at"):
        problems.append("no timestamp")
    if outcome == "submitted" and not (result.get("confirmation") or "").strip():
        # Not fatal -- recorded as unverified rather than refused, because the
        # application probably did happen and losing the record is worse than
        # holding an imperfect one. But it is never silently a clean submit.
        problems.append("submitted with no confirmation text (unverified)")
    if outcome == "stopped" and not result.get("blocked_by"):
        problems.append("stopped without naming a condition")
    return problems


def record(result_path: Path, *, store_path: Path | None = None,
           log_path: Path | None = None,
           packages_dir: Path | None = None,
           dry_run: bool = False) -> tuple[int, dict]:
    store_path = store_path or ROOT / config.JOBS_XLSX
    log_path = log_path or ROOT / "data" / "decision_log.jsonl"
    packages_dir = packages_dir or ROOT / "packages"

    if not result_path.exists():
        return EXIT_CANNOT_READ, {"error": f"no result at {result_path}"}
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        return EXIT_CANNOT_READ, {"error": f"result unreadable: {exc}"}

    problems = validate(result)
    fatal = [p for p in problems if "unverified" not in p]
    if fatal:
        return EXIT_REJECTED, {"error": "; ".join(fatal), "problems": problems}

    job_id = result["job_id"]
    outcome = result["outcome"]
    unverified = any("unverified" in p for p in problems)

    pkg = packages_dir / job_id.replace(":", "_")
    intact, why = (package_intact(pkg) if pkg.exists()
                   else (False, "no package on disk for this job"))

    rows = tracker.load_tracker(str(store_path))
    row = rows.get(job_id)
    if row is None:
        return EXIT_REJECTED, {"error": f"no job {job_id!r} in the tracker"}

    report = {"job_id": job_id, "outcome": outcome, "company": row.get("company", ""),
              "title": row.get("title", ""), "package_intact": intact,
              "package_detail": why, "unverified": unverified,
              "problems": problems}

    if dry_run:
        return EXIT_OK, report

    log = DecisionLog(log_path)
    actor = {"id": "cowork", "display_name": "Claude Cowork", "via": "browser"}

    if outcome == "submitted":
        entry_type, after = APPLICATION, {"status": "applied"}
        mutations = {job_id: {"status": "applied",
                              "date_applied": result["at"][:10],
                              "applied_via": result.get("applied_via",
                                                        "company site")}}
    else:
        # A stop or a failure changes nothing about the job. It is still a fact
        # worth keeping: the gate's hit rate is the earliest signal this system
        # can produce.
        entry_type, after, mutations = STOPPED, {}, {}

    log.record(
        type=entry_type,
        entity={"kind": "job", "id": job_id},
        actor=actor,
        input={k: result.get(k) for k in
               ("job_id", "outcome", "at", "blocked_by", "applied_via")},
        validations=[
            {"rule": "package_intact",
             "result": "pass" if intact else "fail", "detail": why},
            {"rule": "confirmation_present",
             "result": "fail" if unverified else "pass",
             "detail": (result.get("confirmation") or "")[:160]},
        ],
        rationale=(f"Cowork {outcome}"
                   + (f": {', '.join(result['blocked_by'])}"
                      if result.get("blocked_by") else "")),
        effects={"company": row.get("company", ""), "title": row.get("title", ""),
                 "confirmation": (result.get("confirmation") or "")[:200],
                 "screenshot": result.get("screenshot", ""),
                 "error": result.get("error", "")},
        before={job_id: {"status": row.get("status", "")}} if mutations else {},
        after={job_id: after} if mutations else {},
        reversible=bool(mutations))

    if mutations:
        report["rows_changed"] = TrackerStore(store_path).apply(mutations)
    return EXIT_OK, report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Read a Cowork result back into the log and the tracker.")
    ap.add_argument("result", help="runs/<job_id>/result.json")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    code, report = record(Path(args.result), dry_run=args.dry_run)

    if args.json:
        print(json.dumps(report, indent=2))
        return code
    if "error" in report:
        print(f"REJECTED: {report['error']}")
        return code

    print(f"{report['company']} — {report['title']}")
    print(f"  outcome        : {report['outcome']}")
    print(f"  package intact : {report['package_intact']} ({report['package_detail']})")
    if report["problems"]:
        for p in report["problems"]:
            print(f"  note           : {p}")
    if "rows_changed" in report:
        print(f"  tracker        : {report['rows_changed']} row(s) updated")
    else:
        print("  tracker        : unchanged (recorded in the log only)")
    return code


if __name__ == "__main__":
    sys.exit(main())
