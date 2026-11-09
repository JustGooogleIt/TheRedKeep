"""Massive on its free plan: 5 calls a minute, and no ETF holdings."""

from datetime import date

import httpx
import pytest

from redkeep.config import MASSIVE_HOST, Settings
from redkeep.data.massive import Massive
from redkeep.http import Http
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
