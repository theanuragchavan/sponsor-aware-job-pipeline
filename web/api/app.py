"""FastAPI application.

Wiring only: build the store, registry, log and action layer once, hand them to
routes as request-scoped dependencies, and translate ActionError into the single
error envelope the UI renders.

Run:  uvicorn web.api.app:app --reload
"""
from __future__ import annotations

import datetime as dt
import time
from collections import defaultdict, deque
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import bootstrap  # noqa: F401
from . import views
from .actions import ActionError, Actions
from .audit import DecisionLog
from .contacts import Contacts
from .referrals import github as gh_referrals, linkedin as li_referrals
from .demo_reset import DemoReset
from .models import (ActionResponse, CompanyDetail, CompanySummary, JobDetail,
                     JobPage, LogApplicationRequest, Meta,
                     ResolveCompanyRequest, ReviewItem,
                     SetStatusRequest)
from .registry import Registry
from .settings import load_settings
from .store import TrackerStore

import shortlist  # noqa: E402
import sponsor_check  # noqa: E402
import tracker  # noqa: E402

settings = load_settings()
store = TrackerStore(settings.tracker_path)
registry = Registry(settings.register_csv, settings.aliases_path,
                    settings.rejections_path)
log = DecisionLog(settings.decision_log_path, settings.max_log_entries)
contacts = Contacts(settings.contacts_path)
actions = Actions(store, registry, log, settings)

# Only ever constructed in demo mode. A snapshot restored over a local instance
# would overwrite the real tracker and destroy real decisions, so DemoReset
# refuses to construct without it and this refuses to build one.
demo_reset = DemoReset(settings, store, registry, log) if settings.is_demo else None

app = FastAPI(
    title="Resolve",
    description="A decision surface over the sponsor-aware job pipeline.",
    version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"])


@app.get("/manifest.webmanifest", include_in_schema=False)
def manifest() -> JSONResponse:
    """Served, not static, so the install differs by APP_MODE.

    Two identical icons where one writes a real job tracker and one writes
    fixtures is a mistake waiting to happen — the local install is blue and
    called "Resolve", the demo is amber and called "Resolve Demo". This is a
    safety property, not decoration.
    """
    demo = settings.is_demo
    variant = "demo" if demo else "local"
    return JSONResponse(
        {
            "id": f"/?mode={variant}",
            "name": "Resolve Demo" if demo else "Resolve",
            "short_name": "Resolve Demo" if demo else "Resolve",
            "description": ("Synthetic demo of the sponsor-aware job pipeline."
                            if demo else
                            "Employer identity across the UK sponsor register."),
            # Both modes open the tool. "/" is the landing, and neither the
            # installed app nor the launcher should make you click past a
            # pitch to reach your own work.
            "start_url": "/app",
            "scope": "/",
            "display": "standalone",
            "background_color": "#E0AA4A" if demo else "#2F6AE0",
            "theme_color": "#E0AA4A" if demo else "#2F6AE0",
            "icons": [
                {"src": f"/icons/icon-{variant}.svg", "sizes": "any",
                 "type": "image/svg+xml", "purpose": "any"},
                {"src": f"/icons/icon-{variant}.svg", "sizes": "any",
                 "type": "image/svg+xml", "purpose": "maskable"},
            ],
        },
        media_type="application/manifest+json")


def _mount_ui() -> None:
    """Serve the built front end from this same service, if it has been built.

    One origin instead of two. The plan called for Vercel plus a separate API,
    but that buys a CORS surface, a second deploy to keep in sync, and two URLs
    to explain, in exchange for nothing this app needs. A single container that
    serves both is less to get wrong and gives one link to put on a CV.

    In development the UI runs on Vite's own server and proxies /api here, so
    this mount is simply absent and nothing changes.
    """
    dist = Path(__file__).resolve().parent.parent / "ui" / "dist"
    if not dist.is_dir():
        return

    from fastapi.staticfiles import StaticFiles

    from starlette.exceptions import HTTPException as StarletteHTTPException

    class SpaFiles(StaticFiles):
        """Fall back to index.html so client-side routes survive a refresh.

        StaticFiles *raises* HTTPException(404) rather than returning a 404
        response, so catching the exception is the only thing that works —
        inspecting `response.status_code` silently never fires.
        """

        @staticmethod
        def _cache(response, path: str):
            """Two rules, and getting them the wrong way round is a real bug.

            `index.html` names the hashed bundle for THIS build, so a browser
            that caches it keeps loading yesterday's app and no amount of
            rebuilding helps — the fix looks like "press ctrl-shift-R forever",
            which is not a fix. It must revalidate every load.

            `/assets/*` is content-hashed by Vite: a changed file gets a changed
            name, so those can be cached hard and effectively forever.
            """
            if path.startswith("assets/") or path.startswith("assets\\"):
                response.headers["cache-control"] = \
                    "public, max-age=31536000, immutable"
            else:
                response.headers["cache-control"] = "no-cache"
            return response

        async def get_response(self, path: str, scope):
            try:
                return self._cache(await super().get_response(path, scope), path)
            except StarletteHTTPException as exc:
                if exc.status_code != 404:
                    raise
                # An unknown /api path must stay a 404. Serving index.html for
                # it hands the caller HTML with a 200 for a request that failed,
                # which turns every typo'd endpoint into a confusing parse error
                # instead of an honest "no such route".
                #
                # StaticFiles hands us an OS-native path — 'api\\nope' on
                # Windows, 'api/node' on Linux — so this has to normalise before
                # comparing. Matching on "api/" alone works on the deployment
                # target and silently fails on the development machine, which is
                # the worst way round to get it wrong.
                head = path.replace("\\", "/").split("/", 1)[0]
                if head == "api":
                    raise
                return self._cache(
                    await super().get_response("index.html", scope),
                    "index.html")

    app.mount("/", SpaFiles(directory=str(dist), html=True), name="ui")


@app.on_event("startup")
def _warm() -> None:
    """Pay the 2.3s workbook load and the 0.8s register load at boot.

    Without this the first visitor absorbs both, which on a free tier that
    sleeps means the cold-start penalty lands on exactly the person you most
    want to impress.
    """
    store.rows()
    registry.lookup
    if demo_reset is not None:
        # Before the server accepts connections, so the snapshot is the image's
        # data and not some early visitor's decisions. Get this order wrong and
        # every later "reset" faithfully restores their work instead.
        n = demo_reset.capture()
        print(f"demo snapshot captured: {n} files, "
              f"resetting every {demo_reset.interval // 3600}h")


@app.middleware("http")
async def _demo_housekeeping(request: Request, call_next):
    """Restore the demo on a timer.

    Driven by traffic rather than a background thread: a thread would have to
    be reaped on shutdown and would fire on an idle instance for no one's
    benefit. Nobody visiting means nothing to tidy.
    """
    if demo_reset is not None:
        demo_reset.maybe_reset()
    return await call_next(request)


@app.exception_handler(ActionError)
async def _action_error(_request: Request, exc: ActionError):
    return JSONResponse(
        status_code=exc.http_status,
        content={"error": {"code": exc.code, "message": exc.message,
                           "validations": exc.validations,
                           "retryable": exc.retryable}})


# --- rate limiting (demo only; 0 disables) ----------------------------------

_hits: dict[str, deque] = defaultdict(deque)


def rate_limit(request: Request) -> None:
    if not settings.rate_limit_per_min:
        return
    ip = request.client.host if request.client else "?"
    now = time.time()
    bucket = _hits[ip]
    while bucket and now - bucket[0] > 60:
        bucket.popleft()
    if len(bucket) >= settings.rate_limit_per_min:
        raise HTTPException(
            status_code=429,
            detail=f"Slow down — {settings.rate_limit_per_min} actions/min.")
    bucket.append(now)


def actor(x_actor: str = Header(default=""),
          x_actor_name: str = Header(default="")) -> tuple[str, str]:
    """Attribution, not authentication. Stated plainly on the About page."""
    return x_actor, x_actor_name


# --- reads ------------------------------------------------------------------

@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.get("/api/meta", response_model=Meta)
def meta() -> Meta:
    rows = store.rows()
    companies = {(r.get("company") or "").strip() for r in rows.values()}
    return Meta(
        mode=settings.mode,
        actor={"id": settings.actor_id, "display_name": settings.actor_name},
        dataset={"rows": len(rows), "companies": len(companies - {""}),
                 "aliases": len(registry.aliases_raw)},
        register_info={"entries": len(registry.lookup),
                       "route": sponsor_check.SKILLED_WORKER_ROUTE},
        capabilities={"liveness": False, "writeback": True},
        demo_reset=({"every_hours": demo_reset.interval // 3600,
                     "next_in_seconds": demo_reset.seconds_until_due(),
                     "resets_so_far": demo_reset.resets}
                    if demo_reset is not None else None))


@app.get("/api/jobs", response_model=JobPage)
def jobs(q: str = "", company: str = "", status: str = "", tier: str = "",
         source: str = "", sponsor: str = "", eligible: bool | None = None,
         sort: str = "score", limit: int = Query(50, le=500),
         offset: int = 0) -> JobPage:
    items = [views.job_summary(r) for r in store.rows().values()]

    def keep(j) -> bool:
        if q and q.lower() not in f"{j.title} {j.company}".lower():
            return False
        if company and j.company != company:
            return False
        if status and j.status != status:
            return False
        if tier and j.tier != tier:
            return False
        if source and j.source != source:
            return False
        if sponsor and j.sponsor_match != sponsor:
            return False
        if eligible is not None and j.eligible != eligible:
            return False
        return True

    items = [j for j in items if keep(j)]
    facets = {
        "status": _count(items, "status"),
        "tier": _count(items, "tier"),
        "source": _count(items, "source"),
        "sponsor_match": _count(items, "sponsor_match"),
    }
    if sort == "age":
        items.sort(key=lambda j: (j.age_days is None, j.age_days or 0))
    else:
        items.sort(key=lambda j: (-j.score, j.company.lower()))
    return JobPage(total=len(items), items=items[offset:offset + limit],
                   facets=facets)


def _count(items, field: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in items:
        value = str(getattr(item, field) or "—")
        out[value] = out.get(value, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


@app.get("/api/jobs/{job_id}", response_model=JobDetail)
def job(job_id: str) -> JobDetail:
    row = store.row(job_id)
    if row is None:
        raise ActionError("not_found", f"No job {job_id!r}.")
    return views.job_detail(row, registry, log)


@app.get("/api/companies", response_model=list[CompanySummary])
def companies(q: str = "", resolved: bool | None = None,
              limit: int = Query(200, le=1000)) -> list[CompanySummary]:
    out = views.company_summaries(store.rows().values(), registry)
    if q:
        out = [c for c in out if q.lower() in c.company.lower()]
    if resolved is not None:
        out = [c for c in out
               if (c.resolution.method != "none") is resolved]
    return out[:limit]


@app.get("/api/companies/{key}", response_model=CompanyDetail)
def company(key: str) -> CompanyDetail:
    rows = list(store.rows().values())
    for name in {(r.get("company") or "").strip() for r in rows}:
        if name and sponsor_check.normalize_name(name) == key.upper():
            detail = views.company_detail(name, rows, registry, log)
            if detail:
                return detail
    raise ActionError("not_found", f"No company {key!r}.")


@app.get("/api/review", response_model=list[ReviewItem])
def review() -> list[ReviewItem]:
    return views.review_queue(store.rows().values(), registry)


@app.get("/api/register/search")
def register_search(q: str, limit: int = Query(20, le=100)) -> list[dict]:
    return [{"register_name": name, "rating": rating}
            for name, rating in registry.search(q, limit)]


@app.get("/api/audit")
def audit(entity: str = "", type: str = "",
          limit: int = Query(100, le=500)) -> list[dict]:
    return log.read(entity=entity or None, type=type or None, limit=limit)


@app.get("/api/audit/verify")
def audit_verify() -> dict:
    return log.verify(registry.aliases_raw)


# --- actions ----------------------------------------------------------------

@app.get("/api/summary")
def summary() -> dict:
    """The numbers the overview screen opens with.

    One request rather than the client counting over a 3,308-row job list:
    the counting is trivial here and shipping the whole store to the browser
    to do it there is not.
    """
    rows = list(store.rows().values())
    applied, ready, blocked, no_sponsor = [], [], 0, 0

    for row in rows:
        status = (row.get("status") or "").strip().lower()
        if status and status != tracker.DEFAULT_STATUS:
            applied.append(row)
            continue
        if shortlist.disqualify(row, max_age=None) is not None:
            blocked += 1
            if (row.get("sponsor_match") or "").lower() == "no":
                no_sponsor += 1
            continue
        ready.append(row)

    waiting = sum(1 for r in applied
                  if (r.get("status") or "").strip().lower() == "applied")
    return {
        "tracked": len(rows),
        "ready_to_apply": len(ready),
        "applied": len(applied),
        "awaiting_reply": waiting,
        "filtered_out": blocked,
        "filtered_no_sponsor": no_sponsor,
        "needs_a_name_decision": len(views.review_queue(rows, registry)),
    }


@app.post("/api/actions/log-application/preview", response_model=ActionResponse)
def log_application_preview(body: LogApplicationRequest) -> ActionResponse:
    return ActionResponse(**actions.log_application(
        **body.model_dump(), preview=True).as_dict())


@app.post("/api/actions/log-application", response_model=ActionResponse,
          dependencies=[Depends(rate_limit)])
def log_application(body: LogApplicationRequest,
                    who: tuple[str, str] = Depends(actor)) -> ActionResponse:
    return ActionResponse(**actions.log_application(
        **body.model_dump(), preview=False,
        actor_id=who[0], actor_name=who[1]).as_dict())


@app.post("/api/actions/set-status", response_model=ActionResponse,
          dependencies=[Depends(rate_limit)])
def set_status(body: SetStatusRequest,
               who: tuple[str, str] = Depends(actor)) -> ActionResponse:
    return ActionResponse(**actions.set_status(
        **body.model_dump(), actor_id=who[0], actor_name=who[1]).as_dict())


@app.get("/api/referrals/{company}")
def referrals(company: str, contributors: bool = False) -> dict:
    """Who might refer you into this company.

    Three groups, deliberately not merged. GitHub gives real people but only
    those who made org membership public; LinkedIn gives the alumni and language
    signals GitHub cannot, but only as searches HE runs; saved contacts are what
    he has already found. Presenting them as one ranked list would imply an
    equivalence that is not there.
    """
    try:
        found = gh_referrals.people_at(company, include_contributors=contributors)
    except gh_referrals.GitHubUnavailable as exc:
        found = {"org": None, "people": [], "error": str(exc)}
    return {
        "company": company,
        "github": found,
        "linkedin_searches": li_referrals.searches_for(company),
        "saved": contacts.for_company(company),
    }


@app.post("/api/contacts")
def save_contact(body: dict) -> dict:
    return contacts.upsert(
        company=body.get("company", ""), name=body.get("name", ""),
        source=body.get("source", "manual"), handle=body.get("handle", ""),
        url=body.get("url", ""), location=body.get("location", ""),
        signals=body.get("signals") or [], job_id=body.get("job_id", ""),
        notes=body.get("notes", ""))


@app.post("/api/contacts/{contact_id}/status")
def set_contact_status(contact_id: str, body: dict) -> dict:
    status = (body.get("status") or "").strip().lower()
    from .contacts import STATUSES
    if status not in STATUSES:
        raise ActionError("unknown_status",
                          f"One of: {', '.join(STATUSES)}.")
    row = contacts.set_status(contact_id, status, body.get("note", ""))
    if row is None:
        raise ActionError("not_found", f"No contact {contact_id!r}.")
    return row


@app.get("/api/follow-ups")
def follow_ups(after_days: int = 10) -> list[dict]:
    """Applications that have gone quiet.

    Derived on read rather than stored, so it can never disagree with the
    tracker. An application with no date is not silently included — it is
    reported with `days: null`, because "unknown" and "overdue" are different
    things and only one of them needs chasing.
    """
    out = []
    today = dt.date.today()
    for row in store.rows().values():
        if (row.get("status") or "").strip().lower() != "applied":
            continue
        raw = (row.get("date_applied") or "").strip()
        days = None
        if raw:
            try:
                days = (today - dt.date.fromisoformat(raw)).days
            except ValueError:
                days = None
        if days is None or days >= after_days:
            out.append({"id": row["id"], "company": row.get("company", ""),
                        "title": row.get("title", ""), "date_applied": raw,
                        "days": days,
                        "applied_via": row.get("applied_via", "")})
    out.sort(key=lambda r: (r["days"] is None, -(r["days"] or 0)))
    return out


@app.post("/api/actions/resolve-company/preview", response_model=ActionResponse)
def resolve_preview(body: ResolveCompanyRequest) -> ActionResponse:
    result = actions.resolve_company(**body.model_dump(), preview=True)
    return ActionResponse(**result.as_dict())


@app.post("/api/actions/resolve-company", response_model=ActionResponse,
          dependencies=[Depends(rate_limit)])
def resolve(body: ResolveCompanyRequest,
            who: tuple[str, str] = Depends(actor)) -> ActionResponse:
    result = actions.resolve_company(**body.model_dump(), preview=False,
                                     actor_id=who[0], actor_name=who[1])
    return ActionResponse(**result.as_dict())

# Registered last, deliberately: a catch-all mount at "/" shadows anything
# declared after it, so every /api route above must already exist.
_mount_ui()
