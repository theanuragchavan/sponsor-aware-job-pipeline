"""
boards.py — the curated registry of which employer uses which job board.

Why hand-curated rather than discovered: the Home Office sponsor register has
five columns (Organisation Name, Town/City, County, Type & Rating, Route) and
122,767 Skilled Worker entries. No website, no domain, no sector. It can verify
a name; it cannot find a job board. Filtering it by tech-sounding tokens yields
4,600 organisations headed by "0xA Technologies Ltd" and "1 WAY TECH SOLUTIONS
LIMITED" — two-person consultancy shells, not employers worth probing.

So the direction is slug-first, register-as-gate. Adding a row costs about
thirty seconds, because the slug is simply the last part of a careers URL:

    boards.greenhouse.io/monzo      -> greenhouse, monzo
    jobs.lever.co/example           -> lever,      example
    jobs.ashbyhq.com/example        -> ashby,      example
    apply.workable.com/example      -> workable,   example

`register_name` is the column that makes the whole thing work. Greenhouse
reports Monzo's employer name as "Monzo", and the register's key is "MONZO
BANK", so an ingested job would score sponsor_match=no and be dropped by the
shortlist — invisible, for the exact reason it was collected. Fill it with the
precise register string (use the /sponsor skill) or leave it blank and let the
alias overlay in sponsor_check handle it.

Statuses: active (pull it), empty (valid board, no jobs today — re-check, never
delete), paused (stop pulling), unknown (never successfully fetched).
"""

from __future__ import annotations

import csv
import os
from pathlib import Path

ADZUNA_HOME = Path(__file__).resolve().parent.parent
BOARDS_CSV = ADZUNA_HOME / "data" / "ats_boards.csv"

FIELDS = ["company", "ats", "slug", "register_name",
          "status", "last_ok", "notes"]

ACTIVE_STATUSES = {"active", "empty", ""}


def load_boards(path=None):
    """Read the registry. Returns [] if it doesn't exist yet."""
    path = Path(path or BOARDS_CSV)
    if not path.exists():
        return []
    with open(path, encoding="utf-8-sig", newline="") as fh:
        rows = []
        for row in csv.DictReader(fh):
            if not (row.get("slug") or "").strip():
                continue
            if (row.get("company") or "").lstrip().startswith("#"):
                continue                       # commented-out line
            rows.append({k: (row.get(k) or "").strip() for k in FIELDS})
        return rows


def save_boards(boards, path=None):
    """Write the registry atomically, so a crash can't truncate it."""
    path = Path(path or BOARDS_CSV)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".csv.tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        for board in sorted(boards, key=lambda b: (b.get("ats", ""),
                                                   b.get("company", "").lower())):
            writer.writerow({k: board.get(k, "") for k in FIELDS})
    os.replace(tmp, path)


def iter_active(boards, family=None, slug=None):
    """Boards worth fetching this run, optionally narrowed for a manual run."""
    for board in boards:
        if board.get("status", "") not in ACTIVE_STATUSES:
            continue
        if family and board.get("ats") != family:
            continue
        if slug and board.get("slug") != slug:
            continue
        yield board


def mark(boards, board, status, note=""):
    """Record the outcome of a fetch against the matching registry row."""
    import datetime as dt
    for candidate in boards:
        if (candidate.get("ats") == board.get("ats")
                and candidate.get("slug") == board.get("slug")):
            candidate["status"] = status
            if status in ("active", "empty"):
                candidate["last_ok"] = dt.date.today().isoformat()
            if note:
                candidate["notes"] = note
            return candidate
    return None
