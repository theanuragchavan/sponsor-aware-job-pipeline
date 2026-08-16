"""Tests for the web action layer: validation, writeback, audit, idempotency.

These pin the guarantees that make the app safe to point at a real store, and
every one of them exists because the failure it guards would be silent:

  - a wrong resolve puts a job on the shortlist that cannot legally be taken
  - a whole-snapshot write deletes rows the 09:00 run inserted minutes earlier
  - a locked workbook returns success while the change goes nowhere
  - a double-submitted action applies twice
  - demo mode writes the real store

Like the rest of this suite they build their own workbook and their own
miniature register. Nothing here reads the live store, and nothing needs a
network: SPONSOR_REGISTER_CSV points `sponsor_check.ensure_register` at a
fixture, so the offline guarantee is structural rather than hopeful.

Run:  python tests/test_web_api.py
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sponsor_check  # noqa: E402
import tracker  # noqa: E402

from web.api.actions import ActionError, Actions  # noqa: E402
from web.api.audit import DecisionLog  # noqa: E402
from web.api.demo_reset import DemoReset  # noqa: E402
from web.api.registry import Registry  # noqa: E402
from web.api.settings import Settings  # noqa: E402
from web.api.store import StoreLockedError, TrackerStore  # noqa: E402

REGISTER_CSV = """Organisation Name,Town/City,County,Type & Rating,Route
MONZO BANK,London,,Worker (A rating),Skilled Worker
Roofoods Ltd T/A Deliveroo,London,,Worker (A rating),Skilled Worker
Northwind Systems,Leeds,,Worker (B rating),Skilled Worker
Amazon Charitable Trust,London,,Worker (A rating),Skilled Worker
Kestrel Resourcing,London,,Worker (A rating),Skilled Worker
Someone Else Entirely,Hull,,Worker (A rating),Temporary Worker - Creative Worker
"""


def _row(jid, title="Solutions Engineer", company="Monzo", **over):
    row = {name: "" for name in tracker.FIELDNAMES}
    row.update({
        "id": str(jid), "title": title, "company": company,
        "location": "London", "salary_max": "65000", "tier": "1",
        "date_first_seen": "2026-08-01", "status": tracker.DEFAULT_STATUS,
        "sponsor_match": "no", "sponsor_rating": "",
    })
    row.update(over)
    return row


def _env(*rows):
    """A complete isolated world: store, register, overlays, log, actions."""
    tmp = tempfile.mkdtemp(prefix="webapi-")
    store_path = os.path.join(tmp, "store.xlsx")
    tracker._write_beautified_xlsx(store_path, list(rows) or [_row("1")])

    reg_path = os.path.join(tmp, "register.csv")
    with open(reg_path, "w", encoding="utf-8") as fh:
        fh.write(REGISTER_CSV)
    os.environ["SPONSOR_REGISTER_CSV"] = reg_path

    settings = Settings(
        mode="local", tracker_path=store_path,
        aliases_path=os.path.join(tmp, "aliases.json"),
        rejections_path=os.path.join(tmp, "rejections.json"),
        decision_log_path=os.path.join(tmp, "log.jsonl"),
        contacts_path=os.path.join(tmp, "contacts.json"),
        register_csv=reg_path, actor_id="tester", actor_name="Tester",
        cors_origins=(), rate_limit_per_min=0, max_log_entries=0)

    store = TrackerStore(store_path)
    registry = Registry(reg_path, settings.aliases_path,
                        settings.rejections_path)
    log = DecisionLog(settings.decision_log_path)
    return Actions(store, registry, log, settings), store, log, registry, tmp


def _resolve(actions, **over):
    kwargs = dict(company="Monzo", register_name="MONZO BANK",
                  rationale="Register records the legal entity, not the brand.")
    kwargs.update(over)
    return actions.resolve_company(**kwargs)


# --- the register gate ------------------------------------------------------

def test_resolving_to_a_missing_register_entry_is_refused():
    """The 'validate against available capacity' clause. Without it the app
    happily writes an alias to a company that holds no licence at all."""
    actions, _store, log, _reg, _tmp = _env()
    try:
        _resolve(actions, register_name="NOT ON THE REGISTER")
    except ActionError as exc:
        assert exc.code == "register_entry_not_found", exc.code
        assert log.entries() == [], "a refused action must not be logged"
        return
    raise AssertionError("resolved against an entry that does not exist")


def test_a_non_skilled_worker_route_is_not_a_register_entry():
    """The lookup is Skilled Worker only, so route validation comes free —
    but only if the lookup is genuinely the thing being consulted."""
    actions, _s, _l, _r, _tmp = _env()
    try:
        _resolve(actions, register_name="SOMEONE ELSE ENTIRELY")
    except ActionError as exc:
        assert exc.code == "register_entry_not_found", exc.code
        return
    raise AssertionError("a Creative Worker licence passed as Skilled Worker")


def test_agency_is_refused_then_recorded_when_overridden():
    """An agency's licence covers its own staff, not the roles it advertises.
    Overriding is allowed; doing it silently is not."""
    actions, _s, log, _r, _tmp = _env(_row("1", company="Kestrel Resourcing"))
    try:
        _resolve(actions, company="Kestrel Resourcing",
                 register_name="KESTREL RESOURCING")
        raise AssertionError("an agency was resolved without acknowledgement")
    except ActionError as exc:
        assert exc.code == "agency_company", exc.code

    res = _resolve(actions, company="Kestrel Resourcing",
                   register_name="KESTREL RESOURCING", acknowledge_agency=True)
    assert res.applied
    entry = log.entries()[-1]
    overridden = [v for v in entry["validations"]
                  if v["rule"] == "not_an_agency" and v.get("overridden")]
    assert overridden, "the override left no trace in the log"
    assert overridden[0]["override_reason"], "no reason recorded"


def test_a_decision_without_a_reason_is_refused():
    actions, _s, _l, _r, _tmp = _env()
    for bad in ("", "   ", "ok"):
        try:
            _resolve(actions, rationale=bad)
        except ActionError as exc:
            assert exc.code == "rationale_required", exc.code
            continue
        raise AssertionError(f"rationale {bad!r} was accepted")


def test_resolving_an_untracked_company_is_refused():
    actions, _s, _l, _r, _tmp = _env()
    try:
        _resolve(actions, company="Never Heard Of Them")
    except ActionError as exc:
        assert exc.code == "not_found", exc.code
        return
    raise AssertionError("aliased a company with no tracked jobs")


# --- preview must equal apply -----------------------------------------------

def test_preview_computes_the_effect_and_writes_nothing():
    """The number the UI shows before you commit has to be the real number,
    or the confirmation dialog is decoration."""
    actions, store, log, _r, _tmp = _env(
        _row("1"), _row("2"), _row("3", company="Northwind Systems"))

    pre = _resolve(actions, preview=True)
    assert pre.applied is False
    assert pre.effects["rows_changed"] == 2, pre.effects
    assert log.entries() == [], "preview wrote to the log"
    assert all(r["sponsor_match"] == "no"
               for r in store.rows().values() if r["company"] == "Monzo")

    post = _resolve(actions)
    assert post.applied is True
    assert post.effects["rows_changed"] == pre.effects["rows_changed"], \
        "preview promised a different number than apply delivered"


# --- writeback --------------------------------------------------------------

def test_resolving_restamps_only_the_sponsor_columns():
    """status/notes/date_applied are hand-maintained. The whole reason the
    alias overlay exists is that a rebuild must never touch them."""
    actions, store, _l, _r, _tmp = _env(
        _row("1", status="applied", notes="sent 14 Aug",
             date_applied="2026-08-14", applied_via="company site"))
    _resolve(actions)
    row = store.rows()["1"]
    assert row["sponsor_match"] == "yes"
    assert row["sponsor_rating"] == "A"
    assert row["status"] == "applied"
    assert row["notes"] == "sent 14 Aug"
    assert row["date_applied"] == "2026-08-14"
    assert row["applied_via"] == "company site"


def _insert_externally(store, jid="9999"):
    """Another writer — the 09:00 run — lands a new row while we hold a snapshot."""
    fresh = tracker.load_tracker(str(store.path))
    fresh[jid] = _row(jid, title="Graduate Software Engineer",
                      company="Northwind Systems")
    tracker.save_tracker(str(store.path), fresh)


def test_apply_does_not_delete_rows_written_since_the_snapshot():
    """The 09:00 run and the API both write. If a tab holds a snapshot from
    08:55 and we save it wholesale, everything main.py inserted at 09:00 is
    deleted — silently, permanently, noticed weeks later if at all.

    This drives `apply()` directly and never calls `rows()` in between, because
    that is the only shape that actually holds the race open. Going through
    `resolve_company` does NOT test this: it calls `rows()` first, which
    re-reads on the changed stamp and refreshes the snapshot before the write,
    so a genuinely broken `apply` still passes. That version of this test was
    decorative and is now the assertion below it.
    """
    _actions, store, _l, _r, _tmp = _env(_row("1"))
    store.rows()                                   # snapshot taken at 08:55
    _insert_externally(store)                      # the 09:00 run lands

    store.apply({"1": {"sponsor_match": "yes"}})   # no rows() call in between

    after = tracker.load_tracker(str(store.path))
    assert "9999" in after, "the concurrently inserted row was deleted"
    assert after["1"]["sponsor_match"] == "yes"


def test_resolving_also_preserves_concurrently_inserted_rows():
    """The same guarantee through the real entry point, end to end."""
    actions, store, _l, _r, _tmp = _env(_row("1"))
    store.rows()
    _insert_externally(store)

    _resolve(actions)

    after = tracker.load_tracker(str(store.path))
    assert "9999" in after, "the concurrently inserted row was deleted"
    assert after["1"]["sponsor_match"] == "yes"


def test_a_locked_store_reports_failure_and_leaves_no_side_file():
    """save_tracker's default writes a .LOCKED-* file nothing ever reads back.
    For a button click that is the worst outcome: a success, and the change is
    gone. Interactive writes must raise instead."""
    actions, store, log, _r, tmp = _env(_row("1"))

    original = tracker._atomic_write

    def _locked(path, ordered):
        raise PermissionError("open in Excel")

    tracker._atomic_write = _locked
    try:
        _resolve(actions)
        raise AssertionError("a locked store reported success")
    except ActionError as exc:
        assert exc.code == "store_locked", exc.code
        assert exc.retryable is True
    finally:
        tracker._atomic_write = original

    side = [f for f in os.listdir(tmp) if ".LOCKED-" in f]
    assert side == [], f"wrote a side file nobody reads: {side}"

    types = [e["type"] for e in log.entries()]
    assert types[-1].endswith(".failed"), \
        f"no compensating entry after a failed write: {types}"


# --- the audit trail --------------------------------------------------------

def test_the_log_records_what_was_checked_not_just_what_changed():
    actions, _s, log, _r, _tmp = _env(_row("1"))
    _resolve(actions)
    entry = log.entries()[-1]

    rules = {v["rule"] for v in entry["validations"]}
    assert {"register_entry_exists", "not_an_agency", "not_already_resolved",
            "rationale_present", "company_is_tracked"} <= rules, rules
    assert entry["actor"]["id"] == "tester"
    assert entry["rationale"]
    assert entry["before"]["1"]["sponsor_match"] == "no"
    assert entry["after"]["1"]["sponsor_match"] == "yes"


def test_the_alias_file_is_a_projection_of_the_log():
    """If the log is the source of truth, it must be able to rebuild the file."""
    actions, _s, log, reg, _tmp = _env(_row("1"))
    _resolve(actions)
    assert log.replay_aliases() == reg.aliases_raw


def test_verify_reports_cli_written_aliases_as_unattributed():
    """The 29 aliases confirmed before this log existed have no entries. That
    gap is reported, never backfilled with an actor who did not decide."""
    actions, _s, log, reg, _tmp = _env(_row("1"))
    sponsor_check.save_aliases(
        {"Deliveroo": {"register_name": "ROOFOODS LTD T A DELIVEROO",
                       "rating": "A", "confirmed": "2026-08-15"}},
        str(actions.settings.aliases_path))
    reg.invalidate_overlays()
    report = log.verify(reg.aliases_raw)
    assert report["unattributed_aliases"] == ["Deliveroo"], report


def test_the_hash_chain_notices_a_rewritten_entry():
    """Append-only is the guarantee; the chain is what makes it checkable."""
    actions, _s, log, _r, _tmp = _env(_row("1"))
    _resolve(actions)
    assert log.verify()["chain_ok"] is True

    rows = [json.loads(l) for l in
            open(log.path, encoding="utf-8").read().splitlines() if l.strip()]
    rows[0]["rationale"] = "something else entirely"
    with open(log.path, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, sort_keys=True) + "\n")

    assert log.verify()["chain_ok"] is False, "a tampered entry went unnoticed"


def test_a_repeated_action_id_does_not_apply_twice():
    actions, store, log, _r, _tmp = _env(_row("1"))
    first = _resolve(actions, action_id="abc-123")
    assert first.effects["rows_changed"] == 1

    before = len(log.entries())
    second = _resolve(actions, action_id="abc-123")
    assert second.replayed is True
    assert second.effects["rows_changed"] == 0
    assert len(log.entries()) == before, "a replay appended a second entry"


# --- demo isolation ---------------------------------------------------------

def test_demo_settings_never_point_at_the_real_store():
    """The sibling of 'never probe a non-Adzuna id': the failure would be
    silent, permanent, and land in his actual job hunt."""
    from web.api.settings import ADZUNA_HOME, load_settings
    os.environ["APP_MODE"] = "demo"
    try:
        demo = load_settings()
    finally:
        os.environ.pop("APP_MODE", None)

    real = ADZUNA_HOME / "jobs_tracker_beautified.xlsx"
    assert demo.tracker_path != real
    assert demo.aliases_path != ADZUNA_HOME / "data" / "sponsor_aliases.json"
    for path in (demo.tracker_path, demo.aliases_path, demo.rejections_path,
                 demo.decision_log_path):
        assert "demo" in str(path).lower(), path


# --- the demo reset ---------------------------------------------------------
# The demo is writable on purpose and nothing put it back once keep-warm stopped
# it sleeping. These pin the three ways a periodic restore could go wrong, and
# the first is the one that matters: pointed at a local instance it would
# overwrite the real tracker and destroy real decisions.

def _demo_env(*rows):
    """Same isolated world, but flagged as demo so DemoReset will construct."""
    actions, store, log, registry, tmp = _env(*rows)
    demo = Settings(**{**actions.settings.__dict__, "mode": "demo"})
    actions.settings = demo
    return actions, store, log, registry, demo, tmp


def test_reset_refuses_to_construct_outside_demo_mode():
    """The unrecoverable mistake. A snapshot restored over a local instance
    overwrites the real workbook, and there is no undo for that."""
    actions, store, log, registry, _tmp = _env(_row("1"))
    assert actions.settings.mode == "local"
    try:
        DemoReset(actions.settings, store, registry, log)
    except ValueError as exc:
        assert "demo" in str(exc).lower()
        return
    raise AssertionError("DemoReset constructed against a local instance")


def test_reset_restores_the_snapshot_and_forgets_the_decision():
    actions, store, log, registry, settings, _tmp = _demo_env(_row("1"))
    reset = DemoReset(settings, store, registry, log, interval_seconds=0)
    assert reset.capture() >= 1

    _resolve(actions)
    assert store.rows()["1"]["sponsor_match"] == "yes"
    assert len(log.entries()) == 1

    assert reset.reset(force=True) is True
    assert store.rows()["1"]["sponsor_match"] == "no", "workbook not restored"
    assert log.entries() == [], "decision log not restored"
    assert registry.aliases_raw == {}, "alias overlay not restored"


def test_reset_does_not_fire_before_its_interval():
    """Otherwise every request wipes the demo and no visitor sees their own
    decision land, which is the one thing they came to see."""
    actions, store, log, registry, settings, _tmp = _demo_env(_row("1"))
    reset = DemoReset(settings, store, registry, log, interval_seconds=3600)
    reset.capture()

    _resolve(actions)
    reset.maybe_reset()
    assert store.rows()["1"]["sponsor_match"] == "yes", "reset fired too early"
    assert reset.resets == 0


def test_capture_before_writes_is_what_makes_reset_meaningful():
    """Snapshotting after a visitor has acted pins THEIR decisions as the
    pristine state, and every later reset faithfully restores their work. This
    is why capture() runs in the startup hook, before the port is accepting."""
    actions, store, log, registry, settings, _tmp = _demo_env(_row("1"))
    _resolve(actions)                       # a visitor got in first

    late = DemoReset(settings, store, registry, log, interval_seconds=0)
    late.capture()
    late.reset(force=True)
    assert store.rows()["1"]["sponsor_match"] == "yes", (
        "a late snapshot restores the visitor's decision -- correct behaviour "
        "for this object, which is exactly why the caller must capture early")


# --- single-service routing -------------------------------------------------
# The deployed service serves the API and the built UI from one origin, so a
# catch-all static mount sits at "/". These pin the two ways that goes wrong.

def test_an_unknown_api_path_stays_a_404():
    """The SPA fallback must not swallow /api.

    Serving index.html with a 200 for a failed API call is worse than a 404:
    the client gets HTML where it expects JSON and reports a parse error, so
    every typo'd endpoint becomes a debugging session.

    This also guards a platform bug. StaticFiles hands the handler an
    OS-native path — 'api\\\\nope' on Windows, 'api/nope' on Linux — so a check
    written as `path.startswith("api/")` passes on the deployment target and
    fails on the development machine. Silent in exactly the place it would be
    caught.
    """
    from fastapi.testclient import TestClient

    import web.api.app as web_app

    with TestClient(web_app.app) as client:
        if client.get("/").status_code == 404:
            return          # UI not built in this checkout; nothing to guard

        res = client.get("/api/definitely-not-a-route")
        assert res.status_code == 404, (
            f"unknown API path returned {res.status_code} "
            f"{res.headers.get('content-type')} — the SPA fallback swallowed it")

        spa = client.get("/some/client/route")
        assert spa.status_code == 200, "client-side route did not fall back"
        assert "text/html" in spa.headers.get("content-type", "")


# --- runner -----------------------------------------------------------------
if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL  {fn.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001 - report, don't mask
            failed += 1
            print(f"ERROR {fn.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
