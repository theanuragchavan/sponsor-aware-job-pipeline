"""Run the ingestion scripts from the app, and stream what they say.

This drives `main.py` and `ats_main.py` as subprocesses. It does not
reimplement them. They are the same scripts the 09:00 scheduled task runs, they
are tested, and a second copy of ingestion logic living in the web layer would
be two things to keep in step for no benefit.

Three guards, each for a failure this project has already had:

**One run at a time.** The tracker merge is insert-only and never revisits an
id, so two concurrent runs are not self-correcting — the second would write on
top of a half-finished first. A second press is refused, not queued.

**Not while the workbook is open in Excel.** Checked before starting rather
than after, because `save_tracker`'s default is to write a `.LOCKED-*` side
file that nothing ever reads back. Finding out at the end means a run that
looks successful and changed nothing.

**Board ingestion is dry-run by default.** `ats_main.py` is deliberately absent
from the 09:00 task because a mapping bug there writes hundreds of wrong rows
that have to be undone by hand in Excel. The button shows what it *would* add
first.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import bootstrap  # noqa: F401

ADZUNA_HOME = Path(__file__).resolve().parent.parent.parent

#: Same interpreter that is running the API. Resolving "python" from PATH picks
#: up a venv without openpyxl on this machine, which is a documented trap in
#: this project's own notes.
PYTHON = sys.executable


@dataclass
class Run:
    """One in-flight or finished run, and everything it printed."""
    kind: str
    started: float = field(default_factory=time.time)
    finished: float | None = None
    lines: list[str] = field(default_factory=list)
    returncode: int | None = None
    error: str = ""

    @property
    def running(self) -> bool:
        return self.finished is None

    def as_dict(self, since: int = 0) -> dict:
        return {
            "kind": self.kind,
            "running": self.running,
            "started": self.started,
            "seconds": round((self.finished or time.time()) - self.started, 1),
            "returncode": self.returncode,
            "error": self.error,
            "lines": self.lines[since:],
            "total_lines": len(self.lines),
        }


class Runner:
    """Holds at most one run. Deliberately not a queue."""

    def __init__(self, tracker_path: Path):
        self.tracker_path = Path(tracker_path)
        self._lock = threading.Lock()
        self.current: Run | None = None

    # --- guards -------------------------------------------------------------

    def workbook_is_locked(self) -> bool:
        """True when Excel holds the store open.

        Windows keeps an exclusive lock, so opening for append raises. A `~$`
        sidecar next to the file is the other tell and costs nothing to check.
        """
        if not self.tracker_path.exists():
            return False
        sidecar = self.tracker_path.with_name("~$" + self.tracker_path.name)
        if sidecar.exists():
            return True
        try:
            with open(self.tracker_path, "ab"):
                return False
        except PermissionError:
            return True
        except OSError:
            return False

    # --- running ------------------------------------------------------------

    def start(self, kind: str) -> Run:
        if kind not in ("adzuna", "boards"):
            raise ValueError(f"unknown run {kind!r}")

        with self._lock:
            if self.current is not None and self.current.running:
                raise RuntimeError(
                    "A search is already running. Wait for it to finish — "
                    "starting a second would write on top of the first.")
            if self.workbook_is_locked():
                raise RuntimeError(
                    "The tracker is open in Excel. Close it first, or the run "
                    "finishes and quietly saves nowhere.")

            run = Run(kind=kind)
            self.current = run

        cmd = ([PYTHON, "main.py"] if kind == "adzuna"
               else [PYTHON, "ats_main.py", "--dry-run", "--max-new", "50"])
        threading.Thread(target=self._pump, args=(run, cmd), daemon=True).start()
        return run

    def _pump(self, run: Run, cmd: list[str]) -> None:
        try:
            proc = subprocess.Popen(
                cmd, cwd=str(ADZUNA_HOME),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                # The scripts print progress; without this Python buffers it
                # into one lump at exit and "streaming" shows nothing until the
                # run is already over.
                env={**os.environ, "PYTHONUNBUFFERED": "1"})
        except Exception as exc:                       # noqa: BLE001
            run.error = f"{type(exc).__name__}: {exc}"
            run.finished = time.time()
            run.returncode = -1
            return

        assert proc.stdout is not None
        for line in proc.stdout:
            text = line.rstrip()
            if text:
                run.lines.append(text)
            # A runaway loop must not fill memory. 5,000 lines is far more than
            # any real run produces.
            if len(run.lines) > 5000:
                run.lines.append("... output truncated at 5,000 lines")
                proc.kill()
                break

        run.returncode = proc.wait()
        run.finished = time.time()
