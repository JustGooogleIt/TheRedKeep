"""Eval E10's data helpers (the statistics are in test_granger.py). Synthetic data only."""

import json
from datetime import date

import numpy as np
import pytest

from redkeep import leadlag
from redkeep.factors import index


def test_stand_ins_read_every_factor_off_4pm_closes():
    ds = [date(2026, 1, 5), date(2026, 1, 6)]
    lv = {t: {ds[0]: 100.0, ds[1]: 101.0} for t in ("SPY", "IEF", "TIP", "UUP", "USO", "HYG", "FXI", "SMH", "GLD")}
    lv["IEF"] = {ds[0]: 100.0, ds[1]: 99.0}  # bonds down: yields up
    lv["VIXCLS"] = {ds[0]: 15.0, ds[1]: 17.5}
    X = leadlag.stand_in_moves(lv, ds)
    assert np.isnan(X[0]).all()
    assert X[1, index("eq")] == pytest.approx(1.0) and X[1, index("rates")] == pytest.approx(1.0)
    assert X[1, index("infl")] == pytest.approx(2.0)  # inflation-protected bonds beat plain ones
    assert X[1, index("credit")] == pytest.approx(-2.0)  # high yield beat Treasuries: spreads narrower
    assert X[1, index("vix")] == pytest.approx(2.5) and X[1, index("chips")] == pytest.approx(0.0)


def test_families_group_a_kalshi_series_and_a_polymarket_event():
    assert leadlag.family("kalshi:KXFED-26DEC-T3.25", "kalshi:KXFED-26DEC") == "kalshi:KXFED"
    assert leadlag.family("kalshi:KXFED-27JAN-T3.25", "kalshi:KXFED-27JAN") == "kalshi:KXFED"
    assert leadlag.family("polymarket:123", "polymarket:777") == "polymarket:777"


def test_material_markets_takes_one_per_group_the_one_labels_move_most(tmp_path):
    def row(key, group, moves):
        return {"key": key, "venue": key.split(":")[0], "group": group, "title": key, "text": key, "closes": None,
                "macro": True, "moves": moves, "reason": "", "model": "claude", "rubric": "r1", "labeled_at": "2026-11-08"}

    rows = [row("kalshi:A-1", "kalshi:A", [1, 0, 0, 0, 0, 0, 0, 0, 0, 0]),
            row("kalshi:A-2", "kalshi:A", [2, 1, 0, 0, 0, 0, 0, 0, 0, 0]),
            row("kalshi:B-1", "kalshi:B", [0] * 10),  # not material
            row("polymarket:9", "polymarket:9", [0, 0, 0, -1, 0, 0, 0, 0, 0, 0])]
    p = tmp_path / "labels.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows))
    assert [r.key for r in leadlag.material_markets(p)] == ["kalshi:A-2", "polymarket:9"]
