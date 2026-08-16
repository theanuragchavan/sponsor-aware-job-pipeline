"""Put the demo back the way it started, periodically.

The demo is writable on purpose: a visitor confirms an alias and watches rows
restamp, which is the entire point. Nothing put it back. The container used to
sleep and come back fresh, but adding a keep-warm ping removed even that — so
decisions now accumulate forever, and after enough traffic the queue empties,
the log fills with strangers' reasoning, and the next visitor opens a demo that
demonstrates nothing.

The fix is a snapshot taken at boot and restored on a timer.

Three properties worth naming, because each one is a way this could go wrong:

**It only ever runs in demo mode.** A reset against a local instance would
overwrite the real tracker and destroy real decisions. `install()` refuses to
arm unless `settings.is_demo`, and `DemoReset` refuses to construct without it.
Two checks for one mistake, because that mistake is unrecoverable.

**The snapshot is taken from the image, before anything can write.** Startup
order matters: if a request landed first, the snapshot would capture that
visitor's decisions and every later "reset" would restore them. Taken in the
startup hook, before the server accepts connections.

**A reset never interrupts a write.** It takes the same store lock an action
takes, so it either happens cleanly between actions or waits. A restore halfway
through a writeback would leave the alias file and the workbook disagreeing.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

DEFAULT_INTERVAL_SECONDS = 6 * 60 * 60


class DemoReset:
    def __init__(self, settings, store, registry, log,
                 interval_seconds: int = DEFAULT_INTERVAL_SECONDS):
        if not settings.is_demo:
            raise ValueError(
                "DemoReset refuses to run outside demo mode — restoring a "
                "snapshot over a local instance would destroy real decisions")
        self.settings = settings
        self.store = store
        self.registry = registry
        self.log = log
        self.interval = interval_seconds
        self._lock = threading.Lock()
        self._snapshot: dict[Path, bytes] = {}
        self._last_reset = time.monotonic()
        self.resets = 0

    # --- snapshot -----------------------------------------------------------

    def capture(self) -> int:
        """Read the pristine demo files into memory. Call once, at startup.

        In memory rather than a copied directory: the whole fixture is well
        under a megabyte, and a copy on disk is one more thing a stray write
        could reach.
        """
        paths = [self.settings.tracker_path, self.settings.aliases_path,
                 self.settings.rejections_path, self.settings.decision_log_path]
        with self._lock:
            self._snapshot = {}
            for path in paths:
                p = Path(path)
                # None records "this did not exist", which is a state that has
                # to be restorable too. The decision log is the case that
                # matters: on a fresh image there are no decisions and no file,
                # a visitor makes one and the file appears, and a reset that
                # only rewrites files it already has would leave their decision
                # in place forever. Absence is part of the snapshot.
                self._snapshot[p] = p.read_bytes() if p.exists() else None
            self._last_reset = time.monotonic()
        return sum(1 for v in self._snapshot.values() if v is not None)

    @property
    def captured(self) -> bool:
        return bool(self._snapshot)

    def due(self) -> bool:
        return (self.captured
                and time.monotonic() - self._last_reset >= self.interval)

    def seconds_until_due(self) -> int:
        if not self.captured:
            return -1
        return max(0, int(self.interval - (time.monotonic() - self._last_reset)))

    # --- restore ------------------------------------------------------------

    def reset(self, force: bool = False) -> bool:
        """Restore the snapshot. Returns True if it actually ran."""
        if not self.captured:
            return False
        if not force and not self.due():
            return False

        with self._lock:
            # Re-check inside the lock: two requests can both see it as due.
            if not force and not self.due():
                return False

            # The store's own lock, so a reset can never land mid-writeback and
            # leave the workbook and the alias overlay disagreeing.
            with self.store._lock:                      # noqa: SLF001
                for path, blob in self._snapshot.items():
                    if blob is None:
                        path.unlink(missing_ok=True)    # it did not exist
                        continue
                    path.parent.mkdir(parents=True, exist_ok=True)
                    tmp = path.with_suffix(path.suffix + ".restoring")
                    tmp.write_bytes(blob)
                    tmp.replace(path)                   # atomic on-volume

                # Everything cached is now stale by construction.
                self.store._rows = None                 # noqa: SLF001
                self.store._stamp = None                # noqa: SLF001
            self.registry.invalidate_overlays()

            self._last_reset = time.monotonic()
            self.resets += 1
            return True

    def maybe_reset(self) -> None:
        """Cheap enough to call on every request: one clock comparison."""
        if self.due():
            self.reset()
