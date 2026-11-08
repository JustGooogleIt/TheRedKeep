"""Claude's funnel labels (DESIGN.md 6.2 steps 2 and 3; section 8, "Claude teaches, Laya scales").

* **Cheap filters** drop thin markets, spreads too wide to say anything and markets all but decided. The catalogue
  already leaves out sports, entertainment and weather.
* **The sample** is even and repeatable: markets ordered by a hash of their ids, at most ``PER_GROUP`` from one
  group, so a 40-rung ladder can't fill it.
* **The labels.** Claude reads each market's title, outcome, group and rules, and answers under a fixed rubric with
  structured output: is the market macro, and how far each factor would move the day it is decided if it resolved
  yes rather than no, from −2 to +2 in typical weekly moves. A market is *material* when any factor would move by
  about half a typical move or more. Batches of ``BATCH`` markets share one call. Every request and response is saved
  under the cache directory, keyed by the rubric version, the model and the exact request, as for the judge, with the
  tokens and seconds it took, so E0 can price "Claude on every market".
"""

from __future__ import annotations

import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal, Sequence

from pydantic import BaseModel

from ..factors import FACTORS, KEYS
from ..markets import Market

LABEL_VERSION = "funnel-rubric-v1"
BATCH = 40  # markets per call
PER_GROUP = 2  # markets of one group in the sample
MIN_VOLUME = 5_000.0  # dollars traded
MAX_SPREAD = 0.20  # a wider quote says little about the odds
EDGE = 0.01  # odds within a point of 0 or 1: all but decided
FALLBACK_BETA = "server-side-fallback-2026-07-01"
Progress = Callable[[str], None]


def _factor_lines() -> str:
    return "\n".join(f"- {f.key}: {f.name}. Typical weekly move about {f.typical_default:g} {f.unit}; + means {f.up}."
                     for f in FACTORS)


RUBRIC = f"""\
You screen prediction markets for a portfolio risk engine that tracks ten market factors. For each market, picture \
the day it is decided, and compare a world where it resolves YES with one where it resolves NO. For each factor, \
give the difference, YES minus NO, on that day, in the factor's typical weekly moves:

{_factor_lines()}

Scale: 0 for less than half a typical move, or no clear direction; +1 or -1 for about half a typical move to two; \
+2 or -2 for more than two. Judge the size of the surprise, not the topic: a minor appointment, an election in a \
small economy, a celebrity, a sports result, a single company's product or a crypto price moves none of these \
factors. A Fed decision, a large tariff, a war between major powers, a recession, an inflation print well away from \
forecasts or a long government shutdown can. When the market is one outcome of a group (a rung of a ladder, or one of \
several mutually exclusive answers), compare this outcome with the others, using the group and the rules.

macro: true when the outcome is a macroeconomic, policy, geopolitical or market-wide event, false otherwise.
reason: one plain sentence a portfolio manager would understand, naming the main channel, for example "A surprise \
cut would pull yields down and lift stocks." or "A celebrity outcome: no factor would move."

Label every market, in the order given, and copy each market's id exactly."""

Move = Literal[-2, -1, 0, 1, 2]


class Moves(BaseModel):
    eq: Move
    rates: Move
    infl: Move
    usd: Move
    oil: Move
    credit: Move
    vix: Move
    china: Move
    chips: Move
    gold: Move


assert tuple(Moves.model_fields) == KEYS, "the rubric's factors must be the engine's, in order"


class MarketLabel(BaseModel):
    id: str
    macro: bool
    moves: Moves
    reason: str


class LabelBatch(BaseModel):
    labels: list[MarketLabel]


class LabelRefused(RuntimeError):
    pass


@dataclass
class Row:
    """One labeled market, as stored in ``data/funnel/labels.jsonl``."""
    key: str
    venue: str
    group: str  # venue and event id: a ladder or outcome set is one group
    title: str
    text: str  # what the screen reads: title, outcome, group title and the start of the rules
    closes: str | None
    macro: bool
    moves: list[int]
    reason: str
    model: str
    rubric: str
    labeled_at: str
    source: str = "sample"  # "sample": the even sample; "band": a market the screen was unsure about
    tokens_in: float = 0.0  # this market's share of its call's tokens and seconds
    tokens_out: float = 0.0
    seconds: float = 0.0

    @property
    def material(self) -> bool:
        return max(abs(m) for m in self.moves) >= 1


def group_of(m: Market) -> str:
    return f"{m.venue}:{m.event_id}"


def brief(m: Market) -> dict[str, Any]:
    return {"id": m.key, "venue": m.venue, "title": m.title, "outcome": m.outcome, "group": m.event_title,
            "group_outcomes_mutually_exclusive": m.exclusive,
            "closes": m.close_time.date().isoformat() if m.close_time else None, "rules": m.rules[:600]}


# -- the cheap filters and the sample ----------------------------------------------------------------------


@dataclass
class Filtered:
    kept: list[Market]
    dropped: dict[str, int] = field(default_factory=dict)


def cheap_filters(markets: Sequence[Market], now: datetime | None = None) -> Filtered:
    now = now or datetime.now(timezone.utc)
    out = Filtered([])
    for m in markets:
        mid = m.mid
        why = ("closed" if m.close_time is not None and m.close_time < now
               else "thin" if m.volume < MIN_VOLUME
               else "wide spread" if m.bid is not None and m.ask is not None and m.ask - m.bid > MAX_SPREAD
               else "all but decided" if mid is not None and not EDGE <= mid <= 1 - EDGE
               else None)
        if why:
            out.dropped[why] = out.dropped.get(why, 0) + 1
        else:
            out.kept.append(m)
    return out


def _h(seed: str, key: str) -> str:
    return hashlib.sha256(f"{seed}|{key}".encode()).hexdigest()


def sample(markets: Sequence[Market], n: int, *, per_group: int = PER_GROUP, seed: str = "funnel") -> list[Market]:
    """An even, repeatable sample: markets in the order of a hash of their ids, at most ``per_group`` per group."""
    taken: dict[str, int] = {}
    out = []
    for m in sorted(markets, key=lambda m: _h(seed, m.key)):
        g = group_of(m)
        if taken.get(g, 0) < per_group:
            taken[g] = taken.get(g, 0) + 1
            out.append(m)
            if len(out) >= n:
                break
    return out


# -- Claude ---------------------------------------------------------------------------------------------------


@dataclass
class Usage:
    calls: int = 0
    cached: int = 0  # batches read back from the cache
    refused: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    seconds: float = 0.0

    def add(self, other: "Usage") -> None:
        for k, v in asdict(other).items():
            setattr(self, k, getattr(self, k) + v)


class Labeler:
    def __init__(self, client: Any, *, model: str, effort: str = "medium", log_dir: Path | None = None,
                 batch: int = BATCH, workers: int = 4):
        self.client, self.model, self.effort, self.log_dir = client, model, effort, log_dir
        self.batch, self.workers = batch, workers

    def _call(self, markets: Sequence[Market]) -> tuple[dict[str, MarketLabel], Usage, dict]:
        """One batch: its labels by market key, what it cost, and the saved record's numbers."""
        body = json.dumps({"markets": [brief(m) for m in markets]}, indent=1, ensure_ascii=False)
        key = hashlib.sha256(f"{LABEL_VERSION}|{self.model}|{self.effort}|{body}".encode()).hexdigest()[:32]
        path = self.log_dir / "funnel" / f"{key}.json" if self.log_dir else None
        if path and path.exists():
            rec = json.loads(path.read_text())
            u = Usage(cached=1)
        else:
            t0 = time.perf_counter()
            resp = self.client.beta.messages.parse(
                model=self.model,
                max_tokens=16000,
                betas=[FALLBACK_BETA],
                fallbacks="default",
                thinking={"type": "adaptive"},
                output_config={"effort": self.effort},
                system=[{"type": "text", "text": RUBRIC, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": body}],
                output_format=LabelBatch,
            )
            if resp.stop_reason == "refusal":
                raise LabelRefused(f"the labeler declined a batch of {len(markets)} markets")
            if resp.stop_reason == "max_tokens" or resp.parsed_output is None:
                raise RuntimeError(f"the labeler returned no labels (stop_reason={resp.stop_reason})")
            usage = resp.usage.model_dump() if getattr(resp, "usage", None) else {}
            rec = {"rubric": LABEL_VERSION, "model": self.model, "served_by": getattr(resp, "model", None),
                   "request": json.loads(body), "output": resp.parsed_output.model_dump(), "usage": usage,
                   "seconds": time.perf_counter() - t0}
            if path:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(rec, indent=1, ensure_ascii=False))
            u = Usage(calls=1)
        usage = rec.get("usage") or {}
        tin = sum(int(usage.get(k) or 0) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
        u.tokens_in, u.tokens_out, u.seconds = tin, int(usage.get("output_tokens") or 0), float(rec.get("seconds") or 0)
        got = {lb.id: lb for lb in LabelBatch.model_validate(rec["output"]).labels}
        return got, u, rec

    def label(self, markets: Sequence[Market], *, source: str = "sample",
              progress: Progress = lambda _: None) -> tuple[list[Row], Usage, int]:
        """Rows for the markets Claude labeled, what it cost, and how many it left unlabeled."""
        batches = [list(markets[i:i + self.batch]) for i in range(0, len(markets), self.batch)]
        total, rows, missing = Usage(), [], 0
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")

        def one(b: list[Market]):
            try:
                return b, *self._call(b)
            except LabelRefused:
                return b, {}, Usage(refused=1), {}

        with ThreadPoolExecutor(self.workers) as ex:
            for n, (b, got, u, rec) in enumerate(ex.map(one, batches), 1):
                total.add(u)
                done = [m for m in b if m.key in got]
                missing += len(b) - len(done)
                share = 1 / max(len(done), 1)
                for m in done:
                    lb = got[m.key]
                    rows.append(Row(
                        key=m.key, venue=m.venue, group=group_of(m), title=m.title, text=m.text(),
                        closes=m.close_time.date().isoformat() if m.close_time else None, macro=lb.macro,
                        moves=[int(getattr(lb.moves, k)) for k in KEYS], reason=lb.reason,
                        model=str(rec.get("served_by") or rec.get("model") or self.model), rubric=LABEL_VERSION,
                        labeled_at=now, source=source, tokens_in=u.tokens_in * share, tokens_out=u.tokens_out * share,
                        seconds=u.seconds * share))
                if n % 10 == 0 or n == len(batches):
                    progress(f"  labeled {sum(len(x) for x in batches[:n]):,} of {len(markets):,} markets "
                             f"({total.calls} calls, {total.cached} from the cache)")
        return rows, total, missing


# -- the labels file ----------------------------------------------------------------------------------------------


def read_rows(path: Path) -> list[Row]:
    if not path.exists():
        return []
    return [Row(**json.loads(line)) for line in path.read_text().splitlines() if line.strip()]


def append_rows(path: Path, rows: Sequence[Row]) -> int:
    """Add rows for markets not labeled yet under this rubric; returns how many were new."""
    have = {(r.key, r.rubric) for r in read_rows(path)}
    new = [r for r in rows if (r.key, r.rubric) not in have]
    if new:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            for r in new:
                f.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")
    return len(new)
