"""Runtime configuration for the web layer.

Everything the API touches on disk is named here, and nothing below this module
reads an environment variable. That is the whole point: in demo mode the paths
swap wholesale, and there must be exactly one place where that happens. A stray
`os.getenv` in a router is how a public demo ends up writing the real store.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# web/api/settings.py -> web/api -> web -> D:\Adzuna
ADZUNA_HOME = Path(__file__).resolve().parent.parent.parent
DEMO_DATA = ADZUNA_HOME / "web" / "demo" / "data"


@dataclass(frozen=True)
class Settings:
    mode: str                  # "local" | "demo"
    tracker_path: Path
    aliases_path: Path
    rejections_path: Path
    decision_log_path: Path
    register_csv: Path | None  # None -> the real cached/downloaded register
    actor_id: str
    actor_name: str
    cors_origins: tuple[str, ...]
    rate_limit_per_min: int
    max_log_entries: int

    @property
    def is_demo(self) -> bool:
        return self.mode == "demo"


def _origins() -> tuple[str, ...]:
    raw = os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
    return tuple(o.strip() for o in raw.split(",") if o.strip())


def load_settings() -> Settings:
    mode = os.getenv("APP_MODE", "local").lower()
    if mode not in ("local", "demo"):
        raise ValueError(f"APP_MODE must be 'local' or 'demo', got {mode!r}")

    if mode == "demo":
        base = Path(os.getenv("DEMO_DATA_DIR", DEMO_DATA))
        return Settings(
            mode="demo",
            tracker_path=base / "demo_tracker.xlsx",
            aliases_path=base / "demo_aliases.json",
            rejections_path=base / "demo_rejections.json",
            decision_log_path=base / "demo_decision_log.jsonl",
            register_csv=base / "demo_register.csv",
            actor_id="visitor",
            actor_name="Demo visitor",
            cors_origins=_origins(),
            rate_limit_per_min=int(os.getenv("RATE_LIMIT_PER_MIN", "20")),
            max_log_entries=int(os.getenv("MAX_LOG_ENTRIES", "5000")),
        )

    data = ADZUNA_HOME / "data"
    return Settings(
        mode="local",
        tracker_path=Path(os.getenv("TRACKER_PATH",
                                    ADZUNA_HOME / "jobs_tracker_beautified.xlsx")),
        aliases_path=data / "sponsor_aliases.json",
        rejections_path=data / "sponsor_rejections.json",
        decision_log_path=data / "decision_log.jsonl",
        register_csv=(Path(os.environ["SPONSOR_REGISTER_CSV"])
                      if os.getenv("SPONSOR_REGISTER_CSV") else None),
        actor_id=os.getenv("ACTOR_ID", "anurag"),
        actor_name=os.getenv("ACTOR_NAME", "Anurag Chavan"),
        cors_origins=_origins(),
        rate_limit_per_min=int(os.getenv("RATE_LIMIT_PER_MIN", "0")),  # 0 = off
        max_log_entries=int(os.getenv("MAX_LOG_ENTRIES", "0")),        # 0 = off
    )
