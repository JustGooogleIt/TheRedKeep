"""FRED daily series through the public ``fredgraph.csv`` download (no key).

The CSV's first column is ``observation_date`` (``DATE`` in older files);
missing values are ``.`` or empty.
"""

from __future__ import annotations

import csv
import io
from datetime import date, timedelta

from ..http import Http

URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"


def parse_csv(text: str) -> dict[str, dict[date, float]]:
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return {}
    head = rows[0]
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
    def __init__(self, http: Http, *, ttl: float | None = 6 * 3600):
        self.http, self.ttl = http, ttl

    def series(self, ids: list[str], start: date, end: date) -> dict[str, dict[date, float]]:
        text = self.http.get_text(URL, {
            "id": ",".join(ids), "cosd": start.isoformat(), "coed": end.isoformat(),
        }, ttl=None if end < date.today() - timedelta(days=7) else self.ttl)
        return parse_csv(text)
