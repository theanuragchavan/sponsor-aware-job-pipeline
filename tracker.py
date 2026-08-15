"""
Tracker store — flat-CSV read/write, dedupe by Adzuna job id, and the bridge to
sponsor_check for sponsorship attachment.

No SQL, no database — a single CSV is the whole store (see Section 1 of the
brief). Rows are keyed in memory by Adzuna's job `id` so re-running the script,
or finding the same job under two categories, never creates duplicates.
"""

import csv
import datetime as dt
import html
import logging
import os
import re

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

import config
import sponsor_check

logger = logging.getLogger(__name__)


def _word_regex(words):
    """Compile a case-insensitive whole-word alternation, or None if empty."""
    if not words:
        return None
    return re.compile(r"\b(" + "|".join(re.escape(w) for w in words) + r")\b",
                      re.IGNORECASE)


# Compiled once each: trades/construction noise, and too-senior roles.
_NOISE_RE = _word_regex(config.TITLE_EXCLUDE)
_SENIORITY_RE = _word_regex(getattr(config, "SENIORITY_EXCLUDE", []))


def is_noise_title(title):
    """True if the job title matches a TITLE_EXCLUDE term (trades/construction noise)."""
    return bool(_NOISE_RE.search(title or "")) if _NOISE_RE else False


def is_too_senior(title):
    """True if the title names a seniority above the target profile."""
    return bool(_SENIORITY_RE.search(title or "")) if _SENIORITY_RE else False


def unsuitable_reason(title):
    """Why a title should be dropped, or None if it's a suitable role.

    Returns a short human-readable reason ("trades/construction noise" or
    "too senior") so every filtered row can be logged, never dropped silently.
    """
    if is_noise_title(title):
        return "trades/construction noise"
    if is_too_senior(title):
        return "too senior"
    return None


# --- Apply-shortlist logic --------------------------------------------------
# These NEVER drop a row from the store — they only decide whether a
# sponsor-confirmed row is a *genuine* target worth surfacing on the shortlist.
_RECRUITER_KW_RE = _word_regex(getattr(config, "RECRUITER_KEYWORDS", []))
_RECRUITER_NAMES = [n.lower() for n in getattr(config, "RECRUITER_COMPANIES", [])]
_APPLY_BLOCK_RE = _word_regex(getattr(config, "APPLY_BLOCK_TERMS", []))
_ROLE_INCLUDE_RE = _word_regex(getattr(config, "ROLE_INCLUDE_TERMS", []))


def is_agency(company):
    """True if the company looks like a recruiter/staffing agency.

    Agency postings match the AGENCY's sponsor licence, not the end-employer's,
    so they must not be treated as sponsor-confirmed targets.
    """
    c = (company or "").lower()
    if not c:
        return False
    if _RECRUITER_KW_RE and _RECRUITER_KW_RE.search(company or ""):
        return True
    return any(name in c for name in _RECRUITER_NAMES)


def apply_ready_reason(row):
    """Why a sponsor-confirmed row is NOT a genuine target, or None if it is.

    Genuine target = sponsor_match 'yes' AND direct employer (not an agency)
    AND a role within the target profile (not senior/trades/apprentice/
    security-cleared). Returned reason is for logging; None means "shortlist it".
    """
    status = (row.get("status") or "new").strip().lower()
    if status not in ("", "new"):
        return f"already actioned ({status})"   # applied/ignored/etc. leave the shortlist
    if (row.get("sponsor_match") or "") != "yes":
        return "not sponsor-confirmed"
    if is_agency(row.get("company", "")):
        return "recruiter/agency posting"
    reason = unsuitable_reason(row.get("title", ""))
    if reason:
        return reason
    if _APPLY_BLOCK_RE and _APPLY_BLOCK_RE.search(row.get("title", "")):
        return "won't sponsor in practice (cleared/apprentice/intern)"
    if _ROLE_INCLUDE_RE and not _ROLE_INCLUDE_RE.search(row.get("title", "")):
        return "off-target role (not software/AI/data)"
    return None


def is_apply_ready(row):
    """True if the row belongs on the Apply Shortlist."""
    return apply_ready_reason(row) is None


def _dedupe_key(row):
    """Collapse London/UK twins and reposts of the same role at the same firm."""
    def norm(s):
        s = re.sub(r"[^a-z0-9 ]", "", (s or "").lower())
        return re.sub(r"\b(ltd|limited|plc|group|uk|inc|llp)\b", "", s).strip()
    return (norm(row.get("company", "")), norm(row.get("title", "")))


def _shortlist_rank(row):
    """London first, then by descending salary."""
    london = 0 if "london" in (row.get("location") or "").lower() else 1
    return (london, -_as_int(row.get("salary_max")))


def apply_shortlist(rows):
    """Genuine sponsor-confirmed targets, deduped, London-first then salary."""
    best = {}
    for r in rows:
        if not is_apply_ready(r):
            continue
        k = _dedupe_key(r)
        if k not in best or _shortlist_rank(r) < _shortlist_rank(best[k]):
            best[k] = r
    return sorted(best.values(), key=_shortlist_rank)

# CSV schema (brief Section 6) + extensions: sponsor_route, description.
# `description` is Adzuna's JD snippet (truncated) — a triage aid in Excel and
# the seed text to hand to a deep-evaluation tool. Full JD lives at redirect_url.
# `source` and `apply_deadline` were APPENDED (2026-08-14) when a second job
# source arrived. Appending matters: _load_xlsx zips header->values, so an older
# 20-column workbook still loads cleanly with the new keys simply absent, and
# load_tracker backfills them. Inserting mid-list would misalign every row.
FIELDNAMES = [
    "id", "title", "company", "location",
    "salary_min", "salary_max", "contract_type",
    "category", "tier", "posted_date", "redirect_url",
    "date_first_seen", "status",
    "sponsor_match", "sponsor_rating", "sponsor_route",
    "applied_via", "date_applied", "notes", "description",
    "source", "apply_deadline",
]

# Non-Adzuna ids carry a family prefix ("gh:4567890"). Greenhouse ids are plain
# integers exactly like Adzuna's, so an unprefixed id would collide and the
# insert-only merge would silently drop the newcomer. The prefix also forces
# Excel to store the cell as text, which sidesteps the float round-trip that the
# ".0" strip in _load_xlsx exists to clean up.
SOURCE_ADZUNA = "adzuna"


def source_of(job_id):
    """The source a row id belongs to. A bare id means Adzuna, by construction."""
    jid = str(job_id or "")
    return jid.split(":", 1)[0] if ":" in jid else SOURCE_ADZUNA

# Strip any HTML tags Adzuna leaves in the snippet, unescape entities, and
# collapse whitespace so the cell reads cleanly.
_TAG_RE = re.compile(r"<[^>]+>")


def _clean_snippet(text):
    if not text:
        return ""
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()

# Status values the user edits by hand are preserved across runs.
DEFAULT_STATUS = "new"


def load_tracker(path):
    """Load the store into {id: row_dict}. Returns {} if the file doesn't exist.

    The store is the beautified .xlsx workbook; a legacy .csv path still loads
    for one-off migrations. Manual edits (status/notes/applied_via) round-trip.
    """
    if not os.path.exists(path):
        return {}
    rows = _load_xlsx(path) if path.lower().endswith(".xlsx") else _load_csv(path)
    if path.lower().endswith(".xlsx"):
        _harvest_shortlist_edits(path, rows)
    logger.info("Loaded %d existing rows from %s", len(rows), path)
    return rows


# Fields edited by hand. They round-trip from the main sheet, and are harvested
# back off the "Apply Shortlist" view before that view is regenerated.
EDITABLE_FIELDS = ("status", "date_applied", "applied_via", "notes")


def _harvest_shortlist_edits(path, rows_by_id):
    """Pull hand-edits made on the 'Apply Shortlist' sheet back into the store.

    That sheet is rebuilt from scratch every run, so anything typed into it is
    lost unless it is read back first — and logging an application on the sheet
    you're actually working from is the obvious thing to do. Rows match on `id`
    when present, otherwise on the same normalised (company, title) key the
    shortlist already dedupes by, so edits on older id-less workbooks still
    survive the upgrade.

    Only ever *adds* information: blank cells and a `status` of "new" (the
    default a regenerated view carries) are ignored, so a stale view can't
    revert a row. If the same field is edited on both sheets between runs, the
    shortlist wins.
    """
    try:
        wb = openpyxl.load_workbook(path, data_only=True)
    except (OSError, KeyError, ValueError):
        return
    if _SHORTLIST_TITLE not in wb.sheetnames:
        return
    ws = wb[_SHORTLIST_TITLE]
    header = [c.value for c in ws[1]]
    if not header:
        return

    by_key = {_dedupe_key(r): r for r in rows_by_id.values()}
    recovered = 0
    for record in ws.iter_rows(min_row=2, values_only=True):
        view = {k: v for k, v in zip(header, record) if k is not None}
        jid = str(view.get("id") or "").strip()
        if jid.endswith(".0"):
            jid = jid[:-2]
        target = rows_by_id.get(jid) or by_key.get(_dedupe_key(view))
        if target is None:
            continue
        for field in EDITABLE_FIELDS:
            val = view.get(field)
            val = "" if val is None else str(val).strip()
            if not val:
                continue
            if field == "status" and val.lower() == DEFAULT_STATUS:
                continue
            if val != (target.get(field) or "").strip():
                target[field] = val
                recovered += 1
    if recovered:
        logger.info("Recovered %d hand-edit(s) from the '%s' sheet.",
                    recovered, _SHORTLIST_TITLE)


def _load_csv(path):
    rows = {}
    with open(path, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            rows[row["id"]] = row
    return rows


def _load_xlsx(path):
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb[_SHEET_TITLE] if _SHEET_TITLE in wb.sheetnames else wb.active
    header = [c.value for c in ws[1]]
    rows = {}
    for record in ws.iter_rows(min_row=2, values_only=True):
        row = {}
        for key, val in zip(header, record):
            if key is None:
                continue
            row[key] = "" if val is None else str(val)
        jid = row.get("id", "")
        if jid.endswith(".0"):          # id may round-trip as "123.0"
            jid = jid[:-2]
            row["id"] = jid
        if jid:
            # Backfill the columns added 2026-08-14 so a workbook written before
            # then loads with a usable `source` rather than an empty string —
            # anything reading source == "adzuna" would otherwise skip every
            # pre-existing row.
            row.setdefault("apply_deadline", "")
            if not row.get("source"):
                row["source"] = source_of(jid)
            rows[jid] = row
    return rows


def _sort_key(r):
    """Sponsor-confirmed first, then London, then by descending salary."""
    confirmed = 0 if r.get("sponsor_match") == "yes" else 1
    london = 0 if "london" in (r.get("location") or "").lower() else 1
    return (confirmed, london, -_as_int(r.get("salary_max")))


def save_tracker(path, rows_by_id):
    """Write {id: row} to the beautified .xlsx store, sponsor+London first.

    File-lock-safe: writes to a temp file then atomically replaces the store, so
    a crash mid-write can't corrupt it. If the store is open in Excel (Windows
    holds an exclusive lock -> PermissionError), this run is written to a
    timestamped side file and a loud warning is logged instead of the run
    crashing and losing the day's new jobs.
    """
    ordered = sorted(rows_by_id.values(), key=_sort_key)
    try:
        _atomic_write(path, ordered)
    except PermissionError:
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        base, ext = os.path.splitext(path)
        fallback = f"{base}.LOCKED-{stamp}{ext}"
        _write_beautified_xlsx(fallback, ordered)
        logger.warning(
            "Store %s is locked (open in Excel?) — wrote this run to %s instead. "
            "Close the workbook; the next run will update the main store.",
            path, fallback)
        return
    logger.info("Wrote %d rows to %s", len(ordered), path)


def _atomic_write(path, ordered):
    """Write to a temp file, then os.replace onto the target (atomic on-volume).

    Raises PermissionError (leaving the target untouched) if the target is locked
    by another process, e.g. open in Excel.
    """
    tmp = f"{path}.tmp-{os.getpid()}"
    _write_beautified_xlsx(tmp, ordered)
    try:
        os.replace(tmp, path)
    except PermissionError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


# --- Beautified Excel workbook ---------------------------------------------
# Styling mirrors a hand-built example workbook: navy header with white
# bold text, zebra banding, frozen header row, salary as numbers, and the job
# link as a clickable hyperlink.
_SHEET_TITLE = "Jobs Tracker"
_HEADER_FILL = PatternFill("solid", fgColor="1F4E78")
_HEADER_FONT = Font(bold=True, color="FFFFFF", size=11)
_BAND_FILL = PatternFill("solid", fgColor="F2F6FA")
_LINK_FONT = Font(color="0563C1", underline="single", size=11)
_THIN = Side(style="thin", color="D9D9D9")
_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)
_HEADER_ALIGN = Alignment(horizontal="center", vertical="center", wrap_text=True)
_CELL_ALIGN = Alignment(vertical="center")
_WRAP_ALIGN = Alignment(vertical="center", wrap_text=True)

_COL_WIDTHS = {
    "id": 13, "title": 42, "company": 24, "location": 22,
    "salary_min": 12, "salary_max": 12, "contract_type": 13, "category": 14,
    "tier": 7, "posted_date": 13, "redirect_url": 14, "date_first_seen": 15,
    "status": 10, "sponsor_match": 13, "sponsor_rating": 13, "sponsor_route": 16,
    "applied_via": 14, "date_applied": 13, "notes": 30, "description": 70,
    "source": 10, "apply_deadline": 14,
}
_WRAP_COLS = {"title", "company", "location", "notes", "description"}
_SALARY_COLS = {"salary_min", "salary_max"}

# The "Apply Shortlist" is a regenerated view of the genuine, deduped,
# sponsor-confirmed direct-employer targets, London first. It is rebuilt every
# run, but status/date_applied/applied_via/notes typed into it are harvested
# back into the store first (see _harvest_shortlist_edits), so logging an
# application on the sheet you're working from is safe. The `id` column is what
# makes that mapping exact — don't remove it.
_SHORTLIST_TITLE = "Apply Shortlist"
_SHORTLIST_COLS = [
    "id", "title", "company", "location", "tier", "salary_max", "sponsor_rating",
    "status", "date_applied", "applied_via", "notes", "redirect_url",
]
_SHORTLIST_WIDTHS = {
    "id": 13, "title": 44, "company": 26, "location": 22, "tier": 7,
    "salary_max": 12, "sponsor_rating": 9, "status": 12, "date_applied": 13,
    "applied_via": 16, "notes": 34, "redirect_url": 16,
}
_SHORTLIST_WRAP = {"title", "company", "location", "notes"}


def _write_beautified_xlsx(path, ordered):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = _SHEET_TITLE

    for ci, name in enumerate(FIELDNAMES, start=1):
        cell = ws.cell(row=1, column=ci, value=name)
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = _HEADER_ALIGN
        cell.border = _BORDER
        ws.column_dimensions[get_column_letter(ci)].width = _COL_WIDTHS.get(name, 14)
    ws.row_dimensions[1].height = 20
    ws.freeze_panes = "A2"

    for ri, row in enumerate(ordered, start=2):
        band = _BAND_FILL if ri % 2 == 0 else None
        for ci, name in enumerate(FIELDNAMES, start=1):
            cell = ws.cell(row=ri, column=ci)
            val = row.get(name, "")
            if name in _SALARY_COLS:
                n = _as_int(val)
                cell.value = n if n else None
                cell.number_format = "#,##0"
            elif name == "redirect_url" and val:
                cell.value = val
                cell.hyperlink = val
                cell.font = _LINK_FONT
            else:
                cell.value = val if val != "" else None
            cell.alignment = _WRAP_ALIGN if name in _WRAP_COLS else _CELL_ALIGN
            cell.border = _BORDER
            if band is not None:
                cell.fill = band

    _write_shortlist_sheet(wb, ordered)
    wb.save(path)


# Green banner so the shortlist reads as "these are the ones to act on".
_SHORTLIST_FILL = PatternFill("solid", fgColor="2E7D32")


def _write_shortlist_sheet(wb, ordered):
    """Add/refresh the 'Apply Shortlist' view sheet from the ordered rows."""
    shortlist = apply_shortlist(ordered)
    ws = wb.create_sheet(_SHORTLIST_TITLE)

    for ci, name in enumerate(_SHORTLIST_COLS, start=1):
        cell = ws.cell(row=1, column=ci, value=name)
        cell.fill = _SHORTLIST_FILL
        cell.font = _HEADER_FONT
        cell.alignment = _HEADER_ALIGN
        cell.border = _BORDER
        ws.column_dimensions[get_column_letter(ci)].width = _SHORTLIST_WIDTHS.get(name, 14)
    ws.row_dimensions[1].height = 20
    ws.freeze_panes = "A2"

    for ri, row in enumerate(shortlist, start=2):
        band = _BAND_FILL if ri % 2 == 0 else None
        for ci, name in enumerate(_SHORTLIST_COLS, start=1):
            cell = ws.cell(row=ri, column=ci)
            val = row.get(name, "")
            if name == "salary_max":
                n = _as_int(val)
                cell.value = n if n else None
                cell.number_format = "#,##0"
            elif name == "redirect_url" and val:
                cell.value = val
                cell.hyperlink = val
                cell.font = _LINK_FONT
            else:
                cell.value = val if val != "" else None
            cell.alignment = _WRAP_ALIGN if name in _SHORTLIST_WRAP else _CELL_ALIGN
            cell.border = _BORDER
            if band is not None:
                cell.fill = band


def _as_int(value):
    """Best-effort int for salary sorting; missing/blank -> 0."""
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def to_row(api_job, tier):
    """Map a raw Adzuna job dict into a tracker row (status defaults to 'new').

    `category` records Adzuna's own assigned category label (always present and
    useful when reading the CSV), not the search tag we queried with.
    """
    company = (api_job.get("company") or {}).get("display_name", "")
    location = (api_job.get("location") or {}).get("display_name", "")
    category = (api_job.get("category") or {}).get("label", "")
    posted = (api_job.get("created") or "")[:10]
    return {
        "id": str(api_job.get("id", "")),
        "title": api_job.get("title", ""),
        "company": company,
        "location": location,
        "salary_min": api_job.get("salary_min", ""),
        "salary_max": api_job.get("salary_max", ""),
        "contract_type": api_job.get("contract_type", ""),
        "category": category,
        "tier": tier,
        "posted_date": posted,
        "redirect_url": api_job.get("redirect_url", ""),
        "date_first_seen": dt.date.today().isoformat(),
        "status": DEFAULT_STATUS,
        "sponsor_match": "",
        "sponsor_rating": "",
        "sponsor_route": "",
        "applied_via": "",
        "date_applied": "",
        "notes": "",
        "description": _clean_snippet(api_job.get("description", "")),
    }


def attach_sponsorship(row, lookup, aliases=None):
    """Stamp sponsor_match / sponsor_rating / sponsor_route onto a row in place.

    `aliases` is the human-confirmed overlay (sponsor_check.load_aliases). It is
    optional so every existing caller keeps working; pass it to pick up mappings
    exact matching misses, e.g. Monzo -> MONZO BANK.
    """
    if aliases is None:
        aliases = sponsor_check.load_aliases()
    match, rating, route = sponsor_check.match_company(
        row["company"], lookup, aliases)
    row["sponsor_match"] = match
    row["sponsor_rating"] = rating
    row["sponsor_route"] = route
    return row


def merge_new_jobs(existing, api_jobs, tier, lookup):
    """Add jobs whose id isn't already tracked. Returns the list of NEW rows.

    Existing rows are left untouched so manual edits (status, notes, applied_via)
    survive re-runs. Dedupe is keyed on Adzuna's job id.
    """
    new_rows = []
    for job in api_jobs:
        jid = str(job.get("id", ""))
        if not jid or jid in existing:
            continue
        row = attach_sponsorship(to_row(job, tier), lookup)
        existing[jid] = row
        new_rows.append(row)
    return new_rows


def merge_rows(existing, rows):
    """Insert-only merge of already-built tracker rows. Returns the new ones.

    The sibling of merge_new_jobs for sources that build their own rows rather
    than handing over an Adzuna payload. Deliberately a separate function: the
    Adzuna path is covered by tests that assert hand-edits survive a re-run, and
    those keep their meaning only while that path stays byte-identical.

    Insert-only for the same reason as merge_new_jobs — an existing row may
    carry hand-typed status/notes/date_applied, and re-fetching must never
    overwrite them.
    """
    new_rows = []
    for row in rows:
        jid = str(row.get("id", ""))
        if not jid or jid in existing:
            continue
        existing[jid] = row
        new_rows.append(row)
    return new_rows
