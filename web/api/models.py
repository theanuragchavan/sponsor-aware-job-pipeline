"""Pydantic models. These are the OpenAPI spec, and the OpenAPI spec is the
TypeScript client, so every field named here is type-checked in the browser.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class Validation(BaseModel):
    rule: str
    result: Literal["pass", "fail"]
    detail: str = ""
    overridable: bool = False
    overridden: bool = False
    override_reason: str = ""


class Change(BaseModel):
    entity: str
    title: str = ""
    field: str
    before: str = ""
    after: str = ""


class ErrorBody(BaseModel):
    code: str
    message: str
    validations: list[Validation] = Field(default_factory=list)
    retryable: bool = False


class ErrorEnvelope(BaseModel):
    error: ErrorBody


class Resolution(BaseModel):
    method: Literal["exact", "alias", "board", "none"]
    register_name: str = ""
    rating: str = ""
    confirmed_by: str = ""
    confirmed_at: str = ""
    rationale: str = ""


class JobSummary(BaseModel):
    id: str
    title: str
    company: str
    location: str = ""
    tier: str = ""
    status: str = ""
    source: str = ""
    posted_date: str = ""
    age_days: int | None = None
    salary_max: str = ""
    sponsor_match: str = ""
    sponsor_rating: str = ""
    redirect_url: str = ""
    score: int = 0
    eligible: bool = True
    disqualified_reason: str | None = None


class JobDetail(JobSummary):
    description: str = ""
    apply_deadline: str = ""
    notes: str = ""
    date_applied: str = ""
    applied_via: str = ""
    score_reasons: list[str] = Field(default_factory=list)
    clearance_hint: str = ""
    resolution: Resolution | None = None
    decision_trail: list[dict[str, Any]] = Field(default_factory=list)


class Suggestion(BaseModel):
    register_name: str
    rating: str = ""
    why: str = ""
    rejected: bool = False


class CompanySummary(BaseModel):
    key: str
    company: str
    jobs: int
    tech_jobs: int
    tiers: list[str] = Field(default_factory=list)
    is_agency: bool = False
    resolution: Resolution


class CompanyDetail(CompanySummary):
    suggestions: list[Suggestion] = Field(default_factory=list)
    job_list: list[JobSummary] = Field(default_factory=list)
    decision_trail: list[dict[str, Any]] = Field(default_factory=list)


class ReviewItem(BaseModel):
    key: str
    company: str
    rows: int
    tiers: list[str] = Field(default_factory=list)
    tech_titles: list[str] = Field(default_factory=list)
    priority: bool = False
    suggestions: list[Suggestion] = Field(default_factory=list)


class Meta(BaseModel):
    mode: Literal["local", "demo"]
    actor: dict[str, str]
    dataset: dict[str, Any]
    # `register` would shadow BaseModel.register; the wire name stays `register`
    # so the TS client and the UI are unaffected.
    register_info: dict[str, Any] = Field(serialization_alias="register",
                                          validation_alias="register")
    capabilities: dict[str, bool]
    demo_reset: dict[str, Any] | None = None

    model_config = {"populate_by_name": True}


class JobPage(BaseModel):
    total: int
    items: list[JobSummary]
    facets: dict[str, dict[str, int]] = Field(default_factory=dict)


# --- action payloads --------------------------------------------------------

class ResolveCompanyRequest(BaseModel):
    action_id: str = ""
    company: str
    register_name: str
    rationale: str = ""
    source_rule: str = "manual"
    supersede: bool = False
    acknowledge_agency: bool = False


class LogApplicationRequest(BaseModel):
    action_id: str = ""
    job_id: str
    applied_via: str = "company site"
    date_applied: str = ""
    notes: str = ""
    #: sha256 of the CV actually attached. Checked against the attestation
    #: store; blank fails the gate, which is overridable with a written reason.
    cv_sha256: str = ""
    override_reason: str = ""


class SetStatusRequest(BaseModel):
    action_id: str = ""
    job_id: str
    status: str
    note: str = ""


class ActionResponse(BaseModel):
    applied: bool
    replayed: bool = False
    validations: list[Validation] = Field(default_factory=list)
    changes: list[Change] = Field(default_factory=list)
    effects: dict[str, Any] = Field(default_factory=dict)
    log_entry_id: str | None = None
