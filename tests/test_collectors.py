from __future__ import annotations

import json

import httpx

from prediction_market.collectors import fetch_kalshi_markets, fetch_polymarket_markets


def test_fetch_polymarket_markets_filters_to_orderbook_enabled_markets() -> None:
    payload = [
        {
            "active": True,
            "closed": False,
            "conditionId": "0xabc",
            "slug": "market-one",
            "clobTokenIds": '["1", "2"]',
        },
        {
            "active": True,
            "closed": False,
            "conditionId": "0xdef",
            "slug": "market-two",
            "clobTokenIds": "[]",
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "gamma-api.polymarket.com"
        assert request.url.path == "/markets"
        return httpx.Response(200, json=payload)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        markets = fetch_polymarket_markets(client, limit=10)

    assert [market["slug"] for market in markets] == ["market-one"]


def test_fetch_kalshi_markets_uses_open_status_filter() -> None:
    observed_queries: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed_queries.append(request.url.query.decode())
        return httpx.Response(200, json={"markets": [{"ticker": "KXTEST", "status": "active"}]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        markets = fetch_kalshi_markets(client, limit=1)

    assert markets == [{"ticker": "KXTEST", "status": "active"}]
    assert "status=open" in observed_queries[0]


def test_mock_transport_payload_is_json_serializable() -> None:
    payload = {"markets": [{"ticker": "KXTEST"}]}
    assert json.loads(json.dumps(payload)) == payload
