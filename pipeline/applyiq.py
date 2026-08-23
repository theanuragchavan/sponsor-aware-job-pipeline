"""Read Adzuna's ApplyIQ daily emails into the same shape `log_manual` takes.

ApplyIQ submits applications on his behalf and has been doing so since at least
2026-07-22. None of them are in the tracker. That is the denominator problem
again, and at several times the scale it was found at last time: the funnel was
dividing by 12 when the real figure is closer to 50.

It matters more here than for a forgotten Student Circus application, because
this is the channel nobody chose job by job. Whether it works is an empirical
question, and an unrecorded channel cannot be measured, only argued about.
Recording it turns "is ApplyIQ worth leaving on" from an opinion into a number
the existing funnel already knows how to compute.

Two states appear in each email and they are not the same fact:

* **applied** -- ApplyIQ submitted it. A real application, dated, whose
  screening answers were composed by Adzuna rather than looked up.
* **info required** -- ApplyIQ stopped and wants input. Not an application.
  It is a work queue, and the natural first target for a recorded browser
  session: one form, on one site, already signed into.

Only the first becomes a tracker row. Counting the second would inflate the
denominator in the flattering direction, which is the exact error this module
exists to undo.

    python pipeline/applyiq.py --dir data/applyiq_emails
    python pipeline/applyiq.py --dir data/applyiq_emails --json out.json

The parser takes text, not credentials: it never touches Gmail. Bodies are
dumped alongside it by whatever has mail access, which keeps this a pure
function that can be tested against a fixture.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

EXIT_OK = 0
EXIT_NOTHING = 1
EXIT_CANNOT = 2

#: Distinct from plain "adzuna", which means he found it on Adzuna and applied
#: himself. Telling those two apart in the channel funnel is the entire reason
#: for recording these.
APPLIED_VIA = "adzuna applyiq"

#: A job block begins with a markdown heading whose link carries both the state
#: and the application id. The id separator is mangled in transit ("=" arrives
#: as a space, or as "!", depending on the encoding), so match any one
#: non-digit rather than the character it should have been.
_BLOCK = re.compile(r"##\s*(?P<title>.+?)\[\]\((?P<url>[^)]*?)\)", re.S)
_TAB = re.compile(r"tab=(?P<tab>applied|info_required)")
_APPID = re.compile(r"application_id\D?(?P<id>\d+)")
_FIELD = re.compile(
    r"^\*(?P<key>Company|Location|Salary):\*\s*(?P<val>.+?)\s*$", re.M)

#: Characters the mailer substitutes, mapped back. Non-breaking space and
#: non-breaking hyphen both appear inside company names and titles.
_SUBSTITUTIONS = ((" ", " "), ("‑", "-"),
                  ("’", "'"), ("‘", "'"))


def _clean(text: str) -> str:
    for bad, good in _SUBSTITUTIONS:
        text = text.replace(bad, good)
    return text


def parse(body: str, received: str) -> list[dict]:
    """Every job block in one daily email, in the order it appears.

    `received` is the date the mail arrived, which is also the application
    date: the summaries go out around 23:20 covering that same day.
    """
    body = _clean(body)
    blocks = list(_BLOCK.finditer(body))
    out: list[dict] = []

    for i, match in enumerate(blocks):
        url = match.group("url")
        tab = _TAB.search(url)
        if not tab:
            continue                    # a "view all" or footer link, not a job

        # Fields belong to this heading until the next one starts.
        end = blocks[i + 1].start() if i + 1 < len(blocks) else len(body)
        fields = {f.group("key").lower(): f.group("val")
                  for f in _FIELD.finditer(body[match.start():end])}
        if "company" not in fields:
            continue                    # the same block's duplicate link

        appid = _APPID.search(url)
        entry = {
            "company": fields["company"],
            "title": re.sub(r"\s+", " ", match.group("title")).strip(),
            "location": fields.get("location", ""),
            "date_applied": received,
            "applied_via": APPLIED_VIA,
            "state": tab.group("tab"),
            "applyiq_id": appid.group("id") if appid else "",
        }
        if fields.get("salary"):
            entry["salary"] = fields["salary"]
        if entry not in out:
            out.append(entry)
    return out


def parse_dir(path: Path) -> list[dict]:
    """Parse every YYYY-MM-DD*.txt body in a directory.

    The filename carries the date because the body does not: the mail says
    "today" and means the day it was sent.
    """
    out: list[dict] = []
    for f in sorted(path.glob("*.txt")):
        date = f.stem[:10]
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            print(f"  skipped {f.name}: filename must start YYYY-MM-DD")
            continue
        out.extend(parse(f.read_text(encoding="utf-8", errors="replace"), date))
    return out


def for_log_manual(entries: list[dict]) -> list[dict]:
    """Just the submitted ones, shaped for `log_manual --file`.

    `state` and `applyiq_id` are dropped rather than passed through: the
    tracker has no column for either, and a note is where an unmodelled fact
    belongs.
    """
    out = []
    for e in entries:
        if e["state"] != "applied":
            continue
        row = {k: v for k, v in e.items()
               if k in ("company", "title", "location", "date_applied",
                        "applied_via")}
        ident = f"; ApplyIQ id {e['applyiq_id']}" if e["applyiq_id"] else ""
        row["notes"] = (f"Submitted by Adzuna ApplyIQ without per-job "
                        f"review{ident}. Screening answers composed by Adzuna, "
                        f"not looked up from screening.yml.")
        out.append(row)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Parse ApplyIQ daily emails into application records.")
    ap.add_argument("--dir", required=True,
                    help="directory of YYYY-MM-DD*.txt message bodies")
    ap.add_argument("--json", help="write the log_manual-shaped records here")
    args = ap.parse_args(argv)

    src = Path(args.dir)
    if not src.is_dir():
        print(f"CANNOT PARSE: {src} is not a directory.")
        return EXIT_CANNOT

    entries = parse_dir(src)
    if not entries:
        print("No job blocks found. Are these ApplyIQ daily summaries?")
        return EXIT_NOTHING

    sent = [e for e in entries if e["state"] == "applied"]
    queue = [e for e in entries if e["state"] == "info_required"]

    print(f"{len(sent)} submitted, {len(queue)} waiting on information\n")
    print("SUBMITTED")
    for e in sent:
        print(f"  {e['date_applied']}  {e['company'][:26]:<26} "
              f"{e['title'][:46]}")
    if queue:
        print("\nWAITING ON INFORMATION -- never sent, so never applications")
        for e in queue:
            print(f"  {e['date_applied']}  {e['company'][:26]:<26} "
                  f"{e['title'][:46]}")

    if args.json:
        Path(args.json).write_text(
            json.dumps(for_log_manual(entries), indent=2) + "\n",
            encoding="utf-8")
        print(f"\nWrote {len(sent)} record(s) to {args.json}. Preview with:"
              f"\n  python pipeline/log_manual.py --file {args.json}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
