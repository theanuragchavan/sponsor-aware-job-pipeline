"""Tests for recording applications made outside the pipeline.

Two things here can corrupt the record silently, and both did.

**Matching the wrong row.** Company plus title is not unique -- Sparta Global
posts one Junior AI Engineer requisition per city -- so matching on the pair
picks whichever came first and marks the wrong one applied. `job_id` has to win
when it is given.

**Deciding nothing changed when something did.** Both Snowflake rows sat at
`status: applied` with `applied_via` blank. A comparison that only looks at
status calls that "already recorded" and the channel stays missing forever,
which quietly removes those rows from the only analysis they exist for.

Run:  python tests/test_log_manual.py
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipeline import log_manual  # noqa: E402


# --- which row does an entry mean? ----------------------------------------

def test_job_id_beats_company_and_title():
    """Two live requisitions, same role, different cities."""
    rows = {
        "5828153179": {"id": "5828153179", "company": "Sparta Global Limited",
                       "title": "Junior AI Engineer", "location": "Sheffield"},
        "5828153182": {"id": "5828153182", "company": "Sparta Global Limited",
                       "title": "Junior AI Engineer", "location": "London"},
    }
    entry = {"company": "Sparta Global Limited", "title": "Junior AI Engineer",
             "job_id": "5828153182"}
    assert log_manual.find_existing(entry, rows)["location"] == "London"


def test_without_a_job_id_it_falls_back_to_company_and_title():
    rows = {"x": {"id": "x", "company": "Verkada",
                  "title": "Technical Support Engineer"}}
    entry = {"company": "verkada", "title": "technical support engineer"}
    assert log_manual.find_existing(entry, rows) is not None


def test_an_unknown_company_matches_nothing():
    rows = {"x": {"id": "x", "company": "Verkada", "title": "Engineer"}}
    assert log_manual.find_existing({"company": "Deliveroo",
                                     "title": "Engineer"}, rows) is None


# --- has anything actually changed? ---------------------------------------

def test_a_blank_channel_on_an_applied_row_still_counts_as_a_change():
    """The Snowflake case. Status agrees; the record is still incomplete."""
    row = {"status": "applied", "applied_via": "", "date_applied": "2026-08-21"}
    entry = {"status": "applied", "applied_via": "company site",
             "date_applied": "2026-08-21"}
    assert log_manual.differs(entry, row)


def test_a_status_transition_counts():
    row = {"status": "applied", "applied_via": "company site",
           "date_applied": "2026-08-21"}
    entry = {"status": "rejected", "applied_via": "company site",
             "date_applied": "2026-08-21"}
    assert log_manual.differs(entry, row)


def test_a_row_that_already_agrees_is_left_alone():
    row = {"status": "applied", "applied_via": "student circus",
           "date_applied": "2026-08-21"}
    entry = {"status": "applied", "applied_via": "student circus",
             "date_applied": "2026-08-21"}
    assert not log_manual.differs(entry, row)


def test_an_entry_that_omits_a_field_never_blanks_it():
    """Otherwise a terse entry silently erases a channel already recorded."""
    row = {"status": "applied", "applied_via": "student circus",
           "date_applied": "2026-08-21"}
    entry = {"status": "applied"}
    assert not log_manual.differs(entry, row)


# --- what a manual row may and may not claim ------------------------------

def test_status_defaults_to_applied():
    row = {"status": "new", "applied_via": "", "date_applied": ""}
    assert log_manual.differs({"applied_via": "adzuna"}, row)


def test_it_never_invents_a_sponsor_verdict():
    """`build_row` must ask the register, not accept a claim from the file.

    A hand-written entry asserting sponsorship would poison the one field every
    other decision is built on. Asserted against the names the function calls,
    rather than its text, so a comment mentioning match_company cannot pass it.
    """
    names = log_manual.build_row.__code__.co_names
    assert "match_company" in names, \
        "build_row no longer consults the sponsor register"


def test_an_entry_missing_a_required_field_is_skipped_not_guessed():
    steps = log_manual.plan([{"company": "X", "title": "Y"}], {}, {}, {})
    assert steps[0]["action"] == "skip"
    assert "date_applied" in steps[0]["why"]


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
