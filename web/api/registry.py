"""Cached view of the sponsor register and the alias/rejection overlays.

The register is 121,188 entries and takes ~0.8s to load, plus ~0.2s to index.
Neither belongs in a request. Everything here is built once and reused; the
overlays are cheap and reload on mtime change so a CLI `--apply` in another
terminal is picked up without a restart.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from . import bootstrap  # noqa: F401

import sponsor_check  # noqa: E402

from .settings import ADZUNA_HOME  # noqa: E402


class Registry:
    def __init__(self, register_csv: Path | None, aliases_path: Path,
                 rejections_path: Path):
        self.register_csv = Path(register_csv) if register_csv else None
        self.aliases_path = Path(aliases_path)
        self.rejections_path = Path(rejections_path)
        self._lock = threading.RLock()
        self._lookup: dict[str, str] | None = None
        self._index: dict[str, list[str]] | None = None
        self._ta_index: dict[str, list[str]] | None = None
        self._overlay_stamps: dict[str, tuple] = {}
        self._aliases: dict[str, dict] | None = None
        self._rejections: dict[str, list[str]] | None = None
        self._boards: dict[str, str] | None = None
        self._board_stamp: tuple | None = None

    # --- register -----------------------------------------------------------

    def _ensure_register(self) -> None:
        if self._lookup is not None:
            return
        # load_sponsor_lookup() takes no path; it resolves one through
        # sponsor_check.ensure_register, which honours SPONSOR_REGISTER_CSV.
        # Setting it here is what keeps demo mode off the network.
        if self.register_csv:
            os.environ["SPONSOR_REGISTER_CSV"] = str(self.register_csv)
        lookup = sponsor_check.load_sponsor_lookup()
        if lookup is None:
            raise RuntimeError(
                "sponsor register unavailable — set SPONSOR_REGISTER_CSV or "
                "run once with a network connection to populate the cache")
        self._lookup = lookup
        self._index = sponsor_check.build_name_index(lookup)
        self._ta_index = sponsor_check.build_trading_index(lookup)

    @property
    def lookup(self) -> dict[str, str]:
        with self._lock:
            self._ensure_register()
            return self._lookup

    def suggest(self, company: str, limit: int = 3):
        with self._lock:
            self._ensure_register()
            return sponsor_check.suggest_matches(
                company, self._lookup, self._index, self._ta_index, limit)

    def search(self, query: str, limit: int = 20) -> list[tuple[str, str]]:
        """Prefix search over register keys, for the manual-override picker."""
        key = sponsor_check.normalize_name(query)
        if len(key) < 2:
            return []
        with self._lock:
            self._ensure_register()
            exact = [(k, self._lookup[k]) for k in self._index.get(
                key.split(" ")[0], []) if k.startswith(key)]
            if len(exact) >= limit:
                return sorted(exact)[:limit]
            wide = [(k, v) for k, v in self._lookup.items() if key in k]
            return sorted({*exact, *wide})[:limit]

    def has_entry(self, register_name: str) -> bool:
        return sponsor_check.normalize_name(register_name) in self.lookup

    def rating_for(self, register_name: str) -> str:
        return self.lookup.get(sponsor_check.normalize_name(register_name), "")

    # --- overlays -----------------------------------------------------------

    def _reload_if_changed(self, path: Path, attr: str, default):
        stamp = None
        if path.exists():
            st = path.stat()
            stamp = (st.st_mtime_ns, st.st_size)
        if self._overlay_stamps.get(attr) != stamp or \
                getattr(self, attr) is None:
            self._overlay_stamps[attr] = stamp
            if not path.exists():
                setattr(self, attr, default())
            else:
                try:
                    setattr(self, attr,
                            json.loads(path.read_text(encoding="utf-8")))
                except (json.JSONDecodeError, OSError):
                    setattr(self, attr, default())
        return getattr(self, attr)

    @property
    def aliases_raw(self) -> dict[str, dict]:
        """As written on disk, keyed on the original company spelling."""
        with self._lock:
            return self._reload_if_changed(
                self.aliases_path, "_aliases", dict)

    @property
    def aliases(self) -> dict[str, dict]:
        """Normalised keys, the shape `sponsor_check.match_company` expects."""
        return {sponsor_check.normalize_name(k): v
                for k, v in self.aliases_raw.items()}

    @property
    def rejections(self) -> dict[str, list[str]]:
        """{normalised company: [rejected register names]}."""
        with self._lock:
            return self._reload_if_changed(
                self.rejections_path, "_rejections", dict)

    def is_rejected(self, company: str, register_name: str) -> bool:
        key = sponsor_check.normalize_name(company)
        return register_name in self.rejections.get(key, [])

    # --- the board registry -------------------------------------------------

    @property
    def board_names(self) -> dict[str, str]:
        """{normalised company: register name} from data/ats_boards.csv.

        The THIRD source of sponsorship truth, and the one that is easy to miss.
        `register_name` in that file is described in the project's own notes as
        "load-bearing": Greenhouse reports Monzo as "Monzo" while the register
        says "MONZO BANK", so without it every ingested board job fails the gate
        it was collected to pass.

        It is human-curated, which puts it on the same footing as a confirmed
        alias rather than below it. Omitting it made the UI tell Anurag there
        was "no entry found" for Palantir, Faculty, OpenAI, Monzo, Deliveroo and
        nine others — his fourteen most important employers, every one of them
        correctly verified in the data.
        """
        path = ADZUNA_HOME / "data" / "ats_boards.csv"
        with self._lock:
            stamp = None
            if path.exists():
                st = path.stat()
                stamp = (st.st_mtime_ns, st.st_size)
            if self._board_stamp != stamp or self._boards is None:
                self._board_stamp = stamp
                out: dict[str, str] = {}
                if path.exists():
                    import csv
                    with open(path, encoding="utf-8-sig", newline="") as fh:
                        for row in csv.DictReader(fh):
                            company = (row.get("company") or "").strip()
                            register = (row.get("register_name") or "").strip()
                            if company and register:
                                out[sponsor_check.normalize_name(company)] = register
                self._boards = out
            return self._boards

    def invalidate_overlays(self) -> None:
        with self._lock:
            self._overlay_stamps.clear()
            self._aliases = None
            self._rejections = None
