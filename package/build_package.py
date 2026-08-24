"""Assemble one job into a folder an agent can execute without improvising.

The package is the whole interface. Cowork does not query the tracker, does not
re-run the gate, does not decide which CV to attach. Everything it needs is in
one directory and everything it must not do is stated in `gate.json`.

That is deliberate. An agent that can look things up can look up the wrong
thing, and an agent that can re-derive a decision can re-derive it differently.
The narrow interface is what makes the ten conditions mean anything at
execution time rather than only at build time.

Layout:

    packages/<job_id>/
        job.json        company, title, url, sponsor status, tier
        jd.txt          the posting text as captured
        cv.pdf          the attested file, copied, with its digest recorded
        letter.md       the grounded draft
        answers.json    resolved form answers, looked up not composed
        gate.json       the verdict, all ten conditions, both outcomes
        MANIFEST.json   sha256 of everything above

The manifest matters more than it looks. It is how you answer "is the CV in
this package the one that passed the gate" without trusting the filename --
which is exactly the failure the CV attestation system exists for.

Never writes into `packages/<id>/` twice without --force. A package is a record
of what was prepared, and silently rebuilding one loses the ability to say what
was actually sent.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
import tracker  # noqa: E402
from package import autosubmit_gate as gate  # noqa: E402

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_CANNOT_BUILD = 2


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def resolve_cv_path(cv_ref: str) -> Path | None:
    """The actual file to attach, from a short name, a path, or a digest."""
    p = Path(cv_ref)
    if p.exists():
        return p
    sys.path.insert(0, str(ROOT / "pipeline"))
    import cv_gate
    store = cv_gate.load(cv_gate.attestations_path())
    digest, _ = cv_gate.resolve(cv_ref, store)
    if not digest:
        return None
    build = Path(store[digest].get("path", ""))
    if build.exists():
        return build
    # The attested build and the renamed copy in to_send/ are the same bytes,
    # which is the entire point of keying on content. Prefer the one he
    # actually uploads.
    for candidate in Path(r"D:\Resume\to_send").glob("*.pdf"):
        if sha256_of(candidate) == digest:
            return candidate
    return None


def answers_for(questions: list[str], screening: dict) -> dict:
    """Resolved answers plus what could not be resolved and why.

    Unresolved entries are carried, not dropped. An agent that receives a
    question with no answer must be able to see that it has no answer -- an
    absent key reads as "nothing to fill in", which is how a blank required
    field becomes a submitted form.
    """
    out: dict = {"resolved": {}, "unresolved": []}
    by_id = {a["id"]: a for a in screening.get("answers", [])}
    for raw in questions:
        text, widget = gate.question_widget(raw)
        state, detail, kind = gate.resolve_question(text, screening,
                                                    widget=widget)
        if state == "answer":
            entry = by_id[detail]
            out["resolved"][text] = {
                "answer": entry.get("answer"),
                "long": entry.get("long"),
                "source_id": entry["id"],
                # An answer nothing has matched to a field is carried as such.
                # The gate refuses it; a person reading the package should see
                # the same caveat the gate saw.
                "match": kind,
                "widget": widget,
            }
        else:
            out["unresolved"].append({"question": text, "state": state,
                                      "reason": detail, "match": kind,
                                      "widget": widget})
    return out


def build(job_id: str, *, draft: Path | None, cv_ref: str,
          questions: list[str], jd_text: str = "",
          out_dir: Path | None = None, force: bool = False) -> tuple[int, dict]:
    out_dir = out_dir or ROOT / "packages"
    rows = tracker.load_tracker(str(ROOT / config.JOBS_XLSX))
    job = rows.get(job_id)
    if job is None:
        return EXIT_CANNOT_BUILD, {"error": f"no job {job_id!r} in the tracker"}

    target = out_dir / job_id.replace(":", "_")
    if target.exists() and any(target.iterdir()) and not force:
        return EXIT_REFUSED, {
            "error": f"{target} already exists. A package records what was "
                     f"prepared; rebuilding silently loses that. Use --force."}

    verdict = gate.evaluate(job, draft=draft, cv_ref=cv_ref,
                            questions=questions)
    screening = gate.load_screening(ROOT / "data" / "screening.yml")

    target.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    (target / "job.json").write_text(json.dumps({
        "id": job_id,
        "company": job.get("company", ""),
        "title": job.get("title", ""),
        "location": job.get("location", ""),
        "url": job.get("redirect_url", ""),
        "ats": job_id.split(":", 1)[0] if ":" in job_id else "adzuna",
        "sponsor_match": job.get("sponsor_match", ""),
        "sponsor_rating": job.get("sponsor_rating", ""),
        "tier": job.get("tier", ""),
        "deadline": job.get("apply_deadline", ""),
    }, indent=2), encoding="utf-8")
    written.append(target / "job.json")

    (target / "jd.txt").write_text(
        jd_text or job.get("description", "") or "", encoding="utf-8")
    written.append(target / "jd.txt")

    cv_path = resolve_cv_path(cv_ref) if cv_ref else None
    if cv_path:
        shutil.copy2(cv_path, target / "cv.pdf")
        written.append(target / "cv.pdf")

    if draft and Path(draft).exists():
        shutil.copy2(draft, target / "letter.md")
        written.append(target / "letter.md")

    (target / "answers.json").write_text(
        json.dumps(answers_for(questions, screening), indent=2),
        encoding="utf-8")
    written.append(target / "answers.json")

    (target / "gate.json").write_text(json.dumps(verdict, indent=2),
                                      encoding="utf-8")
    written.append(target / "gate.json")

    manifest = {
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "job_id": job_id,
        "autosubmit": verdict["autosubmit"],
        "files": {p.name: {"sha256": sha256_of(p), "bytes": p.stat().st_size}
                  for p in written},
    }
    if cv_path:
        manifest["cv_source"] = str(cv_path)
    (target / "MANIFEST.json").write_text(json.dumps(manifest, indent=2),
                                          encoding="utf-8")

    return EXIT_OK, {"path": str(target), "verdict": verdict,
                     "files": len(written)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Assemble one job into a package Cowork can execute.")
    ap.add_argument("job_id")
    ap.add_argument("--draft")
    ap.add_argument("--cv", default="")
    ap.add_argument("--question", action="append", default=[])
    ap.add_argument("--jd", help="file holding the posting text")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)

    jd_text = ""
    if args.jd and Path(args.jd).exists():
        jd_text = Path(args.jd).read_text(encoding="utf-8", errors="replace")

    code, report = build(args.job_id,
                         draft=Path(args.draft) if args.draft else None,
                         cv_ref=args.cv, questions=args.question,
                         jd_text=jd_text, force=args.force)
    if "error" in report:
        print(f"REFUSED: {report['error']}")
        return code

    print(f"wrote {report['path']} ({report['files']} files)")
    print()
    print(gate.render(report["verdict"]))
    return code


if __name__ == "__main__":
    sys.exit(main())
