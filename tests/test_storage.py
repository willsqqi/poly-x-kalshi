from __future__ import annotations

import pandas as pd

from prediction_market.normalize import MARKET_COLUMNS, ORDERBOOK_COLUMNS, TRADE_COLUMNS
from prediction_market.storage import write_processed_tables


def test_write_processed_tables_creates_parquet_and_csv(tmp_path) -> None:
    tables = {
        "markets": pd.DataFrame(
            [
                {
                    "platform": "kalshi",
                    "market_id": "KXTEST",
                    "ticker_or_slug": "KXTEST",
                    "title": "Test market",
                    "category": None,
                    "status": "active",
                    "close_time": "2026-06-06T20:00:00Z",
                    "outcomes": ["Yes", "No"],
                    "volume": 1.0,
                    "liquidity": 2.0,
                    "raw_payload": "{}",
                }
            ],
            columns=MARKET_COLUMNS,
        ),
        "orderbook_snapshots": pd.DataFrame(columns=ORDERBOOK_COLUMNS),
        "trades": pd.DataFrame(columns=TRADE_COLUMNS),
    }

    paths = write_processed_tables(tables, tmp_path)

    assert paths["markets"]["parquet"].exists()
    assert paths["markets"]["csv"].exists()
    assert paths["orderbook_snapshots"]["parquet"].exists()
    assert paths["trades"]["csv"].exists()
    assert pd.read_parquet(paths["markets"]["parquet"]).iloc[0]["market_id"] == "KXTEST"
