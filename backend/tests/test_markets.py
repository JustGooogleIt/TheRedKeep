from datetime import date, datetime, timedelta, timezone

import httpx
import pytest

from redkeep.data.fred import Fred, FredFormatError, parse_csv
from redkeep.data.polymarket import Polymarket
from redkeep.http import Http
from redkeep.markets import from_kalshi_event, from_polymarket_event, is_sports_polymarket, parse_time


def test_polymarket_markets_parse_json_in_string_fields(gamma_events):
    fed = gamma_events[0]
    ms = from_polymarket_event(fed)
    assert [m.outcome for m in ms] == ["25 bps increase", "50+ bps increase", "No change"]
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
    hike = ms[0]
    assert hike.key == "kalshi:KXFEDDECISION-26DEC-H25" and hike.series == "KXFEDDECISION"
    assert hike.bid == pytest.approx(0.60) and hike.ask == pytest.approx(0.62) and hike.mid == pytest.approx(0.61)
    assert hike.volume == pytest.approx(3_000_000 * 0.61)
    assert "December 09, 2026" in hike.rules
    assert hike.category == "Economics"


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


# -- data clients against stand-in servers (behaviour checked on the live APIs on 2026-11-08) --------------


def stub(handler):
    return Http(transport=httpx.MockTransport(handler), sleep=lambda s: None)


def test_fred_fetches_one_series_per_request():
    seen = []

    def handler(req):
        sid = req.url.params["id"]
        seen.append(sid)
        assert "," not in sid  # several ids in one request come back as a zip, and ignore the dates
        return httpx.Response(200, text=f"observation_date,{sid}\n2026-11-02,1.5\n2026-11-03,.\n")

    out = Fred(stub(handler)).series(["DGS10", "VIXCLS"], date(2026, 11, 1), date(2026, 11, 6))
    assert seen == ["DGS10", "VIXCLS"]
    assert out == {"DGS10": {date(2026, 11, 2): 1.5}, "VIXCLS": {date(2026, 11, 2): 1.5}}


def test_fred_zip_is_an_error_not_garbage():
    with pytest.raises(FredFormatError):
        parse_csv("PK\x03\x04 zipped")


def test_polymarket_pages_past_the_100_event_cap():
    offsets = []

    def handler(req):
        off = int(req.url.params["offset"])
        offsets.append(off)
        assert int(req.url.params["limit"]) <= 100
        n = 100 if off < 200 else 40  # the server caps pages at 100 events
        return httpx.Response(200, json=[{"id": off + i, "markets": []} for i in range(n)])

    evs = Polymarket(stub(handler)).events(since=date(2026, 11, 8))
    assert offsets == [0, 100, 200] and len(evs) == 240


def test_polymarket_history_stays_inside_the_15_day_limit():
    spans = []

    def handler(req):
        a, b = int(req.url.params["startTs"]), int(req.url.params["endTs"])
        spans.append(b - a)
        return httpx.Response(200, json={"history": [{"t": a + 3600, "p": 0.4}, {"t": b, "p": 0.5}]})

    end = datetime(2026, 11, 8, 12, tzinfo=timezone.utc)
    start = end - timedelta(days=180)
    pts = Polymarket(stub(handler)).history("tok", start, end)
    assert max(spans) <= 15 * 86400 and len(spans) >= 13
    assert all(start <= t <= end for t, _ in pts)
