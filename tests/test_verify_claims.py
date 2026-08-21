"""Tests for the grounding gate.

The CV gate proves a document is readable. This is the one that proves it is
true, and the assertions worth having are the adversarial ones: four ways to
lie, each of which passed some earlier version of this file.

Every fixture builds its own miniature CONTENT_MASTER. Running against the real
52 KB one would make these slow and would couple them to content that changes.

Run:  python tests/test_verify_claims.py
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import verify_claims as vc  # noqa: E402

SOURCE = """# Content Master

## 7. Quantified metrics

| Value | What it measures | Source | TARGET or ACHIEVED |
|---|---|---|---|
| **0.85** | classifier macro-F1 | code | ACHIEVED |
| **2.4M** | tweets ingested | inventory | ACHIEVED |
| **20 lakhs INR** | pharma revenue goal | doc | **TARGET** |
| **>90%** | pharma accuracy goal | doc | **TARGET** |
| **17** | live GitHub repos | inventory | ACHIEVED |

I worked at Mavera and studied at Nottingham, using Python and PyTorch.
"""


def _check(draft, source=SOURCE, **kw):
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "cm.md"
        p.write_text(source, encoding="utf-8")
        text, status = vc.load_source(p)
    return vc.check(draft, text, status, **kw)


# --- grounding ------------------------------------------------------------

def test_a_number_in_the_source_is_grounded():
    r = _check("I trained a classifier to 0.85 macro-F1 at Mavera using Python.")
    assert r["ungrounded"] == [], r


def test_an_invented_number_is_caught():
    r = _check("I trained a classifier to 0.97 macro-F1 at Mavera using Python.")
    assert "0.97" in r["ungrounded"], r


def test_an_inflated_scale_is_caught():
    """The failure career-ops' magnitude-suffix bug used to let through."""
    r = _check("I processed 4.2M conversations at Mavera using Python.")
    assert "4.2m" in r["ungrounded"], r


def test_the_magnitude_suffix_belongs_to_the_number():
    """Without this, '2.4M tweets' normalises to '2.4' and matches nothing.

    career-ops shipped the inverse bug: '50k users' became '50', so a 1000x
    inflation passed while a smaller one was caught.
    """
    assert vc.extract_numbers("2.4M tweets") == ["2.4m"]
    assert vc.extract_numbers("50k users") == ["50k"]
    r = _check("I ingested 2.4M tweets at Mavera with Python.")
    assert r["ungrounded"] == [], r


def test_a_wider_modifier_window_still_extracts_the_claim():
    """career-ops' window of 2 dropped three-modifier phrasings entirely.

    A dropped claim is worse than a wrong one: with nothing extracted on the
    draft side there is nothing to compare, so a changed number passes.
    """
    assert vc.MODIFIER_WINDOW >= 4
    r = _check("I shipped 5.5M live streaming pipeline events at Mavera in Python.")
    assert "5.5m" in r["ungrounded"], r


def test_a_year_is_not_a_claim():
    r = _check("Since 2024 I have worked at Mavera with Python and PyTorch.")
    assert r["ungrounded"] == [], r


# --- the target/achieved distinction --------------------------------------

def test_a_target_quoted_as_an_outcome_is_caught():
    """The subtlest of the four, and the one that survived v1.

    "20 lakhs INR" IS in CONTENT_MASTER, so an appears-or-not check passes it
    while the sentence claims a result that never happened.
    """
    r = _check("At Mavera I delivered 20 lakhs INR in revenue using Python.")
    assert r["target_as_result"], r
    assert r["target_as_result"][0]["value"] == "20lakh", r


def test_a_target_mentioned_as_a_goal_is_fine():
    r = _check("The Mavera project targeted 20 lakhs INR in revenue, in Python.")
    assert r["target_as_result"] == [], r


def test_an_achieved_number_quoted_as_an_outcome_is_fine():
    r = _check("At Mavera I achieved 0.85 macro-F1 with Python and PyTorch.")
    assert r["target_as_result"] == [], r


def test_the_lakh_suffix_does_not_hide_a_target():
    """The v1 bug: the matcher searched for the literal '20lakh'.

    Canonical form carries a suffix the prose does not, so every
    lakh/crore/million TARGET slipped through silently.
    """
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "cm.md"
        p.write_text(SOURCE, encoding="utf-8")
        _, status = vc.load_source(p)
    assert status.get("20lakh") == "TARGET", status


# --- the substance floor ---------------------------------------------------

def test_a_gutted_draft_is_refused():
    """The escape route: an agent one attempt from a blocking gate deletes
    the offending sentence rather than fixing it."""
    r = _check("Dear Hiring Manager, I am applying. Best regards.")
    assert r["below_floor"], r


def test_evidence_counts_names_not_only_digits():
    """A good cover letter argues in prose.

    v1 counted numbers and demanded three, which failed the real Syntasso
    letter -- one figure, five named specifics. Counting digits would push
    drafts toward statistics they do not need.
    """
    r = _check("At Mavera I used Python and PyTorch on the Subway project.")
    assert not r["below_floor"], r
    assert r["claim_count"] == 0, r
    assert r["evidence_count"] >= 3, r


def test_the_floor_does_not_move_with_the_draft():
    """Flagship 2's volume floor rejected a correct recovery because it was
    anchored to an inflated baseline. A rolling floor cannot tell 'saying less
    because the previous draft was wrong' from 'saying less to dodge the gate'.

    Asserted behaviourally: the same draft gets the same verdict regardless of
    what was checked before it.
    """
    rich = "At Mavera I hit 0.85 macro-F1 with Python, PyTorch and Nottingham."
    thin = "Dear Hiring Manager, I am applying. Best regards."
    assert _check(thin)["below_floor"] is True
    assert _check(rich)["below_floor"] is False
    # Re-check the thin one after the rich one: unchanged.
    assert _check(thin)["below_floor"] is True


# --- the verdict contract --------------------------------------------------

def test_both_outcomes_are_explained():
    """An accepted draft should be as auditable as a rejected one."""
    good = _check("At Mavera I hit 0.85 macro-F1 with Python and PyTorch.")
    code, lines = vc.verdict(good)
    assert code == vc.EXIT_GROUNDED
    assert len(lines) == 3 and all(l.startswith("PASS") for l in lines), lines

    bad = _check("At Mavera I hit 0.99 macro-F1 with Python and PyTorch.")
    code, lines = vc.verdict(bad)
    assert code == vc.EXIT_UNGROUNDED
    assert any(l.startswith("FAIL") for l in lines), lines


def test_typographic_differences_are_not_findings():
    """A PDF extractor emits curly quotes; a markdown source does not."""
    assert vc.normalise("I’m at 0.85 — good") == "I'm at 0.85 - good"


def test_cannot_verify_when_the_source_is_missing():
    """No CONTENT_MASTER is not the same fact as nothing to find."""
    with tempfile.TemporaryDirectory() as tmp:
        draft = Path(tmp) / "d.md"
        draft.write_text("At Mavera I hit 0.85 macro-F1.", encoding="utf-8")
        code = vc.main([str(draft), "--source", str(Path(tmp) / "nope.md")])
        assert code == vc.EXIT_CANNOT_VERIFY, code


def test_cannot_verify_when_the_draft_is_missing():
    with tempfile.TemporaryDirectory() as tmp:
        code = vc.main([str(Path(tmp) / "nope.md")])
        assert code == vc.EXIT_CANNOT_VERIFY, code


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
