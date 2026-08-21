"""Regression tests for the closed-ad filter (age gate + liveness probe).

The bug these lock down (found 2026-08-14): the shortlist scored ad age but
never gated on it, so the same 47-day-old listings sat at #1 and #2 for six
weeks. Yoti's ad had been 410 Gone for some time; nothing noticed, because
nothing ever asked.

The subtler bug, found while fixing the first one: an age cut-off applied
BEFORE the network check binned Syntasso's Solutions Engineer ad at 47 days
even though it was still live and open. Age is a guess, a 410 is a fact, and
the guess must never get to overrule the fact. That ordering is what
`test_verify_live_defaults_to_no_age_gate` exists to protect.

Run:  python tests/test_staleness.py
Exits non-zero on failure. Also collects under pytest if it's ever installed.
"""
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "pipeline"))

import liveness  # noqa: E402
import shortlist  # noqa: E402
import tracker  # noqa: E402


def _row(jid="1", title="Solutions Engineer", company="Northwind Systems",
         age_days=5, **over):
    posted = (datetime.now() - timedelta(days=age_days)).strftime("%Y-%m-%d")
    row = {
        "id": str(jid), "title": title, "company": company,
        "location": "London", "tier": "1", "status": "new",
        "sponsor_match": "yes", "sponsor_rating": "A",
        "posted_date": posted, "date_first_seen": posted,
        "salary_min": "", "redirect_url": "",
        "source": "adzuna", "apply_deadline": "",
    }
    row.update(over)
    return row


# --- age gate ---------------------------------------------------------------

def test_old_ad_is_dropped_when_a_max_age_is_set():
    reason = shortlist.disqualify(_row(age_days=47), max_age=30)
    assert reason and "older than 30d" in reason, reason


def test_fresh_ad_survives_the_same_gate():
    assert shortlist.disqualify(_row(age_days=5), max_age=30) is None


def test_no_max_age_means_no_age_gate():
    assert shortlist.disqualify(_row(age_days=400), max_age=None) is None


def test_undateable_row_is_never_dropped_for_age():
    """We drop on evidence of staleness, never on absence of a date."""
    row = _row(age_days=99, posted_date="", date_first_seen="")
    assert shortlist.disqualify(row, max_age=30) is None


def test_rows_from_an_employers_own_board_ignore_the_age_gate():
    """Feed presence is proof of life; age is only a guess about it.

    An ATS row was in that board's feed on the last successful fetch, so it is
    open regardless of when it was first published. Applying the age proxy
    anyway hid every Graphcore and Wayve role first published more than 30 days
    ago — 35 of them — behind a rule invented for a different problem. Closed
    ATS roles are removed by their absence from the feed instead.
    """
    old = _row(jid="gh:123", age_days=90, source="gh")
    assert shortlist.disqualify(old, max_age=30) is None

    # ...but an Adzuna row of the same age is still gated.
    assert shortlist.disqualify(_row(age_days=90), max_age=30) is not None


def test_the_ranking_and_the_displayed_age_agree():
    """score() must not reward an ad that age_days() calls old.

    They disagreed: score() read date_first_seen first, age_days() read
    posted_date first. On 2026-08-20 that put two Palantir requisitions at the
    top of the briefing, printed as "384d ago" and "833d ago", each scored +10
    for "posted this week".

    The trigger is any row where the two dates differ a lot, which the
    2026-08-14 ATS backfill produced 235 of in one morning: an old posting
    first seen today.
    """
    old_ad_seen_today = _row(
        posted_date=(datetime.now() - timedelta(days=385)).strftime("%Y-%m-%d"),
        date_first_seen=datetime.now().strftime("%Y-%m-%d"))

    assert shortlist.age_days(old_ad_seen_today) >= 380
    points, why = shortlist.score(old_ad_seen_today)
    assert "posted this week" not in why, why
    assert any("d old" in w for w in why), why


def test_a_genuinely_fresh_ad_still_earns_the_bonus():
    """The fix must not cost the signal it was guarding."""
    _, why = shortlist.score(_row(age_days=2))
    assert "posted this week" in why, why


def test_an_undateable_row_is_neither_rewarded_nor_penalised_for_age():
    _, why = shortlist.score(_row(posted_date="", date_first_seen=""))
    assert not any("posted this week" in w or "d old" in w for w in why), why


def test_age_gate_does_not_override_the_other_disqualifiers():
    row = _row(title="Senior Solutions Engineer", age_days=1)
    assert "seniority" in shortlist.disqualify(row, max_age=30)


# --- the ordering rule ------------------------------------------------------

def test_verify_live_defaults_to_no_age_gate():
    """Syntasso regression: a live 47-day ad must survive to be probed.

    Mirrors the resolution order in main(): --max-age unset plus --verify-live
    means the network decides, not the calendar.
    """
    def resolve(max_age_arg, verify_live):
        if max_age_arg is None:
            return None if verify_live else 30
        return max_age_arg if max_age_arg > 0 else None

    assert resolve(None, True) is None, "verifying should stand the age gate down"
    assert resolve(None, False) == 30, "unverified runs still need the proxy"
    assert resolve(60, True) == 60, "an explicit --max-age must be honoured"
    assert resolve(0, False) is None, "--max-age 0 disables the gate"

    # And the gate genuinely would have binned the live ad.
    syntasso = _row(jid="5775685395", age_days=47)
    assert shortlist.disqualify(syntasso, max_age=30) is not None
    assert shortlist.disqualify(syntasso, max_age=None) is None


# --- liveness cache ---------------------------------------------------------

def _cache_in_tmp(monkey_path):
    liveness.CACHE_PATH = monkey_path
    return {}


def test_dead_is_cached_forever():
    """A delisted id never comes back, so we must never pay to re-check it."""
    old = (datetime.now() - timedelta(days=900)).isoformat(timespec="seconds")
    cache = {"42": {"verdict": liveness.DEAD, "checked": old}}
    assert liveness.cached_verdict(cache, "42") == liveness.DEAD


def test_live_expires_so_a_closed_ad_gets_noticed():
    fresh = datetime.now().isoformat(timespec="seconds")
    stale = (datetime.now()
             - timedelta(days=liveness.LIVE_TTL_DAYS + 1)).isoformat(timespec="seconds")
    assert liveness.cached_verdict(
        {"7": {"verdict": liveness.LIVE, "checked": fresh}}, "7") == liveness.LIVE
    assert liveness.cached_verdict(
        {"7": {"verdict": liveness.LIVE, "checked": stale}}, "7") is None


def test_unknown_is_never_cached():
    """A timeout must not harden into a permanent verdict."""
    assert liveness.cached_verdict(
        {"9": {"verdict": liveness.UNKNOWN,
               "checked": datetime.now().isoformat()}}, "9") is None


def test_cache_survives_a_corrupt_file():
    path = os.path.join(tempfile.mkdtemp(prefix="liveness-"), "cache.json")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("{not json")
    original = liveness.CACHE_PATH
    try:
        liveness.CACHE_PATH = __import__("pathlib").Path(path)
        assert liveness.load_cache() == {}
    finally:
        liveness.CACHE_PATH = original


def test_probe_failure_fails_open():
    """No API key must mean 'unknown', never 'dead'."""
    original = liveness._api_key
    try:
        liveness._api_key = lambda: None
        out = liveness.check(["123", "456"], cache={})
        assert all(v == liveness.UNKNOWN for v, _ in out.values()), out
    finally:
        liveness._api_key = original


def test_detail_url_is_rebuilt_from_the_id():
    """The stored land/ad URL shape does not give a clean 200/410."""
    assert liveness.detail_url("5775685395") == \
        "https://www.adzuna.co.uk/jobs/details/5775685395"


# --- multi-source safety ----------------------------------------------------
# Added 2026-08-14 alongside the second job source. detail_url() rebuilds an
# Adzuna URL from a bare id, and Adzuna answers 410 for an unknown id on that
# route — which this module treats as DEAD and caches permanently. Feeding it a
# Greenhouse id would therefore delete a live job from every future shortlist,
# for a reason that never existed. The daily run uses --verify-live, so this
# would have fired on day one.

def test_non_adzuna_ids_are_never_probed_or_cached():
    cache = {}
    probed = []

    original = liveness._probe
    try:
        def _spy(job_id, key, timeout=90):
            probed.append(job_id)
            return liveness.DEAD, "410 Gone"     # the poison verdict
        liveness._probe = _spy
        out = liveness.check(["gh:4567890"], cache=cache)
    finally:
        liveness._probe = original

    assert probed == [], f"namespaced id was probed: {probed}"
    assert out["gh:4567890"][0] == liveness.UNKNOWN, out
    assert cache == {}, f"a false DEAD was cached forever: {cache}"


def test_detail_url_refuses_a_namespaced_id():
    for bad in ("gh:4567890", "lever:abc-123", "ashby:x"):
        try:
            liveness.detail_url(bad)
        except ValueError:
            continue
        raise AssertionError(f"detail_url({bad!r}) built an Adzuna URL")


def test_existing_adzuna_cache_entries_still_resolve():
    """The fix must not invalidate a single paid-for verdict.

    Every key in the live cache is a bare digit string, so "bare" already means
    "Adzuna" and the new namespace is disjoint from it.
    """
    cache = {"5775689205": {"verdict": liveness.DEAD,
                            "checked": datetime.now().isoformat(timespec="seconds")}}
    assert liveness.cached_verdict(cache, "5775689205") == liveness.DEAD


# --- eligibility gates ------------------------------------------------------
# Added 2026-08-15. Six of the shortlist's own top fourteen were roles that
# would auto-reject: four gated on UK security clearance, two internships
# requiring a later graduation year. The ranking looked excellent and was
# unusable.

def test_clearance_titles_are_dropped():
    """SC normally needs five continuous years of UK residency; DV more."""
    for title in ("Forward Deployed Software Engineer - UK Government",
                  "Forward Deployed Software Engineer - NATO",
                  "Software Engineer - eDV",
                  "Platform Engineer (SC Cleared)",
                  "Developer, Developed Vetting required"):
        reason = shortlist.disqualify(_row(title=title), max_age=None)
        assert reason and "clearance" in reason, f"{title!r} -> {reason!r}"


def test_internships_and_apprenticeships_are_dropped():
    """A 2026 graduate is not eligible: Palantir's London internships require
    graduating in 2028, and UK apprenticeships need existing right to work."""
    for title in ("Software Engineer, Internship",
                  "Forward Deployed Software Engineer, Internship - Commercial",
                  "Digital and Technology Solutions Apprenticeship Level 6",
                  "Graduate Placement Programme"):
        reason = shortlist.disqualify(_row(title=title), max_age=None)
        assert reason, f"{title!r} was not dropped"


def test_ordinary_roles_survive_both_gates():
    """The gates must not swallow the jobs they exist to make room for."""
    for title in ("Forward Deployed Software Engineer, New Grad - Commercial",
                  "Associate Solution Engineer",
                  "Graduate Software Engineer",
                  "Machine Learning Engineer"):
        assert shortlist.disqualify(_row(title=title), max_age=None) is None, title


def test_clearance_hint_flags_but_never_drops():
    """Faculty's Forward Deployed Engineer says "you may need to be eligible
    for UK Developed Vetting" because of *some* of their government work. 187
    rows mention clearance somewhere in their text; dropping on that would bin
    most of the board. Flag it, keep it, let a human read the posting."""
    desc = ("Because of the nature of the work we do with our Government "
            "clients, you may need to be eligible for UK Developed Vetting "
            "(DV) and willing to work on site.")
    row = _row(title="Forward Deployed Engineer", description=desc)
    assert shortlist.disqualify(row, max_age=None) is None, "must not drop"
    assert shortlist.CLEARANCE_HINT.search(desc), "must flag"


def test_one_definition_of_an_agency():
    """This module used to keep a private KNOWN_AGENCIES set that disagreed
    with the config-driven one, so Salt was an agency to the shortlist and not
    to the sponsor review queue. The queue kept asking for human decisions on
    recruitment firms."""
    for name in ("Salt", "Sanderson", "Oliver James", "Hays", "Robert Walters"):
        assert shortlist.is_agency(name) is tracker.is_agency(name) is True, name
    for name in ("Palantir", "Monzo", "Snowflake"):
        assert shortlist.is_agency(name) is tracker.is_agency(name) is False, name


# --- runner -----------------------------------------------------------------
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
