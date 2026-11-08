"""Sample portfolios and holdings parsing."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from .config import DATA_DIR

Holding = tuple[str, float, str]  # (ticker, amount, "usd" | "shares")


@dataclass(frozen=True)
class Sample:
    id: str
    name: str
    who: str
    note: str
    holdings: list[Holding]


def samples(path: Path | None = None) -> dict[str, Sample]:
    raw = tomllib.loads((path or DATA_DIR / "portfolios.toml").read_text())
    return {p["id"]: Sample(p["id"], p["name"], p.get("who", ""), p.get("note", ""),
                            [(t.upper(), float(a), u) for t, a, u in p["holdings"]]) for p in raw["portfolio"]}


def parse_holdings(text: str) -> list[Holding]:
    """``"VTI:300000, NVDA:120sh"``: dollars by default, or shares with an ``sh`` suffix."""
    out = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        t, _, amt = part.partition(":")
        unit = "shares" if amt.strip().lower().endswith("sh") else "usd"
        val = float(amt.strip().lower().removesuffix("sh").replace("$", "").replace("_", ""))
        if val <= 0:
            raise ValueError(f"amount must be positive: {part}")
        out.append((t.strip().upper(), val, unit))
    if not out:
        raise ValueError("no holdings given")
    return out
