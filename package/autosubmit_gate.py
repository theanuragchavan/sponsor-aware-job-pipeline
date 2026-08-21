"""Ten conditions. All must hold, or an agent fills the form and stops.

`CLAUDE.md` rule 2 currently says never auto-apply, and that rule was written
for good reasons. It is superseded here deliberately, not forgotten: auto-submit
is allowed, but only where every precondition is a fact something already
checked, and never on the strength of a model deciding a role looks fine.

The verdict shape is borrowed from the Flagship 2 harness's `gate()`: a bool
plus a human-readable PASS/FAIL line for **every** condition, whichever way it
went. An approved application has to be as auditable as a blocked one, because
the approved ones are the ones you have to explain later.

Failing is not an error. Nine of ten passing means fill the form, stop at the
review screen, and say which one stopped it. The gate's job is to draw a line
between "a machine may finish this" and "a person must", not to permit or
forbid the application itself.

Why these ten
-------------
Each is either a fact already established upstream or a property of the
artefacts. None is a judgement:

  1-3  is this real       recorded ATS, sponsor-confirmed, still live
  4-5  should we          not already applied, not on his own do-not-apply list
  6-7  is it true         CV attested by content, letter grounded
  8-9  can it be answered every field looked up, no free prose
  10   volume             a daily cap

Condition 8 is the load-bearing one. Every answer comes from `data/screening.yml`,
which is transcribed by hand. An agent optimising for "submit successfully" has
a real incentive to answer the sponsorship question the way that gets through.
A lookup table has no incentive.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import tracker  # noqa: E402
from pipeline import shortlist  # noqa: E402

#: ATS families with a recorded skill. Starts narrow on purpose -- widen it by
#: recording a session, not by editing a list.
RECORDED_ATS: tuple[str, ...] = ()

#: Applications a machine may finish per day. Volume is what makes a pipeline
#: look like spray-and-pray to a recruiter, and the whole system is built on
#: the opposite premise.
DAILY_CAP = 3


def ok(rule: str, detail: str = "") -> dict:
    return {"rule": rule, "result": "pass", "detail": detail}


def no(rule: str, detail: str) -> dict:
    return {"rule": rule, "result": "fail", "detail": detail}


def _ats_family(job_id: str) -> str:
    return job_id.split(":", 1)[0] if ":" in job_id else "adzuna"


def _applications_today(log_path: Path, today: str) -> int:
    if not log_path.exists():
        return 0
    n = 0
    for line in log_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("type") == "application.logged" and e.get("ts", "")[:10] == today:
            n += 1
    return n


def load_screening(path: Path) -> dict:
    """Answers plus the never-auto list. Missing file is not an empty file."""
    if not path.exists():
        return {}
    try:
        import yaml
    except ImportError:
        return {}
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 - a malformed file must fail the gate, not crash
        return {}


def resolve_question(question: str, screening: dict) -> tuple[str, str]:
    """('answer'|'needs_input'|'never_auto'|'unknown', detail).

    Unknown is the common case early on and it is not a problem: it stops the
    submission and you answer by hand, and that answer joins the file. The
    whitelist widens by use rather than by someone deciding it should.
    """
    q = re.sub(r"\s+", " ", (question or "").strip().lower())
    if not q:
        return "unknown", "empty question"

    for phrase in screening.get("never_auto", []):
        if phrase in q:
            return "never_auto", f"matches never_auto: {phrase!r}"

    for entry in screening.get("answers", []):
        for m in entry.get("matches", []):
            if m in q:
                if entry.get("needs_input"):
                    return "needs_input", entry.get("needs_input_reason", "")
                return "answer", entry["id"]
    return "unknown", "no canonical answer on file"


def evaluate(job: dict, *, draft: Path | None = None, cv_ref: str = "",
             questions: list[str] | None = None,
             screening_path: Path | None = None,
             log_path: Path | None = None,
             recorded_ats: tuple[str, ...] = RECORDED_ATS,
             daily_cap: int = DAILY_CAP,
             today: str | None = None) -> dict:
    """Run all ten. Returns {autosubmit, conditions, blocked_by}."""
    screening_path = screening_path or ROOT / "data" / "screening.yml"
    log_path = log_path or ROOT / "data" / "decision_log.jsonl"
    today = today or dt.date.today().isoformat()
    questions = questions or []
    screening = load_screening(screening_path)

    c: list[dict] = []
    job_id = str(job.get("id", ""))

    # 1. a recorded skill for this form shape
    family = _ats_family(job_id)
    c.append(ok("ats_recorded", family) if family in recorded_ats
             else no("ats_recorded",
                     f"no recorded session for {family!r} "
                     f"(recorded: {', '.join(recorded_ats) or 'none yet'})"))

    # 2. sponsor-confirmed. The register, not the job ad's claim.
    if (job.get("sponsor_match") or "").strip().lower() == "yes":
        c.append(ok("sponsor_confirmed",
                    f"{job.get('sponsor_rating', '')} rated"))
    else:
        c.append(no("sponsor_confirmed",
                    f"sponsor_match={job.get('sponsor_match') or 'unset'!r}"))

    # 3. still live. Never submit into a 404.
    live = (job.get("_liveness") or "").lower()
    c.append(ok("posting_live", live or "from feed") if live != "dead"
             else no("posting_live", "posting is dead"))

    # 4. not already applied. The Snowflake failure, structurally.
    status = (job.get("status") or "").strip().lower()
    c.append(ok("not_already_applied") if status in ("", "new")
             else no("not_already_applied", f"status={status!r}"))

    # 5. his own recorded rulings
    ruling = shortlist.ruling_for(job, shortlist.load_decisions())
    if ruling and (ruling.get("verdict") or "").upper() == "CUT":
        c.append(no("not_blacklisted", f"CUT: {ruling.get('reason', '')}"))
    else:
        c.append(ok("not_blacklisted"))

    # 6. the CV is attested by content
    if cv_ref:
        r = subprocess.run(  # nosec B603 - fixed argv, no shell
            [sys.executable, str(ROOT / "pipeline" / "cv_gate.py"),
             cv_ref, "--quiet"],
            capture_output=True, text=True, cwd=ROOT)
        c.append(ok("cv_attested", cv_ref) if r.returncode == 0
                 else no("cv_attested", (r.stdout or r.stderr).strip()[:120]))
    else:
        c.append(no("cv_attested", "no CV named"))

    # 7. the letter says nothing CONTENT_MASTER does not
    if draft and Path(draft).exists():
        r = subprocess.run(  # nosec B603
            [sys.executable, str(ROOT / "pipeline" / "verify_claims.py"),
             str(draft)],
            capture_output=True, text=True, cwd=ROOT)
        c.append(ok("letter_grounded", Path(draft).name) if r.returncode == 0
                 else no("letter_grounded",
                         "ungrounded or below the substance floor"))
    else:
        c.append(no("letter_grounded", "no draft supplied"))

    # 8. every question resolves to something written down
    unresolved = [(q, why) for q in questions
                  for state, why in [resolve_question(q, screening)]
                  if state in ("unknown", "needs_input")]
    if not screening:
        c.append(no("answers_on_file", "no screening.yml, or it is unreadable"))
    elif unresolved:
        c.append(no("answers_on_file",
                    "; ".join(f"{q[:48]!r}: {why}" for q, why in unresolved[:3])))
    else:
        c.append(ok("answers_on_file", f"{len(questions)} question(s)"))

    # 9. no free prose. Unsupervised writing about an employer is where
    #    fabrication enters, and the grounding gate cannot check text that does
    #    not exist yet.
    free_text = [q for q in questions
                 if resolve_question(q, screening)[0] == "never_auto"]
    c.append(ok("no_free_text") if not free_text
             else no("no_free_text", f"{len(free_text)} open question(s): "
                                     f"{free_text[0][:60]!r}"))

    # 10. volume
    sent = _applications_today(log_path, today)
    c.append(ok("under_daily_cap", f"{sent}/{daily_cap} today")
             if sent < daily_cap
             else no("under_daily_cap", f"{sent} already sent today"))

    blocked = [x["rule"] for x in c if x["result"] == "fail"]
    return {"job_id": job_id, "autosubmit": not blocked,
            "conditions": c, "blocked_by": blocked}


def render(verdict: dict) -> str:
    lines = [f"job {verdict['job_id']}",
             "AUTOSUBMIT: " + ("yes" if verdict["autosubmit"] else "NO")]
    for x in verdict["conditions"]:
        mark = "PASS" if x["result"] == "pass" else "FAIL"
        lines.append(f"  {mark} {x['rule']}"
                     + (f" -- {x['detail']}" if x["detail"] else ""))
    if not verdict["autosubmit"]:
        lines.append("")
        lines.append("Fill the form and stop at the review screen. Blocked by: "
                     + ", ".join(verdict["blocked_by"]))
    return "\n".join(lines)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(
        description="Decide whether an agent may submit this application.")
    ap.add_argument("job_id")
    ap.add_argument("--draft")
    ap.add_argument("--cv", default="")
    ap.add_argument("--question", action="append", default=[],
                    help="a form question; repeatable")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    import config
    rows = tracker.load_tracker(str(ROOT / config.JOBS_XLSX))
    job = rows.get(args.job_id)
    if job is None:
        print(f"no job {args.job_id!r} in the tracker")
        return 2

    verdict = evaluate(job, draft=Path(args.draft) if args.draft else None,
                       cv_ref=args.cv, questions=args.question)
    print(json.dumps(verdict, indent=2) if args.json else render(verdict))
    return 0 if verdict["autosubmit"] else 1


if __name__ == "__main__":
    sys.exit(main())
