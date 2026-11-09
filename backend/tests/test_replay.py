"""The historical replay (eval E8), offline: a synthetic episode with planted jumps, and stand-in venues that serve
closed markets the way Polymarket and Kalshi do. Nothing here is market data."""

import json
from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace

import numpy as np
import pytest

from redkeep import cli, factordata, replay
from redkeep.config import Settings
from redkeep.factors import FACTORS, index
from redkeep.markets import Market
from redkeep.matching.judge import Verdict
from redkeep.pipeline import Clients

from .synth import business_days, factor_levels

DAYS = business_days(date(2017, 12, 1), date(2025, 4, 30))
BASE = date(2025, 3, 28)
EP = [d for d in DAYS if date(2025, 3, 31) <= d <= date(2025, 4, 11)]
LOOK = [d for d in DAYS if BASE - timedelta(days=170) < d <= BASE]
PROXY = {"eq": "SP500", "rates": "DGS10", "infl": "T10YIE", "usd": "DTWEXBGS", "oil": "DCOILWTICO",
         "credit": "BAMLH0A0HYM2", "vix": "VIXCLS"}
# The planted jumps, in typical moves: the truth the replay should find.
D_UNIVERSAL = {"eq": -4.0, "oil": -2.0, "usd": -1.0}
D_RECESSION = {"eq": -2.0, "rates": -2.0}
D_CHINA = {"eq": -3.0}

EPISODE = """
title = "A synthetic episode"
sub = "Planted jumps on random walks."
start = 2025-03-31
end = 2025-04-11
lookback_days = 180
notes = [{ day = 2025-04-03, note = "The universal tariff resolves." }]

[[event]]
id = "universal"
title = "US announces universal tariffs"
short = "Universal"
question = "Will the US announce universal tariffs by April 30, 2025?"
resolves = 2025-04-30
search = ["universal tariffs"]
happened = 2025-04-02T20:10:00Z

[[event]]
id = "china"
title = "China retaliates with tariffs"
short = "China"
question = "Will China retaliate with tariffs by April 30, 2025?"
resolves = 2025-04-30
search = ["China retaliate tariffs"]
happened = 2025-04-04T10:00:00Z
analogs = [{ date = 2019-05-13, days = 1, surprise = 0.7, note = "A planted retaliation day" }]

[[event]]
id = "recession"
title = "US recession in 2025"
short = "Recession"
question = "Will the US enter a recession in 2025?"
resolves = 2025-12-31
search = ["recession 2025"]

[[event]]
id = "pause"
title = "US pauses the tariffs"
short = "Pause"
question = "Will the US pause the tariffs by April 30, 2025?"
resolves = 2025-04-30
search = ["pause"]
happened = 2025-04-09T17:18:00Z

[[link]]
parent = "universal"
child = "recession"
w = 0.3
reason = "Planted as independent, so the data shouldn't confirm it."
"""


def at(d: date, hh: int = 19, mm: int = 30) -> datetime:
    return datetime.combine(d, time(hh, mm), timezone.utc)


def walk(days, q0, jumps, seed):
    rng = np.random.default_rng(seed)
    out, q = {}, q0
    for d in days:
        q = float(np.clip(q + jumps.get(d, 0.0) + rng.normal(0, 0.003), 0.02, 0.98))
        out[d] = q
    return out


def plant(levels, typical, day, delta, dq):
    for key, d in delta.items():
        f = FACTORS[index(key)]
        move = d * dq * typical[index(key)]
        s = levels[PROXY[key]]
        for t in s:
            if t >= day:
                s[t] = s[t] + move / f.scale if f.transform == "diff" else s[t] * (1 + move / 100)


def build_world(seed: int = 3, after: date | None = None, linked: bool = False):
    """Odds paths and factor levels with the planted jumps. ``after`` reshuffles every price after that day, to check
    that no evening up to it sees them. ``linked`` moves the recession odds half as far as the tariff odds on the
    tariff's days, so the data confirms the episode's link."""
    levels = factor_levels(DAYS, seed=seed)
    typical = factordata.build({k: {d: v for d, v in s.items() if d <= BASE} for k, s in levels.items()}).typical
    ju = {LOOK[i]: (0.2 if k % 2 == 0 else -0.18) for k, i in enumerate([8, 20, 33, 45, 58, 70, 84, 96, 108, 115])}
    jr = {LOOK[i]: (0.15 if k % 2 == 0 else -0.12) for k, i in enumerate([14, 27, 39, 52, 64, 77, 90, 101, 112])}
    jr[date(2025, 4, 7)] = 0.12
    if linked:
        jr.update({d: 0.5 * dq for d, dq in ju.items()})
    u = walk(LOOK + EP[:3], 0.3, ju, 1)  # to the close of April 2; it happens after that close
    r = walk(LOOK + EP, 0.25, jr, 2)
    for d, dq in ju.items():
        plant(levels, typical, d, D_UNIVERSAL, dq)
    plant(levels, typical, date(2025, 4, 3), D_UNIVERSAL, 1 - u[date(2025, 4, 2)])
    for d, dq in jr.items():
        plant(levels, typical, d, D_RECESSION, dq)
    plant(levels, typical, date(2025, 4, 4), D_CHINA, 1 - 0.4)
    plant(levels, typical, date(2019, 5, 13), {"eq": D_CHINA["eq"] * 0.7}, 1.0)
    if after:
        rng = np.random.default_rng(99)
        for s in levels.values():
            for t in s:
                if t > after:
                    s[t] *= 1 + rng.normal(0, 0.05)
        r = {d: (q if d <= after else float(rng.uniform(0.05, 0.95))) for d, q in r.items()}
    hourly = {
        "tok-u": [(at(d), q) for d, q in u.items()] + [(at(date(2025, 4, 2), 20, 30), 0.97)],
        "tok-c": [(at(date(2025, 4, 3), 19, 0), 0.4), (at(date(2025, 4, 4), 12), 0.99)],
        "KXREC-25": [(at(d), q) for d, q in r.items()],
    }
    return levels, hourly


def pm_event(i, title, token, start, end):
    return {"id": f"ev{i}", "title": title, "slug": f"ev-{i}", "negRisk": False, "tags": [{"slug": "economy"}],
            "markets": [{"id": f"m{i}", "question": title, "outcomes": '["Yes", "No"]', "outcomePrices": '["1", "0"]',
                         "clobTokenIds": json.dumps([token, token + "-no"]), "endDate": end, "closed": True,
                         "startDate": start, "description": f"Resolves yes if: {title}", "volumeNum": 2e6}]}


class PastPolymarket:
    """Closed events under one tag. The universal tariff market serves hourly prices in windows; the China market only
    over its whole life; daily prices are stamped at midnight UTC, as Polymarket's are."""

    def __init__(self, hourly):
        self.hourly = hourly
        self.events = [
            pm_event(1, "Will the US announce universal tariffs by April 30?", "tok-u", "2024-09-01T00:00:00Z", "2025-04-30T12:00:00Z"),
            pm_event(2, "Will China retaliate with tariffs on US goods by April 30?", "tok-c", "2025-04-03T15:00:00Z",
                     "2025-04-30T12:00:00Z"),
            pm_event(3, "Will the Fed hike in June?", "tok-f", "2024-01-01T00:00:00Z", "2025-02-01T12:00:00Z"),
        ]

    def listed_events(self, tag, start, end, *, closed, pages=3):
        return self.events if tag == "economy" and closed else []

    def search_closed(self, q, limit=20):
        return []

    def search(self, q, limit=10):
        return []

    def history(self, token, start, end, fidelity_min=60):
        return [p for p in self.hourly.get(token, []) if start <= p[0] <= end] if token == "tok-u" else []

    def lifetime(self, token, *, fidelity_min=1440):
        pts = self.hourly.get(token, [])
        if fidelity_min == 60:
            return list(pts) if token == "tok-c" else []
        return daily_points(pts, hour=0)


def daily_points(pts, hour):
    """The last price of each UTC day, stamped at ``hour`` the next day."""
    last = {}
    for t, q in sorted(pts):
        last[t.date()] = q
    return [(datetime.combine(d + timedelta(days=1), time(hour), timezone.utc), q) for d, q in sorted(last.items())]


class PastKalshi:
    def __init__(self, hourly):
        self.hourly = hourly

    def series(self, category):
        return [{"ticker": "KXREC", "title": "Recession", "category": "Economics", "frequency": "custom", "tags": ["Economy"]},
                {"ticker": "KXRAIN", "title": "Rain in NYC", "category": "Climate and Weather", "frequency": "daily"}] \
            if category == "Economics" else []

    def settled(self, series, *, archived, pages=25):
        if series != "KXREC" or not archived:
            return []
        return [{"ticker": "KXREC-25", "event_ticker": "KXREC-25", "market_type": "binary", "title": "Recession in 2025?",
                 "rules_primary": "Resolves yes if the US enters a recession in 2025.", "close_time": "2026-01-31T00:00:00Z",
                 "open_time": "2024-06-01T14:00:00Z", "volume": 90000, "last_price_dollars": "0.0000", "result": "no"}]

    def history(self, series, ticker, start, end, *, period_min=60, archived=False):
        assert archived, "an archived market is read from the historical endpoint"
        return [p for p in self.hourly.get(ticker, []) if start <= p[0] <= end]

    def daily(self, series, ticker, start, end, *, archived):
        return daily_points(self.hourly.get(ticker, []), hour=4)


class Fred:
    def __init__(self, levels):
        self.levels = levels

    def series(self, ids, start, end):
        return {i: {d: v for d, v in self.levels[i].items() if start <= d <= end} for i in ids if i in self.levels}


class NoMassive:
    available = False


class Judge:
    WORDS = {"universal": "universal", "china": "retaliate", "recession": "recession", "pause": "pause"}

    def __init__(self):
        self.today, self.seen = set(), {}

    def judge(self, spec, markets, today):
        self.today.add(today)
        self.seen[spec.id] = [m.title for m in markets]
        out = []
        for m in markets:
            hit = self.WORDS[spec.id] in m.title.lower()
            out.append(Verdict(id=m.key, relation="same_question" if hit else "unrelated", polarity="yes", same_entity=hit,
                               threshold="not_applicable", deadline="same", resolution_source="same", market_threshold=None,
                               market_deadline=None, score=0.95 if hit else 0.05, quoted_rule="", reason="Synthetic."))
        return out


def clients(levels, hourly):
    return Clients(PastPolymarket(hourly), PastKalshi(hourly), Fred(levels), NoMassive(), Judge(), None, model="test")


@pytest.fixture(scope="module")
def episode(tmp_path_factory):
    path = tmp_path_factory.mktemp("ep") / "synthetic.toml"
    path.write_text(EPISODE)
    return replay.load_episode(path)


@pytest.fixture(scope="module")
def rep(episode, tmp_path_factory):
    c = clients(*build_world())
    out = replay.run(Settings(cache_dir=tmp_path_factory.mktemp("c")), c, episode, progress=lambda _: None)
    out["_judged_on"] = sorted(c.judge.today)
    out["_seen"] = c.judge.seen
    return out


def ev(rep, eid):
    return next(e for e in rep["events"] if e["id"] == eid)


def test_the_tariff_episode_file_loads():
    ep = replay.load_episode("tariffs-2025")
    assert (ep.start, ep.end, ep.lookback) == (date(2025, 3, 31), date(2025, 4, 11), 180)
    by = {e.spec.id: e for e in ep.events}
    assert by["universal"].happened == datetime(2025, 4, 2, 20, 10, tzinfo=timezone.utc)
    assert by["fed-may-cut"].happened is None and len(ep.notes) == 10 and all(d in ep.notes for d in EP)
    days = [d for d in EP]
    # After the April 2 close, so April 3; China's announcement before the open, so April 4; the pause midday.
    assert [replay.resolution_day(by[k].happened, days) for k in ("universal", "china-retaliates", "pause")] == [3, 4, 7]
    assert all(a.date < ep.start for e in ep.events for a in e.spec.analogs)


def test_odds_at_the_close_read_hourly_and_daily_prices():
    d = date(2025, 4, 2)  # 4 p.m. in New York is 20:00 UTC in April
    hourly = [(at(d, 19, 0), 0.40), (at(d, 20, 0), 0.45), (at(d, 21, 0), 0.90)]
    assert replay.at_closes(hourly, [d]) == {d: 0.45}
    # Polymarket's daily price is stamped at midnight UTC, after the close: it counts from the next day's close.
    daily = [(datetime(2025, 4, 2, tzinfo=timezone.utc), 0.40), (datetime(2025, 4, 3, tzinfo=timezone.utc), 0.90)]
    assert replay.at_closes(daily, [d, d + timedelta(days=1)]) == {d: 0.40, d + timedelta(days=1): 0.90}
    assert replay.at_closes(daily, [d], listed=at(d, 20, 30)) == {}  # listed after that close
    assert replay.at_closes(daily, [d + timedelta(days=9)]) == {}  # too stale


def test_the_judge_matches_as_of_the_first_evening(rep):
    assert rep["_judged_on"] == [BASE]
    assert rep["base"] == BASE.isoformat() and [d["d"] for d in rep["days"]] == [d.isoformat() for d in EP]
    assert {e["id"]: e["match"]["kind"] for e in rep["events"]} == {"universal": "match", "china": "match",
                                                                     "recession": "match", "pause": "none"}
    assert ev(rep, "universal")["odds"]["granularity"] == ["hourly"]
    assert ev(rep, "recession")["odds"]["markets"][0]["key"] == "kalshi:KXREC-25"
    assert rep["catalogue"]["polymarket"] == 2  # the Fed market closed before the episode
    json.dumps({k: v for k, v in rep.items() if not k.startswith("_")}, allow_nan=False)


def test_an_event_that_happened_is_matched_only_among_markets_listed_before_it(rep):
    # The China market was listed on April 3, after the universal tariffs were announced on April 2.
    china = "Will China retaliate with tariffs on US goods by April 30?"
    assert china in rep["_seen"]["china"] and china not in rep["_seen"]["universal"]
    m = lambda t: SimpleNamespace(opened=t)  # noqa: E731
    at = datetime(2025, 4, 2, 20, 10, tzinfo=timezone.utc)
    early, late, unknown = m(at - timedelta(hours=1)), m(at + timedelta(hours=1)), m(None)
    assert replay.listed_before([early, late, unknown], at) == [early, unknown]
    assert replay.listed_before([early, late], None) == [early, late]


def test_an_event_counts_only_once_a_market_lists_it(rep):
    china = ev(rep, "china")
    assert china["listed"] == "2025-04-03"
    assert china["q"][:3] == [None, None, None] and china["q"][3] == pytest.approx(0.4)
    assert china["q"][4:] == [None] * 6  # resolved on April 4
    assert ev(rep, "pause")["q"] == [None] * len(EP)
    assert ev(rep, "pause")["res"] == 7 and ev(rep, "pause")["out"] == 1
    pause = next(c for c in rep["calls"] if c["event"] == "pause")
    assert pause["pred"] == [None] * 10 and "No market" in pause["why"]


def test_calls_use_the_evening_before(rep):
    u = ev(rep, "universal")
    assert u["res"] == 3 and u["out"] == 1
    call = next(c for c in rep["calls"] if c["event"] == "universal")
    eq = index("eq")
    q_before = u["q"][2]  # the close of April 2, before the announcement after it
    assert call["q"] == pytest.approx(q_before) and 0.2 < q_before < 0.8
    # Expected: (1 - q) times the jump found in the windows, the right way, about the planted size.
    assert call["pred"][eq] < 0 and call["real"][eq] < 0
    assert call["pred"][eq] == pytest.approx((1 - q_before) * D_UNIVERSAL["eq"], abs=1.5)
    assert call["pred"][index("china")] is None  # no data for that factor without Massive
    # China's call rests on its analog alone, since its market is a day old.
    china = next(c for c in rep["calls"] if c["event"] == "china")
    assert china["q"] == pytest.approx(0.4) and china["pred"][eq] < 0
    assert ev(rep, "china")["readings"][4] == ["Past events (1)"]


def test_explained_moves_follow_the_odds(rep):
    eq = index("eq")
    days = {d["d"]: d for d in rep["days"]}
    # April 3: the universal tariff's odds go to 1, and the planted move with them.
    apr3 = days["2025-04-03"]
    assert apr3["explained"][eq] < 0 and apr3["z"][eq] < 0
    assert apr3["explained"][eq] == pytest.approx(apr3["z"][eq], abs=1.2)
    assert apr3["explained"][index("gold")] is None  # no gold data
    m = rep["metrics"]
    assert m["explained"]["corr"] > 0.5 and m["explained"]["explained_share"] > 0.2
    # Only the planted factors carry a signal here; on the others the truth is zero and the sign a coin flip.
    call = next(c for c in rep["calls"] if c["event"] == "universal")
    assert all(np.sign(call["pred"][index(k)]) == np.sign(v) for k, v in D_UNIVERSAL.items())
    assert m["calls"]["factor_signs_right"] > m["calls"]["factor_signs"] / 2
    assert [r["event"] for r in m["ranking"]][:1] == ["universal"]


def test_jumps_are_reestimated_each_evening_from_its_own_data(rep):
    u = ev(rep, "universal")
    eq = index("eq")
    first, after = u["d"][0][eq], u["d"][4][eq]  # the first evening, and the evening of the April 3 resolution
    assert first < 0 and after < 0 and first != after
    # The resolution day is a big, clean reading, so the error bar shrinks once it's in.
    assert u["sd"][4][eq] < u["sd"][0][eq]


def test_no_evening_sees_later_prices(episode, rep, tmp_path):
    cut = date(2025, 4, 3)
    other = replay.run(Settings(cache_dir=tmp_path), clients(*build_world(after=cut)), episode, progress=lambda _: None)
    k = EP.index(cut) + 1  # evenings: the first one, then the close of each day
    for a, b in zip(rep["events"], other["events"]):
        assert a["d"][: k + 1] == b["d"][: k + 1], a["id"]
        assert a["q"][: EP.index(cut) + 1] == b["q"][: EP.index(cut) + 1]
    assert other["days"][: EP.index(cut) + 1] == rep["days"][: EP.index(cut) + 1]
    assert other["events"][0]["d"][k + 2] != rep["events"][0]["d"][k + 2]  # later evenings do see the change


def test_links_need_the_data(rep):
    (ln,) = rep["links"]
    assert len(ln["w"]) == len(EP) + 1 and set(ln["status"]) <= {"confirmed", "unconfirmed", "untestable"}
    assert all((w is None) == (st != "confirmed") for w, st in zip(ln["w"], ln["status"]))


def test_the_left_out_sources_are_named(rep):
    assert any("hindsight" in x for x in rep["left_out"])
    assert any("China and trade" in w for w in rep["warnings"])  # no Massive: China, chips and gold are left out


def test_prices_fall_back_to_daily(tmp_path):
    levels, hourly = build_world()
    c = clients(levels, hourly)
    m = Market("polymarket", "x", "e", "t", "t", None, "", None, None, None, None, 0.0, 0.0, 0.0, False, "", token_yes="tok-u")
    start, end = at(date(2025, 3, 1)), at(date(2025, 4, 3))
    assert replay.prices(c, m, start, end, archived=False, hourly=True).granularity == "hourly"
    assert replay.prices(c, m, start, end, archived=False, hourly=False).granularity == "daily"
    assert replay.prices(c, replace(m, token_yes="tok-c"), start, end, archived=False, hourly=True).granularity == "hourly"
    assert replay.prices(c, replace(m, token_yes="nope"), start, end, archived=False, hourly=True).granularity == "none"


def test_metrics_by_hand():
    calls = [{"event": "a", "pred": [-1.0] + [None] * 9, "real": [-2.0] + [0.5] * 9, "has": [True] + [False] * 9},
             {"event": "b", "pred": [0.5, 0.2] + [None] * 8, "real": [-1.0, 0.1] + [None] * 8, "has": [True, True] + [False] * 8}]
    real = np.zeros((3, 10))
    real[:, 0] = [2.0, -1.0, 0.5]
    expl = np.zeros((3, 10))
    expl[:, 0] = [1.0, -1.0, 0.0]
    has = np.zeros(10, bool)
    has[0] = True
    m = replay.metrics(calls, expl, real, has, [])
    assert (m["calls"]["factor_signs"], m["calls"]["factor_signs_right"]) == (3, 2)
    assert [s["ratio"] for s in m["calls"]["stocks"]] == [2.0, -2.0]
    assert m["explained"]["explained_share"] == pytest.approx(1 - (1 + 0 + 0.25) / (4 + 1 + 0.25))
    assert (m["explained"]["big_days"], m["explained"]["big_days_right"]) == (2, 2)


def test_the_replay_command(episode, tmp_path, monkeypatch, capsys):
    c = clients(*build_world())
    s = Settings(cache_dir=tmp_path / "c", out_dir=tmp_path / "out")
    monkeypatch.setattr("redkeep.pipeline.make_clients", lambda s, **kw: c)
    monkeypatch.setattr(Settings, "from_env", classmethod(lambda cls: s))
    monkeypatch.setattr(replay, "REPLAYS", tmp_path / "replays")
    assert cli.main(["replay", "--episode", str(episode_path(tmp_path)), "--write"]) == 0
    out = capsys.readouterr().out
    assert "Calls, from the evening before" in out and "No market priced it" in out
    saved = json.loads((tmp_path / "replays" / "synthetic.json").read_text())
    assert saved["kind"] == "redkeep.replay" and saved["episode"] == "synthetic"


def episode_path(tmp_path):
    p = tmp_path / "synthetic.toml"
    p.write_text(EPISODE)
    return p
