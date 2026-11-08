"""FRED daily series through the public ``fredgraph.csv`` download (no key).

The CSV's first column is ``observation_date`` (``DATE`` in older files);
missing values are ``.`` or empty.

Each series is fetched on its own. Checked against live responses on
2026-11-08: a request naming several ids ignores the date range and returns
the whole history, and when the series differ in frequency or aggregation
(the S&P 500 and the VIX are "daily, close"; yields are "daily") the answer is
a zip of one CSV per group instead of a CSV.
"""

from __future__ import annotations

import csv
import io
from datetime import date, timedelta

from ..http import Http
from ..store import Store

URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"


class FredFormatError(ValueError):
    pass


def parse_csv(text: str) -> dict[str, dict[date, float]]:
    if text.startswith("PK"):
        raise FredFormatError("FRED sent a zip, not a CSV: request one series at a time")
    rows = list(csv.reader(io.StringIO(text, newline="")))
    if not rows:
        return {}
    head = rows[0]
    if head[0].strip().lower() not in ("observation_date", "date"):
        raise FredFormatError(f"FRED sent something other than a CSV: {text[:80]!r}")
    out: dict[str, dict[date, float]] = {h: {} for h in head[1:]}
    for r in rows[1:]:
        if not r or not r[0]:
            continue
        d = date.fromisoformat(r[0][:10])
        for h, v in zip(head[1:], r[1:]):
            v = v.strip()
            if v and v != ".":
                out[h][d] = float(v)
    return out


class Fred:
    """With a ``store``, every series read is also recorded there, so later revisions show up as new versions."""

    def __init__(self, http: Http, *, ttl: float | None = 6 * 3600, store: Store | None = None):
        self.http, self.ttl, self.store = http, ttl, store

    def series(self, ids: list[str], start: date, end: date) -> dict[str, dict[date, float]]:
        ttl = None if end < date.today() - timedelta(days=7) else self.ttl
        out: dict[str, dict[date, float]] = {}
        for i in ids:
            text = self.http.get_text(URL, {"id": i, "cosd": start.isoformat(), "coed": end.isoformat()}, ttl=ttl)
            got = parse_csv(text)
            out[i] = {d: v for d, v in got.get(i, {}).items() if start <= d <= end}
            if self.store is not None:
                self.store.add_closes("fred", i, out[i])
        return out
