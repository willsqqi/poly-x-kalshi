from __future__ import annotations

import os

import httpx
import pytest

from prediction_market.fifa_arbitrage import fetch_kalshi_fifa_markets, fetch_polymarket_fifa_markets, normalize_fifa_candidates


@pytest.mark.skipif(os.getenv("RUN_LIVE_FIFA_ARBITRAGE_TESTS") != "1", reason="live FIFA arbitrage smoke test disabled")
def test_live_fifa_candidate_discovery_smoke() -> None:
    with httpx.Client(timeout=30.0, headers={"User-Agent": "poly-x-kalshi-fifa-live-test"}) as client:
        polymarket = fetch_polymarket_fifa_markets(client, max_markets=100, page_size=100)
        kalshi = fetch_kalshi_fifa_markets(client, max_markets=100, page_size=100)

    frame = normalize_fifa_candidates(polymarket, kalshi, run_id="live-smoke")

    assert {"venue", "market_id", "title", "keyword_hits"}.issubset(frame.columns)
    assert len(polymarket) + len(kalshi) == len(frame)
