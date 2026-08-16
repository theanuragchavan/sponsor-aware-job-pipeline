"""Append-only decision log.

The pipeline records a confirmed alias as `{register_name, rating, confirmed}` —
a date, with no record of who decided or why. This module is the missing half:
every action writes one line here, and the line carries **what the system
checked before allowing the change**, not just what changed. That distinction is
what makes it an audit trail rather than a changelog.

Three properties, in order of how much they matter:

1. **Append-only.** Opened "a", never rewritten, never rotated. An undo appends
   an inverse entry; it does not delete the original.
2. **The alias file is a projection of this log.** `replay_aliases()` rebuilds
   `sponsor_aliases.json` from the entries, so the log is the source of truth
   and the JSON is a cache. `verify()` reports drift between them.
3. **The log is also the idempotency store.** A repeated `action_id` replays the
   recorded response instead of applying the effect twice.

The 29 aliases confirmed through the CLI before this existed have no entries, so
they replay as unattributed. That is surfaced honestly rather than backfilled
with a fake actor — an audit trail that invents its own history is worth less
than one with a gap in it.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

GENESIS = "sha256:" + "0" * 64

# Entry types that write an alias into the projection.
CONFIRM = "alias.confirm"
REJECT = "alias.reject"
APPLICATION = "application.logged"
STATUS = "job.status_set"
UNDO_SUFFIX = ".undone"
FAILED_SUFFIX = ".failed"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z")


def _digest(entry: dict) -> str:
    body = {k: v for k, v in entry.items() if k != "hash"}
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


class DecisionLog:
    def __init__(self, path: Path, max_entries: int = 0):
        self.path = Path(path)
        self.max_entries = max_entries          # 0 = unlimited
        self._lock = threading.RLock()

    # --- reading ------------------------------------------------------------

    def entries(self) -> list[dict]:
        """Every entry, oldest first. A corrupt line is skipped, not fatal.

        A half-written final line is the expected failure mode after a crash;
        losing the whole log because of it would be absurd.
        """
        if not self.path.exists():
            return []
        out = []
        with open(self.path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return out

    def read(self, *, entity: str | None = None, type: str | None = None,
             limit: int = 100, before_seq: int | None = None) -> list[dict]:
        """Newest first, filtered. `entity` is 'kind:id', e.g. 'company:MONZO'."""
        rows = self.entries()
        if entity:
            kind, _, ident = entity.partition(":")
            rows = [r for r in rows
                    if r.get("entity", {}).get("kind") == kind
                    and str(r.get("entity", {}).get("id")) == ident]
        if type:
            rows = [r for r in rows if r.get("type") == type]
        if before_seq is not None:
            rows = [r for r in rows if r.get("seq", 0) < before_seq]
        return list(reversed(rows))[:limit]

    def find_by_action_id(self, action_id: str) -> dict | None:
        if not action_id:
            return None
        for entry in reversed(self.entries()):
            if entry.get("action_id") == action_id:
                return entry
        return None

    # --- writing ------------------------------------------------------------

    def record(self, *, type: str, entity: dict, actor: dict,
               input: dict, validations: list[dict], rationale: str,
               effects: dict, before: dict | None = None,
               after: dict | None = None, action_id: str = "",
               reversible: bool = True) -> dict:
        """Append one entry and return it. The only way to write this file."""
        with self._lock:
            existing = self.entries()
            if self.max_entries and len(existing) >= self.max_entries:
                raise RuntimeError(
                    f"decision log is full ({self.max_entries} entries)")

            prev = existing[-1] if existing else None
            entry = {
                "seq": (prev["seq"] + 1) if prev else 1,
                "id": os.urandom(8).hex(),
                "action_id": action_id,
                "ts": _utcnow(),
                "actor": actor,
                "type": type,
                "entity": entity,
                "input": input,
                "validations": validations,
                "rationale": rationale,
                "effects": effects,
                "before": before or {},
                "after": after or {},
                "reversible": reversible,
                "prev_hash": prev["hash"] if prev else GENESIS,
            }
            entry["hash"] = _digest(entry)

            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, sort_keys=True, ensure_ascii=False)
                         + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            return entry

    # --- projection ---------------------------------------------------------

    def replay_aliases(self, entries: Iterable[dict] | None = None
                       ) -> dict[str, dict]:
        """Rebuild the alias overlay from the log, in the on-disk JSON shape.

        Keyed on the company as originally spelled, matching what
        `sponsor_check.save_aliases` writes and `load_aliases` normalises on
        read. A later reject removes an earlier confirm; an undo removes the
        entry it reverses.
        """
        rows = list(entries if entries is not None else self.entries())
        undone = {r.get("input", {}).get("target_log_entry_id")
                  for r in rows if r.get("type", "").endswith(UNDO_SUFFIX)}

        out: dict[str, dict] = {}
        for r in rows:
            if r.get("id") in undone or r.get("type", "").endswith(FAILED_SUFFIX):
                continue
            company = r.get("input", {}).get("company")
            if not company:
                continue
            if r.get("type") == CONFIRM:
                out[company] = {
                    "register_name": r["input"].get("register_name", ""),
                    "rating": r.get("effects", {}).get("rating", ""),
                    "confirmed": r.get("ts", "")[:10],
                    "actor": r.get("actor", {}).get("id", ""),
                    "rationale": r.get("rationale", ""),
                    "log_entry_id": r.get("id", ""),
                }
            elif r.get("type") == REJECT:
                out.pop(company, None)
        return out

    def verify(self, alias_file: dict[str, dict] | None = None) -> dict:
        """Chain integrity plus drift between the log and the alias file."""
        rows = self.entries()

        chain_ok, broken_at = True, None
        prev_hash = GENESIS
        for r in rows:
            if r.get("prev_hash") != prev_hash or r.get("hash") != _digest(r):
                chain_ok, broken_at = False, r.get("seq")
                break
            prev_hash = r["hash"]

        replayed = self.replay_aliases(rows)
        unattributed: list[str] = []
        drift: list[str] = []
        if alias_file is not None:
            for company in alias_file:
                if company not in replayed:
                    # Written by the CLI, which predates this log and does not
                    # write to it. Attribution is genuinely unknown.
                    unattributed.append(company)
            for company, rec in replayed.items():
                on_disk = alias_file.get(company)
                if not on_disk:
                    drift.append(company)
                elif on_disk.get("register_name") != rec["register_name"]:
                    drift.append(company)

        return {
            "chain_ok": chain_ok,
            "broken_at_seq": broken_at,
            "count": len(rows),
            "projection_matches_log": not drift,
            "drift": sorted(drift),
            "unattributed_aliases": sorted(unattributed),
        }
