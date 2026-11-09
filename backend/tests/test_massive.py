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
