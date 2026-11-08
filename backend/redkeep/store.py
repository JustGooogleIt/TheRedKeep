"""Point-in-time storage (DESIGN.md milestone M1): every price the engine reads, kept with the time it was seen.

A backtest or a replay is only honest if it sees what was knowable at the time. Every row carries two clocks:

* the time it is about: an odds point's time, a candle's hour, a close's day, the date of a holdings file;
* ``seen_at``: when this store recorded it, in UTC. That is at or after the fetch, so it never claims knowledge
  earlier than it had.

The data clients write what they read, so every command records as it goes; ``redkeep record`` (``record.py``)
keeps up the history that sources drop. Hyperliquid keeps only the latest 5,000 candles of each interval, about 208
days of hourly ones, and neither venue keeps past quotes.

**Layout.** ``<root>/<table>/<YYYY-MM>/<stamp>-<id>.parquet``: append-only Parquet segments, one per write, filed by
the month they were written. A segment is written under a temporary name and renamed into place, and never changed
after that, so a recorder and a refresh can write at the same time. Reads query the segments through DuckDB.
``compact`` merges each month's segments into one.

**Versions.** Every table is versioned by its natural key. A write adds a row only when it differs from the latest
version stored for that key. Recording the same data again adds nothing, and a revision adds a version beside the
old one: a FRED restatement, say, or an hour's candle that was still open when first seen. Quotes work the same way,
with one more field: a catalogue read marks the markets the venue's previous read listed and this one doesn't as no
longer listed. So the catalogue as of any time is every market whose last state by then was listed, at the quotes
it had, and an hourly read of some 66,000 markets stores only the quotes that moved.

**As of a time T,** a read answers one of two questions:

* ``as_of=T``: what the sources say now about times up to T. It returns nothing about later times, and the latest
  version of each row. For prices that never change once printed (odds points, finished candles), that is exactly
  what was knowable at T, even when the store recorded it later. That is what a replay of April 2025 needs.
* ``as_of=T, known=True``: only what this store had seen by T. This is the strict view, for data that can be
  revised (closes, holdings, market details), and the only one quotes have, since they exist only as recorded.

All times are UTC. They are stored as naive timestamps (DuckDB's ``TIMESTAMP``) and returned timezone-aware.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence
from zoneinfo import ZoneInfo

import duckdb

from .markets import Market

NY = ZoneInfo("America/New_York")
CLOSE = time(16)  # a daily close counts as known from 4 p.m. New York time on its day
FAR = datetime(9999, 1, 1)


@dataclass(frozen=True)
class Table:
    name: str
    columns: tuple[tuple[str, str], ...]  # (name, DuckDB type), without seen_at
    key: tuple[str, ...]  # the natural key: a row is a new version of the row with the same key
    time: str | None = None  # the column a row is about

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(n for n, _ in self.columns)

    @property
    def values(self) -> tuple[str, ...]:
        return tuple(n for n in self.names if n not in self.key)


TABLES: dict[str, Table] = {t.name: t for t in (
    # A market's Yes price over time: the Yes token on Polymarket, the ticker on Kalshi; step is the spacing in minutes.
    Table("odds", (("venue", "VARCHAR"), ("market", "VARCHAR"), ("step", "INTEGER"), ("t", "TIMESTAMP"), ("q", "DOUBLE")),
          key=("venue", "market", "step", "t"), time="t"),
    # Hyperliquid candles, by open time.
    Table("candles", (("coin", "VARCHAR"), ("interval", "VARCHAR"), ("t", "TIMESTAMP"), ("t_end", "TIMESTAMP"),
                      ("open", "DOUBLE"), ("high", "DOUBLE"), ("low", "DOUBLE"), ("close", "DOUBLE"),
                      ("volume", "DOUBLE"), ("trades", "BIGINT")),
          key=("coin", "interval", "t"), time="t"),
    # Daily levels: FRED series and Massive closes.
    Table("closes", (("source", "VARCHAR"), ("series", "VARCHAR"), ("day", "DATE"), ("value", "DOUBLE")),
          key=("source", "series", "day"), time="day"),
    # ETF holdings, by the fund's effective date.
    Table("holdings", (("etf", "VARCHAR"), ("effective", "DATE"), ("ticker", "VARCHAR"), ("name", "VARCHAR"),
                       ("weight", "DOUBLE")),
          key=("etf", "effective", "ticker"), time="effective"),
    # A listed market's details, versioned; its prices are in quotes.
    Table("markets", (("venue", "VARCHAR"), ("market", "VARCHAR"), ("event_id", "VARCHAR"), ("title", "VARCHAR"),
                      ("event_title", "VARCHAR"), ("outcome", "VARCHAR"), ("rules", "VARCHAR"),
                      ("close_time", "TIMESTAMP"), ("exclusive", "BOOLEAN"), ("url", "VARCHAR"), ("series", "VARCHAR"),
                      ("token_yes", "VARCHAR"), ("category", "VARCHAR"), ("strike", "VARCHAR")),
          key=("venue", "market")),
    # A market's quotes in the catalogue, and whether it is still listed.
    Table("quotes", (("venue", "VARCHAR"), ("market", "VARCHAR"), ("bid", "DOUBLE"), ("ask", "DOUBLE"),
                     ("last", "DOUBLE"), ("volume", "DOUBLE"), ("volume_24h", "DOUBLE"), ("liquidity", "DOUBLE"),
                     ("listed", "BOOLEAN")),
          key=("venue", "market")),
)}


def naive(t: datetime | None) -> datetime | None:
    """A UTC timestamp without its timezone, the way the store keeps it. A naive input is taken to be UTC already."""
    if t is None or t.tzinfo is None:
        return t
    return t.astimezone(timezone.utc).replace(tzinfo=None)


def aware(t: datetime | None) -> datetime | None:
    return None if t is None else t.replace(tzinfo=timezone.utc)


def last_close_day(as_of: datetime | None) -> date:
    """The last day whose close was in by ``as_of``: closes count from 4 p.m. New York time."""
    if as_of is None:
        return date.max
    ny = (aware(as_of) if as_of.tzinfo is None else as_of).astimezone(NY)
    return ny.date() if ny.time() >= CLOSE else ny.date() - timedelta(days=1)


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _lit(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


class Store:
    """The point-in-time store under ``root``.

    Writes are buffered and land in a new segment on ``flush``, or when a table's buffer fills. A read of a table
    flushes it first, so a read always sees every write before it. Safe to share between threads.
    """

    def __init__(self, root: Path, *, buffer_rows: int = 200_000):
        self.root = Path(root)
        self.buffer_rows = buffer_rows
        self.added: dict[str, int] = {}  # rows this instance has written, per table
        self._buf: dict[str, list[tuple]] = {}
        self._lock = threading.RLock()

    # -- writing ----------------------------------------------------------------------------------

    def add(self, table: str, rows: Iterable[Sequence[Any]], *, seen_at: datetime | None = None) -> None:
        """Queue rows (in the table's column order, without ``seen_at``) seen at ``seen_at`` (default: now)."""
        t = TABLES[table]
        seen = naive(seen_at or datetime.now(timezone.utc))
        stamps = [i for i, (_, ty) in enumerate(t.columns) if ty == "TIMESTAMP"]
        new = []
        for r in rows:
            r = list(r)
            if len(r) != len(t.columns):
                raise ValueError(f"{table} rows have {len(t.columns)} values, not {len(r)}: {r!r}")
            for i in stamps:
                r[i] = naive(r[i])
            new.append((*r, seen))
        if not new:
            return
        with self._lock:
            buf = self._buf.setdefault(table, [])
            buf.extend(new)
            if len(buf) >= self.buffer_rows:
                self._flush(table)

    def flush(self) -> dict[str, int]:
        """Write every buffered row that is new; returns the rows written per table."""
        with self._lock:
            return {name: self._flush(name) for name in list(self._buf)}

    def _flush(self, table: str) -> int:
        rows = self._buf.pop(table, None)
        if not rows:
            return 0
        t = TABLES[table]
        with duckdb.connect() as con:
            self._load(con, "batch", t, rows)
            self._view(con, "old", t)
            con.execute(f"CREATE TEMP TABLE fresh AS {_new_versions(t)}")
            n = con.execute("SELECT count(*) FROM fresh").fetchone()[0]
            if n:
                self._write_segment(con, t, "fresh")
        self.added[table] = self.added.get(table, 0) + n
        return n

    @staticmethod
    def _load(con, name: str, t: Table, rows: list[tuple]) -> None:
        names = (*t.names, "seen_at")
        types = (*(ty for _, ty in t.columns), "TIMESTAMP")
        sel = ", ".join(f"unnest(${i + 1}::{ty}[]) AS {_q(n)}" for i, (n, ty) in enumerate(zip(names, types)))
        con.execute(f"CREATE TEMP TABLE {name} AS SELECT {sel}, unnest(range({len(rows)})) AS _i",
                    [list(col) for col in zip(*rows)])

    def _view(self, con, name: str, t: Table, files: list[Path] | None = None) -> None:
        """``name`` as a view of the table's segments (or an empty table when there are none)."""
        files = self.segments(t.name) if files is None else files
        cols = ", ".join(f"{_q(n)} {ty}" for n, ty in (*t.columns, ("seen_at", "TIMESTAMP")))
        if not files:
            con.execute(f"CREATE TEMP TABLE {name} ({cols})")
            return
        sel = ", ".join(_q(n) for n in (*t.names, "seen_at"))
        paths = ", ".join(_lit(p) for p in files)
        con.execute(f"CREATE TEMP VIEW {name} AS SELECT {sel} FROM read_parquet([{paths}], union_by_name = true)")

    def _write_segment(self, con, t: Table, src: str, *, month: str | None = None) -> Path:
        now = datetime.now(timezone.utc)
        folder = self.root / t.name / (month or now.strftime("%Y-%m"))
        folder.mkdir(parents=True, exist_ok=True)
        stem = f"{now:%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}"
        tmp, final = folder / f".{stem}.parquet.tmp", folder / f"{stem}.parquet"
        cols = ", ".join(_q(n) for n in (*t.names, "seen_at"))
        order = ", ".join(_q(n) for n in (*t.key, "seen_at"))
        con.execute(f"COPY (SELECT {cols} FROM {src} ORDER BY {order}) TO {_lit(tmp)} (FORMAT parquet, COMPRESSION zstd)")
        os.replace(tmp, final)
        return final

    # -- writing, by kind -------------------------------------------------------------------------

    def add_odds(self, venue: str, market: str, step: int, points: Iterable[tuple[datetime, float]], **kw) -> None:
        self.add("odds", ((venue, market, step, t, q) for t, q in points), **kw)

    def add_candles(self, coin: str, interval: str, candles: Iterable[Any], **kw) -> None:
        """Candles with ``start``, ``end``, ``open``, ``high``, ``low``, ``close``, ``volume`` and ``trades``."""
        self.add("candles", ((coin, interval, c.start, c.end, c.open, c.high, c.low, c.close, c.volume, c.trades)
                             for c in candles), **kw)

    def add_closes(self, source: str, series: str, levels: dict[date, float], **kw) -> None:
        self.add("closes", ((source, series, d, v) for d, v in levels.items()), **kw)

    def add_holdings(self, etf: str, effective: date, rows: Iterable[tuple[str, str, float]], **kw) -> None:
        self.add("holdings", ((etf, effective, ticker, name, weight) for ticker, name, weight in rows), **kw)

    def add_markets(self, markets: Sequence[Market], *, seen_at: datetime | None = None) -> None:
        """One catalogue read of a venue: each market's details and quotes (new versions only where they changed),
        and the markets the venue's previous read listed and this one doesn't, marked as no longer listed. An empty
        read marks nothing: it is likelier a failure than every market closing at once."""
        if not markets:
            return
        seen = seen_at or datetime.now(timezone.utc)
        self.add("markets", ((m.venue, m.id, m.event_id, m.title, m.event_title, m.outcome, m.rules, m.close_time,
                              m.exclusive, m.url, m.series, m.token_yes, m.category,
                              json.dumps(m.strike, sort_keys=True) if m.strike else None) for m in markets), seen_at=seen)
        rows = [(m.venue, m.id, m.bid, m.ask, m.last, m.volume, m.volume_24h, m.liquidity, True) for m in markets]
        now = {(m.venue, m.id) for m in markets}
        for venue in sorted({m.venue for m in markets}):
            listed = self.sql("SELECT market FROM quotes WHERE venue = $1 GROUP BY market HAVING arg_max(listed, seen_at)",
                              [venue], tables=["quotes"])
            rows += [(venue, mk, None, None, None, None, None, None, False) for (mk,) in listed if (venue, mk) not in now]
        self.add("quotes", rows, seen_at=seen)

    # -- reading ----------------------------------------------------------------------------------

    def sql(self, query: str, params: Sequence[Any] = (), *, tables: Sequence[str] = tuple(TABLES)) -> list[tuple]:
        """Run ``query`` with each of ``tables`` available under its own name, after flushing them."""
        with self._lock:
            for name in tables:
                self._flush(name)
            with duckdb.connect() as con:
                for name in tables:
                    self._view(con, _q(name), TABLES[name])
                return con.execute(query, list(params)).fetchall()

    @staticmethod
    def _cuts(as_of: datetime | None, known: bool) -> tuple[datetime, datetime]:
        cut = naive(as_of) if as_of is not None else FAR
        return cut, (cut if known else FAR)

    def odds(self, venue: str, market: str, start: datetime, end: datetime, *, step: int = 60,
             as_of: datetime | None = None, known: bool = False) -> list[tuple[datetime, float]]:
        """A market's Yes price from ``start`` to ``end``, oldest first, in the latest version (seen by ``as_of`` with
        ``known``); with ``as_of``, nothing after it."""
        cut, seen = self._cuts(as_of, known)
        rows = self.sql('SELECT t, arg_max(q, seen_at) FROM odds WHERE venue = $1 AND market = $2 AND step = $3 '
                        'AND t BETWEEN $4 AND $5 AND t <= $6 AND seen_at <= $7 GROUP BY t ORDER BY t',
                        [venue, market, step, naive(start), naive(end), cut, seen], tables=["odds"])
        return [(aware(t), q) for t, q in rows]

    def candles(self, coin: str, interval: str, start: datetime, end: datetime, *, as_of: datetime | None = None,
                known: bool = False) -> list[tuple]:
        """Candles that open from ``start`` to ``end``, oldest first, as (start, end, open, high, low, close, volume,
        trades). With ``as_of``, only candles finished by then; with ``known`` too, the version seen by then, which
        for the hour that was still open is the candle so far."""
        cut, seen = self._cuts(as_of, known)
        done = "t <= $6" if known else "t_end <= $6"
        vals = ", ".join(f"arg_max({_q(n)}, seen_at)" for n in ("t_end", "open", "high", "low", "close", "volume", "trades"))
        rows = self.sql(f'SELECT t, {vals} FROM candles WHERE coin = $1 AND "interval" = $2 AND t BETWEEN $3 AND $4 '
                        f'AND {done} AND seen_at <= $5 GROUP BY t ORDER BY t',
                        [coin, interval, naive(start), naive(end), seen, cut], tables=["candles"])
        return [(aware(r[0]), aware(r[1]), *r[2:]) for r in rows]

    def closes(self, source: str, series: str, start: date, end: date, *, as_of: datetime | None = None,
               known: bool = False) -> dict[date, float]:
        """A daily series from ``start`` to ``end``; with ``as_of``, only the days whose close was in by then."""
        cut, seen = self._cuts(as_of, known)
        last = min(end, last_close_day(as_of))
        rows = self.sql('SELECT "day", arg_max("value", seen_at) FROM closes WHERE source = $1 AND series = $2 '
                        'AND "day" BETWEEN $3 AND $4 AND seen_at <= $5 GROUP BY "day" ORDER BY "day"',
                        [source, series, start, last, seen], tables=["closes"])
        return dict(rows)

    def holdings(self, etf: str, *, as_of: datetime | None = None,
                 known: bool = False) -> tuple[date, list[tuple[str, str, float]]] | None:
        """The fund's holdings on its latest effective date by ``as_of``, largest first."""
        cut, seen = self._cuts(as_of, known)
        rows = self.sql("WITH v AS (SELECT effective, ticker, arg_max(name, seen_at) AS name, arg_max(weight, seen_at) AS w "
                        "FROM holdings WHERE etf = $1 AND effective <= $2 AND seen_at <= $3 GROUP BY effective, ticker) "
                        "SELECT effective, ticker, name, w FROM v WHERE effective = (SELECT max(effective) FROM v) "
                        "ORDER BY w DESC NULLS LAST, ticker",
                        [etf, last_close_day(as_of) if as_of else date.max, seen], tables=["holdings"])
        return (rows[0][0], [(t, n, w) for _, t, n, w in rows]) if rows else None

    def catalogue(self, *, as_of: datetime | None = None, venue: str | None = None) -> list[Market]:
        """The catalogue as of ``as_of``: every market still listed in its venue's last read by then, with the quotes
        and details it had."""
        cut = naive(as_of) if as_of is not None else FAR
        names = [n for n in TABLES["markets"].values]
        details = ", ".join(f"arg_max({_q(n)}, seen_at) AS {_q(n)}" for n in names)
        quotes = ", ".join(f"arg_max({_q(n)}, seen_at) AS {_q(n)}" for n in TABLES["quotes"].values)
        rows = self.sql(
            f"WITH q AS (SELECT venue, market, {quotes} FROM quotes WHERE seen_at <= $1 "
            f"AND ($2::VARCHAR IS NULL OR venue = $2) GROUP BY venue, market), "
            f"d AS (SELECT venue, market, {details} FROM markets WHERE seen_at <= $1 GROUP BY venue, market) "
            f"SELECT q.venue, q.market, q.bid, q.ask, q.last, q.volume, q.volume_24h, q.liquidity, "
            f"{', '.join('d.' + _q(n) for n in names)} FROM q JOIN d USING (venue, market) WHERE q.listed "
            f"ORDER BY q.venue, q.market",
            [cut, venue], tables=["quotes", "markets"])
        out = []
        for r in rows:
            v, mid, bid, ask, last, vol, vol24, liq = r[:8]
            d = dict(zip(names, r[8:]))
            out.append(Market(venue=v, id=mid, event_id=d["event_id"], title=d["title"], event_title=d["event_title"],
                              outcome=d["outcome"], rules=d["rules"] or "", close_time=aware(d["close_time"]), bid=bid,
                              ask=ask, last=last, volume=vol or 0.0, volume_24h=vol24 or 0.0, liquidity=liq or 0.0,
                              exclusive=bool(d["exclusive"]), url=d["url"] or "", series=d["series"],
                              token_yes=d["token_yes"], category=d["category"],
                              strike=json.loads(d["strike"]) if d["strike"] else {}))
        return out

    def last_time(self, table: str, **key: Any) -> datetime | date | None:
        """The latest time stored for rows matching ``key`` (column = value), e.g. the newest candle of a perp."""
        t = TABLES[table]
        where = " AND ".join(f"{_q(k)} = ${i + 1}" for i, k in enumerate(key)) or "true"
        [(v,)] = self.sql(f"SELECT max({_q(t.time)}) FROM {_q(table)} WHERE {where}", list(key.values()), tables=[table])
        return aware(v) if isinstance(v, datetime) else v

    # -- housekeeping -----------------------------------------------------------------------------

    def segments(self, table: str) -> list[Path]:
        return sorted((self.root / table).glob("*/*.parquet"))

    def status(self) -> list[dict]:
        """For each table: rows, segments, bytes, the span the rows are about and the span they were seen in."""
        out = []
        for name, t in TABLES.items():
            files = self.segments(name)
            about = f"min({_q(t.time)}), max({_q(t.time)})" if t.time else "NULL, NULL"
            [(n, a, b, s0, s1)] = self.sql(f"SELECT count(*), {about}, min(seen_at), max(seen_at) FROM {_q(name)}",
                                           tables=[name])
            span = tuple(aware(x) if isinstance(x, datetime) else x for x in (a, b))
            out.append({"table": name, "rows": n, "segments": len(files), "bytes": sum(p.stat().st_size for p in files),
                        "about": span, "seen": (aware(s0), aware(s1))})
        return out

    def compact(self) -> dict[str, tuple[int, int]]:
        """Merge each month's segments of each table into one, dropping repeated versions (two writers can store the
        same one). Returns (segments before, after) per table. Run it while nothing else writes to the store."""
        with self._lock:
            self.flush()
            out = {}
            for name, t in TABLES.items():
                before = self.segments(name)
                for month in sorted({p.parent for p in before}):
                    for stale in month.glob(".*.parquet.tmp"):  # a write that died before its rename
                        stale.unlink(missing_ok=True)
                    files = sorted(month.glob("*.parquet"))
                    if len(files) < 2:
                        continue
                    with duckdb.connect() as con:
                        self._view(con, "seg", t, files)
                        con.execute(f"CREATE TEMP TABLE merged AS {_compacted(t)}")
                        self._write_segment(con, t, "merged", month=month.name)
                    for p in files:
                        p.unlink()
                out[name] = (len(before), len(self.segments(name)))
            return out


def _new_versions(t: Table) -> str:
    """SQL for the rows of ``batch`` that are new versions: different from the version before them, whether that is
    the latest one stored (in ``old``) or an earlier row of the batch. Within one write, the last row given for a
    key wins."""
    key = ", ".join(_q(k) for k in t.key)
    pack = ", ".join(f"{_q(v)} := {_q(v)}" for v in t.values)
    latest = ", ".join(f"arg_max({_q(v)}, seen_at) AS {_q(v)}" for v in t.values)
    cols = ", ".join(_q(k) if k in t.key else f"v.{_q(k)} AS {_q(k)}" for k in t.names)
    return (f"WITH keys AS (SELECT DISTINCT {key} FROM batch), "
            f"latest AS (SELECT {key}, {latest}, max(seen_at) AS seen_at FROM old SEMI JOIN keys USING ({key}) "
            f"GROUP BY {key}), "
            f"given AS (SELECT * FROM batch QUALIFY row_number() OVER (PARTITION BY {key}, seen_at ORDER BY _i DESC) = 1), "
            f"seq AS (SELECT {key}, struct_pack({pack}) AS v, seen_at, false AS fresh FROM latest "
            f"UNION ALL SELECT {key}, struct_pack({pack}) AS v, seen_at, true AS fresh FROM given), "
            f"lagged AS (SELECT *, lag(v) OVER (PARTITION BY {key} ORDER BY seen_at, fresh) AS prev FROM seq) "
            f"SELECT {cols}, seen_at FROM lagged WHERE fresh AND (prev IS NULL OR prev IS DISTINCT FROM v)")


def _compacted(t: Table) -> str:
    """SQL for the rows of ``seg`` worth keeping: every version that differs from the one before it."""
    cols = ", ".join(_q(n) for n in (*t.names, "seen_at"))
    key = ", ".join(_q(k) for k in t.key)
    pack = ", ".join(f"{_q(v)} := {_q(v)}" for v in t.values)
    return (f"SELECT {cols} FROM (SELECT *, lag(struct_pack({pack})) OVER (PARTITION BY {key} ORDER BY seen_at) AS prev "
            f"FROM seg) WHERE prev IS NULL OR prev IS DISTINCT FROM struct_pack({pack})")
