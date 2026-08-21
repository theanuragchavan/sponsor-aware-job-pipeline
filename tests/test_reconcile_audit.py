"""Tests for the log/store drift detector.

The detector exists because of a specific incident. On 2026-08-16 the decision
log recorded `application.logged` for Snowflake — Associate Solution Engineer,
applied 2026-07-20. The tracker has that row back at `status: new`. Because
`shortlist.disqualify` only drops rows whose status is set and not "new", the
role returned to the shortlist and sat at #2 for five days.

Every automated write path was tested against a copy of the real workbook and
all of them preserve an applied status, so this is not a patch to a broken
function — it is a detector for a class the system had no way to notice.

The properties worth pinning are the ones that decide whether it can be
trusted: last-write-wins (a row cycled through states during a test must not
read as drift), `.failed` and `.undone` entries must be ignored (they state
outright that nothing was written), and "the log is unreadable" must never be
reported as "the store is fine".

No real store or log required: everything below builds its own in a temp dir.

Run:  python tests/test_reconcile_audit.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import reconcile_audit  # noqa: E402
from web.api.audit import DecisionLog  # noqa: E402

JOB = "ashby:a2a81d70-4235-4885-9f89-d2c4bc7f28a4"
OTHER = "lever:2aa14e4f-d406-486e-9aa8-6ff3358d70a0"


def _log(tmp) -> DecisionLog:
    return DecisionLog(Path(tmp) / "decision_log.jsonl")


def _applied(log, job=JOB, when="2026-07-20", **kw):
    return log.record(
        type="application.logged",
        entity={"kind": "job", "id": job},
        actor={"id": "test"},
        input={"job_id": job, "date_applied": when,
               "applied_via": "company site"},
        validations=[], rationale="test",
        effects={"job_id": job},
        before={job: {"status": "new"}},
        after={job: {"status": "applied"}}, **kw)


def _status(log, job, status, **kw):
    return log.record(
        type="job.status_set",
        entity={"kind": "job", "id": job},
        actor={"id": "test"},
        input={"job_id": job, "status": status},
        validations=[], rationale="test",
        effects={"job_id": job, "to": status},
        before={}, after={job: {"status": status}}, **kw)


def _rows(**overrides):
    row = {"id": JOB, "company": "Snowflake",
           "title": "Associate Solution Engineer",
           "status": "new", "date_applied": "", "applied_via": ""}
    row.update(overrides)
    return {JOB: row}


# --- the projection -------------------------------------------------------

def test_an_application_projects_status_date_and_method():
    with tempfile.TemporaryDirectory() as tmp:
        log = _log(tmp)
        _applied(log)
        got = reconcile_audit.replay_statuses(log.entries())
        assert got[JOB]["status"] == "applied", got
        assert got[JOB]["date_applied"] == "2026-07-20", got
        assert got[JOB]["applied_via"] == "company site", got


def test_last_write_wins_on_a_row_cycled_through_states():
    """The Palantir row was walked applied->interview->...->new during a test.

    Its final state is `new`, and the store holding `new` is correct. Comparing
    every entry against the final store instead of folding them would report
    twelve phantom losses and bury the one real one.
    """
    with tempfile.TemporaryDirectory() as tmp:
        log = _log(tmp)
        for s in ("applied", "interview", "screening", "offer", "ignored", "new"):
            _status(log, OTHER, s)
        got = reconcile_audit.replay_statuses(log.entries())
        assert got[OTHER]["status"] == "new", got


def test_a_failed_entry_is_ignored():
    """`*.failed` says in as many words that nothing was written."""
    with tempfile.TemporaryDirectory() as tmp:
        log = _log(tmp)
        entry = _applied(log)
        log.record(type="application.logged.failed",
                   entity=entry["entity"], actor={"id": "test"},
                   input={"target_log_entry_id": entry["id"],
                          "reason": "store_locked"},
                   validations=[], rationale="locked",
                   effects={}, after={JOB: {"status": "applied"}})
        drift = reconcile_audit.find_drift(
            reconcile_audit.replay_statuses(log.entries()), _rows())
        # The original still stands; only the compensating entry is skipped.
        assert [d["field"] for d in drift].count("status") == 1, drift


def test_an_undone_entry_is_reversed():
    with tempfile.TemporaryDirectory() as tmp:
        log = _log(tmp)
        entry = _applied(log)
        log.record(type="application.logged.undone",
                   entity=entry["entity"], actor={"id": "test"},
                   input={"target_log_entry_id": entry["id"]},
                   validations=[], rationale="undo",
                   effects={}, after={})
        got = reconcile_audit.replay_statuses(log.entries())
        assert JOB not in got or "status" not in got.get(JOB, {}), got


def test_the_log_only_speaks_about_fields_it_records():
    """A row carries 20 columns. The log has no opinion on 17 of them."""
    with tempfile.TemporaryDirectory() as tmp:
        log = _log(tmp)
        log.record(type="job.status_set",
                   entity={"kind": "job", "id": JOB}, actor={"id": "t"},
                   input={}, validations=[], rationale="t", effects={},
                   after={JOB: {"status": "applied", "notes": "hello",
                                "sponsor_rating": "A"}})
        got = reconcile_audit.replay_statuses(log.entries())
        assert set(got[JOB]) & set(reconcile_audit.TRACKED_FIELDS) == {"status"}
        assert "sponsor_rating" not in got[JOB], got


# --- drift ----------------------------------------------------------------

def test_the_snowflake_loss_is_detected():
    with tempfile.TemporaryDirectory() as tmp:
        log = _log(tmp)
        _applied(log)
        drift = reconcile_audit.find_drift(
            reconcile_audit.replay_statuses(log.entries()), _rows())
        fields = {d["field"] for d in drift}
        assert fields == {"status", "date_applied", "applied_via"}, drift
        assert drift[0]["company"] == "Snowflake", drift


def test_a_store_that_agrees_reports_nothing():
    with tempfile.TemporaryDirectory() as tmp:
        log = _log(tmp)
        _applied(log)
        rows = _rows(status="applied", date_applied="2026-07-20",
                     applied_via="company site")
        assert reconcile_audit.find_drift(
            reconcile_audit.replay_statuses(log.entries()), rows) == []


def test_a_row_that_vanished_is_worse_than_a_wrong_value():
    with tempfile.TemporaryDirectory() as tmp:
        log = _log(tmp)
        _applied(log)
        drift = reconcile_audit.find_drift(
            reconcile_audit.replay_statuses(log.entries()), {})
        assert len(drift) == 1 and drift[0]["field"] == "*", drift
        assert drift[0]["actual"] == "row missing", drift


# --- the three exit codes -------------------------------------------------

def test_a_missing_log_cannot_verify_rather_than_passing():
    with tempfile.TemporaryDirectory() as tmp:
        code, report = reconcile_audit.reconcile(
            Path(tmp) / "store.xlsx", Path(tmp) / "nope.jsonl")
        assert code == reconcile_audit.EXIT_CANNOT_VERIFY, (code, report)
        assert "error" in report


def test_a_missing_store_cannot_verify():
    with tempfile.TemporaryDirectory() as tmp:
        log = _log(tmp)
        _applied(log)
        code, _ = reconcile_audit.reconcile(Path(tmp) / "nope.xlsx", log.path)
        assert code == reconcile_audit.EXIT_CANNOT_VERIFY, code


def test_a_broken_chain_cannot_verify_rather_than_reporting_drift():
    """A log that fails its own integrity check proves nothing either way."""
    with tempfile.TemporaryDirectory() as tmp:
        log = _log(tmp)
        _applied(log)
        rows = [json.loads(l) for l in log.path.read_text(
            encoding="utf-8").splitlines() if l.strip()]
        rows[0]["input"]["date_applied"] = "1999-01-01"   # hash no longer matches
        log.path.write_text("\n".join(json.dumps(r, sort_keys=True)
                                      for r in rows) + "\n", encoding="utf-8")
        store = Path(tmp) / "store.xlsx"
        store.write_bytes(b"not really a workbook")
        code, report = reconcile_audit.reconcile(store, log.path)
        assert code == reconcile_audit.EXIT_CANNOT_VERIFY, (code, report)
        assert "chain" in report.get("error", ""), report


def test_cannot_verify_is_distinct_from_drift():
    """Three codes, not two — the whole reason exit 2 exists."""
    assert len({reconcile_audit.EXIT_CLEAN,
                reconcile_audit.EXIT_DRIFT,
                reconcile_audit.EXIT_CANNOT_VERIFY}) == 3


# --- repair ---------------------------------------------------------------

def _workbook(tmp, **overrides):
    """A real two-sheet workbook with one Snowflake row, status new."""
    import tracker
    row = {name: "" for name in tracker.FIELDNAMES}
    row.update({"id": JOB, "title": "Associate Solution Engineer",
                "company": "Snowflake", "location": "London",
                "tier": "1", "source": "ashby", "status": "new",
                "sponsor_match": "yes", "sponsor_rating": "A",
                "posted_date": "2026-08-14", "date_first_seen": "2026-08-14"})
    row.update(overrides)
    path = Path(tmp) / "store.xlsx"
    tracker.save_tracker(str(path), {JOB: row})
    return path


def test_repair_restores_what_the_log_asserts():
    with tempfile.TemporaryDirectory() as tmp:
        log = _log(tmp)
        _applied(log)
        store = _workbook(tmp)

        code, report = reconcile_audit.reconcile(store, log.path)
        assert code == reconcile_audit.EXIT_DRIFT, report

        outcome = reconcile_audit.repair(store, log.path, report["drift"])
        assert outcome["repaired"] == 3, outcome

        code_after, after = reconcile_audit.reconcile(store, log.path)
        assert code_after == reconcile_audit.EXIT_CLEAN, after


def test_repair_records_itself_in_the_chain():
    """A record that can be silently corrected is not a record."""
    with tempfile.TemporaryDirectory() as tmp:
        log = _log(tmp)
        _applied(log)
        store = _workbook(tmp)
        _, report = reconcile_audit.reconcile(store, log.path)
        reconcile_audit.repair(store, log.path, report["drift"])

        entries = DecisionLog(log.path).entries()
        assert entries[-1]["type"] == reconcile_audit.REPAIR, entries[-1]
        assert DecisionLog(log.path).verify()["chain_ok"], "chain broke"


def test_repair_never_logs_a_second_application():
    """The job was applied to once. Logging it again is a worse lie."""
    with tempfile.TemporaryDirectory() as tmp:
        log = _log(tmp)
        _applied(log)
        store = _workbook(tmp)
        _, report = reconcile_audit.reconcile(store, log.path)
        reconcile_audit.repair(store, log.path, report["drift"])

        applications = [e for e in DecisionLog(log.path).entries()
                        if e["type"] == "application.logged"]
        assert len(applications) == 1, applications


def test_repair_skips_a_vanished_row_rather_than_inventing_one():
    with tempfile.TemporaryDirectory() as tmp:
        log = _log(tmp)
        _applied(log)
        store = _workbook(tmp, id="something:else")   # the logged row is absent
        _, report = reconcile_audit.reconcile(store, log.path)
        outcome = reconcile_audit.repair(store, log.path, report["drift"])
        assert outcome["repaired"] == 0 and outcome["skipped"] == 1, outcome


# --- the defect this run also fixed ---------------------------------------

def test_the_log_no_longer_claims_a_row_count_it_cannot_know():
    """`rows_changed` was hardcoded to 1 and hashed in before the write.

    The entry is written before the store on purpose, so the count is a
    prediction at record time. Logging it as a measurement made seq 7 assert a
    write the tracker has no record of.
    """
    from web.api.actions import Actions
    kept = Actions._for_log({"company": "Snowflake", "rows_changed": 1})
    assert "rows_changed" not in kept, kept
    assert kept["company"] == "Snowflake", kept


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL  {fn.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001 - report, don't mask
            failed += 1
            print(f"ERROR {fn.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
