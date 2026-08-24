"""Tests for the ten conditions that decide whether a machine may submit.

This is the most safety-critical file in the repo. Everything else prepares an
application; this decides whether a person has to look at it before it reaches
an employer. So the tests are adversarial rather than confirmatory: each one
asks how the gate could wrongly say yes.

Three properties carry most of the weight.

**It fails closed.** A missing screening file, a malformed one, an unknown
question -- every unknown state blocks. A gate that opens when it cannot answer
is worse than no gate, because it looks like protection.

**Condition 8 cannot be talked around.** Every form answer is read from a file
written by hand. An agent optimising for "submit successfully" has a real
incentive to answer the sponsorship question the way that gets through, and
would be right to on its own terms. A lookup table has no terms.

**All ten are required.** Nine passing is not nine-tenths of a submission; it
is a stop. Tested by knocking out each condition individually.

Run:  python tests/test_autosubmit_gate.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from package import autosubmit_gate as gate  # noqa: E402

SCREENING = """
version: 1
answers:
  - id: requires_sponsorship
    matches: ["require sponsorship", "need sponsorship", "sponsorship to work"]
    type: boolean_yes
    answer: "Yes"
  - id: right_to_work
    matches: ["right to work", "authorised to work"]
    not_if: ["without sponsorship", "united states"]
    type: text
    answer: "A paragraph about immigration status."
  - id: criminal_convictions
    matches: ["criminal conviction"]
    type: boolean_no
    answer: "No"
  - id: salary_expectation
    matches: ["salary expectation"]
    type: text
    answer: "I would rather hear the range."
    needs_input: true
    needs_input_reason: "Depends whether the posting states a range."
never_auto:
  - "why do you want to work"
  - "tell us about yourself"
"""


def _screening(tmp, body=SCREENING):
    p = Path(tmp) / "screening.yml"
    p.write_text(body, encoding="utf-8")
    return p


def _job(**over):
    row = {"id": "lever:abc123", "company": "Palantir",
           "title": "Forward Deployed Software Engineer",
           "status": "new", "sponsor_match": "yes", "sponsor_rating": "A"}
    row.update(over)
    return row


def _evaluate(tmp, job=None, *, questions=(), cv_ref="", draft=None,
              recorded=("lever",), cap=3, log=None):
    """Evaluate with the CV and letter steps stubbed out.

    Those two shell out to cv_gate and verify_claims, which need the real
    attestation store and CONTENT_MASTER. They have their own suites; what is
    under test here is the composition.
    """
    return gate.evaluate(
        job or _job(), draft=draft, cv_ref=cv_ref, questions=list(questions),
        screening_path=_screening(tmp), recorded_ats=recorded, daily_cap=cap,
        log_path=Path(log) if log else Path(tmp) / "no-log.jsonl",
        today="2026-08-21")


def _result(verdict, rule):
    return next(c["result"] for c in verdict["conditions"] if c["rule"] == rule)


# --- the shape of the verdict ---------------------------------------------

def test_all_ten_conditions_are_reported():
    with tempfile.TemporaryDirectory() as tmp:
        v = _evaluate(tmp)
        assert len(v["conditions"]) == 10, [c["rule"] for c in v["conditions"]]


def test_both_outcomes_are_explained():
    """An approved application must be as auditable as a blocked one.

    The approved ones are the ones you have to explain later.
    """
    with tempfile.TemporaryDirectory() as tmp:
        v = _evaluate(tmp)
        for c in v["conditions"]:
            assert c["result"] in ("pass", "fail"), c
            assert "rule" in c and "detail" in c, c


def test_autosubmit_requires_every_condition():
    with tempfile.TemporaryDirectory() as tmp:
        v = _evaluate(tmp)
        assert v["autosubmit"] is (not v["blocked_by"]), v


# --- condition 8: the answers ---------------------------------------------

def test_the_sponsorship_question_resolves_however_a_form_words_it():
    with tempfile.TemporaryDirectory() as tmp:
        s = gate.load_screening(_screening(tmp))
        for q in ("Do you now, or will you in the future, require sponsorship?",
                  "Will you need sponsorship?",
                  "Do you require sponsorship to work in the UK?"):
            state, _, kind = gate.resolve_question(q, s,
                                                   widget="boolean_radio")
            assert (state, kind) == ("answer", "exact"), (q, state, kind)


def test_an_unknown_question_blocks_rather_than_being_guessed():
    with tempfile.TemporaryDirectory() as tmp:
        v = _evaluate(tmp, questions=["How many years of Kubernetes?"])
        assert _result(v, "answers_on_file") == "fail", v


def test_a_needs_input_answer_blocks():
    """Half an answer is not an answer.

    Salary depends on whether the posting states a range, and a number typed
    into a form is not retractable.
    """
    with tempfile.TemporaryDirectory() as tmp:
        v = _evaluate(tmp, questions=["What are your salary expectations?"])
        assert _result(v, "answers_on_file") == "fail", v


def test_free_prose_is_refused_not_attempted():
    """Unsupervised writing about an employer is where fabrication enters.

    The grounding gate cannot check text that does not exist yet.
    """
    with tempfile.TemporaryDirectory() as tmp:
        v = _evaluate(tmp, questions=["Why do you want to work at Palantir?"])
        assert _result(v, "no_free_text") == "fail", v


# --- condition 8: a hit is not yet an answer -------------------------------
#
# Found 2026-08-24, by reading a form filler that types its answers and filters
# its lookup on field type. Ours matched a phrase anywhere in the question and
# took the first entry that hit, which is three different mistakes.

def test_a_right_to_work_question_that_means_sponsorship_is_refused():
    """The exact wording the old matcher got wrong.

    "Do you have the right to work in the UK without requiring sponsorship?"
    is a yes/no field whose truthful answer is No. The old code answered it
    with the right-to-work paragraph and condition 8 passed. This is the one
    question the whole design exists to protect.
    """
    with tempfile.TemporaryDirectory() as tmp:
        s = gate.load_screening(_screening(tmp))
        state, why, kind = gate.resolve_question(
            "Do you have the right to work in the UK without sponsorship?",
            s, widget="boolean_radio")
        assert state == "unknown", (state, why)
        assert kind == "vetoed", kind

        v = _evaluate(tmp, questions=[{
            "question": "Do you have the right to work in the UK "
                        "without sponsorship?",
            "widget": "boolean_radio"}])
        assert _result(v, "answers_on_file") == "fail", v


def test_another_countrys_work_authorisation_is_not_answered_from_this_file():
    """A UK sponsor can run one global Workday tenant, so this gets asked."""
    with tempfile.TemporaryDirectory() as tmp:
        s = gate.load_screening(_screening(tmp))
        state, _, kind = gate.resolve_question(
            "Are you authorised to work in the United States?", s,
            widget="boolean_radio")
        assert (state, kind) == ("unknown", "vetoed"), (state, kind)


def test_a_paragraph_is_never_offered_to_a_yes_no_field():
    """Type is declared in the file and was never read.

    Whatever a filler does with a paragraph on a two-option radio, the file
    did not tell it to do that.
    """
    with tempfile.TemporaryDirectory() as tmp:
        s = gate.load_screening(_screening(tmp))
        state, why, kind = gate.resolve_question(
            "What is your right to work?", s, widget="boolean_radio")
        assert (state, kind) == ("unknown", "type_mismatch"), (state, kind, why)


def test_a_boolean_answer_does_fit_a_radio():
    """The gate has to be able to say yes, or it is not a gate."""
    with tempfile.TemporaryDirectory() as tmp:
        s = gate.load_screening(_screening(tmp))
        state, detail, kind = gate.resolve_question(
            "Do you require sponsorship to work in the UK?", s,
            widget="boolean_radio")
        assert (state, kind) == ("answer", "exact"), (state, kind)
        assert detail == "requires_sponsorship"

        v = _evaluate(tmp, questions=[{
            "question": "Do you require sponsorship to work in the UK?",
            "widget": "boolean_radio"}])
        assert _result(v, "answers_on_file") == "pass", v


def test_two_matching_answers_is_ambiguous_rather_than_first_wins():
    """File order is not a tiebreak. It is an accident of transcription."""
    with tempfile.TemporaryDirectory() as tmp:
        s = gate.load_screening(_screening(tmp))
        state, why, kind = gate.resolve_question(
            "What is your right to work, and do you need sponsorship?", s,
            widget="text")
        assert (state, kind) == ("unknown", "ambiguous"), (state, kind)
        assert "requires_sponsorship" in why and "right_to_work" in why, why


def test_an_answer_never_matched_to_a_field_does_not_pass():
    """A bare question string means nobody looked at the field.

    The answer is genuinely on file, so a package built for a person to read
    still carries it. The gate treats "not checked" as "not a fit".
    """
    with tempfile.TemporaryDirectory() as tmp:
        s = gate.load_screening(_screening(tmp))
        state, _, kind = gate.resolve_question(
            "Do you require sponsorship?", s)
        assert (state, kind) == ("answer", "widget_unknown"), (state, kind)

        v = _evaluate(tmp, questions=["Do you require sponsorship?"])
        assert _result(v, "answers_on_file") == "fail", v


def test_an_answer_with_no_declared_type_fails_closed():
    with tempfile.TemporaryDirectory() as tmp:
        s = gate.load_screening(_screening(tmp, """
version: 1
answers:
  - id: untyped
    matches: ["notice period"]
    answer: "Immediately."
never_auto: []
"""))
        state, _, kind = gate.resolve_question("What is your notice period?", s,
                                               widget="text")
        assert (state, kind) == ("unknown", "type_unknown"), (state, kind)


def test_a_textarea_nothing_answers_is_free_prose():
    """The second way to be free prose, and only visible because the widget is.

    An employer's open box need not use any never-auto phrasing to be an
    open box.
    """
    with tempfile.TemporaryDirectory() as tmp:
        v = _evaluate(tmp, questions=[{
            "question": "Anything further for the hiring panel?",
            "widget": "textarea"}])
        assert _result(v, "no_free_text") == "fail", v


def test_a_misspelled_widget_is_refused_not_ignored():
    """A typo must not quietly become "widget unobserved", which looks like
    caution and behaves like it until someone fixes the typo."""
    import argparse
    with_widget = gate.parse_question("Do you require sponsorship?::text")
    assert with_widget["widget"] == "text", with_widget
    try:
        gate.parse_question("Do you require sponsorship?::bolean_radio")
    except argparse.ArgumentTypeError:
        return
    raise AssertionError("a bad widget name was accepted")


def test_a_missing_screening_file_fails_closed():
    """No answers on file is not "no questions to answer"."""
    with tempfile.TemporaryDirectory() as tmp:
        v = gate.evaluate(_job(), screening_path=Path(tmp) / "nope.yml",
                          recorded_ats=("lever",), today="2026-08-21",
                          log_path=Path(tmp) / "no-log.jsonl")
        assert _result(v, "answers_on_file") == "fail", v


def test_a_malformed_screening_file_fails_closed():
    """A YAML syntax error must block, not crash and not pass."""
    with tempfile.TemporaryDirectory() as tmp:
        v = _evaluate(tmp, questions=["Do you require sponsorship?"],
                      )
        assert v is not None
        bad = _screening(tmp, "answers: [unclosed\n  - nope")
        v2 = gate.evaluate(_job(), screening_path=bad, recorded_ats=("lever",),
                           today="2026-08-21",
                           log_path=Path(tmp) / "no-log.jsonl")
        assert _result(v2, "answers_on_file") == "fail", v2


# --- the other nine, knocked out one at a time -----------------------------

def test_an_unrecorded_ats_blocks():
    """Cowork has no session for this form shape, so it cannot drive it."""
    with tempfile.TemporaryDirectory() as tmp:
        v = _evaluate(tmp, recorded=())
        assert _result(v, "ats_recorded") == "fail", v


def test_an_unconfirmed_sponsor_blocks():
    """The register, not the job ad's claim. His #1 filter."""
    with tempfile.TemporaryDirectory() as tmp:
        for value in ("no", "unconfirmed", ""):
            v = _evaluate(tmp, _job(sponsor_match=value))
            assert _result(v, "sponsor_confirmed") == "fail", value


def test_a_dead_posting_blocks():
    with tempfile.TemporaryDirectory() as tmp:
        v = _evaluate(tmp, _job(_liveness="dead"))
        assert _result(v, "posting_live") == "fail", v


def test_an_already_applied_job_blocks():
    """The Snowflake failure, structurally.

    That row went back to status new and returned to the shortlist for five
    days. If it had also been auto-submittable, it would have been applied to
    twice.
    """
    with tempfile.TemporaryDirectory() as tmp:
        for status in ("applied", "screening", "interview", "rejected"):
            v = _evaluate(tmp, _job(status=status))
            assert _result(v, "not_already_applied") == "fail", status


def test_the_daily_cap_blocks():
    """Volume is what makes a pipeline look like spray-and-pray."""
    with tempfile.TemporaryDirectory() as tmp:
        log = Path(tmp) / "log.jsonl"
        log.write_text("\n".join(
            json.dumps({"type": "application.logged",
                        "ts": "2026-08-21T10:00:00Z"}) for _ in range(3)),
            encoding="utf-8")
        v = _evaluate(tmp, cap=3, log=log)
        assert _result(v, "under_daily_cap") == "fail", v


def test_yesterdays_applications_do_not_count_against_today():
    with tempfile.TemporaryDirectory() as tmp:
        log = Path(tmp) / "log.jsonl"
        log.write_text(json.dumps({"type": "application.logged",
                                   "ts": "2026-08-20T10:00:00Z"}),
                       encoding="utf-8")
        v = _evaluate(tmp, cap=1, log=log)
        assert _result(v, "under_daily_cap") == "pass", v


def test_no_cv_named_blocks():
    with tempfile.TemporaryDirectory() as tmp:
        v = _evaluate(tmp, cv_ref="")
        assert _result(v, "cv_attested") == "fail", v


def test_no_draft_blocks():
    with tempfile.TemporaryDirectory() as tmp:
        v = _evaluate(tmp, draft=None)
        assert _result(v, "letter_grounded") == "fail", v


# --- the default posture ---------------------------------------------------

def test_no_ats_is_recorded_by_default():
    """Shipping with a family pre-enabled would auto-submit on a fresh clone.

    Widened by recording a session, never by editing a list.
    """
    assert gate.RECORDED_ATS == (), gate.RECORDED_ATS


def test_the_daily_cap_is_small_by_default():
    assert 1 <= gate.DAILY_CAP <= 5, gate.DAILY_CAP


def test_an_empty_question_is_unknown_not_answered():
    with tempfile.TemporaryDirectory() as tmp:
        s = gate.load_screening(_screening(tmp))
        assert gate.resolve_question("", s)[0] == "unknown"
        assert gate.resolve_question("   ", s)[0] == "unknown"


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
