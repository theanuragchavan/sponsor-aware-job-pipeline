"""Tests for the return leg: reading a Cowork result back into the record.

The property under test throughout is that an agent's own report is treated as
a claim, not as evidence. `loop-verifier`'s contract says it in one line --
"'The transcript says tests pass' is not evidence; a pytest run you executed
is" -- and the loop-engineering NOTES record a worker that posted a fully
fabricated report including an invented exit code, with zero commands run.

An agent that will invent a test result will invent a submission confirmation.
So: a submitted outcome with no confirmation text is recorded as unverified
rather than accepted quietly, and a result describing a package that changed
after it was built is flagged, because it is describing something else.

Run:  python tests/test_record_result.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from package import record_result as rr  # noqa: E402

JOB = "lever:abc123"


def _result(tmp, **over):
    body = {"job_id": JOB, "outcome": "submitted",
            "at": "2026-08-21T22:45:00Z",
            "confirmation": "Application received. Ref ABC-123"}
    body.update(over)
    p = Path(tmp) / "result.json"
    p.write_text(json.dumps(body), encoding="utf-8")
    return p


def _package(tmp, *, tamper=False):
    """A package with a manifest, optionally edited after the fact."""
    pkgs = Path(tmp) / "packages"
    d = pkgs / JOB.replace(":", "_")
    d.mkdir(parents=True)
    (d / "job.json").write_text('{"id": "lever:abc123"}', encoding="utf-8")
    manifest = {"job_id": JOB, "files": {
        "job.json": {"sha256": rr.sha256_of(d / "job.json"), "bytes": 24}}}
    (d / "MANIFEST.json").write_text(json.dumps(manifest), encoding="utf-8")
    if tamper:
        (d / "job.json").write_text('{"id": "something-else"}', encoding="utf-8")
    return pkgs


# --- validation ------------------------------------------------------------

def test_a_valid_submission_passes_validation():
    assert rr.validate({"job_id": JOB, "outcome": "submitted",
                        "at": "2026-08-21T22:45:00Z",
                        "confirmation": "Received"}) == []


def test_an_unknown_outcome_is_rejected():
    """Three outcomes, not two. 'The gate stopped it' and 'it crashed' are
    different facts about the world."""
    problems = rr.validate({"job_id": JOB, "outcome": "maybe",
                            "at": "2026-08-21T22:45:00Z"})
    assert any("not one of" in p for p in problems), problems


def test_a_stop_must_name_the_condition_that_stopped_it():
    """A stop with no reason cannot be counted or acted on."""
    problems = rr.validate({"job_id": JOB, "outcome": "stopped",
                            "at": "2026-08-21T22:45:00Z"})
    assert any("without naming a condition" in p for p in problems), problems


def test_a_submission_without_confirmation_is_flagged_unverified():
    """The load-bearing one.

    An agent reporting success is exactly the claim this system exists not to
    take at face value. Not fatal -- the application probably did happen and
    losing the record is worse than holding an imperfect one -- but never
    silently a clean submit.
    """
    problems = rr.validate({"job_id": JOB, "outcome": "submitted",
                            "at": "2026-08-21T22:45:00Z"})
    assert any("unverified" in p for p in problems), problems


# --- package integrity -----------------------------------------------------

def test_an_intact_package_verifies():
    with tempfile.TemporaryDirectory() as tmp:
        pkgs = _package(tmp)
        ok, why = rr.package_intact(pkgs / JOB.replace(":", "_"))
        assert ok, why


def test_a_package_edited_after_the_build_is_caught():
    """A result describing an edited package is describing something else.

    Same question cv_gate asks about a PDF, one level up.
    """
    with tempfile.TemporaryDirectory() as tmp:
        pkgs = _package(tmp, tamper=True)
        ok, why = rr.package_intact(pkgs / JOB.replace(":", "_"))
        assert not ok and "changed" in why, why


def test_a_package_with_no_manifest_cannot_be_verified():
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp) / "empty"
        d.mkdir()
        ok, why = rr.package_intact(d)
        assert not ok and "MANIFEST" in why, why


def test_a_missing_file_is_caught():
    with tempfile.TemporaryDirectory() as tmp:
        pkgs = _package(tmp)
        (pkgs / JOB.replace(":", "_") / "job.json").unlink()
        ok, why = rr.package_intact(pkgs / JOB.replace(":", "_"))
        assert not ok and "missing" in why, why


# --- reading it back -------------------------------------------------------

def test_a_missing_result_cannot_be_read():
    with tempfile.TemporaryDirectory() as tmp:
        code, report = rr.record(Path(tmp) / "nope.json")
        assert code == rr.EXIT_CANNOT_READ, (code, report)


def test_malformed_json_cannot_be_read():
    """Distinct from a rejection: unreadable is not the same as invalid."""
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "result.json"
        p.write_text("{not json", encoding="utf-8")
        code, _ = rr.record(p)
        assert code == rr.EXIT_CANNOT_READ, code


def test_a_job_not_in_the_tracker_is_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        p = _result(tmp, job_id="lever:does-not-exist")
        code, report = rr.record(p, dry_run=True)
        assert code == rr.EXIT_REJECTED, (code, report)


def test_the_three_exit_codes_are_distinct():
    assert len({rr.EXIT_OK, rr.EXIT_REJECTED, rr.EXIT_CANNOT_READ}) == 3


def test_a_stop_is_recorded_under_its_own_type():
    """A stop changes nothing about the job but is still worth keeping.

    How often the gate fires, and on which condition, is the earliest signal
    this system can produce -- available long before any employer replies.
    """
    assert rr.STOPPED != "application.logged"
    assert rr.STOPPED.startswith("cowork.")


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
