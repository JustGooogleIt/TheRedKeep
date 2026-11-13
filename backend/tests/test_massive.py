"""Massive on its free plan: 5 calls a minute, and no ETF holdings."""

from datetime import date

import httpx
import pytest

from redkeep.config import MASSIVE_HOST, Settings
from redkeep.data.massive import Massive
from redkeep.http import Http, HttpError
from redkeep.pipeline import make_clients


def test_massive_calls_are_spaced_for_the_free_plan_by_default(monkeypatch):
    monkeypatch.delenv("MASSIVE_CALLS_PER_MINUTE", raising=False)
    s = Settings.from_env()
    assert s.massive_calls_per_minute == 5
    assert s.spacing()[MASSIVE_HOST] == pytest.approx(12.6)  # 5 a minute, with 5% to spare
    assert s.spacing()["api.hyperliquid.xyz"] == s.request_spacing["api.hyperliquid.xyz"]  # other hosts unchanged


@pytest.mark.parametrize("raw, gap", [("0", 0.05), ("100", 0.63), ("", 12.6)])
def test_a_paid_plan_sets_its_own_limit(monkeypatch, raw, gap):
    monkeypatch.setenv("MASSIVE_CALLS_PER_MINUTE", raw)
    assert Settings.from_env().spacing()[MASSIVE_HOST] == pytest.approx(gap)


@pytest.mark.parametrize("raw", ["five", "-1", "nan"])
def test_a_limit_that_is_not_a_number_is_refused(monkeypatch, raw):
    monkeypatch.setenv("MASSIVE_CALLS_PER_MINUTE", raw)
    with pytest.raises(ValueError, match="MASSIVE_CALLS_PER_MINUTE"):
        Settings.from_env()


def test_the_clients_use_that_spacing(tmp_path):
    s = Settings(massive_api_key="key", cache_dir=tmp_path / ".cache")
    c = make_clients(s, claude=False, record=False)
    assert c.massive.http.min_interval[MASSIVE_HOST] == pytest.approx(12.6)


def _http(statuses: list[int], gap: dict[str, float], slept: list[float]) -> Http:
    def handler(request: httpx.Request) -> httpx.Response:
        status = statuses.pop(0)
        return httpx.Response(status, json={"results": []} if status == 200 else {"error": "too many requests"})

    return Http(transport=httpx.MockTransport(handler), min_interval=gap, sleep=slept.append)


def test_a_call_over_the_limit_waits_a_whole_spacing_before_trying_again():
    slept: list[float] = []
    http = _http([429, 200], {MASSIVE_HOST: 12.6}, slept)
    assert http.get_json(f"https://{MASSIVE_HOST}/v2/aggs/ticker/SPY/range/1/day/2026-01-01/2026-01-31") == {"results": []}
    assert slept[0] == pytest.approx(12.6)  # not the 1 second a busy server gets


def test_an_unpaced_host_still_backs_off_from_one_second():
    slept: list[float] = []
    http = _http([429, 200], {}, slept)
    http.get_json("https://example.com/x")
    assert slept == [1.0]


def test_a_call_limit_gets_more_patience_than_a_failing_server():
    slept: list[float] = []
    http = _http([429] * 6 + [200], {}, slept)
    assert http.get_json("https://example.com/x") == {"results": []}
    assert slept == [1.0, 2.0, 4.0, 8.0, 16.0, 32.0]
    with pytest.raises(HttpError):
        _http([503] * 5 + [200], {}, []).get_json("https://example.com/y")


def test_a_key_without_holdings_is_asked_about_them_once():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(403, json={"status": "NOT_AUTHORIZED"})

    m = Massive(Http(transport=httpx.MockTransport(handler), sleep=lambda s: None), "key", ttl=None)
    assert m.constituents("QQQ", date(2026, 11, 6)) is None
    assert m.constituents("SPY", date(2026, 11, 6)) is None
    assert calls == ["/etf-global/v1/constituents"]  # the second fund costs no call


# ETF Global's constituents: (effective date, processed date) -> holdings of SPY. Massive sorts this dataset only by
# these fields, and refuses any other with a 400, as it refused effective_date on the first live run.
SORTS = {"composite_ticker", "constituent_ticker", "processed_date"}
FILES = {
    ("2026-10-30", "2026-11-02"): [("AAPL", 0.070), ("MSFT", 0.050)],
    ("2026-10-30", "2026-11-05"): [("AAPL", 0.071), ("MSFT", 0.049), ("NVDA", 0.065)],  # the same date processed again
    ("2026-10-15", "2026-11-06"): [("AAPL", 0.068), ("MSFT", 0.052)],  # an older date, processed last
    ("2026-11-05", "2026-11-09"): [("AAPL", 0.072), ("MSFT", 0.048)],  # processed after the as-of day
}


def _etf_global(calls: list[dict]) -> Http:
    rows = [{"composite_ticker": "SPY", "constituent_ticker": t, "constituent_name": t, "weight": w,
             "effective_date": eff, "processed_date": done} for (eff, done), held in FILES.items() for t, w in held]

    def keep(r: dict, q: httpx.QueryParams) -> bool:
        for k, v in q.multi_items():
            field, _, op = k.partition(".")
            if field in ("sort", "limit"):
                continue
            x = r[field]
            if not {"": x == v, "lte": x <= v, "gt": x > v}[op]:
                return False
        return True

    def handler(request: httpx.Request) -> httpx.Response:
        q = request.url.params
        calls.append(dict(q))
        field, _, way = q["sort"].partition(".")
        if field not in SORTS:
            return httpx.Response(400, json={"status": "ERROR", "error": f"Invalid query parameter: 'sort'. "
                                             f"Expected one of [composite_ticker, processed_date, constituent_ticker], but found: '{field}'"})
        got = sorted((r for r in rows if keep(r, q)), key=lambda r: r[field], reverse=way == "desc")
        return httpx.Response(200, json={"status": "OK", "results": got[:int(q["limit"])]})

    return Http(transport=httpx.MockTransport(handler), sleep=lambda s: None)


def test_holdings_come_from_the_latest_date_known_on_the_day_and_its_last_processing():
    calls: list[dict] = []
    m = Massive(_etf_global(calls), "key", ttl=None)
    eff, rows = m.constituents("SPY", date(2026, 11, 6))
    assert eff == date(2026, 10, 30)  # not Oct 15, processed last, nor Nov 5, which nobody had on Nov 6
    assert [(r["constituent_ticker"], r["weight"]) for r in rows] == [("AAPL", 0.071), ("NVDA", 0.065), ("MSFT", 0.049)]
    assert all(c["sort"].split(".")[0] in SORTS for c in calls)
    assert len(calls) == 4  # three steps to the latest date, then its holdings


def test_a_request_massive_refuses_is_reported_and_not_sent_again():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(400, json={"status": "ERROR", "error": "Invalid query parameter: 'sort'."})

    m = Massive(Http(transport=httpx.MockTransport(handler), sleep=lambda s: None), "key", ttl=None)
    with pytest.raises(HttpError, match="400"):
        m.constituents("QQQ", date(2026, 11, 6))
    assert m.constituents("SPY", date(2026, 11, 6)) is None
    assert calls == ["/etf-global/v1/constituents"]  # every fund would be refused the same way


def test_a_next_page_url_keeps_its_own_query():
    got: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        got.append(request.url.params.get("cursor"))
        return httpx.Response(200, json={})

    Http(transport=httpx.MockTransport(handler), sleep=lambda s: None).get_json("https://api.massive.com/x?cursor=abc")
    assert got == ["abc"]  # an empty params dict once stripped it, so the first page came back forever


def test_holdings_over_several_pages_are_read_page_by_page():
    held = [{"composite_ticker": "VWO", "constituent_ticker": f"T{i:02d}", "constituent_name": f"T{i:02d}",
             "weight": 0.001 * (i + 1), "effective_date": "2026-10-30", "processed_date": "2026-11-02"} for i in range(12)]
    cursors: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        q = request.url.params
        if "cursor" in q:
            cursors.append(q["cursor"])
            i = int(q["cursor"])
        elif q.get("composite_ticker") != "VWO":  # what a lost cursor asks for: every fund's holdings
            return httpx.Response(400, json={"status": "ERROR", "error": "no fund named"})
        elif q.get("limit") == "1":  # the steps to the latest effective date
            return httpx.Response(200, json={"status": "OK", "results": [] if "effective_date.gt" in q else held[:1]})
        else:
            i = 0
        body = {"status": "OK", "results": held[i:i + 5]}
        if i + 5 < len(held):  # the server pages at five here, whatever the limit asked for
            body["next_url"] = f"https://api.massive.com/etf-global/v1/constituents?cursor={i + 5}"
        return httpx.Response(200, json=body)

    m = Massive(Http(transport=httpx.MockTransport(handler), sleep=lambda s: None), "key", ttl=None)
    eff, rows = m.constituents("VWO", date(2026, 11, 6))
    assert eff == date(2026, 10, 30) and len(rows) == 12 and cursors == ["5", "10"]
    assert [r["constituent_ticker"] for r in rows[:2]] == ["T11", "T10"]  # largest first


def test_pages_that_come_round_again_stop_with_an_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": [{"t": 1767225600000, "c": 1.0}],
                                         "next_url": "https://api.massive.com/v2/aggs/next?cursor=same"})

    m = Massive(Http(transport=httpx.MockTransport(handler), sleep=lambda s: None), "key", ttl=None)
    with pytest.raises(HttpError, match="came round again"):
        m.closes("SPY", date(2026, 1, 1), date(2026, 1, 31))


def test_at_hour_reads_the_last_regular_session_bar_before_the_fixing_time():
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo

    ny = ZoneInfo("America/New_York")

    def bar(y, m, d, h, c):
        return {"t": int(datetime(y, m, d, h, tzinfo=ny).astimezone(timezone.utc).timestamp() * 1000), "c": c}

    rows = [bar(2025, 4, 9, 8, 27.0), bar(2025, 4, 9, 10, 28.1), bar(2025, 4, 9, 11, 28.2), bar(2025, 4, 9, 13, 28.6),
            bar(2025, 1, 6, 9, 30.0), bar(2025, 1, 6, 15, 30.5), bar(2025, 1, 7, 7, 31.0)]

    def handler(request):
        assert "/range/1/hour/" in request.url.path
        return httpx.Response(200, json={"results": rows})

    m = Massive(Http(transport=httpx.MockTransport(handler)), "key")
    got = m.at_hour("UUP", date(2025, 1, 1), date(2025, 4, 30))
    assert got == {date(2025, 4, 9): 28.2, date(2025, 1, 6): 30.0}  # summer and winter time; pre-market bars don't count
