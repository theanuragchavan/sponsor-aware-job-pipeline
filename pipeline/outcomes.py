"""What happened to each application, projected from the decision log.

The gap this closes is the one every learning idea in the plan has been blocked
on. The tracker holds one `status` cell per job, which can say `interview` but
cannot say *when* it became interview, or that it was `screening` for eleven
days first, or that the rejection arrived four days after a take-home. One job
has many stage events and a cell holds one string.

So stages are events in the append-only log, and this reads them back. That was
the settled design and the reasons are worth restating, because the obvious
alternatives are both wrong:

- **Not tracker columns.** You would need a column per stage per job, and the
  09:00 run rewrites that file. The Snowflake loss is what that looks like.
- **Not a second store.** It would duplicate the hash chain and the undo that
  already exist, and there would then be two answers to "what happened", which
  is worse than none.

The tracker's `status` stays the current-state cache. This is the history, and
`reconcile_audit.py` already checks the two agree.

What it can answer with ZERO positives
--------------------------------------
That matters, because there is one logged application and no replies. All three
of these are base rates, not preference weights, so they need volume rather
than success:

- **Channel funnel** -- which `applied_via` x `source` combinations produce any
  reply at all. Learnable from silence.
- **Time to silence** -- the distribution behind `/api/follow-ups`, which
  currently guesses `after_days=10`. Once ten applications have gone quiet, the
  real median replaces the guess.
- **Gate hit rate** -- how often autosubmit blocked, and on which condition.
  Available immediately, and the fastest way to see whether the ten conditions
  are calibrated or theatre.

What it deliberately cannot answer yet: whether a role type, a tier or a
sponsor rating predicts an interview. That needs positives, and inventing a
preference weight from zero of them is how a ranker learns to be confidently
wrong. `shortlist.score()` stays frozen until >=30 applications on attested
CVs and >=5 that reach screening.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
import tracker  # noqa: E402
from web.api.audit import (APPLICATION, FAILED_SUFFIX, STATUS, UNDO_SUFFIX,
                           DecisionLog)  # noqa: E402

#: One event per thing that happened, in the order they can happen. Ordering is
#: data, not decoration: it is what lets a projection say a stage was skipped
#: and what makes "furthest reached" answerable.
STAGES = ("applied", "ack", "screening", "interview", "offer",
          "hired", "rejected", "withdrawn")

#: Terminal stages. Anything else is still live and can still be chased.
TERMINAL = frozenset({"offer", "hired", "rejected", "withdrawn"})

#: Log types that carry a stage. `cowork.stopped` deliberately does not -- a
#: blocked submission is not a stage of an application that never happened.
OUTCOME = "job.outcome"

#: Tracker statuses that mean an application was actually sent.
#:
#: `rejected` belongs here -- it is an outcome of applying. `ignored` and
#: `closed` do not: deciding not to apply is not applying, and the first run
#: counted two ignored rows as applications, which would have made the reply
#: rate look worse than reality by inflating the denominator with jobs nobody
#: ever contacted.
#:
#: Deliberately narrower than `actions.STATUS_VOCAB` and broader than
#: `actions.OPEN_STATUSES`, which asks a different question -- is this still
#: worth chasing -- and so excludes `rejected`.
APPLIED_STATUSES = frozenset({"applied", "screening", "interview", "offer",
                              "rejected"})


def _stage_of(entry: dict) -> str | None:
    """The stage an entry asserts, or None if it asserts none."""
    t = entry.get("type", "")
    if t == APPLICATION:
        return "applied"
    if t == OUTCOME:
        stage = (entry.get("input", {}) or {}).get("stage", "")
        return stage if stage in STAGES else None
    if t == STATUS:
        status = (entry.get("input", {}) or {}).get("status", "")
        # set_status is the manual path and uses the tracker vocabulary, which
        # overlaps but is not identical. Map only where the meaning is exact.
        return {"applied": "applied", "screening": "screening",
                "interview": "interview", "offer": "offer",
                "rejected": "rejected"}.get(status)
    return None


def timeline(entries) -> dict[str, list[dict]]:
    """{job_id: [{stage, at, source, note}, ...]} in chronological order.

    Skips `.failed` entries, which state outright that nothing happened, and
    anything reversed by a later `.undone` -- the same two exclusions
    `replay_aliases` makes, for the same reason.
    """
    rows = list(entries)
    undone = {r.get("input", {}).get("target_log_entry_id")
              for r in rows if r.get("type", "").endswith(UNDO_SUFFIX)}

    out: dict[str, list[dict]] = collections.defaultdict(list)
    for r in rows:
        if r.get("id") in undone or r.get("type", "").endswith(FAILED_SUFFIX):
            continue
        stage = _stage_of(r)
        if not stage:
            continue
        job_id = str((r.get("entity") or {}).get("id")
                     or (r.get("input") or {}).get("job_id") or "")
        if not job_id:
            continue
        at = (r.get("input", {}) or {}).get("at") or r.get("ts", "")
        out[job_id].append({
            "stage": stage,
            "at": str(at)[:10],
            "source": r.get("actor", {}).get("id", ""),
            "note": r.get("rationale", ""),
            "seq": r.get("seq"),
        })
    for events in out.values():
        events.sort(key=lambda e: (e["at"], e["seq"] or 0))
    return dict(out)


def furthest(events: list[dict]) -> str:
    """The furthest stage reached, by the order in STAGES.

    Not the last event: a rejection after an interview means the interview
    still happened, and a funnel that forgot that would report the interview
    rate as zero.
    """
    seen = [e["stage"] for e in events if e["stage"] in STAGES]
    return max(seen, key=STAGES.index) if seen else ""


def days_quiet(events: list[dict], today: dt.date | None = None) -> int | None:
    """Days since the last event, or None if the application is resolved.

    Resolved applications are not quiet, they are finished. Reporting them as
    overdue is how a follow-up list becomes noise nobody reads.
    """
    if not events:
        return None
    if any(e["stage"] in TERMINAL for e in events):
        return None
    try:
        last = dt.date.fromisoformat(events[-1]["at"])
    except (ValueError, KeyError):
        return None
    return ((today or dt.date.today()) - last).days


def real_applications(timelines: dict[str, list[dict]],
                      rows: dict) -> dict[str, list[dict]]:
    """Drop jobs whose tracker status says no application was ever sent.

    Necessary, and the first run proved it: the log holds twelve stage events
    for one Palantir row that was walked applied -> interview -> screening ->
    offer -> ignored -> new while testing the UI on 2026-08-16. Read literally,
    that is a 100% offer rate. A funnel that counts rehearsals is worse than no
    funnel, because it reports a number and the number is flattering.

    Rationale text would be the obvious filter -- several of those entries say
    "during a test" in as many words -- but reading intent out of prose is
    guesswork, and the one entry that mattered ("Applied via company site.")
    reads exactly like a test would.

    The tracker's `status` is the current-state cache and `reconcile_audit.py`
    already asserts it agrees with the log. So a row still sitting at `new` has
    no live application, whatever its history shows: either it was exploratory,
    or it was withdrawn, and neither belongs in a funnel. Timelines still carry
    every event -- history is history -- but only these are counted.
    """
    live = {}
    for job_id, events in timelines.items():
        status = (rows.get(job_id, {}).get("status") or "").strip().lower()
        if status in APPLIED_STATUSES:
            live[job_id] = events
    return live


def funnel(timelines: dict[str, list[dict]]) -> dict:
    """How many applications reached each stage. Counts, not rates.

    A rate over one application is a number that lies about its own precision.
    """
    counts = collections.Counter()
    for events in timelines.values():
        reached = {e["stage"] for e in events}
        for stage in STAGES:
            if stage in reached:
                counts[stage] += 1
    return {"total": len(timelines),
            "reached": {s: counts[s] for s in STAGES if counts[s]}}


def by_channel(timelines: dict[str, list[dict]], rows: dict) -> list[dict]:
    """Reply rate per applied_via x source. Learnable with zero positives.

    "Any reply at all" rather than "an offer": silence is the signal available
    now, and a channel that never answers is worth knowing about long before
    one that sometimes says yes.
    """
    buckets: dict[tuple, dict] = collections.defaultdict(
        lambda: {"sent": 0, "replied": 0})
    for job_id, events in timelines.items():
        row = rows.get(job_id, {})
        key = (row.get("applied_via") or "unknown",
               row.get("source") or "adzuna")
        buckets[key]["sent"] += 1
        if any(e["stage"] not in ("applied",) for e in events):
            buckets[key]["replied"] += 1

    out = []
    for (via, source), v in sorted(buckets.items()):
        out.append({"applied_via": via, "source": source, **v,
                    # Reported as a fraction, never a percentage, while the
                    # denominators are this small.
                    "rate": f"{v['replied']}/{v['sent']}"})
    return out


def silence_threshold(timelines: dict[str, list[dict]],
                      *, minimum_samples: int = 10,
                      fallback: int = 10) -> tuple[int, str]:
    """The days-quiet cut-off for the follow-up list, measured if possible.

    `/api/follow-ups` currently hardcodes 10 days, which was a guess and is
    still a guess. This replaces it once there is enough data to beat a guess,
    and says which it returned -- a measured threshold and an assumed one
    should never be indistinguishable to the caller.
    """
    gaps: list[int] = []
    for events in timelines.values():
        for a, b in zip(events, events[1:]):
            try:
                d = (dt.date.fromisoformat(b["at"])
                     - dt.date.fromisoformat(a["at"])).days
            except ValueError:
                continue
            if d >= 0:
                gaps.append(d)
    if len(gaps) < minimum_samples:
        return fallback, (f"assumed ({len(gaps)} reply gap(s) observed, "
                          f"need {minimum_samples})")
    return round(statistics.median(gaps)), f"measured from {len(gaps)} gaps"


def report(log_path: Path | None = None,
           store_path: Path | None = None) -> dict:
    log_path = log_path or ROOT / "data" / "decision_log.jsonl"
    store_path = store_path or ROOT / config.JOBS_XLSX

    entries = DecisionLog(log_path).entries() if log_path.exists() else []
    rows = tracker.load_tracker(str(store_path)) if store_path.exists() else {}
    all_tl = timeline(entries)
    tl = real_applications(all_tl, rows)
    excluded = len(all_tl) - len(tl)
    threshold, basis = silence_threshold(tl)

    quiet = []
    for job_id, events in tl.items():
        d = days_quiet(events)
        if d is not None and d >= threshold:
            row = rows.get(job_id, {})
            quiet.append({"job_id": job_id, "company": row.get("company", ""),
                          "title": row.get("title", ""), "days": d,
                          "furthest": furthest(events)})
    quiet.sort(key=lambda r: -r["days"])

    return {
        "applications": len(tl),
        "excluded_no_application": excluded,
        "funnel": funnel(tl),
        "by_channel": by_channel(tl, rows),
        "silence_threshold_days": threshold,
        "threshold_basis": basis,
        "gone_quiet": quiet,
        "timelines": {k: v for k, v in sorted(tl.items())},
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="What happened to each application, from the log.")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    r = report()
    if args.json:
        print(json.dumps(r, indent=2, default=str))
        return 0

    print(f"applications recorded : {r['applications']}")
    if r["excluded_no_application"]:
        print(f"  ({r['excluded_no_application']} job(s) with stage events but "
              f"no application on the tracker — UI tests, or withdrawn)")
    if not r["applications"]:
        print("\nNothing to report yet. The funnel needs applications in it, "
              "and\nthat is a throughput problem rather than a tooling one.")
        return 0

    print("\nfunnel")
    for stage, n in r["funnel"]["reached"].items():
        print(f"  {stage:<12} {n}")

    print("\nby channel")
    for c in r["by_channel"]:
        print(f"  {c['applied_via']:<14} {c['source']:<8} "
              f"replied {c['rate']}")

    print(f"\nsilence threshold     : {r['silence_threshold_days']}d "
          f"({r['threshold_basis']})")
    if r["gone_quiet"]:
        print("\ngone quiet")
        for q in r["gone_quiet"]:
            print(f"  {q['days']:>3}d  {q['company']} — {q['title'][:44]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
