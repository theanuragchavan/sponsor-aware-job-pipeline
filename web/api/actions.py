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
