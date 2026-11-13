"""The ten core factors (DESIGN.md 6.1) and how each one is measured.

A factor's daily change is computed from its proxy's levels:

* ``ret``: percent change of a price (stocks, the dollar index, oil, gold);
* ``diff``: change in a level, times ``scale`` (yields and spreads in basis
  points, the VIX in points);
* ``spread``: percent change of the first ticker minus the second (chips vs. the
  market).

Every factor's daily change spans two 4 p.m. New York closes, the clock the
odds are read on too (DESIGN.md 4.8). The Fed's dollar index is fixed at noon,
so the engine carries it to the close with ``carry``, a fund that trades there
(``factordata.carry_to_close``): measured at noon, about a fifth of each day's
dollar move would land on the next day's change.

A *typical move* is one standard deviation of the factor's weekly change
(DESIGN.md 4.1). Defaults below are only used for display before any data is
loaded; the engine measures them from data.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Transform = Literal["ret", "diff", "spread"]


@dataclass(frozen=True)
class Factor:
    key: str
    name: str
    unit: str
    transform: Transform
    fred: str | None = None
    massive: tuple[str, ...] = ()
    scale: float = 1.0
    typical_default: float = 1.0
    up: str = ""
    fixed: int | None = None  # the hour (New York) the FRED series is fixed at, when that isn't the 4 p.m. close
    carry: str | None = None  # a ticker that trades at the close, to carry the series there from ``fixed``


FACTORS: tuple[Factor, ...] = (
    Factor("eq", "US stocks", "%", "ret", fred="SP500", massive=("SPY",), typical_default=2.2, up="stocks up"),
    Factor("rates", "Interest rates", "bp", "diff", fred="DGS10", scale=100.0, typical_default=12.0, up="yields up"),
    Factor("infl", "Inflation expectations", "bp", "diff", fred="T10YIE", scale=100.0, typical_default=5.0, up="breakevens up"),
    Factor("usd", "US dollar", "%", "ret", fred="DTWEXBGS", typical_default=0.9, up="dollar up", fixed=12, carry="UUP"),
    Factor("oil", "Oil", "%", "ret", fred="DCOILWTICO", typical_default=4.5, up="oil up"),
    Factor("credit", "Credit stress", "bp", "diff", fred="BAMLH0A0HYM2", scale=100.0, typical_default=15.0, up="spreads wider"),
    Factor("vix", "Fear", "pts", "diff", fred="VIXCLS", typical_default=2.5, up="VIX up"),
    Factor("china", "China and trade", "%", "ret", massive=("FXI",), typical_default=3.5, up="China stocks up"),
    Factor("chips", "Chips and AI", "%", "spread", massive=("SMH", "SPY"), typical_default=2.5, up="chips beat the market"),
    Factor("gold", "Gold", "%", "ret", massive=("GLD",), typical_default=2.0, up="gold up"),
)

KEYS: tuple[str, ...] = tuple(f.key for f in FACTORS)
NF = len(FACTORS)
BY_KEY = {f.key: f for f in FACTORS}


def index(key: str) -> int:
    return KEYS.index(key)
