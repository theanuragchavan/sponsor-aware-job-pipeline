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

    The first version banned the string "load_sponsor_lookup" from main(), as a
    proxy for "targets do not come from the register". The proxy went wrong the
    moment --apply needed the register for something else entirely -- resolving
    a company to its exact Home Office name. Assert the invariant itself: the
    target list comes from the tracker, and nothing walks the register in order
    to build it.
    """
    import inspect
    src = inspect.getsource(ats_prober.main)
    assert "targets = targets_from_tracker(" in src
    assert "sorted(lookup.items())" not in src, \
        "register-alphabetical selection was removed on evidence: it yields " \
        "003, 007 TAXI, 0086. See targets_from_tracker's docstring."


# --- resolving the load-bearing column ------------------------------------

def test_an_exact_register_key_resolves():
    assert ats_prober.resolve_register_name("Tracebit", {"TRACEBIT": "A"},
                                            {}) == "TRACEBIT"


def test_a_confirmed_alias_resolves_to_its_register_name():
    """Five of the first sweep's boards resolved this way -- Axle -> AXLE
    ENERGY, Quilter -> QUILTER BUSINESS SERVICES -- all ticked on 2026-08-15.
    """
    # Alias keys are normalise_name() output, which uppercases.
    aliases = {"AXLE": {"register_name": "AXLE ENERGY", "rating": "A"}}
    assert ats_prober.resolve_register_name(
        "Axle", {"AXLE ENERGY": "A"}, aliases) == "AXLE ENERGY"


def test_an_alias_pointing_off_the_skilled_worker_register_is_refused():
    """The register carries one row per route, so an organisation can be on it
    while holding nothing he can use -- Salesforce's first row is a Global
    Business Mobility graduate-trainee licence. `load_sponsor_lookup` already
    filters on Route, so absence from it means the alias is stale, and stale is
    not decided.
    """
    aliases = {"GHOST": {"register_name": "SOME TEMPORARY WORKER LTD"}}
    assert ats_prober.resolve_register_name("Ghost", {"REAL CO": "A"},
                                            aliases) == ""


def test_a_prefix_match_is_not_a_decision():
    """"AXLE" starting "AXLE ENERGY" is the same reasoning that mapped
    Universal Music onto a design agency. sponsor_aliases.json is documented as
    never written by a matcher, only by a decision.
    """
    assert ats_prober.resolve_register_name("Axle", {"AXLE ENERGY": "A"},
                                            {}) == ""


def test_an_unknown_company_resolves_to_nothing():
    assert ats_prober.resolve_register_name("Nobody", {}, {}) == ""
    assert ats_prober.resolve_register_name("", {"": "A"}, {}) == ""


def test_appending_preserves_what_is_already_there(tmp=None):
    """ats_boards.csv is hand-curated; an append that rewrites it can lose
    someone's notes."""
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "ats_boards.csv"
        p.write_text("company,ats,slug,register_name,status,last_ok,notes\n"
                     "Accurx,ashby,accurx,ACCURX,active,2026-08-14,17 jobs\n",
                     encoding="utf-8")
        ats_prober.append_boards(
            [{"company": "Tes", "ats": "greenhouse", "slug": "tes",
              "register_name": "TES", "jobs": 1}], p)
        lines = p.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3, lines
    assert "Accurx" in lines[1], "the existing row must survive"
    assert lines[2].startswith("Tes,greenhouse,tes,TES,active,,")


def test_appending_to_a_file_with_no_trailing_newline_does_not_join_rows():
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "b.csv"
        p.write_text("company,ats,slug,register_name,status,last_ok,notes\n"
                     "Accurx,ashby,accurx,ACCURX,active,,17 jobs",
                     encoding="utf-8")
        ats_prober.append_boards(
            [{"company": "Tes", "ats": "greenhouse", "slug": "tes",
              "register_name": "TES", "jobs": 1}], p)
        lines = p.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3, lines
    assert lines[1] == "Accurx,ashby,accurx,ACCURX,active,,17 jobs"


# --- the cache -------------------------------------------------------------

def test_a_cached_hit_is_reported_not_skipped():
    """A second run of a successful sweep found nothing, because anything in
    the cache was skipped rather than reused -- so --apply had no rows."""
    cache = {"zilch": {"at": "2026-08-22",
                       "hit": {"company": "Zilch", "board_name": "",
                               "slug": "zilch", "ats": "ashby", "jobs": 12}}}
    assert ats_prober.cached_hit(cache, "zilch")["jobs"] == 12


def test_a_cached_hit_is_regraded_against_the_current_rule():
    """`match` is a judgement, and the rule has already changed once. A cache
    that freezes an old verdict quietly un-improves the tool."""
    cache = {"zilch": {"at": "2026-08-22",
                       "hit": {"company": "Zilch", "board_name": "",
                               "slug": "zilch", "ats": "ashby", "jobs": 12,
                               "match": "loose"}}}
    assert ats_prober.cached_hit(cache, "zilch")["match"] == "exact"


def test_a_legacy_cache_entry_is_regraded_too():
    """Entries written before the timestamped format are bare hit dicts. The
    first fix returned them early and five Ashby boards kept a stale verdict.
    """
    cache = {"olix": {"company": "OLIX", "board_name": "", "slug": "olix",
                      "ats": "ashby", "jobs": 22, "match": "loose"}}
    assert ats_prober.cached_hit(cache, "olix")["match"] == "exact"


def test_a_recent_miss_is_believed_and_an_old_one_is_reprobed():
    """Re-probing unresolved companies monthly is this module's stated policy;
    the first version cached misses forever and contradicted it."""
    import datetime as dt
    today = dt.date.today()
    fresh = (today - dt.timedelta(days=1)).isoformat()
    stale = (today - dt.timedelta(days=ats_prober.MISS_TTL_DAYS + 1)).isoformat()
    assert ats_prober.cached_hit({"x": {"at": fresh, "hit": None}}, "x") == {}
    assert ats_prober.cached_hit({"x": {"at": stale, "hit": None}}, "x") is None


def test_an_unseen_company_is_probed():
    assert ats_prober.cached_hit({}, "brand-new") is None


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
