from __future__ import annotations

import pandas as pd

from prediction_market.polymarket_onchain import (
    ORDER_FILLED_TOPIC_V1,
    ORDER_FILLED_TOPIC_V2,
    V2_CTF_EXCHANGE,
    build_token_mapping,
    clean_quant_df,
    clean_users_df,
    decode_orderfilled_log,
    extract_trades,
    parse_gamma_market,
    save_onchain_sample,
)


def test_decode_v2_orderfilled_log() -> None:
    log = _v2_log(side=0, token_id=123, maker_amount=400_000, taker_amount=1_000_000, fee=1_000)

    event = decode_orderfilled_log(log)

    assert event is not None
    assert event["contract"] == "CTF_EXCHANGE_V2"
    assert event["contract_generation"] == "v2"
    assert event["order_hash"] == "0x" + "a" * 64
    assert event["maker"] == "0x1111111111111111111111111111111111111111"
    assert event["taker"] == "0x2222222222222222222222222222222222222222"
    assert event["side"] == 0
    assert event["side_label"] == "BUY"
    assert event["maker_asset_id"] == "0"
    assert event["taker_asset_id"] == "123"
    assert event["token_id"] == "123"
    assert event["fee"] == 1_000
    assert event["timestamp"] == 1_780_000_000


def test_decode_v1_orderfilled_log() -> None:
    log = {
        "address": "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E",
        "topics": [
            ORDER_FILLED_TOPIC_V1,
            "0x" + "b" * 64,
            _address_topic("0x3333333333333333333333333333333333333333"),
            _address_topic("0x4444444444444444444444444444444444444444"),
        ],
        "data": _encode_words([456, 0, 2_000_000, 900_000, 11, 22, 33]),
        "blockNumber": hex(88_000_001),
        "logIndex": "0x4",
        "transactionHash": "0xhash",
        "blockTimestamp": hex(1_780_000_010),
    }

    event = decode_orderfilled_log(log)

    assert event is not None
    assert event["contract_generation"] == "v1"
    assert event["maker_asset_id"] == "456"
    assert event["taker_asset_id"] == "0"
    assert event["token_id"] == "456"
    assert event["maker_fee"] == 11
    assert event["taker_fee"] == 22
    assert event["protocol_fee"] == 33


def test_extract_v2_buy_trade_joins_market_mapping() -> None:
    market = parse_gamma_market(
        {
            "id": "101",
            "question": "Will it happen?",
            "conditionId": "0xcondition",
            "outcomes": '["Yes", "No"]',
            "clobTokenIds": '["123", "456"]',
            "events": [{"id": "9", "slug": "event", "title": "Event title"}],
        }
    )
    mapping = build_token_mapping([market])
    event = decode_orderfilled_log(_v2_log(side=0, token_id=123, maker_amount=400_000, taker_amount=1_000_000))
    assert event is not None

    trades = extract_trades([event], mapping)

    row = trades.iloc[0]
    assert row["market_id"] == "101"
    assert row["condition_id"] == "0xcondition"
    assert row["nonusdc_side"] == "token1"
    assert row["answer"] == "Yes"
    assert row["maker_direction"] == "BUY"
    assert row["taker_direction"] == "SELL"
    assert row["maker_asset"] == "USDC"
    assert row["taker_asset"] == "token1"
    assert row["price"] == 0.4
    assert row["usd_amount"] == 0.4
    assert row["token_amount"] == 1.0


def test_quant_flips_token2_into_token1_perspective() -> None:
    trades = pd.DataFrame(
        [
            {
                "timestamp": 1,
                "datetime": "2026-06-06T00:00:00Z",
                "block_number": 10,
                "transaction_hash": "0xhash",
                "log_index": 1,
                "contract": "CTF_EXCHANGE_V2",
                "contract_generation": "v2",
                "event_id": "event",
                "event_slug": "event",
                "event_title": "Event",
                "market_id": "101",
                "condition_id": "0xcondition",
                "question": "Will it happen?",
                "nonusdc_side": "token2",
                "answer": "No",
                "maker": "0xmaker",
                "taker": "0xtaker",
                "maker_asset": "token2",
                "taker_asset": "USDC",
                "maker_direction": "SELL",
                "taker_direction": "BUY",
                "price": 0.3,
                "usd_amount": 0.3,
                "token_amount": 1.0,
                "fee_amount": 0.0,
                "asset_id": "456",
                "order_hash": "0xorder",
                "builder": "",
                "metadata": "",
            }
        ]
    )

    quant = clean_quant_df(trades)

    assert quant.iloc[0]["nonusdc_side"] == "token1"
    assert quant.iloc[0]["price"] == 0.7


def test_users_split_maker_and_taker_and_negative_sells() -> None:
    trades = pd.DataFrame(
        [
            {
                "timestamp": 1,
                "datetime": "2026-06-06T00:00:00Z",
                "block_number": 10,
                "transaction_hash": "0xhash",
                "log_index": 1,
                "contract": "CTF_EXCHANGE_V2",
                "contract_generation": "v2",
                "event_id": "event",
                "event_slug": "event",
                "event_title": "Event",
                "market_id": "101",
                "condition_id": "0xcondition",
                "question": "Will it happen?",
                "nonusdc_side": "token1",
                "answer": "Yes",
                "maker": "0xmaker",
                "taker": "0xtaker",
                "maker_asset": "token1",
                "taker_asset": "USDC",
                "maker_direction": "SELL",
                "taker_direction": "BUY",
                "price": 0.4,
                "usd_amount": 0.4,
                "token_amount": 1.0,
                "fee_amount": 0.0,
                "asset_id": "123",
                "order_hash": "0xorder",
                "builder": "",
                "metadata": "",
            }
        ]
    )

    users = clean_users_df(trades)

    assert len(users) == 2
    assert users.iloc[0]["user"] == "0xmaker"
    assert users.iloc[0]["role"] == "maker"
    assert users.iloc[0]["token_amount"] == -1.0
    assert users.iloc[1]["user"] == "0xtaker"
    assert users.iloc[1]["token_amount"] == 1.0


def test_save_onchain_sample_writes_expected_files(tmp_path) -> None:
    sample = {
        "summary": {"decoded_events": 0},
        "raw_logs": [],
        "markets": [],
        "events": pd.DataFrame(),
        "token_mapping": pd.DataFrame(),
        "trades": pd.DataFrame(),
        "quant": pd.DataFrame(),
        "users": pd.DataFrame(),
    }

    paths = save_onchain_sample(sample, tmp_path)

    assert paths["raw"]["summary"].exists()
    assert paths["raw"]["orderfilled_logs"].exists()
    assert paths["processed"]["events"].exists()
    assert paths["latest_result"]["trades"].exists()


def _v2_log(
    side: int,
    token_id: int,
    maker_amount: int,
    taker_amount: int,
    fee: int = 0,
) -> dict:
    return {
        "address": V2_CTF_EXCHANGE,
        "topics": [
            ORDER_FILLED_TOPIC_V2,
            "0x" + "a" * 64,
            _address_topic("0x1111111111111111111111111111111111111111"),
            _address_topic("0x2222222222222222222222222222222222222222"),
        ],
        "data": _encode_words([side, token_id, maker_amount, taker_amount, fee, 0, 0]),
        "blockNumber": hex(88_000_000),
        "logIndex": "0x3",
        "transactionHash": "0xhash",
        "blockTimestamp": hex(1_780_000_000),
    }


def _encode_words(values: list[int]) -> str:
    return "0x" + "".join(f"{value:064x}" for value in values)


def _address_topic(address: str) -> str:
    return "0x" + address.lower().replace("0x", "").rjust(64, "0")
