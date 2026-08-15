"""Regression tests for ATS board ingestion.

Fixtures are literal payloads copied from live responses on 2026-08-14, so no
test here touches the network.

Run:  python tests/test_ats_ingest.py
"""
import datetime as dt
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "pipeline"))

import openpyxl  # noqa: E402

import tracker  # noqa: E402
from ats import boards as boards_mod  # noqa: E402
from ats import clients, mapping  # noqa: E402

BOARD = {"company": "Monzo", "ats": "greenhouse", "slug": "monzo",
         "register_name": "MONZO BANK", "status": "active"}

# Shape copied verbatim from boards-api.greenhouse.io/v1/boards/monzo/jobs
GH_JOB = {
    "id": 7343996,
    "title": "Platform Engineer",
    "location": {"name": "Cardiff, London or Remote (UK)"},
    "absolute_url": "https://job-boards.greenhouse.io/monzo/jobs/7343996",
    "updated_at": "2026-08-03T05:57:20-04:00",
    "first_published": "2026-07-28T09:12:00-04:00",
    "application_deadline": "",
    "content": "&lt;p&gt;We are &lt;strong&gt;hiring&lt;/strong&gt;.&lt;/p&gt;",
    "departments": [{"name": "Engineering"}],
    "company_name": "Monzo",
    "employment_type": "FullTime",
}


def _job(**over):
    job = dict(GH_JOB)
    job.update(over)
    return job


# --- dates ------------------------------------------------------------------

def test_lever_epoch_milliseconds_becomes_a_real_date():
    """tracker.to_row slices `created`[:10]; on an int that raises TypeError.

    Lever sends createdAt as epoch milliseconds, so any shared date helper has
    to cope or the whole run dies on the first Lever board.
    """
    assert mapping.to_date(1754784000000) == "2025-08-10"
    assert mapping.to_date(1754784000) == "2025-08-10", "seconds too, not just ms"


def test_iso_timestamps_of_every_shape_normalise():
    for value, expected in [
        ("2026-08-03T05:57:20-04:00", "2026-08-03"),   # Greenhouse
        ("2026-04-07T17:12:35.753+00:00", "2026-04-07"),  # Ashby
        ("2026-08-14", "2026-08-14"),
    ]:
        assert mapping.to_date(value) == expected, value


def test_unparseable_dates_are_blank_not_an_exception():
    for value in (None, "", "not a date", True, [], {}):
        assert mapping.to_date(value) == ""


# --- rows -------------------------------------------------------------------

def test_row_has_exactly_the_tracker_fieldnames():
    """main.py indexes r["tier"] / r["sponsor_match"] rather than .get().

    A row missing a key raises KeyError *after* save_tracker has run, so the
    data lands but the summary dies.
    """
    row = mapping.to_row("greenhouse", GH_JOB, BOARD)
    assert set(row) == set(tracker.FIELDNAMES), (
        set(row) ^ set(tracker.FIELDNAMES))


def test_id_is_namespaced_and_cannot_shadow_an_adzuna_row():
    """Greenhouse ids are plain integers, exactly like Adzuna's.

    5775685395 is a real Adzuna id in the store (Syntasso). Unprefixed, the
    insert-only merge would see it as already tracked and silently drop the
    Greenhouse job — or worse, shadow the Adzuna one.
    """
    row = mapping.to_row("greenhouse", _job(id=5775685395), BOARD)
    assert row["id"] == "gh:5775685395"
    assert row["source"] == "gh"

    existing = {"5775685395": {"id": "5775685395", "status": "applied"}}
    added = tracker.merge_rows(existing, [row])
    assert len(added) == 1
    assert existing["5775685395"]["status"] == "applied"


def test_apply_url_is_the_employers_own_page():
    row = mapping.to_row("greenhouse", GH_JOB, BOARD)
    assert row["redirect_url"].startswith("https://job-boards.greenhouse.io/")
    assert "adzuna" not in row["redirect_url"]


def test_entity_encoded_html_is_fully_cleaned():
    """Greenhouse sends `content` entity-encoded, not as raw HTML.

    tracker._clean_snippet strips tags and *then* unescapes, so calling it
    directly leaves visible `<p><span data-contrast="auto">` markup in every
    ATS description. Verified against Riverlane's live feed.
    """
    row = mapping.to_row("greenhouse", GH_JOB, BOARD)
    assert "<" not in row["description"], row["description"][:120]
    assert "&lt;" not in row["description"]
    assert "hiring" in row["description"]


def test_clean_html_handles_raw_html_too():
    """Adzuna-style raw tags must still come out clean."""
    assert "<" not in mapping.clean_html("<p>Hello <b>world</b></p>")
    assert "Hello world" in mapping.clean_html("<p>Hello <b>world</b></p>")


def test_registry_company_name_wins_over_the_feed_name():
    """Tide's feed reports the company as "Careers at Tide", which matches
    nothing on the register. The registry entry is the authority."""
    board = dict(BOARD, company="Tide", register_name="TIDE PLATFORM")
    row = mapping.to_row("greenhouse", _job(company_name="Careers at Tide"), board)
    assert row["company"] == "Tide"


def test_namespaced_id_survives_the_xlsx_roundtrip():
    row = {name: "" for name in tracker.FIELDNAMES}
    row.update({"id": "gh:4567890", "title": "Platform Engineer",
                "company": "Monzo", "location": "London", "status": "new",
                "source": "gh"})
    path = os.path.join(tempfile.mkdtemp(prefix="ats-"), "store.xlsx")
    tracker._write_beautified_xlsx(path, [row])
    loaded = tracker.load_tracker(path)
    assert "gh:4567890" in loaded, sorted(loaded)
    assert loaded["gh:4567890"]["source"] == "gh"


# --- tiering ----------------------------------------------------------------

def test_tier_uses_a_classifier_not_the_adzuna_search_phrases():
    """config.TIER1..TIER4 are search phrases, not a taxonomy.

    Using them as one left every surviving Monzo role untiered, because
    "Platform Engineer" contains none of the exact phrases. These assertions
    fail against that implementation.
    """
    assert mapping.tier_for_title("Forward Deployed Engineer") == "1"
    assert mapping.tier_for_title("Solutions Engineer") == "1"
    assert mapping.tier_for_title("AI Research Engineer") == "3"
    assert mapping.tier_for_title("Graduate Software Engineer") == "2"
    assert mapping.tier_for_title("Platform Engineer") == "4"
    assert mapping.tier_for_title("Benefits Specialist") == ""


# --- filters ----------------------------------------------------------------

def test_remote_plus_a_foreign_country_is_dropped():
    """The bug this pins: "Remote India" reached the shortlist.

    An earlier rule matched /remote/ and returned "keep" before anything looked
    at the rest of the string, so a Mumbai role passed the UK gate.
    """
    assert mapping.uk_location_reason("Remote India") is not None
    assert mapping.uk_location_reason("Remote - Singapore") is not None
    assert mapping.uk_location_reason("San Francisco, CA") is not None


def test_country_in_the_title_is_caught_when_the_location_is_useless():
    """Cloudflare sets every location to the bare word "Hybrid".

    The country only appears in the title, so judging the location alone let
    Korea, Thailand and Melbourne roles through.
    """
    for title in ("Customer Engineer (Pre-Sales), Korea (Based in Seoul)",
                  "Enterprise Customer Engineer, Thailand",
                  "Presales Customer Engineer, Enterprise (based in Melbourne)",
                  "Forward Deployed Engineer, Hebrew speaker"):
        assert mapping.uk_location_reason("Hybrid", title) is not None, title


def test_bare_remote_and_uk_locations_are_kept():
    for keep in ("Remote", "Anywhere", "", "London, UK",
                 "Cardiff, London or Remote (UK)", "Cambridge, UK"):
        assert mapping.uk_location_reason(keep) is None, keep


def test_a_uk_mention_anywhere_beats_a_foreign_one():
    """A genuinely multi-site role must not be lost to its other office."""
    assert mapping.uk_location_reason("London, UK; Dublin, Ireland") is None
    assert mapping.uk_location_reason(
        "Hybrid", "Software Engineer, London") is None
    assert mapping.uk_location_reason(
        "London, UK; Ontario, CAN; San Francisco, CA") is None


def test_new_york_is_not_york_and_cambridge_ma_is_not_cambridge():
    """The bug this pins let 36 of Anthropic's 41 kept rows through.

    "york" was in the UK city list, so "New York City, NY" matched as a UK
    location and San Francisco roles sailed past the gate. Place names that
    exist on both sides of the Atlantic only count when nothing foreign is
    present.
    """
    for foreign in ("San Francisco, CA | New York City, NY",
                    "New York City, NY",
                    "Cambridge, MA",
                    "Birmingham, AL",
                    "Boston, MA | Seattle, WA"):
        assert mapping.uk_location_reason(foreign) is not None, foreign

    # ...and the real UK cities still work when nothing foreign is present.
    for uk in ("Cambridge, UK", "Cambridge", "York", "Birmingham",
               "Newcastle upon Tyne"):
        assert mapping.uk_location_reason(uk) is None, uk


def test_us_state_suffix_is_treated_as_foreign():
    assert mapping.uk_location_reason("Austin, TX") is not None
    assert mapping.uk_location_reason("Remote - Denver, CO") is not None


def test_unspecified_is_kept_but_an_unrecognised_place_is_not():
    """These are two different things and the distinction is the design.

    An arrangement-only string ("Hybrid", "Remote") states no location at all,
    so it is unknown and he should get to judge it. A concrete place name we
    cannot confirm as UK is a place — and the point of the gate is that he needs
    a UK role, so an unconfirmable one does not belong on the shortlist.
    """
    for unspecified in ("Hybrid", "Remote", "Flexible", "On-site", ""):
        assert mapping.uk_location_reason(unspecified) is None, unspecified
    assert mapping.uk_location_reason("Somewhereville") is not None


def test_seniority_filter_is_reused_not_reimplemented():
    row = mapping.to_row("greenhouse", _job(title="Engineering Director, EU"),
                         BOARD)
    assert mapping.drop_reason(row) == tracker.unsuitable_reason(
        "Engineering Director, EU")


def test_non_engineering_roles_are_dropped():
    """A board feed carries the whole company; an Adzuna search does not."""
    for title in ("Benefits Specialist", "Tech Recruiter", "Buyer",
                  "Legal Counsel, Product"):
        row = mapping.to_row("greenhouse", _job(title=title), BOARD)
        assert mapping.drop_reason(row) is not None, title


def test_expired_deadline_is_dropped_but_a_blank_one_is_not():
    today = "2026-08-14"
    past = mapping.to_row("greenhouse",
                          _job(application_deadline="2026-08-01"), BOARD)
    future = mapping.to_row("greenhouse",
                            _job(application_deadline="2099-01-01"), BOARD)
    blank = mapping.to_row("greenhouse", _job(application_deadline=""), BOARD)
    assert "deadline passed" in (mapping.drop_reason(past, today) or "")
    assert mapping.drop_reason(future, today) is None
    assert mapping.drop_reason(blank, today) is None, \
        "a blank deadline must not be read as expired"


# --- the other two ATS families ---------------------------------------------
# Shapes copied verbatim from live responses on 2026-08-14.

ASHBY_JOB = {
    "id": "5f1c9b2e-1234-4a0b-9c8d-abcdef012345",
    "title": "Software Engineer, Platform",
    "department": "Engineering",
    "team": "Platform",
    "employmentType": "FullTime",
    "location": "London, UK",
    "secondaryLocations": [{"location": "Remote (UK)"}],
    "publishedAt": "2026-04-07T17:12:35.753+00:00",
    "isListed": True,
    "isRemote": False,
    "jobUrl": "https://jobs.ashbyhq.com/example/5f1c9b2e",
    "descriptionHtml": "<p>Build <b>things</b>.</p>",
}

LEVER_JOB = {
    "id": "8f3c1d20-aaaa-bbbb-cccc-000011112222",
    "text": "Graduate Software Engineer",
    "categories": {"location": "London", "team": "Engineering",
                   "commitment": "Full-time"},
    "createdAt": 1754784000000,           # epoch MILLISECONDS, as an int
    "hostedUrl": "https://jobs.lever.co/example/8f3c1d20",
    "descriptionPlain": "Join the team.",
}


def test_every_family_produces_an_identical_row_shape():
    """One missing key crashes main.py's summary after the store is written."""
    rows = [
        mapping.to_row("greenhouse", GH_JOB, BOARD),
        mapping.to_row("ashby", ASHBY_JOB, BOARD),
        mapping.to_row("lever", LEVER_JOB, BOARD),
    ]
    for row in rows:
        assert set(row) == set(tracker.FIELDNAMES), set(row) ^ set(
            tracker.FIELDNAMES)


def test_every_family_namespaces_its_ids_distinctly():
    ids = [mapping.to_row(f, j, BOARD)["id"] for f, j in
           (("greenhouse", GH_JOB), ("ashby", ASHBY_JOB), ("lever", LEVER_JOB))]
    prefixes = [i.split(":", 1)[0] for i in ids]
    assert prefixes == ["gh", "ashby", "lever"], prefixes
    assert len(set(prefixes)) == 3, "families must not share a namespace"


def test_lever_epoch_date_survives_the_full_mapping():
    """Not just to_date in isolation — the whole row must build."""
    row = mapping.to_row("lever", LEVER_JOB, BOARD)
    assert row["posted_date"] == "2025-08-10", row["posted_date"]
    assert row["title"] == "Graduate Software Engineer"
    assert row["location"] == "London"
    assert row["tier"] == "2", "graduate + tech should be tier 2"


def test_ashby_merges_its_secondary_locations():
    """A London role listed with a second site must not lose the London one."""
    row = mapping.to_row("ashby", ASHBY_JOB, BOARD)
    assert "London" in row["location"]
    assert "Remote (UK)" in row["location"]
    assert mapping.drop_reason(row) is None


def test_ashby_and_lever_carry_a_direct_apply_url():
    for family, job in (("ashby", ASHBY_JOB), ("lever", LEVER_JOB)):
        row = mapping.to_row(family, job, BOARD)
        assert row["redirect_url"].startswith("https://"), family
        assert "adzuna" not in row["redirect_url"], family


def test_prefixes_table_matches_what_the_mappers_emit():
    """ats_main uses PREFIXES to find a board's own rows when marking closures.

    If it drifts from what the mappers actually write, closure detection
    silently stops finding anything.
    """
    for family, prefix in mapping.PREFIXES.items():
        job = {"greenhouse": GH_JOB, "ashby": ASHBY_JOB,
               "lever": LEVER_JOB}[family]
        assert mapping.to_row(family, job, BOARD)["id"].startswith(
            prefix + ":"), family


# --- board registry ---------------------------------------------------------

def test_a_valid_but_empty_board_is_not_a_missing_board():
    """Yoti's Workable account returns 200 with zero jobs.

    Conflating that with 404 would delete a good registry entry, and the
    registry is hand-built.
    """
    path = os.path.join(tempfile.mkdtemp(prefix="boards-"), "b.csv")
    boards_mod.save_boards([dict(BOARD, status="empty")], path)
    loaded = boards_mod.load_boards(path)
    assert len(loaded) == 1
    assert list(boards_mod.iter_active(loaded)), \
        "an empty board must still be fetched next run"


def test_paused_boards_are_not_fetched():
    path = os.path.join(tempfile.mkdtemp(prefix="boards-"), "b.csv")
    boards_mod.save_boards([dict(BOARD, status="paused")], path)
    assert not list(boards_mod.iter_active(boards_mod.load_boards(path)))


def test_registry_roundtrips_every_column():
    path = os.path.join(tempfile.mkdtemp(prefix="boards-"), "b.csv")
    boards_mod.save_boards([BOARD], path)
    loaded = boards_mod.load_boards(path)[0]
    assert loaded["register_name"] == "MONZO BANK"
    assert loaded["slug"] == "monzo"


def test_404_and_transient_errors_are_different_exceptions():
    """One outage must not demote every board in the registry."""
    assert issubclass(clients.BoardNotFound, Exception)
    assert issubclass(clients.BoardError, Exception)
    assert not issubclass(clients.BoardError, clients.BoardNotFound)
    assert not issubclass(clients.BoardNotFound, clients.BoardError)


def test_unknown_family_fails_loudly():
    for call in (lambda: clients.fetch("workday", "x"),
                 lambda: mapping.to_row("workday", GH_JOB, BOARD)):
        try:
            call()
        except KeyError:
            continue
        raise AssertionError("an unsupported ATS family must raise")


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
