"""cv_gate.py — refuse to build an application pack around an unverified CV.

The CV verifier lives in the resume repo and needs pypdf and the LaTeX source.
This does not: it hashes a PDF and asks whether that exact content has a
recorded pass. That keeps the two repos independent — Adzuna never learns what
a ligature is — and it closes the hole that made the original verifier
decorative.

The hole: `verify_cv.py` skips its keyword check when no `.tex` sits beside the
PDF, and the files actually uploaded are renamed copies in `to_send/` with no
`.tex` beside them. So the file that reached an employer was the file the gate
half-checked. Keying on sha256 fixes that in both directions — a renamed copy
of an attested build passes on its content, and a copy edited by one byte does
not pass at all.

Usage:
    python pipeline\\cv_gate.py D:\\Resume\\to_send\\Anurag_Chavan_CV.pdf

Exit 0 = every named file has a current attestation. Non-zero = do not send.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

ADZUNA_HOME = Path(__file__).resolve().parent.parent

# Must match VERIFY_VERSION in the resume repo's scripts/verify_cv.py. The two
# are deliberately separate constants: this is the contract between the repos,
# and a mismatch should fail loudly rather than be papered over by an import.
# Raising it there invalidates every stored attestation, which is the point —
# a PDF attested before a check existed was never subjected to it.
REQUIRED_VERSION = 3


def attestations_path() -> Path:
    """Where the resume repo records what passed.

    Resolved from the environment first so this file carries no personal path,
    then from a Resume/ folder beside this repo, which is the real layout.
    """
    env = os.environ.get("CV_ATTESTATIONS")
    if env:
        return Path(env)
    return ADZUNA_HOME.parent / "Resume" / ".cv_attestations.json"


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def verdict(pdf: Path, store: dict) -> tuple[bool, str]:
    """(ok, reason). Reason is written for someone about to send the file."""
    if not pdf.exists():
        return False, "file not found"
    record = store.get(sha256_of(pdf))
    if record is None:
        return False, ("this exact file has never passed verify_cv.py "
                       "(content not attested)")
    got = record.get("verify_version")
    if got != REQUIRED_VERSION:
        return False, ("attested by verify_cv v%s, current is v%d — re-verify, "
                       "the checks have changed since" % (got, REQUIRED_VERSION))
    return True, "attested (built as %s)" % record.get("path", "unknown")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Refuse unverified CV PDFs before an application is packed")
    ap.add_argument("pdfs", nargs="+")
    ap.add_argument("--quiet", action="store_true",
                    help="print only failures")
    args = ap.parse_args(argv)

    store_path = attestations_path()
    store = load(store_path)
    failed = 0

    for name in args.pdfs:
        pdf = Path(name)
        ok, reason = verdict(pdf, store)
        if ok:
            if not args.quiet:
                print("OK    %-46s %s" % (pdf.name, reason))
        else:
            failed += 1
            print("BLOCK %-46s %s" % (pdf.name, reason))

    if failed:
        print()
        print("Do not send. Rebuild and re-verify:")
        print("    <python313> D:\\Resume\\scripts\\verify_cv.py <pdf> --attest")
        if not store:
            print("(no attestation store at %s)" % store_path)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
