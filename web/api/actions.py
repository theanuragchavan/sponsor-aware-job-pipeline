"""The action layer: validate -> preview -> apply -> write back -> record.

Every action in this module runs the same five phases, and `preview=True` stops
after the second. That is deliberate and it is the whole design: the UI never
shows a button whose consequences it has not already computed and displayed.

The shape is lifted from how Palantir describes an ontology action — validate the
change against the underlying data, write it back, and record who made the call.
The validators are the interesting part. They record not just what changed but
what was checked, which check failed, and on whose written word it was overridden.

Ordering rule, which matters after a crash: **the log entry is appended before
the store is written.** An entry with no corresponding change is recoverable
evidence; a change with no entry is an untraceable mutation. When a write fails
we append a compensating `*.failed` entry rather than deleting anything.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

from . import bootstrap  # noqa: F401
from .audit import APPLICATION, CONFIRM, FAILED_SUFFIX, REJECT, STATUS
from .store import StoreLockedError

import shortlist  # noqa: E402
import sponsor_check  # noqa: E402
import tracker  # noqa: E402

STATUS_VOCAB = ("new", "applied", "ignored", "rejected", "interview", "offer",
                "closed")
APPLY_METHODS = ("company site", "greenhouse", "ashby", "lever", "linkedin",
                 "email", "other")
MIN_RATIONALE = 10
MAX_RATIONALE = 500


class ActionError(Exception):
    """A validation failure the UI must render. `code` drives the HTTP status."""

    STATUS = {
        "not_found": 404,
        "already_resolved": 409,
        "already_actioned": 409,
        "store_locked": 409,
        "stale_undo": 409,
        "register_entry_not_found": 422,
        "agency_company": 422,
        "rationale_required": 422,
        "ineligible": 422,
        "unknown_status": 422,
        "unknown_method": 422,
        "not_proposed": 422,
    }

    def __init__(self, code: str, message: str,
                 validations: list[dict] | None = None,
                 retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.validations = validations or []
        self.retryable = retryable

    @property
    def http_status(self) -> int:
        return self.STATUS.get(self.code, 400)


def ok(rule: str, detail: str = "") -> dict:
    return {"rule": rule, "result": "pass", "detail": detail}


def fail(rule: str, detail: str, *, overridable: bool = False) -> dict:
    return {"rule": rule, "result": "fail", "detail": detail,
            "overridable": overridable}


@dataclass
class ActionResult:
    applied: bool
    validations: list[dict] = field(default_factory=list)
    changes: list[dict] = field(default_factory=list)
    effects: dict[str, Any] = field(default_factory=dict)
    log_entry_id: str | None = None
    replayed: bool = False

    def as_dict(self) -> dict:
        return {
            "applied": self.applied,
            "replayed": self.replayed,
            "validations": self.validations,
            "changes": self.changes,
            "effects": self.effects,
            "log_entry_id": self.log_entry_id,
        }


def _require_rationale(rationale: str) -> dict:
    text = (rationale or "").strip()
    if len(text) < MIN_RATIONALE:
        raise ActionError(
            "rationale_required",
            f"A decision needs a reason of at least {MIN_RATIONALE} characters. "
            "It is the part of the record that is worth anything later.")
    if len(text) > MAX_RATIONALE:
        raise ActionError("rationale_required",
                          f"Keep the reason under {MAX_RATIONALE} characters.")
    return ok("rationale_present", f"{len(text)} chars")


class Actions:
    """Bound to one store + registry + log. Constructed per app, not per request."""

    def __init__(self, store, registry, log, settings):
        self.store = store
        self.registry = registry
        self.log = log
        self.settings = settings

    # --- helpers ------------------------------------------------------------

    def _actor(self, actor_id: str = "", actor_name: str = "") -> dict:
        return {
            "id": actor_id or self.settings.actor_id,
            "display_name": actor_name or self.settings.actor_name,
            "via": "web-ui",
        }

    def _replay(self, action_id: str) -> ActionResult | None:
        prior = self.log.find_by_action_id(action_id)
        if not prior:
            return None
        return ActionResult(
            applied=True, replayed=True,
            validations=prior.get("validations", []),
            effects={**prior.get("effects", {}), "rows_changed": 0},
            log_entry_id=prior.get("id"))

    def _rows_for_company(self, company: str) -> list[dict]:
        key = sponsor_check.normalize_name(company)
        return [r for r in self.store.rows().values()
                if sponsor_check.normalize_name(r.get("company", "")) == key]

    def _write_store(self, mutations: dict, entry: dict) -> int:
        """Persist, and on a lock append a compensating entry before raising."""
        try:
            return self.store.apply(mutations)
        except StoreLockedError:
            self.log.record(
                type=entry["type"] + FAILED_SUFFIX,
                entity=entry["entity"], actor=entry["actor"],
                input={"target_log_entry_id": entry["id"],
                       "reason": "store_locked"},
                validations=[], rationale="Store was locked; no rows written.",
                effects={"rows_changed": 0}, action_id="", reversible=False)
            raise ActionError(
                "store_locked",
                "The workbook is open in Excel, so no rows were changed. "
                "Close it and press Retry — your decision is already recorded.",
                retryable=True)

    # --- resolve-company ----------------------------------------------------

    def resolve_company(self, *, company: str, register_name: str,
                        rationale: str = "", source_rule: str = "manual",
                        supersede: bool = False, acknowledge_agency: bool = False,
                        action_id: str = "", preview: bool = False,
                        actor_id: str = "", actor_name: str = "") -> ActionResult:
        """Confirm that `company` is `register_name` on the Home Office register."""
        if not preview and action_id:
            replayed = self._replay(action_id)
            if replayed:
                return replayed

        validations: list[dict] = []

        # 1. the company must actually be tracked — you cannot alias a stranger
        rows = self._rows_for_company(company)
        if not rows:
            raise ActionError("not_found",
                              f"No tracked jobs for {company!r}.")
        validations.append(ok("company_is_tracked", f"{len(rows)} row(s)"))

        # 2. the register entry must exist. This is the "validate against
        #    available capacity" clause: the lookup contains Skilled Worker
        #    entries only, so existence here also proves the route.
        if not self.registry.has_entry(register_name):
            raise ActionError(
                "register_entry_not_found",
                f"{register_name!r} is not a Skilled Worker entry on the "
                "register. Nothing was changed.",
                validations=validations + [
                    fail("register_entry_exists", register_name)])
        rating = self.registry.rating_for(register_name)
        validations.append(ok("register_entry_exists",
                              f"{register_name} ({rating or 'no'} rating, "
                              f"{sponsor_check.SKILLED_WORKER_ROUTE})"))

        # 3. not already resolved elsewhere
        key = sponsor_check.normalize_name(company)
        existing = self.registry.aliases.get(key)
        if existing and existing.get("register_name") != register_name:
            if not supersede:
                raise ActionError(
                    "already_resolved",
                    f"{company} is already resolved to "
                    f"{existing.get('register_name')!r}. Supersede it only if "
                    "the earlier call was wrong.",
                    validations=validations + [
                        fail("not_already_resolved",
                             str(existing.get("register_name")),
                             overridable=True)])
            validations.append({
                "rule": "not_already_resolved", "result": "fail",
                "detail": f"superseding {existing.get('register_name')}",
                "overridden": True, "override_reason": rationale})
        else:
            validations.append(ok("not_already_resolved"))

        # 4. an agency's own licence says nothing about who is actually hiring
        if tracker.is_agency(company):
            if not acknowledge_agency:
                raise ActionError(
                    "agency_company",
                    f"{company} looks like a recruitment agency. Its sponsor "
                    "licence covers its own staff, not the roles it advertises.",
                    validations=validations + [
                        fail("not_an_agency", company, overridable=True)])
            validations.append({
                "rule": "not_an_agency", "result": "fail", "detail": company,
                "overridden": True, "override_reason": rationale})
        else:
            validations.append(ok("not_an_agency"))

        validations.append(_require_rationale(rationale))

        # --- compute the effect, identically for preview and apply ----------
        mutations, changes = {}, []
        for row in rows:
            if (row.get("sponsor_match") or "").strip().lower() == "yes":
                continue
            new = {"sponsor_match": "yes", "sponsor_rating": rating,
                   "sponsor_route": sponsor_check.SKILLED_WORKER_ROUTE}
            mutations[row["id"]] = new
            changes.append({
                "entity": f"job:{row['id']}", "title": row.get("title", ""),
                "field": "sponsor_match",
                "before": row.get("sponsor_match", ""), "after": "yes"})

        effects = {
            "company": company, "register_name": register_name,
            "rating": rating, "rows_changed": len(mutations),
            "job_ids": list(mutations),
            "shortlist_delta": self._shortlist_delta(rows, mutations),
        }

        if preview:
            return ActionResult(applied=False, validations=validations,
                                changes=changes, effects=effects)

        entry = self.log.record(
            type=CONFIRM,
            entity={"kind": "company", "id": key},
            actor=self._actor(actor_id, actor_name),
            input={"company": company, "register_name": register_name,
                   "source_rule": source_rule, "supersede": supersede,
                   "acknowledge_agency": acknowledge_agency},
            validations=validations, rationale=rationale.strip(),
            effects=effects,
            before={r["id"]: {"sponsor_match": r.get("sponsor_match", "")}
                    for r in rows if r["id"] in mutations},
            after={jid: {"sponsor_match": "yes"} for jid in mutations},
            action_id=action_id)

        raw = dict(self.registry.aliases_raw)
        raw[company] = {
            "register_name": register_name, "rating": rating,
            "confirmed": entry["ts"][:10],
            "actor": entry["actor"]["id"],
            "rationale": rationale.strip(),
            "log_entry_id": entry["id"],
        }
        sponsor_check.save_aliases(raw, str(self.settings.aliases_path))
        self.registry.invalidate_overlays()

        changed = self._write_store(mutations, entry)
        effects["rows_changed"] = changed
        return ActionResult(applied=True, validations=validations,
                            changes=changes, effects=effects,
                            log_entry_id=entry["id"])

    # --- log-application ----------------------------------------------------

    def log_application(self, *, job_id: str, applied_via: str = "company site",
                        date_applied: str = "", notes: str = "",
                        override_reason: str = "", action_id: str = "",
                        preview: bool = False, actor_id: str = "",
                        actor_name: str = "") -> ActionResult:
        """Record that an application was sent.

        The interesting validator is `eligible_to_apply`. It runs the SAME
        disqualifier the shortlist uses, so the app refuses to log an
        application to a role that needs security clearance he cannot get, or
        an internship with the wrong graduation year, or an employer with no
        sponsor licence. Those are precisely the applications that come back as
        an auto-reject, and the whole pipeline exists to stop him spending one.

        It is overridable, because a gate you cannot override is a gate people
        route around. But the override is written into the log entry with the
        reason, so "I applied anyway" is a recorded decision rather than a
        silent one.
        """
        if not preview and action_id:
            replayed = self._replay(action_id)
            if replayed:
                return replayed

        validations: list[dict] = []

        row = self.store.row(job_id)
        if row is None:
            raise ActionError("not_found", f"No job {job_id!r}.")
        validations.append(ok("job_exists", row.get("title", "")))

        current = (row.get("status") or "").strip().lower()
        if current and current != tracker.DEFAULT_STATUS:
            raise ActionError(
                "already_actioned",
                f"That one is already marked {current!r}"
                f"{' on ' + row['date_applied'] if row.get('date_applied') else ''}.",
                validations=validations + [fail("not_already_actioned", current)])
        validations.append(ok("not_already_actioned"))

        if applied_via not in APPLY_METHODS:
            raise ActionError("unknown_method",
                              f"How did you apply? One of: "
                              f"{', '.join(APPLY_METHODS)}.")
        validations.append(ok("applied_via_known", applied_via))

        when = (date_applied or dt.date.today().isoformat()).strip()
        try:
            if dt.date.fromisoformat(when) > dt.date.today():
                raise ActionError("ineligible",
                                  "That date is in the future.")
        except ValueError:
            raise ActionError("ineligible",
                              f"{when!r} is not a date (use YYYY-MM-DD).")
        validations.append(ok("date_not_future", when))

        # The gate this action exists for.
        reason = shortlist.disqualify(row, max_age=None)
        if reason:
            if not override_reason.strip():
                raise ActionError(
                    "ineligible",
                    f"This one would auto-reject you: {reason}. "
                    "Apply anyway only if you know something the filter does not.",
                    validations=validations + [
                        fail("eligible_to_apply", reason, overridable=True)])
            validations.append({
                "rule": "eligible_to_apply", "result": "fail", "detail": reason,
                "overridden": True, "override_reason": override_reason.strip()})
        else:
            validations.append(ok("eligible_to_apply"))

        mutations = {row["id"]: {
            "status": "applied", "date_applied": when,
            "applied_via": applied_via,
            **({"notes": notes.strip()} if notes.strip() else {})}}
        changes = [{"entity": f"job:{row['id']}", "title": row.get("title", ""),
                    "field": "status", "before": row.get("status", ""),
                    "after": "applied"}]
        effects = {"job_id": row["id"], "company": row.get("company", ""),
                   "title": row.get("title", ""), "date_applied": when,
                   "applied_via": applied_via, "rows_changed": 1}

        if preview:
            return ActionResult(applied=False, validations=validations,
                                changes=changes, effects=effects)

        entry = self.log.record(
            type=APPLICATION,
            entity={"kind": "job", "id": row["id"]},
            actor=self._actor(actor_id, actor_name),
            input={"job_id": row["id"], "applied_via": applied_via,
                   "date_applied": when, "notes": notes,
                   "override_reason": override_reason},
            validations=validations,
            rationale=notes.strip() or f"Applied via {applied_via}.",
            effects=effects,
            before={row["id"]: {"status": row.get("status", "")}},
            after={row["id"]: {"status": "applied"}},
            action_id=action_id)

        changed = self._write_store(mutations, entry)
        effects["rows_changed"] = changed
        return ActionResult(applied=True, validations=validations,
                            changes=changes, effects=effects,
                            log_entry_id=entry["id"])

    def _shortlist_delta(self, rows: list[dict], mutations: dict) -> int:
        """How many of these rows become shortlist-eligible once resolved."""
        n = 0
        for row in rows:
            if row["id"] not in mutations:
                continue
            probe = dict(row)
            probe["sponsor_match"] = "yes"
            if shortlist.disqualify(probe, max_age=None) is None:
                n += 1
        return n
