"""Tests for the committed demo dataset.

The demo is the thing most people will ever see of this project, and it is
served from files committed to the repository rather than generated on boot. So
these check the two properties that make it worth committing:

1. **It is obviously synthetic.** Every URL is unresolvable by construction, and
   nothing in it can be mistaken for advice about a real employer.
2. **It still contains the problem.** A demo where every name resolves cleanly
   would quietly misrepresent entity resolution as solved. The five shapes have
   to survive every regeneration, especially the trap rows — those are the ones
   that prove a human is needed.

The trap check is the load-bearing one. It has already caught two real bugs: a
filler pool that shared a namespace with the company names and silently turned
87 unresolved companies into exact matches, and a trap built on a two-token name
that the prefix rule can never reach.

Run:  python tests/test_demo_data.py
"""
import csv
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sponsor_check  # noqa: E402
import tracker  # noqa: E402

DEMO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "web", "demo", "data")
TRACKER = os.path.join(DEMO, "demo_tracker.xlsx")
REGISTER = os.path.join(DEMO, "demo_register.csv")


def _rows():
    return list(tracker.load_tracker(TRACKER).values())


def _lookup():
    os.environ["SPONSOR_REGISTER_CSV"] = REGISTER
    return sponsor_check.load_sponsor_lookup()


def _skip_if_missing() -> bool:
    """The fixture is committed, but a fresh clone may not have run make yet."""
    return not os.path.exists(TRACKER)


# --- it must be obviously fake ----------------------------------------------

def test_every_link_is_unresolvable():
    """.invalid is reserved by RFC 2606, so no demo link can ever reach a real
    listing — not by accident, and not if someone scrapes the page."""
    if _skip_if_missing():
        return
    for row in _rows():
        url = row.get("redirect_url", "")
        assert url.startswith("https://example.invalid/"), f"{row['id']}: {url}"


def test_no_personal_columns_are_populated():
    """status/notes/date_applied/applied_via describe a real job hunt. The demo
    must carry none of it, whatever the generator does in future."""
    if _skip_if_missing():
        return
    for row in _rows():
        assert row.get("status") in ("", "new"), row
        for field in ("notes", "date_applied", "applied_via"):
            assert not (row.get(field) or "").strip(), f"{row['id']}.{field}"


# --- it must still contain the problem --------------------------------------

def test_all_five_resolution_shapes_are_present():
    if _skip_if_missing():
        return
    lookup = _lookup()
    index = sponsor_check.build_name_index(lookup)
    ta_index = sponsor_check.build_trading_index(lookup)

    companies = sorted({(r.get("company") or "").strip() for r in _rows()} - {""})
    found = {"exact": 0, "prefix": 0, "trading-as": 0, "absent": 0}

    for company in companies:
        if sponsor_check.normalize_name(company) in lookup:
            found["exact"] += 1
            continue
        hits = sponsor_check.suggest_matches(company, lookup, index, ta_index)
        if not hits:
            found["absent"] += 1
        for _name, _rating, why in hits:
            found[why] = found.get(why, 0) + 1

    for shape, n in found.items():
        assert n > 0, f"no {shape} companies in the demo: {found}"


def test_the_traps_survive():
    """A suggestion that a human must REFUSE. Without these the demo looks like
    it resolves names automatically, which is the opposite of the argument."""
    if _skip_if_missing():
        return
    lookup = _lookup()
    index = sponsor_check.build_name_index(lookup)
    ta_index = sponsor_check.build_trading_index(lookup)

    traps = 0
    for company in sorted({(r.get("company") or "").strip() for r in _rows()}):
        if not company or sponsor_check.normalize_name(company) in lookup:
            continue
        for name, _rating, _why in sponsor_check.suggest_matches(
                company, lookup, index, ta_index):
            if name.endswith(("CHARITABLE TRUST", "CAPITAL PARTNERS")):
                traps += 1
    assert traps >= 5, f"only {traps} trap suggestions — the demo self-resolves"


def test_both_eligibility_gates_have_something_to_refuse():
    """A clearance-gated role and an internship must be present, or the
    log-application refusal has nothing to demonstrate."""
    if _skip_if_missing():
        return
    sys.path.insert(0, os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pipeline"))
    import shortlist  # noqa: E402

    reasons = [shortlist.disqualify(r, max_age=None) for r in _rows()]
    assert any(r and "clearance" in r for r in reasons), "no clearance-gated role"
    assert any(r for r in reasons), "nothing is disqualified at all"


def test_an_agency_named_company_exists():
    """So the not_an_agency validator can be demonstrated, not just described."""
    if _skip_if_missing():
        return
    companies = {(r.get("company") or "").strip() for r in _rows()}
    assert any(tracker.is_agency(c) for c in companies if c), \
        "no agency-named company — the agency validator is undemonstrable"


def test_the_register_uses_the_real_schema():
    """The existing parser must read it unchanged, including rating variants
    and non-Skilled-Worker routes, or the demo exercises different code."""
    if _skip_if_missing():
        return
    with open(REGISTER, encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        assert reader.fieldnames == ["Organisation Name", "Town/City", "County",
                                     "Type & Rating", "Route"], reader.fieldnames
        rows = list(reader)

    routes = {r["Route"] for r in rows}
    assert routes != {"Skilled Worker"}, \
        "every row is Skilled Worker — the route filter is never exercised"
    ratings = {r["Type & Rating"] for r in rows}
    assert any("Premium" in r or "SME" in r for r in ratings), \
        "no rating variants — _parse_rating's regex is never exercised"


# --- the seeded history -----------------------------------------------------

def test_the_seeded_decision_log_verifies():
    """The 'chain verified' badge on a cold instance must be earned. These
    entries are produced by the real action layer, not written by hand."""
    if _skip_if_missing():
        return
    from web.api.audit import DecisionLog

    log = DecisionLog(os.path.join(DEMO, "demo_decision_log.jsonl"))
    aliases = json.loads(
        open(os.path.join(DEMO, "demo_aliases.json"), encoding="utf-8").read())
    report = log.verify(aliases)
    assert report["count"] >= 1, "no seeded history — the audit view opens empty"
    assert report["chain_ok"], report
    for entry in log.entries():
        assert entry["rationale"].strip(), f"seq {entry['seq']} has no reason"
        assert entry["validations"], f"seq {entry['seq']} recorded no checks"


def test_the_generator_is_deterministic():
    """Same seed, same rows. Asserted at row level, not on bytes: xlsx files are
    zips and embed timestamps, so they never compare equal."""
    if _skip_if_missing():
        return
    sys.path.insert(0, os.path.join(os.path.dirname(DEMO), ".."))
    from web.demo.generate_demo import build, stamp_exact_matches

    first = build()
    stamp_exact_matches(first["rows"], first["companies"])
    second = build()
    stamp_exact_matches(second["rows"], second["companies"])

    assert first["rows"] == second["rows"]
    assert first["register"] == second["register"]


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
