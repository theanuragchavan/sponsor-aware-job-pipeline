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

A lookup table also has no semantics, which is the failure this file was
found to have on 2026-08-24. `resolve_question` matched a phrase anywhere in
the question and returned the first entry that hit, so *"Do you have the right
to work in the UK **without requiring sponsorship**?"* -- a yes/no field whose
truthful answer is No -- resolved to the right-to-work paragraph and passed.
The table was honest; the matcher was not reading. Three guards now stand
between a hit and an answer, and any of them failing means the question is
unresolved and a person answers it:

  ambiguity  more than one entry matched, so which one is a guess
  veto       an entry's own `not_if` phrase appears, e.g. "without sponsorship"
  fit        the answer's declared `type` must suit the field's widget

The third is only checkable because a widget's *shape* is observable on a form
nobody recorded -- a two-radio group is a two-radio group on every Workday
tenant. See `ATS_FORMS.md`. A caller that does not say what the widget is gets
its answer, flagged `widget_unknown`, and condition 8 refuses it: an answer
that has never been matched to a field is not a fit, it is an assumption.
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

#: Field shapes a recorded session can name without knowing the employer.
#: Named for what the DOM shows, never for what the question means -- a form
#: filler classifies Workday and Greenhouse by widget structure with no
#: per-tenant string, which is what makes the shape knowable on the employer's
#: own questions page. `ATS_FORMS.md` records the xpaths.
WIDGET_TYPES: tuple[str, ...] = (
    "boolean_radio", "boolean_checkbox", "single_select", "multi_select",
    "text", "textarea", "date", "file",
)

#: Which answer type may be typed into which widget. A paragraph offered to a
#: two-option radio is not a near miss, it is a different answer: whatever the
#: filler picks, the file did not say to pick it.
ANSWER_FITS: dict[str, tuple[str, ...]] = {
    "boolean_yes": ("boolean_radio", "boolean_checkbox", "single_select"),
    "boolean_no": ("boolean_radio", "boolean_checkbox", "single_select"),
    "text": ("text", "textarea"),
}


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


def parse_question(arg: str) -> dict:
    """`--question "Do you require sponsorship?::boolean_radio"`.

    A widget name that is not one of `WIDGET_TYPES` is refused rather than
    ignored: a typo that silently became "widget unobserved" would look like
    caution and behave like it, right up until someone corrected the typo.
    """
    text, _, widget = arg.partition("::")
    widget = widget.strip()
    if widget and widget not in WIDGET_TYPES:
        import argparse
        raise argparse.ArgumentTypeError(
            f"unknown widget {widget!r}; one of {', '.join(WIDGET_TYPES)}")
    return {"question": text.strip(), "widget": widget or None}


def question_widget(q) -> tuple[str, str | None]:
    """Split a question into its text and the widget it is rendered as.

    Accepts a bare string, which means the widget was never observed, and
    `{"question": ..., "widget": ...}`, which means it was.
    """
    if isinstance(q, dict):
        return str(q.get("question", "")), (q.get("widget") or None)
    return str(q), None


def resolve_question(question: str, screening: dict,
                     *, widget: str | None = None) -> tuple[str, str, str]:
    """('answer'|'needs_input'|'never_auto'|'unknown', detail, match_kind).

    `match_kind` says how the answer was arrived at, and only "exact" means a
    single un-vetoed entry whose type suits the field. The others -- ambiguous,
    vetoed, type_mismatch, type_unknown, widget_unknown -- are the reasons a
    person has to answer this one, and each is reported rather than absorbed.

    Unknown is the common case early on and it is not a problem: it stops the
    submission and you answer by hand, and that answer joins the file. The
    whitelist widens by use rather than by someone deciding it should.
    """
    q = re.sub(r"\s+", " ", (question or "").strip().lower())
    if not q:
        return "unknown", "empty question", "none"

    for phrase in screening.get("never_auto", []):
        if phrase in q:
            return "never_auto", f"matches never_auto: {phrase!r}", "exact"

    hits: list[dict] = []
    vetoed: list[tuple[str, str]] = []
    for entry in screening.get("answers", []):
        if not any(m in q for m in entry.get("matches", [])):
            continue
        veto = next((v for v in entry.get("not_if", []) if v in q), None)
        if veto:
            vetoed.append((entry["id"], veto))
        else:
            hits.append(entry)

    # Two entries matching is not a tie to be broken. It means the question
    # asks something the file describes twice, and picking by file order is
    # picking by accident.
    if len(hits) > 1:
        return ("unknown", "matches more than one answer: "
                + ", ".join(e["id"] for e in hits), "ambiguous")
    if not hits:
        if vetoed:
            eid, phrase = vetoed[0]
            return ("unknown",
                    f"{eid} matched but {phrase!r} vetoes it", "vetoed")
        return "unknown", "no canonical answer on file", "none"

    entry = hits[0]
    if entry.get("needs_input"):
        return "needs_input", entry.get("needs_input_reason", ""), "exact"

    declared = entry.get("type", "")
    if widget is None:
        # The answer is genuinely on file; nothing has checked it against a
        # field. Callers building a package for a person to read want it.
        # Condition 8 does not.
        return "answer", entry["id"], "widget_unknown"
    fits = ANSWER_FITS.get(declared)
    if fits is None:
        return ("unknown", f"{entry['id']} declares no usable type "
                f"({declared or 'none'})", "type_unknown")
    if widget not in fits:
        return ("unknown", f"{entry['id']} is {declared}, "
                f"the field is a {widget}", "type_mismatch")
    return "answer", entry["id"], "exact"


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

    # 8. every question resolves to something written down, and the thing
    #    written down fits the field it would be typed into
    asked = []
    for raw in questions:
        text, widget = question_widget(raw)
        state, why, kind = resolve_question(text, screening, widget=widget)
        asked.append({"q": text, "widget": widget, "state": state,
                      "why": why, "kind": kind})

    unresolved = [a for a in asked
                  if a["state"] != "answer" or a["kind"] != "exact"]
    if not screening:
        c.append(no("answers_on_file", "no screening.yml, or it is unreadable"))
    elif unresolved:
        c.append(no("answers_on_file", "; ".join(
            f"{a['q'][:48]!r}: {a['why'] or a['state']} [{a['kind']}]"
            for a in unresolved[:3])))
    else:
        c.append(ok("answers_on_file", f"{len(questions)} question(s)"))

    # 9. no free prose. Unsupervised writing about an employer is where
    #    fabrication enters, and the grounding gate cannot check text that does
    #    not exist yet. Two ways to be free prose: the question is on the
    #    never-auto list, or the field is a textarea nothing on file answers.
    #    The second is only detectable because a widget's shape is observable.
    free_text = [a["q"] for a in asked
                 if a["state"] == "never_auto"
                 or (a["widget"] == "textarea" and a["state"] != "answer")]
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
                    type=parse_question,
                    help="a form question as TEXT or TEXT::WIDGET; repeatable. "
                         "Without a widget the gate cannot confirm the answer "
                         f"fits the field. Widgets: {', '.join(WIDGET_TYPES)}")
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
