from __future__ import annotations

from prediction_market.normalize import normalize_markets, normalize_orderbooks, normalize_trades
from prediction_market.utils import parse_json_array


def test_polymarket_stringified_fields_are_parsed() -> None:
    assert parse_json_array('["Yes", "No"]') == ["Yes", "No"]
    assert parse_json_array('["111", "222"]') == ["111", "222"]

    frame = normalize_markets(
        [
            {
                "conditionId": "0xabc",
                "slug": "will-it-happen",
                "question": "Will it happen?",
                "active": True,
                "closed": False,
                "endDate": "2026-07-01T00:00:00Z",
                "outcomes": '["Yes", "No"]',
                "volume": "123.45",
                "liquidity": "67.89",
            }
        ],
        [],
    )

    row = frame.iloc[0]
    assert row["platform"] == "polymarket"
    assert row["outcomes"] == ["Yes", "No"]
    assert row["volume"] == 123.45
    assert row["liquidity"] == 67.89


def test_kalshi_orderbook_converts_no_bids_to_yes_asks() -> None:
    frame = normalize_orderbooks(
        [],
        [
            {
                "market_id": "KXTEST",
                "retrieved_at": "2026-06-06T19:00:00Z",
                "payload": {
                    "orderbook_fp": {
                        "yes_dollars": [["0.4200", "10.00"], ["0.4500", "5.00"]],
                        "no_dollars": [["0.3500", "4.00"]],
                    }
                },
            }
        ],
    )

    row = frame.iloc[0]
    assert row["best_yes_bid"] == 0.45
    assert row["best_yes_ask"] == 0.65
    assert row["best_no_bid"] == 0.35
    assert row["best_no_ask"] == 0.55
    assert round(row["spread"], 6) == 0.2
    assert row["bid_depth"] == 15.0
    assert row["ask_depth"] == 4.0


def test_empty_orderbook_produces_valid_empty_snapshot() -> None:
    frame = normalize_orderbooks(
        [],
        [
            {
                "market_id": "KXEMPTY",
                "retrieved_at": "2026-06-06T19:00:00Z",
                "payload": {"orderbook_fp": {"yes_dollars": [], "no_dollars": []}},
            }
        ],
    )

    row = frame.iloc[0]
    assert row["market_id"] == "KXEMPTY"
    assert row["best_yes_bid"] is None
    assert row["best_yes_ask"] is None
    assert row["bid_depth"] == 0.0
    assert row["ask_depth"] == 0.0


def test_markets_with_no_recent_trades_return_empty_table_with_schema() -> None:
    frame = normalize_trades([], [{"market_id": "KXEMPTY", "payload": []}])

    assert frame.empty
    assert list(frame.columns) == [
        "platform",
        "market_id",
        "trade_id",
        "timestamp",
        "outcome",
        "side",
        "price",
        "yes_price",
        "size",
        "raw_payload",
    ]


def test_polymarket_no_trade_price_is_converted_to_yes_probability() -> None:
    frame = normalize_trades(
        [
            {
                "market_id": "0xabc",
                "payload": [
                    {
                        "conditionId": "0xabc",
                        "transactionHash": "0xtrade",
                        "timestamp": 1780773772,
                        "outcome": "No",
                        "side": "BUY",
                        "price": 0.47,
                        "size": 6.22,
                    }
                ],
            }
        ],
        [],
    )

    row = frame.iloc[0]
    assert row["price"] == 0.47
    assert row["yes_price"] == 0.53
