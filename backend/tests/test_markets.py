from datetime import date

import pytest

from redkeep.data.fred import parse_csv
from redkeep.markets import from_kalshi_event, from_polymarket_event, is_sports_polymarket, parse_time


def test_polymarket_markets_parse_json_in_string_fields(gamma_events):
    fed = gamma_events[0]
    ms = from_polymarket_event(fed)
    assert [m.outcome for m in ms] == ["25 bps decrease", "50+ bps decrease", "No change"]
    m = ms[0]
    assert m.key == "polymarket:71001" and m.token_yes == "111" and m.exclusive
    assert m.mid == pytest.approx(0.62) and m.spread == pytest.approx(0.02)
    assert m.close_time.date() == date(2026, 12, 9)
    assert m.url.endswith("/fed-decision-in-december")


def test_polymarket_skips_sports_and_head_to_head(gamma_events):
    assert is_sports_polymarket(gamma_events[1])
    assert from_polymarket_event(gamma_events[1]) == []  # "Spurs" vs "Kings" is not a yes/no market
    assert not is_sports_polymarket(gamma_events[2])


def test_kalshi_dollar_strings_and_fixed_point_sizes(kalshi_events):
    ms = from_kalshi_event(kalshi_events[0])
    assert len(ms) == 3 and all(m.exclusive for m in ms)
    cut = ms[0]
    assert cut.key == "kalshi:KXFEDDECISION-26DEC-C25" and cut.series == "KXFEDDECISION"
    assert cut.bid == pytest.approx(0.60) and cut.ask == pytest.approx(0.62) and cut.mid == pytest.approx(0.61)
    assert cut.volume == pytest.approx(3_000_000 * 0.61)
    assert "December 09, 2026" in cut.rules
    assert cut.category == "Economics"


def test_kalshi_reads_old_cent_fields_too():
    ev = {"event_ticker": "E", "series_ticker": "S", "title": "t", "markets": [
        {"ticker": "E-1", "status": "active", "title": "t", "yes_bid": 40, "yes_ask": 44, "last_price": 42, "volume": 1000}]}
    m = from_kalshi_event(ev)[0]
    assert (m.bid, m.ask, m.mid) == pytest.approx((0.40, 0.44, 0.42))


def test_parse_time_variants():
    assert parse_time("2026-11-07 06:02:20.222282+00").year == 2026
    assert parse_time("2026-12-09T18:59:00Z").hour == 18
    assert parse_time("2027-03-31").month == 3
    assert parse_time(None) is None


def test_fred_csv_handles_both_headers_and_missing_values():
    new = "observation_date,DGS10,T10YIE\n2026-11-02,4.10,2.31\n2026-11-03,.,2.30\n2026-11-04,4.12,\n"
    out = parse_csv(new)
    assert out["DGS10"] == {date(2026, 11, 2): 4.10, date(2026, 11, 4): 4.12}
    assert out["T10YIE"] == {date(2026, 11, 2): 2.31, date(2026, 11, 3): 2.30}
    old = "DATE,VIXCLS\n2019-08-14,22.10\n"
    assert parse_csv(old)["VIXCLS"][date(2019, 8, 14)] == 22.10
