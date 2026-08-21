"""Tests for the outcome projection.

Two properties carry the weight, and both were found by running it on real data
rather than reasoned about in advance.

**A rehearsal is not an outcome.** The first run reported a 100% offer rate,
because the log holds twelve stage events for a Palantir row that was walked
applied -> interview -> screening -> offer -> ignored -> new while testing the
UI. A funnel that counts rehearsals is worse than no funnel: it produces a
number, and the number flatters.

**Declining to apply is not applying.** The second run counted two `ignored`
rows as applications, which inflates the denominator with jobs nobody ever
contacted and makes the reply rate look worse than reality.

Neither is a bug in the log. Both are questions the projection has to answer
about what the log means.

Run:  python tests/test_outcomes.py
"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import outcomes  # noqa: E402


def _entry(seq, type_, job_id, ts, **over):
    e = {"seq": seq, "id": f"id{seq}", "type": type_, "ts": ts,
         "entity": {"kind": "job", "id": job_id},
         "actor": {"id": "anurag"}, "input": {}, "rationale": ""}
    e.update(over)
    return e


def _applied(seq, job_id, ts="2026-08-01T10:00:00Z"):
    return _entry(seq, "application.logged", job_id, ts,
                  input={"job_id": job_id})


def _status(seq, job_id, status, ts):
    return _entry(seq, "job.status_set", job_id, ts,
                  input={"job_id": job_id, "status": status})


# --- the projection --------------------------------------------------------

def test_an_application_becomes_an_applied_stage():
    tl = outcomes.timeline([_applied(1, "j1")])
    assert tl["j1"][0]["stage"] == "applied", tl


def test_events_come_back_in_chronological_order():
    tl = outcomes.timeline([
        _status(3, "j1", "interview", "2026-08-10T10:00:00Z"),
        _applied(1, "j1", "2026-08-01T10:00:00Z"),
        _status(2, "j1", "screening", "2026-08-05T10:00:00Z"),
    ])
    assert [e["stage"] for e in tl["j1"]] == ["applied", "screening",
                                              "interview"], tl["j1"]


def test_a_failed_entry_asserts_nothing():
    tl = outcomes.timeline([
        _applied(1, "j1"),
        _entry(2, "application.logged.failed", "j1", "2026-08-02T10:00:00Z",
               input={"job_id": "j1", "reason": "store_locked"}),
    ])
    assert len(tl["j1"]) == 1, tl["j1"]


def test_an_undone_entry_is_reversed():
    tl = outcomes.timeline([
        _applied(1, "j1"),
        _entry(2, "job.status_set.undone", "j1", "2026-08-02T10:00:00Z",
               input={"target_log_entry_id": "id1"}),
    ])
    assert "j1" not in tl or not tl["j1"], tl


def test_a_stopped_cowork_run_is_not_a_stage():
    """A blocked submission is not a stage of an application that never was."""
    tl = outcomes.timeline([
        _entry(1, "cowork.stopped", "j1", "2026-08-01T10:00:00Z",
               input={"job_id": "j1", "blocked_by": ["ats_recorded"]})])
    assert not tl.get("j1"), tl


# --- furthest reached ------------------------------------------------------

def test_furthest_is_not_the_last_event():
    """A rejection after an interview means the interview still happened.

    Taking the last event would report the interview rate as zero for every
    application that ended in a no, which is most of them.
    """
    events = [{"stage": "applied"}, {"stage": "interview"},
              {"stage": "rejected"}]
    assert outcomes.furthest(events) == "rejected"
    assert "interview" in {e["stage"] for e in events}

    events = [{"stage": "applied"}, {"stage": "offer"}, {"stage": "screening"}]
    assert outcomes.furthest(events) == "offer", "out-of-order events"


def test_furthest_of_nothing_is_empty():
    assert outcomes.furthest([]) == ""


# --- days quiet ------------------------------------------------------------

def test_a_resolved_application_is_not_quiet():
    """Finished is not overdue. Reporting it as chaseable is how a follow-up
    list becomes noise nobody reads."""
    events = [{"stage": "applied", "at": "2026-01-01"},
              {"stage": "rejected", "at": "2026-01-05"}]
    assert outcomes.days_quiet(events, dt.date(2026, 8, 21)) is None


def test_a_live_application_reports_days_since_the_last_event():
    events = [{"stage": "applied", "at": "2026-08-01"}]
    assert outcomes.days_quiet(events, dt.date(2026, 8, 21)) == 20


def test_an_undateable_event_is_unknown_not_zero():
    events = [{"stage": "applied", "at": ""}]
    assert outcomes.days_quiet(events, dt.date(2026, 8, 21)) is None


# --- what counts as an application ----------------------------------------

def test_a_ui_test_walked_through_stages_is_not_an_application():
    """The Palantir row: applied -> interview -> offer -> ignored -> new.

    Read literally that is a 100% offer rate.
    """
    tl = {"j1": [{"stage": "applied"}, {"stage": "interview"},
                 {"stage": "offer"}]}
    rows = {"j1": {"status": "new"}}
    assert outcomes.real_applications(tl, rows) == {}


def test_an_ignored_job_is_not_an_application():
    """Deciding not to apply is not applying.

    Counting it inflates the denominator with jobs nobody ever contacted.
    """
    tl = {"j1": [{"stage": "applied"}]}
    for status in ("ignored", "closed", "new", ""):
        assert outcomes.real_applications(tl, {"j1": {"status": status}}) == {}, status


def test_a_rejection_is_still_an_application():
    """It is an outcome of applying, not a decision not to."""
    tl = {"j1": [{"stage": "applied"}, {"stage": "rejected"}]}
    assert outcomes.real_applications(tl, {"j1": {"status": "rejected"}}), \
        "a rejected application must stay in the funnel"


def test_the_timeline_keeps_history_the_funnel_drops_it():
    """History is history. Only the counting is filtered."""
    entries = [_applied(1, "j1"), _status(2, "j1", "interview",
                                          "2026-08-05T10:00:00Z")]
    tl = outcomes.timeline(entries)
    assert len(tl["j1"]) == 2, "history must survive"
    assert outcomes.real_applications(tl, {"j1": {"status": "new"}}) == {}


# --- the threshold ---------------------------------------------------------

def test_the_silence_threshold_says_when_it_is_guessing():
    """A measured threshold and an assumed one must not look the same.

    /api/follow-ups has hardcoded 10 days since it was written. That is a
    guess, and it should be visibly a guess until it is not.
    """
    days, basis = outcomes.silence_threshold({})
    assert days == 10 and "assumed" in basis, (days, basis)


def test_the_threshold_is_measured_once_there_is_enough_data():
    tl = {}
    for i in range(12):
        tl[f"j{i}"] = [{"stage": "applied", "at": "2026-08-01"},
                       {"stage": "screening", "at": "2026-08-08"}]
    days, basis = outcomes.silence_threshold(tl)
    assert days == 7 and "measured" in basis, (days, basis)


def test_one_sample_is_not_enough_to_replace_a_guess():
    tl = {"j1": [{"stage": "applied", "at": "2026-08-01"},
                 {"stage": "screening", "at": "2026-08-02"}]}
    days, basis = outcomes.silence_threshold(tl)
    assert "assumed" in basis, basis


# --- the funnel ------------------------------------------------------------

def test_the_funnel_counts_jobs_not_events():
    tl = {"j1": [{"stage": "applied"}, {"stage": "applied"}]}
    assert outcomes.funnel(tl)["reached"]["applied"] == 1


def test_channel_rates_are_fractions_not_percentages():
    """A percentage over three applications lies about its own precision."""
    tl = {"j1": [{"stage": "applied"}]}
    rows = {"j1": {"applied_via": "company site", "source": "ashby"}}
    assert outcomes.by_channel(tl, rows)[0]["rate"] == "0/1"


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
