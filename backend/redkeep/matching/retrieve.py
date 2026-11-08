"""Candidate retrieval with BM25 over market titles and resolution rules (DESIGN.md 6.3 step 1).

Keyword search is cheap, needs no model and keeps the numbers (thresholds,
dates) that embeddings blur. Retrieval only proposes candidates; the judge
decides. Whole outcome groups are kept together, so ladders and mutually
exclusive outcome sets reach the judge intact, and both venues are
interleaved so a cross-venue check is possible.

Keywords alone favour contracts that repeat the event's words, such as
"emergency Fed cuts this year" for a December decision. A contract that closes
within a week of the event's deadline is the likeliest to ask the same
question, so its score gets a boost (checked on the live catalogue on
2026-11-08: without it, Kalshi's December Fed decision never reached the judge).
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict

import numpy as np

from ..events import EventSpec
from ..markets import Market

STOP = set("""
a an and any are as at be been before but by can do does for from has have if in into is it its least more most no
not of on or other than that the their there these this those to up was were what when which while who will with would
yes after during per each also only over under between both new market markets resolve resolves resolved resolution
""".split())


def tokenize(text: str) -> list[str]:
    out = []
    for t in re.findall(r"[a-z0-9]+(?:\.[0-9]+)?", text.lower()):
        if t in STOP or (len(t) == 1 and not t.isdigit()):
            continue
        if len(t) > 4 and t.endswith("s") and not t.endswith("ss") and not t[0].isdigit():
            t = t[:-1]
        out.append(t)
    return out


class BM25:
    def __init__(self, docs: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.tf = [Counter(d) for d in docs]
        self.len = np.array([len(d) for d in docs], dtype=float)
        self.avg = float(self.len.mean()) if len(docs) else 0.0
        df = Counter(t for d in self.tf for t in d)
        n = len(docs)
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}

    def scores(self, query: list[str]) -> np.ndarray:
        s = np.zeros(len(self.tf))
        norm = self.k1 * (1 - self.b + self.b * self.len / (self.avg or 1))
        for t in set(query):
            idf = self.idf.get(t)
            if idf is None:
                continue
            f = np.array([d.get(t, 0) for d in self.tf], dtype=float)
            s += idf * f * (self.k1 + 1) / (f + norm)
        return s


class Retriever:
    def __init__(self, markets: list[Market]):
        self.markets = markets
        self.index = BM25([tokenize(m.text()) for m in markets])
        self.groups: dict[tuple[str, str], list[int]] = defaultdict(list)
        for i, m in enumerate(markets):
            self.groups[(m.venue, m.event_id)].append(i)
        self.closes = np.array([m.close_time.date().toordinal() if m.close_time else np.nan for m in markets], dtype=float)

    def scores(self, spec: EventSpec, *, near_days: int = 7, boost: float = 1.3) -> np.ndarray:
        """BM25 score of every market for the event, boosted when it closes within ``near_days`` of the deadline."""
        query = tokenize(" ".join([spec.title, spec.question, *spec.search]))
        s = self.index.scores(query)
        gap = np.abs(self.closes - spec.deadline.toordinal())
        return np.where(np.nan_to_num(gap, nan=np.inf) <= near_days, s * boost, s)

    def candidates(self, spec: EventSpec, k: int = 24, per_group: int = 8, min_rel: float = 0.25,
                   pool: int = 200) -> list[tuple[Market, float]]:
        """Up to ``k`` markets from the best groups among the top ``pool``, dropping any under ``min_rel`` of the best."""
        s = self.scores(spec)
        floor = min_rel * float(s.max()) if len(s) else 0.0
        top = [i for i in np.argsort(-s)[:pool] if s[i] > 0 and s[i] >= floor]
        # Rank groups by their best market, then interleave venues.
        best: dict[tuple[str, str], float] = {}
        for i in top:
            g = (self.markets[i].venue, self.markets[i].event_id)
            best[g] = max(best.get(g, 0.0), float(s[i]))
        by_venue: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for g in sorted(best, key=best.get, reverse=True):
            by_venue[g[0]].append(g)
        order = []
        queues = [q for q in by_venue.values()]
        while any(queues):
            for q in queues:
                if q:
                    order.append(q.pop(0))
        out: list[tuple[Market, float]] = []
        for g in order:
            members = sorted((i for i in self.groups[g] if s[i] >= floor), key=lambda i: (-s[i], -self.markets[i].volume))[:per_group]
            for i in members:
                if len(out) >= k:
                    return out
                out.append((self.markets[i], float(s[i])))
        return out
