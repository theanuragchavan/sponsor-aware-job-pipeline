"""Read-side projections: tracker rows -> the shapes the UI renders.

Kept apart from the routers so the routers stay thin and this stays testable
without HTTP. Everything here is pure over (rows, registry) — no I/O, no writes.
"""
from __future__ import annotations

from . import bootstrap  # noqa: F401
from .models import (CompanyDetail, CompanySummary, JobDetail, JobSummary,
                     Resolution, ReviewItem, Suggestion)

import shortlist  # noqa: E402
import sponsor_check  # noqa: E402
import sponsor_review  # noqa: E402
import tracker  # noqa: E402


def resolution_for(company: str, registry) -> Resolution:
    """How this company is matched, and on whose authority.

    Three sources, checked in order of how specific the evidence is:

      alias  a human confirmed this pair in the review queue, with a reason
      board  data/ats_boards.csv maps it, curated by hand when the board was added
      exact  the company name is verbatim on the register

    Leaving `board` out is what made this report "no entry found" for Palantir,
    Faculty, OpenAI, Monzo and ten others, all of them correctly verified —
    their rows said yes and nothing in this function could explain why.
    """
    key = sponsor_check.normalize_name(company)

    alias = registry.aliases.get(key)
    if alias:
        return Resolution(
            method="alias",
            register_name=alias.get("register_name", ""),
            rating=alias.get("rating", ""),
            confirmed_by=alias.get("actor", ""),
            confirmed_at=alias.get("confirmed", ""),
            rationale=alias.get("rationale", ""))

    board = registry.board_names.get(key)
    if board:
        return Resolution(method="board", register_name=board,
                          rating=registry.rating_for(board),
                          rationale="mapped in the board registry when this "
                                    "employer's job board was added")

    if key in registry.lookup:
        return Resolution(method="exact", register_name=key,
                          rating=registry.lookup[key])
    return Resolution(method="none")


def job_summary(row: dict) -> JobSummary:
    reason = shortlist.disqualify(row, max_age=None)
    points, _why = shortlist.score(row)
    return JobSummary(
        id=str(row.get("id", "")),
        title=row.get("title", ""),
        company=row.get("company", ""),
        location=row.get("location", ""),
        tier=str(row.get("tier", "")),
        status=row.get("status", ""),
        source=tracker.source_of(row.get("id", "")),
        posted_date=row.get("posted_date", ""),
        age_days=shortlist.age_days(row),
        salary_max=str(row.get("salary_max", "")),
        sponsor_match=row.get("sponsor_match", ""),
        sponsor_rating=row.get("sponsor_rating", ""),
        redirect_url=row.get("redirect_url", ""),
        score=points,
        eligible=reason is None,
        disqualified_reason=reason)


def job_detail(row: dict, registry, log) -> JobDetail:
    points, why = shortlist.score(row)
    base = job_summary(row).model_dump()
    desc = row.get("description", "") or ""
    hint = shortlist.CLEARANCE_HINT.search(desc)
    return JobDetail(
        **base,
        description=desc,
        apply_deadline=row.get("apply_deadline", ""),
        notes=row.get("notes", ""),
        date_applied=row.get("date_applied", ""),
        applied_via=row.get("applied_via", ""),
        score_reasons=why,
        clearance_hint=hint.group(0) if hint else "",
        resolution=resolution_for(row.get("company", ""), registry),
        decision_trail=log.read(entity=f"job:{row.get('id')}", limit=20))


def _tech_titles(rows: list[dict]) -> list[str]:
    return [r.get("title", "") for r in rows
            if shortlist.TECH_SIGNAL.search(r.get("title", ""))
            and not shortlist.NON_TECH.search(r.get("title", ""))]


def _group(rows) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for row in rows:
        company = (row.get("company") or "").strip()
        if company:
            out.setdefault(company, []).append(row)
    return out


def company_summaries(rows, registry) -> list[CompanySummary]:
    out = []
    for company, group in _group(rows).items():
        tiers = sorted({str(r.get("tier", "")).strip() for r in group
                        if str(r.get("tier", "")).strip()})
        out.append(CompanySummary(
            key=sponsor_check.normalize_name(company),
            company=company, jobs=len(group),
            tech_jobs=len(_tech_titles(group)), tiers=tiers,
            is_agency=tracker.is_agency(company),
            resolution=resolution_for(company, registry)))
    return sorted(out, key=lambda c: (-c.jobs, c.company.lower()))


def suggestions_for(company: str, registry) -> list[Suggestion]:
    return [Suggestion(register_name=name, rating=rating, why=why,
                       rejected=registry.is_rejected(company, name))
            for name, rating, why in registry.suggest(company)]


def company_detail(company: str, rows, registry, log) -> CompanyDetail | None:
    group = _group(rows).get(company)
    if not group:
        return None
    summaries = company_summaries(group, registry)
    base = summaries[0].model_dump()
    return CompanyDetail(
        **base,
        suggestions=suggestions_for(company, registry),
        job_list=[job_summary(r) for r in group],
        decision_trail=log.read(
            entity=f"company:{sponsor_check.normalize_name(company)}",
            limit=20))


def review_queue(rows, registry) -> list[ReviewItem]:
    """The decision queue. Delegates to the CLI's own `candidates()` so the web
    app and `sponsor_review.py` can never disagree about what needs deciding.

    Rejections are filtered here rather than inside `candidates()`: that keeps
    the reuse boundary clean and means an old CLI run still sees its own view.
    """
    entries = sponsor_review.candidates(
        list(rows), registry.lookup, registry.aliases)

    # `candidates()` groups by the raw company string, so "Booth Welsh" and
    # "Booth Welsh Ltd" arrive as two entries. They normalise to one key, and
    # `load_aliases` normalises on read — so a single confirmation already
    # resolves both. Listing them separately asks for two decisions where one
    # will do, and the second silently vanishes once the first is made, which
    # looks like a bug even though it isn't. Merge them here.
    merged: dict[str, ReviewItem] = {}
    for entry in entries:
        company = entry["company"]
        hits = [Suggestion(register_name=name, rating=rating, why=why)
                for name, rating, why in entry["hits"]
                if not registry.is_rejected(company, name)]
        if not hits:
            continue          # every candidate already rejected by a human

        key = sponsor_check.normalize_name(company)
        seen = merged.get(key)
        if seen is None:
            merged[key] = ReviewItem(
                key=key, company=company, rows=entry["rows"],
                tiers=entry["tiers"], tech_titles=entry["tech_titles"],
                priority=bool(entry["score"][0]), suggestions=hits)
            continue

        # Keep the shortest spelling as the label — "Booth Welsh" reads better
        # than "Booth Welsh Ltd" and normalises identically.
        if len(company) < len(seen.company):
            seen.company = company
        seen.rows += entry["rows"]
        seen.tiers = sorted(set(seen.tiers) | set(entry["tiers"]))
        seen.tech_titles = (seen.tech_titles + entry["tech_titles"])[:3]
        seen.priority = seen.priority or bool(entry["score"][0])
        known = {s.register_name for s in seen.suggestions}
        seen.suggestions += [h for h in hits if h.register_name not in known]

    return list(merged.values())
