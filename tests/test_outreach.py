"""Tests for the outreach drafter.

The failure modes here are social rather than technical, and every one of them
is silent: nothing crashes, a message just goes out that costs him the contact.
So these run over the whole cross-product of signals and job titles rather than
one happy path, because the drafts differ by signal and a template edit only
breaks the branch nobody looked at.

Run:  python tests/test_outreach.py
"""
import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from web.api import outreach  # noqa: E402

#: Every branch the drafter has, plus the empty case.
ALL_SIGNALS = [()] + [(key,) for key, _t, _s in outreach.HOOKS]

TITLES = ("Forward Deployed Software Engineer, New Grad", "Solutions Engineer",
          "Graduate Software Engineer", "Machine Learning Engineer",
          "Data Engineer", "Analytics Engineer", "", "Associate Consultant")

NAMES = ("Priya Deshmukh", "Arnav", "chanon roy", "", "李 明",
         "Jean-Baptiste de la Fontaine")


def every_draft():
    """The full cross-product, so no branch escapes the checks below."""
    for signals in ALL_SIGNALS:
        for title in TITLES:
            for name in NAMES:
                yield outreach.draft(
                    person_name=name, company="Palantir", job_title=title,
                    signal_keys=signals)


def test_no_draft_ever_asks_for_a_referral():
    """The golden rule, enforced rather than remembered.

    A first message that asks for a referral asks a stranger to spend their
    reputation on someone they have never met. This is the single easiest thing
    to reintroduce with a well-meaning template edit, so it is checked on
    generated output rather than on the templates.
    """
    for d in every_draft():
        both = f"{d.note}\n{d.message}".lower()
        for pattern in outreach.ASK_PATTERNS:
            assert not re.search(pattern, both), (
                f"draft for signals={d.hook_key!r} title={d.job_title!r} "
                f"matched {pattern!r}")


def test_the_short_version_always_fits():
    """A note that overflows is silently truncated by the site, mid-sentence."""
    over = [(d.hook_key, d.job_title, len(d.note))
            for d in every_draft() if len(d.note) > outreach.NOTE_LIMIT]
    assert not over, f"{len(over)} note(s) over {outreach.NOTE_LIMIT}: {over[:3]}"


def test_no_draft_trips_his_own_voice_rules():
    """voice-dna.md is the gate every outbound text passes. Text this app
    generates does not get an exemption from it."""
    dirty = [(d.hook_key, d.job_title, d.warnings)
             for d in every_draft() if d.warnings]
    assert not dirty, f"{len(dirty)} draft(s) with warnings: {dirty[:3]}"


def test_no_proper_noun_lands_twice_in_a_row():
    """Regression: the first version said the same name in two consecutive
    sentences.

    "I noticed GTU on your profile. I did my B.Tech at GTU" and "I've been
    reading about Palantir. I've applied at Palantir" both read as generated,
    which defeats the purpose of writing a personal message at all.
    """
    for d in every_draft():
        for word in ("Nottingham", "GTU", "Palantir", "Gujarat", "London"):
            sentences = [s for s in re.split(r"(?<=[.?])\s+", d.note) if s]
            for a, b in zip(sentences, sentences[1:]):
                assert not (word in a and word in b), (
                    f"{word!r} in consecutive sentences (signal="
                    f"{d.hook_key!r}):\n  {a}\n  {b}")


def test_a_draft_states_no_number_about_him():
    """Nothing in `ABOUT` carries a metric, and nothing should acquire one.

    A first message with "0.85 macro-F1" in it is a person reciting their CV at
    a stranger. It is also how an invented figure would get out: the marks and
    metrics are real, but they live in CONTENT_MASTER for a resume, not here.
    """
    for d in every_draft():
        body = d.note + d.message
        # Job titles legitimately contain digits ("Engineer II"), so the check
        # is against the parts this module wrote, not the title passed in.
        if d.job_title:
            body = body.replace(d.job_title, "")
        found = re.findall(r"\d+(?:\.\d+)?%?", body)
        assert not found, f"a number reached the draft: {found} in {body[:120]!r}"


def test_every_hook_is_reachable_and_distinct():
    """A hook nobody can trigger is dead code pretending to be a feature."""
    seen = {}
    for key, _template, _said in outreach.HOOKS:
        d = outreach.draft(person_name="A B", company="Palantir",
                           job_title="Solutions Engineer", signal_keys=[key])
        assert d.hook_key == key, (
            f"signal {key!r} produced hook {d.hook_key!r} — an earlier hook "
            f"in HOOKS shadows it")
        assert d.note not in seen, (
            f"{key!r} and {seen.get(d.note)!r} generate identical text")
        seen[d.note] = key


def test_the_strongest_signal_wins():
    """HOOKS is ordered by how much a stranger would care. Someone who is both
    a Nottingham alum and in London should get the alumni opening."""
    d = outreach.draft(person_name="A B", company="Palantir",
                       job_title="Solutions Engineer",
                       signal_keys=["london", "hindi", "nottingham"])
    assert d.hook_key == "nottingham", d.hook_key


def test_the_question_follows_the_role():
    pairs = [("Forward Deployed Software Engineer", "customer's side"),
             ("Machine Learning Engineer", "models versus"),
             ("Data Engineer", "breaks most often"),
             ("Associate Consultant", "first few months")]
    for title, expected in pairs:
        got = outreach.pick_question(title)
        assert expected in got, f"{title!r} -> {got!r}"


def test_the_referral_check_actually_detects_an_ask():
    """The guard above proves drafts are clean. This proves the guard works,
    rather than the regex simply never matching anything."""
    for bad in ("Could you refer me?", "Would you put me forward for it?",
                "Happy to send my CV if you can vouch for me.",
                "Are there any openings on your team?"):
        assert outreach.check(bad), f"an ask slipped through: {bad!r}"


def test_a_draft_is_written_to_a_file_and_nothing_is_sent():
    """The deliverable is a file he edits and sends himself."""
    d = outreach.draft(person_name="Priya Deshmukh", company="Monzo",
                       job_title="Graduate Software Engineer",
                       signal_keys=["nottingham"], channel="LinkedIn",
                       evidence="bio: University of Nottingham")
    with tempfile.TemporaryDirectory() as tmp:
        path = outreach.write_file(d, Path(tmp))
        assert path.exists()
        text = path.read_text(encoding="utf-8")
        assert d.note in text and d.message in text
        assert "send it yourself" in text.lower(), (
            "the file must carry its own instruction; it will be opened weeks "
            "later with no context")
        assert "University of Nottingham" in text, "the evidence was not shown"

    # There is no send path in this module, and adding one must be deliberate.
    assert not [n for n in dir(outreach)
                if "send" in n.lower() or "smtp" in n.lower()], \
        "something in outreach.py now looks like it sends"


def test_a_persons_name_cannot_escape_the_drafts_folder():
    """Names reach this from GitHub profiles, which anyone can set to anything.

    `candidate_orgs` already had a path-traversal bug in this project from
    exactly this source, so the filename is built from a slug rather than from
    the name.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for hostile in ("../../etc/passwd", r"..\..\windows\system32",
                        "C:/Windows/win.ini", "....//....//x"):
            d = outreach.draft(person_name=hostile, company="Palantir",
                               job_title="Solutions Engineer")
            path = outreach.write_file(d, root)
            assert path.parent.resolve() == root.resolve(), (
                f"{hostile!r} wrote to {path}")


def test_the_evidence_shown_is_the_field_that_actually_matched():
    """Regression: a location match was displayed alongside the person's bio.

    One real profile matched "london" on its `location` field while its bio was
    an Edgar Allan Poe quote, and the panel offered the quote as the reason.
    The instruction on that panel is "check this is a real match before you
    send it", which is unfollowable when the evidence is the wrong field.
    """
    from web.api.referrals import profile

    signals = profile.match("London, United Kingdom",
                            "The best things in life make you sweaty.")
    ev = profile.evidence(signals, location="London, United Kingdom",
                          bio="The best things in life make you sweaty.")
    assert ev["london"].startswith("location:"), ev
    assert "sweaty" not in ev["london"], "showed the bio for a location match"

    # And the drafter shows the evidence for the hook it actually chose.
    d = outreach.draft(person_name="A B", company="Palantir",
                       job_title="Solutions Engineer",
                       signal_keys=[s.key for s in signals],
                       evidence_by_signal=ev)
    assert d.hook_evidence == ev[d.hook_key], (
        f"hook {d.hook_key!r} was shown evidence {d.hook_evidence!r}")


def test_evidence_prefers_the_field_a_signal_really_fired_on():
    """A bio naming the university must not be attributed to the location."""
    from web.api.referrals import profile

    signals = profile.match("Berlin", "MSc at the University of Nottingham")
    ev = profile.evidence(signals, location="Berlin",
                          bio="MSc at the University of Nottingham")
    assert ev["nottingham"].startswith("bio:"), ev


def test_a_handle_is_not_used_as_a_first_name():
    """"Hi xX_dev_Xx," is worse than "Hi,"."""
    assert outreach._first_name("xX_dev_Xx") == "", "used a handle as a name"
    assert outreach._first_name("42Bob") == ""
    assert outreach._first_name("Priya Deshmukh") == "Priya"
    assert outreach._first_name("Arnav") == "Arnav"
    assert outreach._first_name("") == ""


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
