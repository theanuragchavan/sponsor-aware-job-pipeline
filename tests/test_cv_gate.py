"""Tests for the CV gate — the thing that refuses to pack an unverified CV.

The gate exists because of a specific incident: around forty applications went
out on PDFs whose text layer was unreadable, and the verifier written to catch
that was never wired to anything. Worse, the verifier skips its keyword check
when no `.tex` sits beside the PDF, and the files actually uploaded are renamed
copies in `to_send/` with no `.tex` beside them — so the file that reached an
employer was the file the check was least thorough about.

The fix is to key on content, not filename. These tests are mostly about that
one property, because it is the whole reason the gate is not just prose in a
skill file.

No resume repo required: everything below builds its own store in a temp dir.

Run:  python tests/test_cv_gate.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import cv_gate  # noqa: E402


def _fixture(tmp, name="cv.pdf", body=b"%PDF-1.7 pretend"):
    pdf = Path(tmp) / name
    pdf.write_bytes(body)
    return pdf


def _store(pdf, version=cv_gate.REQUIRED_VERSION, path="Master_Resume.pdf"):
    return {cv_gate.sha256_of(pdf): {"path": path,
                                     "verify_version": version,
                                     "terms_checked": 16}}


def test_an_attested_file_passes():
    with tempfile.TemporaryDirectory() as tmp:
        pdf = _fixture(tmp)
        ok, why = cv_gate.verdict(pdf, _store(pdf))
        assert ok, why


def test_attestation_is_keyed_on_content_not_name():
    """The to_send/ hole, stated as a test.

    He renames a build before attaching it. The gate must recognise the renamed
    copy as the same bytes — otherwise it blocks every real send and gets
    switched off — and must not recognise an edited one.
    """
    with tempfile.TemporaryDirectory() as tmp:
        original = _fixture(tmp, "Anurag_Chavan_Master_Resume.pdf")
        store = _store(original)

        renamed = _fixture(tmp, "Anurag_Chavan_CV.pdf",
                           original.read_bytes())
        ok, why = cv_gate.verdict(renamed, store)
        assert ok, "a renamed copy of an attested build must pass: %s" % why

        edited = _fixture(tmp, "Anurag_Chavan_Edited.pdf",
                          original.read_bytes() + b"\x00")
        ok, why = cv_gate.verdict(edited, store)
        assert not ok, "a one-byte edit must not inherit the attestation"


def test_an_unattested_file_is_blocked():
    with tempfile.TemporaryDirectory() as tmp:
        pdf = _fixture(tmp)
        ok, why = cv_gate.verdict(pdf, {})
        assert not ok
        assert "never passed" in why, why


def test_an_older_verify_version_is_rejected():
    """A PDF attested before a check existed was never subjected to it.

    Without this, adding a check silently grandfathers every file already in the
    store — the gate would grow stricter for new builds and stay permissive for
    exactly the ones that predate the fix.
    """
    with tempfile.TemporaryDirectory() as tmp:
        pdf = _fixture(tmp)
        ok, why = cv_gate.verdict(pdf, _store(pdf, version=1))
        assert not ok
        assert "re-verify" in why, why


def test_a_missing_file_is_blocked_not_crashed():
    with tempfile.TemporaryDirectory() as tmp:
        ok, why = cv_gate.verdict(Path(tmp) / "nope.pdf", {})
        assert not ok
        assert "not found" in why, why


def test_a_corrupt_store_blocks_rather_than_throwing():
    """Fail closed. A damaged store must not read as 'nothing to check'."""
    with tempfile.TemporaryDirectory() as tmp:
        broken = Path(tmp) / "attestations.json"
        broken.write_text("{not json", encoding="utf-8")
        assert cv_gate.load(broken) == {}
        pdf = _fixture(tmp)
        ok, _ = cv_gate.verdict(pdf, cv_gate.load(broken))
        assert not ok


def test_main_exits_non_zero_when_any_file_is_blocked():
    with tempfile.TemporaryDirectory() as tmp:
        good = _fixture(tmp, "good.pdf", b"good")
        bad = _fixture(tmp, "bad.pdf", b"bad")
        store_path = Path(tmp) / "attestations.json"
        store_path.write_text(json.dumps(_store(good)), encoding="utf-8")
        os.environ["CV_ATTESTATIONS"] = str(store_path)
        try:
            assert cv_gate.main([str(good)]) == 0
            assert cv_gate.main([str(good), str(bad)]) == 1, \
                "one bad file must fail the whole run"
        finally:
            del os.environ["CV_ATTESTATIONS"]


# --- naming a CV instead of hashing one --------------------------------------
#
# Requiring a 64-character digest is what made recording an application not
# happen: nobody types that at the moment they finish applying, so the tracker
# stayed at 3,448 rows and zero applied.

def _store2(**paths):
    """{name: build_path} -> an attestation store keyed on fake digests."""
    return {("%x" % (i + 1)).rjust(64, "0"):
            {"path": p, "verify_version": cv_gate.REQUIRED_VERSION,
             "attested_at": "2026-08-20T00:0%d:00+01:00" % i}
            for i, p in enumerate(paths.values())}


def test_a_short_name_resolves_to_a_digest():
    store = _store2(a="Anurag_Chavan_Resume_AI_ML.pdf",
                    b="Anurag_Chavan_Resume_SWE.pdf")
    digest, how = cv_gate.resolve("aiml", store)
    assert digest and store[digest]["path"].endswith("AI_ML.pdf"), how


def test_short_names_drop_the_tokens_every_build_shares():
    """`Master_Resume` must not shorten to `resume` — that names nothing."""
    assert cv_gate.short_name("Anurag_Chavan_Master_Resume.pdf") == "master"
    assert cv_gate.short_name("Anurag_Chavan_Resume_AI_ML.pdf") == "aiml"
    assert cv_gate.short_name("Anurag_Chavan_Resume_DataScience.pdf") == "datascience"


def test_the_same_build_reattested_is_not_ambiguity():
    """Tectonic is not byte-reproducible, so one CV accumulates many digests.

    Treating those as competing candidates would make every short name
    permanently ambiguous after the second rebuild. The newest wins.
    """
    store = {
        "a" * 64: {"path": "Anurag_Chavan_Resume_AI_ML.pdf",
                   "verify_version": cv_gate.REQUIRED_VERSION,
                   "attested_at": "2026-08-19T03:00:00+01:00"},
        "b" * 64: {"path": "Anurag_Chavan_Resume_AI_ML.pdf",
                   "verify_version": cv_gate.REQUIRED_VERSION,
                   "attested_at": "2026-08-20T02:00:00+01:00"},
    }
    digest, _how = cv_gate.resolve("aiml", store)
    assert digest == "b" * 64, "the newest attestation of a build must win"


def test_two_different_builds_matching_is_refused_not_guessed():
    """Same rule the sponsor matcher follows: propose, never decide.

    Guessing here would attach the wrong document to the record that later has
    to explain an outcome.
    """
    store = _store2(a="Anurag_Chavan_Resume_AI_ML.pdf",
                    b="Anurag_Chavan_Resume_AI_Research.pdf")
    digest, why = cv_gate.resolve("ai", store)
    assert not digest
    assert "be specific" in why, why


def test_an_unknown_name_lists_what_is_available():
    store = _store2(a="Anurag_Chavan_Resume_SWE.pdf")
    digest, why = cv_gate.resolve("nonsense", store)
    assert not digest and "swe" in why, why


def test_a_file_path_is_hashed_on_the_spot():
    with tempfile.TemporaryDirectory() as tmp:
        pdf = _fixture(tmp, "whatever.pdf", b"bytes")
        digest, how = cv_gate.resolve(str(pdf), {})
        assert digest == cv_gate.sha256_of(pdf), how


def test_a_digest_passes_straight_through():
    assert cv_gate.resolve("A" * 64, {})[0] == "a" * 64


def test_superseded_builds_are_not_offered_by_name():
    """An older verify_version means the file predates a check. Not selectable."""
    store = {"a" * 64: {"path": "Anurag_Chavan_Resume_SWE.pdf",
                        "verify_version": cv_gate.REQUIRED_VERSION - 1}}
    assert cv_gate.resolve("swe", store)[0] == ""


def test_the_store_path_carries_no_personal_path():
    """This repo is public. The default must be derived, not hardcoded."""
    source = Path(cv_gate.__file__).read_text(encoding="utf-8")
    assert "D:\\Resume\\.cv_attestations" not in source
    os.environ.pop("CV_ATTESTATIONS", None)
    assert cv_gate.attestations_path().name == ".cv_attestations.json"


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
