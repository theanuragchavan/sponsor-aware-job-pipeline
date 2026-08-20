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
import re
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


HEX64 = re.compile(r"^[0-9a-f]{64}$")

#: Tokens every build shares, so they carry no information in a short name.
NOISE = {"anurag", "chavan", "resume", "cv", "pdf"}


def short_name(build_path: str) -> str:
    """`Anurag_Chavan_Resume_AI_ML.pdf` -> `aiml`. The part that distinguishes it."""
    stem = re.split(r"[\\/]", str(build_path))[-1]
    stem = re.sub(r"\.pdf$", "", stem, flags=re.I)
    parts = [p for p in re.split(r"[_\-\s]+", stem)
             if p and p.lower() not in NOISE]
    return re.sub(r"[^a-z0-9]", "", "".join(parts).lower()) or "cv"


def current_builds(store: dict) -> dict:
    """{short_name: (sha256, record)} — one entry per build, newest attestation.

    Rebuilding is routine and tectonic is not byte-reproducible, so the store
    accumulates several live hashes for the same document. Those are the same CV
    attested again, not competing candidates, and treating them as ambiguity
    would make every short name unusable. Different *builds* colliding is real
    ambiguity; the same build re-attested is not.
    """
    out: dict[str, tuple[str, dict]] = {}
    for digest, rec in store.items():
        if rec.get("verify_version") != REQUIRED_VERSION:
            continue
        key = short_name(rec.get("path", ""))
        seen = out.get(key)
        if seen is None or str(rec.get("attested_at", "")) > str(
                seen[1].get("attested_at", "")):
            out[key] = (digest, rec)
    return out


def resolve(ref: str, store: dict) -> tuple[str, str]:
    """Turn a human's way of naming a CV into a sha256. ("", why) if it cannot.

    Three forms, because a person recording an application has a filename or a
    role in mind, not a hash:

        b1ca8749...          a digest, used as-is
        D:\\Resume\\...\\x.pdf   a file on disk, hashed now
        aiml                 a fragment of an attested build's name

    The fragment form refuses ambiguity rather than guessing, which is the same
    rule the sponsor matcher follows: it proposes and a human decides. Two
    matches means the person has not said which CV they sent, and inventing an
    answer there would silently attach the wrong document to the record that
    later has to explain an outcome.
    """
    ref = (ref or "").strip()
    if not ref:
        return "", "no CV given"
    if HEX64.match(ref.lower()):
        return ref.lower(), "hash"

    path = Path(ref)
    if path.suffix.lower() == ".pdf" or path.exists():
        if not path.exists():
            return "", "no such file: %s" % ref
        return sha256_of(path), "hashed %s" % path.name

    builds = current_builds(store)
    key = re.sub(r"[^a-z0-9]", "", ref.lower())
    if not key:
        return "", "no attested CV matches %r" % ref
    hits = [(name, v) for name, v in builds.items() if key in name]
    if len(hits) == 1:
        name, (digest, rec) = hits[0]
        return digest, "matched %s" % rec.get("path", name)
    if not hits:
        return "", "no attested CV matches %r (try: %s)" % (
            ref, ", ".join(sorted(builds)) or "none attested")
    return "", "%r matches %d CVs (%s) — be specific" % (
        ref, len(hits), ", ".join(sorted(n for n, _v in hits)))


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
    ap.add_argument("pdfs", nargs="*")
    ap.add_argument("--quiet", action="store_true",
                    help="print only failures")
    ap.add_argument("--list", action="store_true",
                    help="show the CVs you can name when logging an application")
    args = ap.parse_args(argv)

    store_path = attestations_path()
    store = load(store_path)
    failed = 0

    if args.list:
        builds = current_builds(store)
        if not builds:
            print("No attested CVs. Run verify_cv.py <pdf> --attest first.")
            return 1
        print("%-13s %-36s %-11s %s" % ("NAME", "BUILD", "SHA", "ATTESTED"))
        for name in sorted(builds):
            digest, rec = builds[name]
            print("%-13s %-36s %-11s %s" % (
                name, str(rec.get("path", ""))[:36], digest[:10],
                rec.get("attested_at", "not recorded")))
        print("\nName any of those when logging an application. A file path or "
              "a full sha256 works too.")
        return 0

    if not args.pdfs:
        ap.error("give at least one PDF, or --list")

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
