"""Tests for the pipeline debugger.

The thing worth protecting is that `explain.py` never restates a rule. Every
stage calls the function the pipeline calls, so the two cannot drift into
disagreeing -- and a debugger that disagrees with the thing it debugs is worse
than none, because it gets believed.

One test exists because of a real divergence found on the first run. Robert
Walters passes the sponsor register (the agency itself holds a licence), and
`disqualify()` returns None for it, so the scanner stage reported "kept" -- but
the scanner does drop it, in `main()` at :374 rather than in `disqualify()`.
Modelling a stage more loosely than the stage models itself is the failure mode
this file guards.

Run:  python tests/test_explain.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import explain, shortlist  # noqa: E402


def _row(**over):
    row = {"id": "123456789", "company": "Palantir",
           "title": "Forward Deployed Software Engineer",
           "sponsor_match": "yes", "status": "new", "source": "lever",
           "url": "https://jobs.lever.co/palantir/abc", "tier": "1",
           "posted_date": "2026-08-01", "date_first_seen": "2026-08-01",
           "location": "London"}
    row.update(over)
    return row


def _named(checks):
    return {name: (ok, detail) for name, ok, detail in checks}


# --- finding the row -------------------------------------------------------

def test_an_exact_id_wins_over_a_word_match():
    rows = [_row(id="999", company="Acme"), _row(id="123456789")]
    assert explain.find_rows("123456789", rows)[0]["id"] == "123456789"


def test_a_url_is_matched_by_its_longest_digit_run():
    """He arrives at this question from a browser more often than from the
    tracker."""
    rows = [_row(id="5340891736")]
    found = explain.find_rows(
        "https://www.adzuna.co.uk/jobs/details/5340891736", rows)
    assert found and found[0]["id"] == "5340891736"


def test_a_url_with_no_id_falls_back_to_the_stored_url():
    """Ashby URLs carry a uuid, not a number."""
    rows = [_row(id="ashby:x", url="https://jobs.ashbyhq.com/monzo/uuid-here")]
    assert explain.find_rows("https://jobs.ashbyhq.com/monzo/uuid-here", rows)


def test_words_match_across_company_and_title():
    rows = [_row(), _row(id="2", company="Monzo", title="Backend Engineer")]
    found = explain.find_rows("palantir forward", rows)
    assert len(found) == 1 and found[0]["company"] == "Palantir"


def test_every_word_must_match_not_any():
    """"palantir plumber" must find nothing rather than every Palantir role."""
    assert explain.find_rows("palantir plumber", [_row()]) == []


def test_an_empty_query_matches_nothing():
    assert explain.find_rows("", [_row()]) == []
    assert explain.find_rows("   ", [_row()]) == []


# --- the stages ------------------------------------------------------------

def test_a_clean_row_passes_every_stage():
    checks = explain.stages(_row(), {"companies": {}, "roles": {}}, None)
    failed = [name for name, ok, _d in checks if not ok]
    assert not failed, failed


def test_passing_stages_are_reported_not_just_failures():
    """"Which filter is wrong" and "which filter saved me" are the same
    question asked twice, and only one of them is answerable from failures."""
    checks = explain.stages(_row(sponsor_match="no"),
                            {"companies": {}, "roles": {}}, None)
    assert any(ok for _n, ok, _d in checks), "no passes were reported"
    assert any(not ok for _n, ok, _d in checks)


def test_a_missing_sponsor_match_is_reported_by_the_sponsor_stage():
    checks = _named(explain.stages(_row(sponsor_match="no"),
                                   {"companies": {}, "roles": {}}, None))
    assert checks["sponsor register"][0] is False


def test_the_scanner_stage_reflects_the_agency_drop_in_main():
    """Robert Walters: a licensed sponsor that is also an agency.

    `disqualify()` returns None for it. The scanner still drops it, at
    shortlist.py:374. The debugger must report what the scanner does, not what
    one of its functions does.
    """
    row = _row(company="Robert Walters", sponsor_match="yes")
    assert shortlist.disqualify(row) is None, \
        "premise: disqualify() alone does not drop an agency row"
    checks = _named(explain.stages(row, {"companies": {}, "roles": {}}, None))
    assert checks["shortlist scanner"][0] is False, checks["shortlist scanner"]


def test_an_actioned_row_is_reported_as_actioned_not_as_unsuitable():
    """Already applied is not the same fact as filtered out, and conflating
    them would send him chasing a rule that is working."""
    checks = _named(explain.stages(_row(status="applied"),
                                   {"companies": {}, "roles": {}}, None))
    assert checks["not yet actioned"][0] is False
    assert "applied" in checks["not yet actioned"][1]


def test_a_recorded_cut_ruling_outranks_everything_computed():
    decisions = {"companies": {"Palantir": {"verdict": "CUT",
                                            "note": "no reply in 3 months"}},
                 "roles": {}}
    checks = _named(explain.stages(_row(), decisions, None))
    assert checks["your decisions.json"][0] is False
    assert "no reply in 3 months" in checks["your decisions.json"][1]


def test_a_keep_ruling_does_not_fail_the_stage():
    decisions = {"companies": {"Palantir": {"verdict": "KEEP"}}, "roles": {}}
    checks = _named(explain.stages(_row(), decisions, None))
    assert checks["your decisions.json"][0] is True


def test_the_age_gate_only_applies_when_a_max_age_is_given():
    """The scanner's age gate is opt-in, and the debugger must not invent it."""
    old = _row(source="adzuna", posted_date="2020-01-01",
               date_first_seen="2020-01-01")
    assert _named(explain.stages(old, {"companies": {}, "roles": {}},
                                 None))["shortlist scanner"][0] is True
    assert _named(explain.stages(old, {"companies": {}, "roles": {}},
                                 30))["shortlist scanner"][0] is False


def test_stages_delegate_rather_than_restating_rules():
    """The property the whole file rests on."""
    import inspect
    src = inspect.getsource(explain.stages)
    for fn in ("tracker.unsuitable_reason", "tracker.is_agency",
               "tracker.apply_ready_reason", "shortlist.disqualify",
               "shortlist.ruling_for"):
        assert fn in src, f"{fn} must be called, not reimplemented"


def test_exit_codes_distinguish_not_found_from_filtered():
    """Never ingested and ingested-then-dropped call for different fixes:
    widen the search, or change a rule."""
    assert explain.EXIT_SURFACES == 0
    assert explain.EXIT_FILTERED == 1
    assert explain.EXIT_NOT_FOUND == 2


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
