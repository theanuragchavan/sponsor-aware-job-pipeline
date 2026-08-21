"""Deterministic done-condition for this project.

Adapted from the loop-engineering scaffold's `verify_python.py`, keeping its
three exit codes because the distinction earns more here than it did in either
proof-of-concept:

    0  VERIFY PASSED  -- every step ran and every step was green.
    1  VERIFY FAILED  -- every step ran, at least one was red.
    2  CANNOT VERIFY  -- a step could not be run at all. Unknown.

This toolchain is genuinely optional in ways `uv` and `npm` were not. tectonic
lives in the resume repo, poppler is not installed on this machine at all, and
the attestation store sits outside this folder entirely. "The checker could not
run" is a real and frequent state here, and reporting it as "the CV is broken"
would send someone rebuilding a document that was fine.

The steps, and why these four
-----------------------------
Each one closes a failure that has already happened, not a hypothetical:

- **tests** -- 207 of them. The ordinary regression net.
- **CV attestation** -- around forty applications went out on PDFs whose text
  layer was unreadable. The verifier existed and nothing ran it for four days.
- **audit chain** -- an application to Snowflake was logged and the tracker
  ended up back at `status: new`, putting a job already applied to back at #2 on
  the shortlist for five days. Nothing noticed because nothing was asking.
- **claim grounding** -- nothing has ever checked whether outbound prose says
  something CONTENT_MASTER does not.

What this deliberately does NOT do
----------------------------------
It never submits anything. There is no step here that touches an employer, and
there must never be one. Submission is gated separately and explicitly; the
loop covers artifact generation only. A verify script that could submit would
make "the session cannot end until this passes" into "the session cannot end
until it has applied for something", which is the opposite of the point.

Runs every step rather than failing fast, so one invocation shows the whole
picture. Uses `sys.executable` throughout: the Stop hook invokes this with the
same interpreter it runs under, and a bare `python` on this machine resolves to
a venv without openpyxl.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]

EXIT_PASSED = 0
EXIT_FAILED = 1
EXIT_CANNOT_VERIFY = 2

#: verify_claims and reconcile_audit both use 2 for "could not check", so a
#: missing CONTENT_MASTER or an unreadable log propagates as unknown rather
#: than being flattened into a failure.
PROPAGATES_CANNOT_VERIFY = frozenset({"claim grounding", "audit chain",
                                      "CV attestation"})


def steps(draft: str | None, cv: str | None) -> list[tuple[str, list[str]]]:
    """The checks, in cheapest-first order.

    CV and draft steps are included only when there is something to check.
    A gate that fails because you have not yet written a cover letter would be
    disarmed within a day, and a disarmed gate protects nothing.
    """
    out: list[tuple[str, list[str]]] = [
        ("tests", [sys.executable, "tests/run_all.py"]),
        ("audit chain", [sys.executable, "reconcile_audit.py", "--quiet"]),
    ]
    if cv:
        out.append(("CV attestation",
                    [sys.executable, "pipeline/cv_gate.py", cv, "--quiet"]))
    if draft:
        out.append(("claim grounding",
                    [sys.executable, "pipeline/verify_claims.py", draft]))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Deterministic done-condition: tests, attestation, "
                    "audit chain, claim grounding.")
    ap.add_argument("--draft", help="outbound text to ground-check")
    ap.add_argument("--cv", help="the CV PDF being attached (name, path or sha)")
    args = ap.parse_args(argv)

    failed: list[str] = []
    unrunnable: list[str] = []

    for name, cmd in steps(args.draft, args.cv):
        print(f"--- {name} ---", flush=True)
        try:
            result = subprocess.run(cmd, cwd=PROJECT)
        except (FileNotFoundError, NotADirectoryError, PermissionError) as err:
            print(f"CANNOT RUN: {name} -- {cmd[0]}: {err.strerror or err}",
                  flush=True)
            unrunnable.append(f"{name} ({cmd[0]} not runnable)")
            continue

        if result.returncode == 0:
            print(f"PASS: {name}", flush=True)
        elif (result.returncode == EXIT_CANNOT_VERIFY
                and name in PROPAGATES_CANNOT_VERIFY):
            # The step ran and told us it could not answer. Passing that through
            # rather than calling it a failure is the whole reason exit 2 exists.
            print(f"CANNOT VERIFY: {name} (step reported unknown)", flush=True)
            unrunnable.append(f"{name} (could not check)")
        else:
            print(f"FAIL: {name} (exit {result.returncode})", flush=True)
            failed.append(name)

    if unrunnable:
        print(f"\nCANNOT VERIFY: {', '.join(unrunnable)}")
        if failed:
            print(f"(also red, but the run is incomplete: {', '.join(failed)})")
        print("Fix the tooling or supply what is missing, then re-run. "
              "This is not a pass.")
        return EXIT_CANNOT_VERIFY
    if failed:
        print(f"\nVERIFY FAILED: {', '.join(failed)}")
        return EXIT_FAILED
    print("\nVERIFY PASSED")
    return EXIT_PASSED


if __name__ == "__main__":
    sys.exit(main())
