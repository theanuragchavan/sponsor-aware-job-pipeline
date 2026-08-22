"""Regression tests for the Reed client.

Fixtures are literal payloads copied from a live response on 2026-08-21, so no
test here touches the network.

Run:  python tests/test_reed_client.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import reed_client as reed  # noqa: E402
import tracker  # noqa: E402

REED_JOB = {
    "jobId": 57205630,
    "employerName": "Vermillion Analytics",
    "jobTitle": "Software Engineer",
    "locationName": "London",
    "minimumSalary": 35000.0,          # a float, not an int
    "maximumSalary": 45000.0,
    "currency": "GBP",
    "expirationDate": "17/09/2026",    # DD/MM/YYYY, not ISO
    "date": "06/08/2026",
    "jobDescription": "Solve productivity problems \u00e2\u20ac\u201d fast.",
    "jobUrl": "https://www.reed.co.uk/jobs/software-engineer/57205630",
}


def test_row_has_exactly_the_tracker_fieldnames():
    """A missing key crashes the store write after rows are already merged."""
    row = reed.to_row(REED_JOB)
    assert set(row) == set(tracker.FIELDNAMES), set(row) ^ set(tracker.FIELDNAMES)


def test_ddmmyyyy_is_not_read_as_iso():
    """06/08/2026 is 6 August. A naive [:10] slice would keep it as a string
    that every downstream date comparison misreads."""
    row = reed.to_row(REED_JOB)
    assert row["posted_date"] == "2026-08-06"
    assert row["apply_deadline"] == "2026-09-17"


def test_an_unparseable_date_is_blank_not_an_exception():
    row = reed.to_row(dict(REED_JOB, date="", expirationDate="not a date"))
    assert row["posted_date"] == ""
    assert row["apply_deadline"] == ""


def test_double_encoded_text_is_repaired():
    """Reed serves UTF-8 as cp1252, so an em dash arrives as three chars."""
    row = reed.to_row(REED_JOB)
    assert "\u2014" in row["description"], repr(row["description"])
    assert "\u00e2\u20ac\u201d" not in row["description"]


def test_clean_text_is_left_alone():
    """The repair must not mangle text that was never double-encoded."""
    assert reed._fix_mojibake("Plain ASCII") == "Plain ASCII"
    assert reed._fix_mojibake("Caf\u00e9 \u2014 already fine") == \
        "Caf\u00e9 \u2014 already fine"


def test_salary_floats_become_plain_ints():
    """The store writes every cell as text; "35000.0" sorts and reads badly."""
    row = reed.to_row(REED_JOB)
    assert row["salary_min"] == "35000"
    assert row["salary_max"] == "45000"


def test_missing_salary_is_blank_not_zero():
    """A blank salary must not become 0, which would fail a salary floor."""
    row = reed.to_row(dict(REED_JOB, minimumSalary=None, maximumSalary=""))
    assert row["salary_min"] == ""
    assert row["salary_max"] == ""


def test_id_is_namespaced_so_source_of_can_route_it():
    """A bare id means Adzuna by construction (tracker.source_of)."""
    row = reed.to_row(REED_JOB)
    assert row["id"] == "reed:57205630"
    assert tracker.source_of(row["id"]) == "reed"


def test_contract_type_maps_onto_reeds_own_filter():
    """The server-side filter is the reason this client exists; a typo that
    silently sends no filter would quietly widen every search."""
    import inspect
    src = inspect.getsource(reed.search_jobs)
    for flag in ("permanent", "contract", "temp", "partTime", "fullTime"):
        assert flag in src, flag


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS ", name)
            except AssertionError as exc:
                fails += 1
                print("FAIL ", name, "->", exc)
            except Exception as exc:  # noqa: BLE001
                fails += 1
                print("ERROR", name, "->", type(exc).__name__, exc)
    total = sum(1 for n in globals() if n.startswith("test_"))
    print(f"\n{total - fails}/{total} passed")
    sys.exit(1 if fails else 0)
