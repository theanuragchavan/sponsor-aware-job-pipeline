"""Tests for the ATS prober.

Three of these come from live runs rather than from reasoning, and they are the
ones worth keeping.

**A generic slug catches the wrong company.** Probing "Universal Music" returned
a Greenhouse board named "Universal" holding two postings, one titled "Open
Applicatons" -- a London design agency. The old boolean guard accepted it
because "universalmusic" starts with "univer", and the same test is *correct*
for "Monzo Bank" -> a board named "Monzo". That ambiguity is not resolvable by
string comparison, so the prober grades the match and a person decides.

**Already tracked is not a miss.** The first version printed "0 new boards"
after successfully finding Palantir's 308-posting Lever board, because the slug
was already in ats_boards.csv. A working prober that reports nothing reads as a
broken one.

**The register is the wrong starting set.** Probed alphabetically it yields
`003`, `007 TAXI`, `0086`. Targets come from the tracker instead.

Run:  python tests/test_ats_prober.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ats_prober  # noqa: E402


# --- slug generation -------------------------------------------------------

def test_corporate_suffixes_are_dropped():
    """No ATS slug contains "Ltd"."""
    assert ats_prober.slug_variants("Palantir Technologies UK Ltd") == \
        ["palantir"], ats_prober.slug_variants("Palantir Technologies UK Ltd")


def test_a_two_word_name_offers_three_spellings_best_first():
    assert ats_prober.slug_variants("MONZO BANK") == \
        ["monzobank", "monzo-bank", "monzo"]


def test_variants_are_deduplicated():
    """A one-word company must not be probed three times.

    Each variant is a request against someone else's server.
    """
    assert ats_prober.slug_variants("Wayve") == ["wayve"]


def test_an_all_suffix_name_falls_back_rather_than_returning_nothing():
    """"Global Services Ltd" is entirely stopwords. Probing the raw name beats
    probing nothing."""
    assert ats_prober.slug_variants("Global Services Ltd")


def test_an_empty_name_produces_no_requests():
    assert ats_prober.slug_variants("") == []
    assert ats_prober.slug_variants("   ") == []


def test_very_short_fragments_are_not_probed():
    """Two-character slugs match half the internet."""
    assert all(len(s) >= 3 for s in ats_prober.slug_variants("BT Group"))


# --- the name guard --------------------------------------------------------

def test_an_identical_name_is_exact():
    assert ats_prober.match_strength("Monzo", "Monzo") == "exact"
    assert ats_prober.match_strength("Monzo Bank", "monzo bank") == "exact"


def test_a_shorter_board_name_is_loose_not_confirmed():
    """The Universal Music failure, and the reason a bool was not enough.

    This is the same shape as the legitimate "Monzo Bank" -> "Monzo", which is
    exactly why neither may be auto-accepted.
    """
    assert ats_prober.match_strength("Universal Music", "Universal") == "loose"
    assert ats_prober.match_strength("Monzo Bank", "Monzo") == "loose"


def test_a_different_company_is_rejected_outright():
    """SmartRecruiters' `palantir` board belongs to a C# consultancy in
    Aberdeen. JOB_SOURCES.md records it by name."""
    assert ats_prober.match_strength(
        "Palantir Technologies", "Aberdeen C# Consulting") == ""


def test_with_no_board_name_a_full_slug_is_as_good_as_a_name_match():
    """Ashby reports no company name, and grading every Ashby hit "loose" made
    eight of eleven findings in a 150-company sweep look doubtful when the real
    cause was the family. A slug spelling the company out in full is evidence.
    """
    assert ats_prober.match_strength("Zilch", "", "zilch") == "exact"
    assert ats_prober.match_strength("Maya HTT", "", "maya-htt") == "exact"


def test_with_no_board_name_a_slug_that_dropped_a_word_still_needs_a_person():
    """"amber" for Amber Labs could be any Amber. This is the case review is
    for, and separating it took the pile from eight to three."""
    assert ats_prober.match_strength("Amber Labs", "", "amber") == "loose"
    assert ats_prober.match_strength(
        "Raspberry Pi Foundation", "", "raspberry") == "loose"


def test_a_reported_name_still_outranks_the_slug():
    """Universal Music must not be rescued by its slug matching."""
    assert ats_prober.match_strength(
        "Universal Music", "Universal", "universal") == "loose"


def test_no_name_and_no_slug_is_loose_never_exact():
    """Absence of contradiction is not confirmation."""
    assert ats_prober.match_strength("Anything At All", "") == "loose"


# --- what is probed at all -------------------------------------------------

def test_smartrecruiters_and_workable_are_not_probed():
    """Both answer 200 for slugs that do not exist, so neither can be used for
    discovery. This is a correctness decision, not an oversight -- assert it so
    nobody restores them for coverage.
    """
    families = {name for name, _fn in ats_prober.PROBES}
    assert "smartrecruiters" not in families
    assert "workable" not in families
    assert families == {"greenhouse", "ashby", "lever"}, families


def test_a_hit_requires_postings_never_a_status_code():
    """Every probe family returns (name, jobs); an empty list is a miss."""
    calls = []

    def empty(slug):
        calls.append(slug)
        return ("Monzo Bank", [])

    saved = ats_prober.PROBES
    try:
        ats_prober.PROBES = (("greenhouse", empty),)
        assert ats_prober.probe_company("Monzo Bank", throttle=0) is None
        assert calls, "the probe must actually have been attempted"
    finally:
        ats_prober.PROBES = saved


def test_a_hit_carries_sample_titles_so_a_person_can_judge_it():
    def one(slug):
        return ("Universal", [{"title": "Freelance Designer"},
                              {"title": "Open Applicatons"}])

    saved = ats_prober.PROBES
    try:
        ats_prober.PROBES = (("greenhouse", one),)
        hit = ats_prober.probe_company("Universal Music", throttle=0)
    finally:
        ats_prober.PROBES = saved

    assert hit["match"] == "loose", hit
    assert "Freelance Designer" in hit["sample"], hit
    assert hit["jobs"] == 2


def test_one_bad_slug_does_not_stop_a_sweep():
    """A sweep runs over a hundred companies; a single network error must not
    end it."""
    def boom(slug):
        raise RuntimeError("connection reset")

    saved = ats_prober.PROBES
    try:
        ats_prober.PROBES = (("greenhouse", boom),)
        assert ats_prober.probe_company("Monzo Bank", throttle=0) is None
    finally:
        ats_prober.PROBES = saved


# --- target selection ------------------------------------------------------

def test_targets_come_from_the_tracker_not_the_register():
    """Probed alphabetically the register yields `003` and `007 TAXI`.

    Asserted against the source rather than by running it, because the real
    tracker is not available to a test.
    """
    import inspect
    src = inspect.getsource(ats_prober.main)
    assert "targets_from_tracker" in src
    assert "load_sponsor_lookup" not in src, \
        "register-alphabetical selection was removed on evidence; see the docstring"


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
