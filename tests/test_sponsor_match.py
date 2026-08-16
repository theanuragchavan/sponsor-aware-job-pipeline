"""Regression tests for sponsor matching and the alias overlay.

The bug these lock down (found 2026-08-14): sponsor matching was exact-only on a
normalised name, but the Home Office register records the *legal entity* while a
job ad names the *brand*. Measured against the live register:

    match_company("Monzo")     -> "no"   register key is MONZO BANK
    match_company("Deliveroo") -> "no"   register key is ROOFOODS LTD T A DELIVEROO
    match_company("Wise")      -> "no"

`shortlist.disqualify` drops any row scored "no", so licensed sponsors' roles
were invisible. 74 of the 492 non-agency companies scored "no" in the store had a
plausible register entry.

The fix is deliberately two-part: a *suggester* that proposes, and a human-
confirmed *alias overlay* that decides. These tests pin both, and pin the thing
that matters most — that rescoring never touches a hand-edited column.

Run:  python tests/test_sponsor_match.py
"""
import json
import re
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sponsor_check  # noqa: E402
import tracker  # noqa: E402

# A miniature register standing in for the real 121k-key lookup. Every entry
# here is the normalised form of a real row.
LOOKUP = {
    "MONZO BANK": "A",
    "ROOFOODS LTD T A DELIVEROO": "A",
    "AMENTUM UK": "A",
    "AMAZON CHARITABLE TRUST": "A",
    "AMAZON UK SERVICES": "A",
    "SYNTASSO": "A",
    "NEARFORM UK": "B",
    "BOOTH WELSH AUTOMATION": "A",
}
INDEX = sponsor_check.build_name_index(LOOKUP)


def _suggest(name):
    return sponsor_check.suggest_matches(name, LOOKUP, INDEX)


# --- the suggester ----------------------------------------------------------

def test_brand_name_suggests_the_legal_entity():
    assert _suggest("Monzo") == [("MONZO BANK", "A", "prefix")]


def test_trading_as_entity_is_found():
    """The register hides Deliveroo inside 'ROOFOODS LTD T A DELIVEROO'."""
    assert _suggest("Deliveroo") == [
        ("ROOFOODS LTD T A DELIVEROO", "A", "trading-as")]


def test_trading_index_does_not_change_what_is_suggested():
    """The trading-as index is a filter, not a different rule.

    Added 2026-08-15 with the index itself. The trading-as rule used to scan all
    121k register keys per company — 3.5 million regex searches across ~900
    companies, 6.8 of the 7 seconds `sponsor_review.candidates()` took, which is
    far too slow to serve a request. Indexing by the first token after ' T A '
    cuts it 178x.

    The risk in that change is not slowness, it is a silently NARROWER rule: an
    index that misses a bucket drops a real suggestion and nobody notices,
    because a missing suggestion looks exactly like "no match found". So pin the
    equivalence directly rather than trusting the speedup.

    The reference below is the pre-index linear scan, reproduced in full. It
    must NOT be `suggest_matches` with `ta_index` left None: that path builds its
    index with the very function under test, so a broken index breaks both sides
    equally and the comparison passes against a mutant. That is the same trap
    `test_prefix_must_end_on_a_word_boundary` fell into, one function below.
    """
    def reference(company, limit=3):
        key = sponsor_check.normalize_name(company)
        if len(key) < 4 or key in LOOKUP:
            return []
        out = []
        prefix_re = re.compile(r"^%s\b" % re.escape(key))
        for cand in INDEX.get(key.split(" ")[0], []):
            if prefix_re.match(cand):
                out.append((cand, LOOKUP[cand], "prefix"))
                if len(out) >= limit:
                    return out
        trading_re = re.compile(r"\bT A %s\b" % re.escape(key))
        for cand in LOOKUP:                      # the old full scan
            if " T A " in cand and trading_re.search(cand):
                out.append((cand, LOOKUP[cand], "trading-as"))
                if len(out) >= limit:
                    break
        return out[:limit]

    ta = sponsor_check.build_trading_index(LOOKUP)
    for name in ("Deliveroo", "Monzo", "Amazon", "Amentum", "Booth Welsh",
                 "Syntasso", "Nearform", "Roofoods", "Wise", "Catalyst"):
        assert (sponsor_check.suggest_matches(name, LOOKUP, INDEX, ta)
                == reference(name)), name


def test_trading_index_buckets_on_the_trading_name_not_the_legal_entity():
    """Deliveroo must be reachable under DELIVEROO, not under ROOFOODS.

    Bucketing on the first token of the whole key is the obvious mistake, and it
    would break the one rule this index exists to serve — the register records
    ROOFOODS, the job ad says Deliveroo, and the whole point is to bridge them.
    """
    ta = sponsor_check.build_trading_index(LOOKUP)
    assert ta.get("DELIVEROO") == ["ROOFOODS LTD T A DELIVEROO"]
    assert "ROOFOODS" not in ta
    # Entries with no trading-as clause must not appear at all.
    assert "MONZO" not in ta


def test_prefix_must_end_on_a_word_boundary():
    """Without \\b the prefix rule silently becomes a substring rule.

    The case that actually exercises this is a MULTI-token name ending part-way
    through a register token. A single-token miss like "Amentumx" proves nothing
    — the first-token index rejects it before the regex ever runs, which is how
    the first version of this test passed against a mutant with \\b removed.

    "BOOTH WEL" indexes under BOOTH, reaches "BOOTH WELSH AUTOMATION", and is a
    raw prefix of it. Only the boundary stops the match.
    """
    assert _suggest("Booth Wel") == [], "\\b missing — prefix became substring"
    assert _suggest("Booth Welsh") == [
        ("BOOTH WELSH AUTOMATION", "A", "prefix")]


def test_exact_match_suggests_nothing():
    """Syntasso is already found; proposing to it would waste a decision."""
    assert sponsor_check.match_company("Syntasso", LOOKUP)[0] == "yes"
    assert _suggest("Syntasso") == []


def test_very_short_names_are_not_guessed():
    """'BT' or 'EY' would prefix-match half the register."""
    assert _suggest("BT") == []
    assert _suggest("EY") == []


def test_ambiguous_names_return_every_candidate():
    """Amazon is genuinely ambiguous — the human picks, not the matcher."""
    keys = [k for k, _r, _w in _suggest("Amazon")]
    assert "AMAZON UK SERVICES" in keys
    assert "AMAZON CHARITABLE TRUST" in keys, "must not silently pick one"


# --- the alias overlay ------------------------------------------------------

def test_confirmed_alias_turns_a_no_into_a_yes():
    aliases = {sponsor_check.normalize_name("Monzo"):
               {"register_name": "MONZO BANK", "rating": "A"}}
    assert sponsor_check.match_company("Monzo", LOOKUP)[0] == "no"
    assert sponsor_check.match_company("Monzo", LOOKUP, aliases) == (
        "yes", "A", sponsor_check.SKILLED_WORKER_ROUTE)


def test_alias_rating_comes_from_the_register_not_the_alias_file():
    """A stale rating in the overlay must not override the live register."""
    aliases = {sponsor_check.normalize_name("Nearform"):
               {"register_name": "NEARFORM UK", "rating": "A"}}   # wrong, it's B
    assert sponsor_check.match_company("Nearform", LOOKUP, aliases)[1] == "B"


def test_alias_lookup_is_spelling_insensitive():
    """'Monzo Ltd' and 'monzo' are the same company."""
    aliases = {sponsor_check.normalize_name("Monzo"):
               {"register_name": "MONZO BANK", "rating": "A"}}
    for spelling in ("Monzo", "monzo", "Monzo Ltd", "MONZO LIMITED"):
        assert sponsor_check.match_company(spelling, LOOKUP, aliases)[0] == "yes"


def test_unavailable_register_still_says_unconfirmed_not_no():
    """A network failure must never be reported as 'this employer can't sponsor'."""
    assert sponsor_check.match_company("Monzo", None)[0] == "unconfirmed"
    assert sponsor_check.match_company("Monzo", None, {})[0] == "unconfirmed"


def test_malformed_alias_file_is_ignored_not_fatal():
    path = os.path.join(tempfile.mkdtemp(prefix="aliases-"), "a.json")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("{not json")
    assert sponsor_check.load_aliases(path) == {}


def test_alias_file_roundtrips():
    path = os.path.join(tempfile.mkdtemp(prefix="aliases-"), "a.json")
    sponsor_check.save_aliases(
        {"Monzo": {"register_name": "MONZO BANK", "rating": "A",
                   "confirmed": "2026-08-14"}}, path)
    with open(path, encoding="utf-8") as fh:
        assert json.load(fh)["Monzo"]["register_name"] == "MONZO BANK"
    loaded = sponsor_check.load_aliases(path)
    assert loaded[sponsor_check.normalize_name("Monzo")]["rating"] == "A"


# --- the thing that must never break ----------------------------------------

def test_attach_sponsorship_only_touches_the_three_sponsor_columns():
    """Rescoring must never clobber a hand-maintained field.

    status / notes / date_applied / applied_via are typed by hand and are the
    reason the tracker merge is insert-only. A rescore that widened its blast
    radius would silently undo weeks of triage.
    """
    row = {name: "" for name in tracker.FIELDNAMES}
    row.update({
        "company": "Monzo", "title": "Backend Engineer",
        "status": "applied", "notes": "GREEN — verified 2026-08-14",
        "date_applied": "2026-08-14", "applied_via": "company site",
        "sponsor_match": "no",
    })
    aliases = {sponsor_check.normalize_name("Monzo"):
               {"register_name": "MONZO BANK", "rating": "A"}}

    tracker.attach_sponsorship(row, LOOKUP, aliases)

    assert row["sponsor_match"] == "yes"
    assert row["sponsor_rating"] == "A"
    assert row["status"] == "applied"
    assert row["notes"] == "GREEN — verified 2026-08-14"
    assert row["date_applied"] == "2026-08-14"
    assert row["applied_via"] == "company site"


def test_attach_sponsorship_still_works_without_an_alias_argument():
    """Every pre-existing caller passes two arguments; none may break."""
    row = {name: "" for name in tracker.FIELDNAMES}
    row["company"] = "Syntasso"
    tracker.attach_sponsorship(row, LOOKUP)
    assert row["sponsor_match"] == "yes"


def test_ticked_boxes_are_parsed_back_out_of_the_review_file():
    """If this breaks, his ticks silently do nothing and he never finds out.

    The heading level matters: the file groups companies under "## Worth
    deciding" with each company as "###", so a parser keyed on "##" would read
    the section headers as company names and match no ticks at all.
    """
    sys.path.insert(0, os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pipeline"))
    import sponsor_review

    body = "\n".join([
        "# Sponsor review", "",
        "## Worth deciding (2)", "",
        "### Monzo",
        "*3 role(s) tracked, tier 1*",
        "  - Backend Engineer",
        "- [x] `MONZO BANK` (A, prefix)", "",
        "### Amazon",
        "- [ ] `AMAZON CHARITABLE TRUST` (A, prefix)",
        "- [X] `AMAZON UK SERVICES` (A, prefix)", "",
        "## Probably not worth it (1)", "",
        "### Avanti",
        "- [ ] `AVANTI CARE SERVICES` (A, prefix)", "",
    ])
    tmp = os.path.join(tempfile.mkdtemp(prefix="review-"), "sponsor_review.md")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(body)

    original = sponsor_review.REVIEW_MD
    try:
        sponsor_review.REVIEW_MD = __import__("pathlib").Path(tmp)
        confirmed = sponsor_review.read_confirmations()
    finally:
        sponsor_review.REVIEW_MD = original

    assert confirmed.get("Monzo") == "MONZO BANK", confirmed
    assert confirmed.get("Amazon") == "AMAZON UK SERVICES", \
        "a lowercase [x] and an uppercase [X] must both count"
    assert "Avanti" not in confirmed, "an unticked company must not be applied"
    assert "Worth deciding (2)" not in str(confirmed), \
        "section headings must not be read as companies"


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
