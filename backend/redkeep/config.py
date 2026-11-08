"""Settings, read from the environment. Keys are never written anywhere."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # backend/
DATA_DIR = ROOT / "data"


@dataclass(frozen=True)
class Settings:
    anthropic_api_key: str | None = None
    massive_api_key: str | None = None
    cache_dir: Path = ROOT / ".cache"
    out_dir: Path = ROOT / "out"

    # Claude: one strong model judges the matches and writes the prior (DESIGN.md section 8).
    # REDKEEP_MODEL names it; unset, the engine uses the newest Opus model the key can call.
    model: str | None = None
    effort: str = "medium"

    horizon_months: float = 6.0

    # Matching thresholds (DESIGN.md 6.3), to be tuned on the labeled set (E1).
    match_min: float = 0.80  # a "same question" match this good sets the odds
    leg_min: float = 0.60  # ladder legs and bounds need at least this
    max_bound_width: float = 0.30  # wider implied ranges count as "nothing fits"
    candidates_per_event: int = 24

    # Liquidity floor for tier A (DESIGN.md 6.4).
    liquid_volume: float = 50_000.0
    liquid_spread: float = 0.05

    # Evidence
    history_days: int = 180  # odds history for jump windows and link checks
    exposure_years: float = 2.0
    window_move: float = 0.05  # a "sharp" daily odds move, in probability

    request_spacing: dict[str, float] = field(default_factory=lambda: {
        "gamma-api.polymarket.com": 0.15,
        "clob.polymarket.com": 0.15,
        "api.elections.kalshi.com": 0.25,
        "fred.stlouisfed.org": 0.5,
        "api.massive.com": 0.05,
    })

    @classmethod
    def from_env(cls) -> "Settings":
        root = Path(os.environ.get("REDKEEP_HOME", ROOT))
        return cls(
            anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY") or None,
            massive_api_key=os.environ.get("MASSIVE_API_KEY") or None,
            cache_dir=root / ".cache",
            out_dir=root / "out",
            model=os.environ.get("REDKEEP_MODEL") or None,
            effort=os.environ.get("REDKEEP_EFFORT", cls.effort),
        )
