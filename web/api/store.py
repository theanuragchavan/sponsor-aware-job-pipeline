"""The tracker store, made safe for interactive use.

Three problems this solves, none of which the CLI has:

1. **Loading is slow.** `tracker.load_tracker` on the 3,308-row workbook takes
   ~2.3s. That can never sit in a request path, so rows are cached and the cache
   is invalidated by a (mtime, size) stamp rather than a timer.

2. **There are two writers.** The 09:00 scheduled run and this API. If `main.py`
   inserts 40 rows at 09:00 while a browser tab holds a snapshot taken at 08:55,
   writing that snapshot back **deletes those 40 rows**, silently and
   permanently. So `apply()` takes field-level mutations, re-reads from disk
   before writing, and touches only the named fields of the named rows. There is
   deliberately no "save these rows" entry point.

3. **The store is often open in Excel.** `save_tracker`'s default is to write a
   `.LOCKED-*` side file that nothing reads back — correct for the unattended
   run, wrong here. We pass `on_lock="raise"` and surface it as a retryable
   error, so the user closes Excel and presses the button again.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

from . import bootstrap  # noqa: F401  (puts the repo root on sys.path)

import tracker  # noqa: E402


class StoreLockedError(RuntimeError):
    """The workbook is open in Excel. Retryable by a human closing it."""


@dataclass(frozen=True)
class Stamp:
    """Cheap identity for the file's current contents."""
    mtime_ns: int
    size: int

    @classmethod
    def of(cls, path: Path) -> "Stamp":
        try:
            st = path.stat()
        except FileNotFoundError:
            return cls(0, 0)
        return cls(st.st_mtime_ns, st.st_size)


class TrackerStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._rows: dict[str, dict] | None = None
        self._stamp: Stamp | None = None

    # --- reads --------------------------------------------------------------

    def rows(self) -> dict[str, dict]:
        """{job_id: row}, reloaded only when the file on disk has changed."""
        with self._lock:
            current = Stamp.of(self.path)
            if self._rows is None or self._stamp != current:
                self._rows = tracker.load_tracker(str(self.path))
                self._stamp = current
            return self._rows

    def stamp(self) -> Stamp:
        return Stamp.of(self.path)

    def row(self, job_id: str) -> dict | None:
        return self.rows().get(str(job_id))

    # --- writes -------------------------------------------------------------

    def apply(self, mutations: dict[str, dict[str, str]]) -> int:
        """Apply {job_id: {field: value}} and persist. Returns rows changed.

        Field-level by design — see the module docstring. Unknown ids and
        no-op assignments are skipped rather than raising, so an action whose
        effect has already landed is idempotent rather than fatal.
        """
        if not mutations:
            return 0

        with self._lock:
            # Re-read under the lock. A snapshot the caller read seconds ago may
            # predate the 09:00 run; the rows it never knew about must survive.
            self._rows = tracker.load_tracker(str(self.path))
            self._stamp = Stamp.of(self.path)
            rows = self._rows

            changed = 0
            for job_id, fields in mutations.items():
                row = rows.get(str(job_id))
                if row is None:
                    continue
                touched = False
                for field, value in fields.items():
                    if row.get(field) != value:
                        row[field] = value
                        touched = True
                changed += 1 if touched else 0

            if not changed:
                return 0

            try:
                tracker.save_tracker(str(self.path), rows, on_lock="raise")
            except PermissionError as exc:
                # Drop the cache: we mutated it in memory but never persisted,
                # so it no longer matches disk and must not be served.
                self._rows = None
                self._stamp = None
                raise StoreLockedError(str(self.path)) from exc

            self._stamp = Stamp.of(self.path)
            return changed
