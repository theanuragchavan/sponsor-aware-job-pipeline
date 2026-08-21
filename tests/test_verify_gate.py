"""Tests for the composed gate — the thing the Stop hook actually runs.

Two properties matter here and neither is about any individual check.

**Cannot-verify must survive composition.** Each sub-check has three exit codes,
and it would be easy for `verify.py` to collapse them into pass/fail on the way
up. Then "poppler is not installed" arrives as "the CV is broken" and someone
rebuilds a document that was fine. Unrunnable outranks failed for the same
reason it does in the loop scaffold: partial evidence is not evidence.

**The gate must never be able to submit.** This is the one that would matter
most if it broke. The loop's premise is "the session cannot end until verify
passes"; if verify could submit an application, that premise becomes "the
session cannot end until it has applied for something". Asserted against the
source text, because it is a property of what the file is allowed to contain.

Run:  python tests/test_verify_gate.py
"""
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import verify  # noqa: E402


# --- composition ----------------------------------------------------------

def test_the_steps_that_need_input_are_skipped_without_it():
    """A gate that fails because no cover letter exists yet gets disarmed.

    And a disarmed gate protects nothing, which is a worse outcome than not
    checking a draft that was never written.
    """
    bare = [name for name, _ in verify.steps(None, None)]
    assert bare == ["tests", "audit chain"], bare

    full = [name for name, _ in verify.steps("d.md", "solutions")]
    assert "CV attestation" in full and "claim grounding" in full, full


def test_every_step_runs_the_same_interpreter():
    """A bare `python` here resolves to a venv without openpyxl on this
    machine, and the Stop hook invokes this with its own interpreter."""
    for _, cmd in verify.steps("d.md", "cv"):
        assert cmd[0] == sys.executable, cmd


def test_the_three_exit_codes_are_distinct():
    assert len({verify.EXIT_PASSED, verify.EXIT_FAILED,
                verify.EXIT_CANNOT_VERIFY}) == 3


def test_cannot_verify_propagates_rather_than_becoming_a_failure():
    """The sub-checks that can honestly answer 'I could not check'."""
    assert "claim grounding" in verify.PROPAGATES_CANNOT_VERIFY
    assert "audit chain" in verify.PROPAGATES_CANNOT_VERIFY
    assert "CV attestation" in verify.PROPAGATES_CANNOT_VERIFY
    # tests is not in the set: pytest exiting non-zero is a real failure.
    assert "tests" not in verify.PROPAGATES_CANNOT_VERIFY


def test_a_missing_source_reports_unknown_not_broken():
    """End to end, with a draft whose CONTENT_MASTER does not exist."""
    out = subprocess.run(
        [sys.executable, str(ROOT / "pipeline" / "verify_claims.py"),
         str(ROOT / "tests" / "test_verify_gate.py"),
         "--source", str(ROOT / "no-such-content-master.md")],
        capture_output=True, text=True, cwd=ROOT)
    assert out.returncode == verify.EXIT_CANNOT_VERIFY, out.stdout
    assert "CANNOT VERIFY" in out.stdout, out.stdout


# --- the boundary ---------------------------------------------------------

def test_the_gate_cannot_submit_anything():
    """The loop covers artifact generation. Submission is gated separately.

    If verify could submit, "the session cannot end until verify passes" would
    become "the session cannot end until it has applied for something".
    """
    source = (ROOT / "scripts" / "verify.py").read_text(encoding="utf-8")
    body = source.split('"""', 2)[-1]          # skip the module docstring
    for forbidden in ("requests.", "urlopen", "httpx", "webbrowser",
                      "playwright", "selenium", "submit("):
        assert forbidden not in body, f"verify.py must not reach the outside: {forbidden}"


def test_the_gate_only_runs_this_project():
    """Least privilege: it invokes the project's own checks, nothing else."""
    for _, cmd in verify.steps("d.md", "cv"):
        target = cmd[1]
        assert not os.path.isabs(target), cmd
        assert ".." not in target, cmd


# --- the hook wiring ------------------------------------------------------

def test_the_stop_hook_is_registered():
    import json
    settings = json.loads(
        (ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
    hooks = settings["hooks"]["Stop"][0]["hooks"]
    assert any("stop_gate.py" in h["command"] for h in hooks), hooks


def test_the_gate_script_matches_the_scaffold_source():
    """No local edits to the deployed copy.

    loop-engineering lost 2 days 21 hours to exactly that, because a session
    working in the skills directory edited the nearest copy. Convention lost to
    proximity, so this asserts rather than trusting.
    """
    import hashlib
    here = ROOT / ".claude" / "hooks" / "stop_gate.py"
    upstream = Path(r"D:\loop-engineering\skill\templates\stop_gate.py")
    if not upstream.exists():
        return                                  # scaffold not on this machine
    assert (hashlib.sha256(here.read_bytes()).hexdigest()
            == hashlib.sha256(upstream.read_bytes()).hexdigest()), \
        "stop_gate.py has drifted from the scaffold template"


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
