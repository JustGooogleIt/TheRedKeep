"""Settings, read from the environment. Keys are never written anywhere."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # backend/
DATA_DIR = ROOT / "data"

# The Anthropic key, in the order the names are tried. The project's own name comes first because
# hosts that run a Claude agent can keep ANTHROPIC_API_KEY for the agent: a Claude Code cloud
# session listed it in its environment settings but never passed it to the commands it ran.
ANTHROPIC_KEY_NAMES = ("REDKEEP_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY")


def first_set(names: tuple[str, ...]) -> tuple[str, str] | None:
    """The first of ``names`` set to a non-empty value, as (name, value)."""
    for name in names:
        if os.environ.get(name):
            return name, os.environ[name]
    return None


@dataclass(frozen=True)
class Settings:
    anthropic_api_key: str | None = None
    anthropic_key_name: str | None = None  # which of ANTHROPIC_KEY_NAMES supplied it
    massive_api_key: str | None = None
    cache_dir: Path = ROOT / ".cache"
    out_dir: Path = ROOT / "out"
    store_dir: Path = ROOT / "store"  # the point-in-time store: every price read, with when it was seen (store.py)
    calibration_file: Path = DATA_DIR / "calibration.json"  # slopes from `redkeep calibrate --write`; none: raw odds

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
    weekend_min_moves: int = 2  # sharp weekend moves an event needs for a Hyperliquid reading

    request_spacing: dict[str, float] = field(default_factory=lambda: {
        "gamma-api.polymarket.com": 0.15,
        "clob.polymarket.com": 0.15,
        "api.elections.kalshi.com": 0.25,
        "fred.stlouisfed.org": 0.5,
        "api.massive.com": 0.05,
        "api.hyperliquid.xyz": 1.5,  # 1,200 weight a minute; a candle request weighs about 28
    })

    @classmethod
    def from_env(cls) -> "Settings":
        root = Path(os.environ.get("REDKEEP_HOME", ROOT))
        name, key = first_set(ANTHROPIC_KEY_NAMES) or (None, None)
        return cls(
            anthropic_api_key=key,
            anthropic_key_name=name,
            massive_api_key=os.environ.get("MASSIVE_API_KEY") or None,
            cache_dir=root / ".cache",
            out_dir=root / "out",
            store_dir=root / "store",
            model=os.environ.get("REDKEEP_MODEL") or None,
            effort=os.environ.get("REDKEEP_EFFORT", cls.effort),
        )
