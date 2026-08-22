"""Tests for the ghost-posting signals.

The one that matters separates two things a duplicate count conflates, and both
examples are real rows from the store:

- Graphcore's "AI Research Engineer" -- three Greenhouse ids, one posted date,
  three cities. One role listed per location.
- Faculty's "Machine Learning Engineer" -- six Ashby ids, six posted dates,
  seven months apart. The same role advertised over and over.

Both count as "copies". They call for opposite responses, and only the posted
date tells them apart.

A third case was found by running it: Archangel Lightworks, three ids over
three days. That is one hiring push churning aggregator ids, and flagging it as
repeated advertising is a false positive -- so the postings must also *span*
real time.

Run:  python tests/test_ghost.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import ghost  # noqa: E402


def _row(**over):
    row = {"id": "1", "company": "Graphcore", "title": "AI Research Engineer",
           "posted_date": "2026-08-01", "date_first_seen": "2026-08-01",
           "location": "London, UK", "sponsor_match": "yes", "status": "new"}
    row.update(over)
    return row


def _sigs(rows, which=0):
    return ghost.signals(rows[which], ghost.index(rows))


# --- grouping --------------------------------------------------------------

def test_case_and_punctuation_do_not_split_a_group():
    a, b = _row(id="1"), _row(id="2", company="GRAPHCORE",
                              title="AI Research Engineer!")
    assert ghost.title_key(a) == ghost.title_key(b)


def test_a_different_title_is_a_different_role():
    assert ghost.title_key(_row()) != ghost.title_key(
        _row(title="Infrastructure Engineer"))


# --- one role, many locations ---------------------------------------------

def test_the_same_role_in_three_cities_is_reported_as_one_role():
    """Graphcore: ids 8632581002/2002/3002, one posted date, three cities.

    Applying three times would be embarrassing.
    """
    rows = [_row(id="gh:8632581002", location="London, UK"),
            _row(id="gh:8632582002", location="Bristol, UK"),
            _row(id="gh:8632583002", location="Cambridge, UK")]
    sig = " ".join(_sigs(rows))
    assert "3 locations" in sig and "one role, not 3" in sig, sig
    assert "advertised" not in sig, "a location split is not a repost"


def test_one_location_and_one_date_is_not_worth_a_signal():
    """Two ids for one role in one city is a dedup question, not a ghost one."""
    rows = [_row(id="1"), _row(id="2")]
    assert _sigs(rows) == []


# --- repeated advertising --------------------------------------------------

def test_six_postings_over_seven_months_reads_as_evergreen():
    """Faculty's Machine Learning Engineer, 2025-12 to 2026-07."""
    dates = ["2025-12-19", "2026-02-23", "2026-06-05", "2026-06-12",
             "2026-06-22", "2026-07-10"]
    rows = [_row(id=str(i), company="Faculty",
                 title="Machine Learning Engineer", posted_date=d,
                 date_first_seen=d) for i, d in enumerate(dates)]
    sig = " ".join(_sigs(rows))
    assert "advertised 6 separate times" in sig, sig
    assert "203 days" in sig, sig


def test_three_ids_over_three_days_is_churn_not_repeated_advertising():
    """Archangel Lightworks. One hiring push, three aggregator ids.

    The date count alone flagged it, which is the false positive the span
    threshold exists to remove.
    """
    rows = [_row(id=str(i), company="Archangel Lightworks Ltd",
                 title="Embedded Software Engineer",
                 posted_date=d, date_first_seen=d)
            for i, d in enumerate(["2026-08-01", "2026-08-02", "2026-08-03"])]
    assert not any("advertised" in s for s in _sigs(rows)), _sigs(rows)


def test_two_dates_far_apart_are_a_relist_not_a_pattern():
    rows = [_row(id="1", posted_date="2026-01-01", date_first_seen="2026-01-01"),
            _row(id="2", posted_date="2026-08-01", date_first_seen="2026-08-01")]
    assert not any("advertised" in s for s in _sigs(rows))


# --- age -------------------------------------------------------------------

def test_a_long_open_posting_is_remarked_on():
    rows = [_row(posted_date="2020-01-01", date_first_seen="2020-01-01")]
    assert any("open " in s and "days" in s for s in _sigs(rows)), _sigs(rows)


def test_a_fresh_posting_carries_no_signal_at_all():
    """Empty is the common answer. A generator that always finds something
    teaches people to ignore it."""
    import datetime as dt
    today = dt.date.today().isoformat()
    rows = [_row(posted_date=today, date_first_seen=today)]
    assert _sigs(rows) == []


def test_an_undateable_row_is_not_called_old():
    """Absence of a date is not evidence of age."""
    rows = [_row(posted_date="", date_first_seen="")]
    assert not any("open " in s for s in _sigs(rows))


def test_age_can_be_suppressed_for_a_caller_that_already_reports_it():
    """shortlist.py's score reasons already carry "386d old". Printing "open
    386 days" underneath restates it, which is exactly the noise this module
    warns about.
    """
    rows = [_row(posted_date="2020-01-01", date_first_seen="2020-01-01")]
    idx = ghost.index(rows)
    assert any("open " in s for s in ghost.signals(rows[0], idx))
    assert ghost.signals(rows[0], idx, include_age=False) == []


def test_suppressing_age_keeps_the_signals_the_caller_cannot_get_elsewhere():
    """The repost and multi-location findings are the whole reason to call it
    from the shortlist; only age is duplicated."""
    rows = [_row(id="1", location="London, UK"),
            _row(id="2", location="Bristol, UK"),
            _row(id="3", location="Cambridge, UK")]
    sigs = ghost.signals(rows[0], ghost.index(rows), include_age=False)
    assert any("3 locations" in s for s in sigs), sigs


def test_the_shortlist_asks_for_signals_without_age():
    """Pins the pair, so re-enabling age in one place cannot silently restore
    the duplication in the other."""
    import inspect

    from pipeline import shortlist as sl
    src = inspect.getsource(sl.main)
    assert "ghost.signals(row, ghost_index, include_age=False)" in src


# --- the boundary the whole module rests on --------------------------------

def test_nothing_here_changes_a_score_or_drops_a_row():
    """score()'s weights are frozen -- there is no outcome data to tune them
    against, and a signal computed from data the ranker already sees would be
    tuning by the back door.

    Checked against the compiled functions rather than the source text: a
    first version grepped for "shortlist.score" and matched the docstring
    sentence explaining why it is never called.
    """
    for fn in (ghost.signals, ghost.index, ghost.title_key):
        names = set(fn.__code__.co_names)
        assert "score" not in names, f"{fn.__name__} reaches score()"
        assert "save_tracker" not in names, f"{fn.__name__} writes to the store"


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
