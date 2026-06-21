from __future__ import annotations

import os

import httpx
import pytest

from prediction_market.collectors import fetch_kalshi_markets, fetch_polymarket_markets


@pytest.mark.skipif(os.getenv("RUN_LIVE_MARKET_TESTS") != "1", reason="live API smoke tests are opt-in")
def test_live_market_discovery_smoke() -> None:
    with httpx.Client(timeout=30.0) as client:
        polymarket_markets = fetch_polymarket_markets(client, limit=1)
        kalshi_markets = fetch_kalshi_markets(client, limit=1)

    assert len(polymarket_markets) == 1
    assert len(kalshi_markets) == 1
