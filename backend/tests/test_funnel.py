"""The funnel (milestone M4) and its eval (E0), offline: a synthetic catalogue, and a stand-in for Claude that labels
by fixed rules. Nothing here is market data or a real label."""

import json
import random
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np
import pytest

from redkeep import cli
from redkeep.config import Settings
from redkeep.factors import KEYS, index
from redkeep.funnel import e0, labels, run
from redkeep.funnel.labels import LabelBatch, Labeler, MarketLabel, Moves, Row
from redkeep.markets import Market
from redkeep.pipeline import Clients

NOW = datetime(2026, 11, 8, 12, tzinfo=timezone.utc)

# Templates: a title, the rule's moves when it resolves yes, and how many groups of it to make.
MACRO = [
    ("Will the Fed cut rates by {n} bps at its {m} meeting?", {"rates": -1, "eq": 1, "usd": -1, "gold": 1}),
    ("Will US CPI inflation come in above {n}% in {m}?", {"rates": 1, "infl": 1, "eq": -1}),
    ("Will the US impose a {n}% tariff on Chinese imports by {m}?", {"china": -2, "eq": -1, "usd": 1, "vix": 1}),
    ("US recession declared by {m} {n}?", {"eq": -2, "rates": -2, "credit": 2, "vix": 2}),
    ("Will Iran close the Strait of Hormuz before {m} {n}?", {"oil": 2, "eq": -1, "vix": 1, "gold": 1}),
    ("Government shutdown lasting {n}+ days by {m}?", {"eq": -1, "vix": 1}),
]
OTHER = [
    "Will {name} release a new album before {m} {n}?",
    "Will {name} post more than {n} times on X in {m}?",
    "Will {name} win the {m} {n} mayoral election?",
    "Will the film {name} gross over ${n} million on its {m} opening weekend?",
    "Will Bitcoin trade above ${n},000 on {m} 1?",
    "Will {name} appear on the cover of a magazine in {m} {n}?",
]
NAMES = ["Aria Vale", "Tomas Brecht", "Lena Ostrova", "Kai Morrow", "Mila Santos", "Ravi Anand", "Noor Haddad"]
MONTHS = ["January", "March", "May", "June", "September", "December"]


def market(i, group, title, *, venue="polymarket", volume=50_000, q=0.4, spread=0.02, rules=""):
    return Market(venue=venue, id=f"m{i}", event_id=group, title=title, event_title=title.split("?")[0], outcome=None,
                  rules=rules or f"This market resolves Yes if: {title}", close_time=NOW + timedelta(days=60),
                  bid=q - spread / 2, ask=q + spread / 2, last=q, volume=volume, volume_24h=0, liquidity=0,
                  exclusive=False, url="")


def catalogue(seed=1, groups=220):
    rng = random.Random(seed)
    out, i = [], 0
    for g in range(groups):
        macro = g % 3 == 0  # a third of the groups are macro
        size = rng.choice([1, 1, 2, 3, 6])
        for k in range(size):
            n, m = rng.choice([10, 25, 50, 100, 145]) + k, rng.choice(MONTHS)
            if macro:
                title = MACRO[g // 3 % len(MACRO)][0].format(n=n, m=m)
            else:
                title = OTHER[g % len(OTHER)].format(name=rng.choice(NAMES), n=n, m=m)
            out.append(market(i, f"g{g}", title, venue="kalshi" if g % 5 == 0 else "polymarket"))
            i += 1
    return out


def rule_moves(title: str) -> dict[str, int]:
    for tmpl, moves in MACRO:
        if title.startswith(tmpl.split("{")[0]):
            return moves
    return {}


class FakeClaude:
    """Labels each market by the template it came from; counts calls."""

    def __init__(self, drop=(), refuse=False):
        self.calls, self.drop, self.refuse = 0, set(drop), refuse
        self.beta = SimpleNamespace(messages=SimpleNamespace(parse=self.parse))

    def parse(self, *, model, messages, output_format, **kw):
        assert output_format is LabelBatch and kw["system"][0]["text"] == labels.RUBRIC
        self.calls += 1
        req = json.loads(messages[0]["content"])
        out = []
        for m in req["markets"]:
            if m["id"] in self.drop:
                continue
            mv = rule_moves(m["title"])
            out.append(MarketLabel(id=m["id"], macro=bool(mv), moves=Moves(**{k: mv.get(k, 0) for k in KEYS}),
                                   reason="Rule-based stand-in."))
        usage = SimpleNamespace(model_dump=lambda: {"input_tokens": 4000, "output_tokens": 800})
        return SimpleNamespace(stop_reason="refusal" if self.refuse else "end_turn", parsed_output=LabelBatch(labels=out),
                               usage=usage, model=model)


def rows_for(markets):
    lab = Labeler(FakeClaude(), model="test", workers=1)
    rows, _, _ = lab.label(markets)
    return rows


def test_the_cheap_filters_and_an_even_sample():
    ms = catalogue()
    odd = [market(9001, "x", "Thin?", volume=100), market(9002, "x", "Wide?", spread=0.4),
           market(9003, "x", "Done?", q=0.995, spread=0.0), market(9004, "x", "Old?")]
    odd[-1].close_time = NOW - timedelta(days=1)
    f = labels.cheap_filters(ms + odd, NOW)
    assert len(f.kept) == len(ms) and f.dropped == {"thin": 1, "wide spread": 1, "all but decided": 1, "closed": 1}
    s = labels.sample(ms, 100)
    assert len(s) == 100 and max(sum(m.event_id == g for m in s) for g in {m.event_id for m in s}) <= 2
    assert [m.key for m in labels.sample(list(reversed(ms)), 100)] == [m.key for m in s]  # repeatable


def test_the_labeler_batches_caches_and_counts(tmp_path):
    ms = catalogue()[:95]
    fake = FakeClaude(drop={ms[3].key})
    lab = Labeler(fake, model="test", log_dir=tmp_path, batch=40, workers=2)
    rows, usage, missing = lab.label(ms)
    assert fake.calls == 3 and usage.calls == 3 and missing == 1 and len(rows) == 94
    assert usage.tokens_in == 3 * 4000 and sum(r.tokens_in for r in rows) == pytest.approx(3 * 4000)
    fed = next(r for r in rows if r.title.startswith("Will the Fed cut"))
    assert fed.material and fed.moves[index("rates")] == -1 and fed.text.startswith(fed.title)
    again, usage2, _ = Labeler(fake, model="test", log_dir=tmp_path, batch=40).label(ms)
    assert fake.calls == 3 and usage2.cached == 3 and [r.moves for r in again] == [r.moves for r in rows]
    # A refused batch is counted and skipped, not fatal.
    _, u, missing = Labeler(FakeClaude(refuse=True), model="test", batch=40).label(ms[:10])
    assert u.refused == 1 and missing == 10


def test_the_labels_file_adds_only_new_markets(tmp_path):
    path = tmp_path / "labels.jsonl"
    rows = rows_for(catalogue()[:30])
    assert labels.append_rows(path, rows[:20]) == 20 and labels.append_rows(path, rows) == 10
    back = labels.read_rows(path)
    assert [r.key for r in back] == [r.key for r in rows] and back[0] == rows[0]


def test_the_screen_learns_claudes_labels():
    rows = rows_for(catalogue())
    train, test = e0.split(rows)
    assert not {r.group for r in train} & {r.group for r in test}  # a group sits on one side only
    model = e0.fit(train)
    p, mv = model.predict([r.text for r in test])
    y = np.array([r.material for r in test])
    assert e0.auc(p, y) > 0.95 and e0.average_precision(p, y) > 0.9
    fed = [i for i, r in enumerate(test) if r.title.startswith("Will the Fed cut")]
    assert fed and all(mv[i, index("rates")] < 0 and mv[i, index("eq")] > 0 for i in fed)


def test_thresholds_by_hand():
    p = np.array([0.95, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1])
    y = np.array([1, 1, 1, 0, 1, 0, 0, 1, 0, 0], bool)
    drop, keep = e0.thresholds(p, y)
    # 97% of the five material markets means all five, so the lowest one (0.3) sets the drop line. The top three are
    # all material; the top five are 80% material, under 90%, so the keep line stays at 0.8.
    assert (drop, keep) == (0.3, 0.8)
    op = e0.operating(p, y, drop, keep)
    assert (op["dropped"], op["kept"], op["to_claude"], op["recall"], op["precision_kept"]) == (2, 3, 5, 1.0, 1.0)


def test_e0_on_synthetic_labels():
    rows = rows_for(catalogue(groups=300))
    rep = e0.study(rows)
    op = rep["operating"]
    assert rep["test"]["markets"] + rep["train"]["markets"] == len(rows)
    assert op["recall"] >= 0.9 and op["dropped"] + op["kept"] + op["to_claude"] == op["markets"]
    assert rep["thresholds"]["drop"] <= (rep["thresholds"]["keep"] or 1)
    recalls = [t["recall"] for t in rep["tops"]]
    assert recalls == sorted(recalls)
    c = rep["cost_per_1000"]
    assert c["claude_every_market"]["tokens_in"] == pytest.approx(1000 * 4000 / 40, rel=0.01) and c["screen"]["seconds"] < 5
    assert c["screen"]["tokens_in"] < c["claude_every_market"]["tokens_in"]
    assert rep["moves"]["rates"]["sign_right"] == rep["moves"]["rates"]["moves"]


def test_the_funnel_commands(tmp_path, monkeypatch, capsys):
    ms = catalogue(groups=300)
    fake = FakeClaude()
    s = Settings(cache_dir=tmp_path / "c", out_dir=tmp_path / "out")
    c = Clients(None, None, None, None, SimpleNamespace(client=fake), None, model="test")
    monkeypatch.setattr("redkeep.pipeline.make_clients", lambda s, **kw: c)
    monkeypatch.setattr("redkeep.pipeline.catalogue", lambda lib, c, today, progress: ms)
    monkeypatch.setattr(Settings, "from_env", classmethod(lambda cls: s))
    monkeypatch.setattr(run, "E0_FILE", tmp_path / "e0.json")
    path = tmp_path / "labels.jsonl"
    assert cli.main(["funnel", "label", "--n", "400", "--labels", str(path)]) == 0
    assert "Labeled 400 markets" in capsys.readouterr().out and len(labels.read_rows(path)) == 400
    calls = fake.calls
    assert cli.main(["funnel", "label", "--n", "400", "--labels", str(path)]) == 0  # nothing new to label
    assert fake.calls == calls

    assert cli.main(["funnel", "e0", "--labels", str(path), "--write", "--check", "10"]) == 0
    out = capsys.readouterr().out
    assert "E0: 400 markets" in out and "Material markets found" in out
    assert json.loads((tmp_path / "e0.json").read_text())["operating"]["recall"] >= 0.9
    sheet = next((tmp_path / "out").glob("funnel-check-*.csv")).read_text().splitlines()
    assert len(sheet) == 11 and sheet[0].startswith("market,title,claude_material")

    assert cli.main(["funnel", "screen", "--labels", str(path), "--claude", "15"]) == 0
    out = capsys.readouterr().out
    rep = json.loads((tmp_path / "out" / "funnel-latest.json").read_text())
    sc = rep["screen"]
    assert sc["sent_to_claude"] <= 15 and sc["new_labels"] == sc["sent_to_claude"]
    assert sum(sc[k] for k in ("dropped", "kept", "claude", "unsure_unchecked")) == len(ms)
    band = [r for r in labels.read_rows(path) if r.source == "band"]
    assert len(band) == sc["new_labels"]
    top = rep["groups"][0]
    assert top["material"] and max(map(abs, top["moves"])) == 2 and "Material:" in out
    # Every material group is a macro template; no celebrity group is kept as material.
    assert all(rule_moves(g["examples"][0]) for g in rep["groups"] if g["material"])


def test_rows_round_trip_through_json():
    r = rows_for(catalogue()[:1])[0]
    assert Row(**json.loads(json.dumps(r.__dict__))) == r
