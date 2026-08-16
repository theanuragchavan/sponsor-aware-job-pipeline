"""FastAPI application.

Wiring only: build the store, registry, log and action layer once, hand them to
routes as request-scoped dependencies, and translate ActionError into the single
error envelope the UI renders.

Run:  uvicorn web.api.app:app --reload
"""
from __future__ import annotations

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
from .models import (ActionResponse, CompanyDetail, CompanySummary, JobDetail,
                     JobPage, Meta, ResolveCompanyRequest, ReviewItem)
from .registry import Registry
from .settings import load_settings
from .store import TrackerStore

import sponsor_check  # noqa: E402

settings = load_settings()
store = TrackerStore(settings.tracker_path)
registry = Registry(settings.register_csv, settings.aliases_path,
                    settings.rejections_path)
log = DecisionLog(settings.decision_log_path, settings.max_log_entries)
actions = Actions(store, registry, log, settings)

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

        async def get_response(self, path: str, scope):
            try:
                return await super().get_response(path, scope)
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
                return await super().get_response("index.html", scope)

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
        capabilities={"liveness": False, "writeback": True})


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
