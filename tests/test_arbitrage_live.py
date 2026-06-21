from __future__ import annotations

import os

import httpx
import pytest

from prediction_market.arbitrage import fetch_polymarket_books_for_markets, fetch_polymarket_candidate_markets


@pytest.mark.skipif(os.getenv("RUN_LIVE_ARBITRAGE_TESTS") != "1", reason="live arbitrage smoke test disabled")
def test_live_polymarket_candidate_books_smoke() -> None:
    with httpx.Client(timeout=30.0) as client:
        markets = fetch_polymarket_candidate_markets(client, limit=5, include_excluded=False)
        books = fetch_polymarket_books_for_markets(client, markets[:2])

    assert not books.empty
    assert {"market_id", "outcome", "best_bid", "best_ask"}.issubset(books.columns)
