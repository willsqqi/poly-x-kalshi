from __future__ import annotations

import httpx
import pandas as pd

from prediction_market.aws_etl.io import read_parquet_dataset, write_parquet
from prediction_market.aws_etl.jobs import (
    build_market_daily_job,
    fetch_gamma_markets_raw_for_tokens,
    fetch_markets_job,
    normalize_trades_job,
)
from prediction_market.aws_etl.paths import LakePaths
from prediction_market.aws_etl.transforms import build_fact_market_daily
from prediction_market.polymarket_onchain import ORDER_FILLED_TOPIC_V2, V2_CTF_EXCHANGE, decode_orderfilled_log


def test_fetch_markets_job_writes_bronze_silver_and_gold(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "prediction_market.aws_etl.jobs.fetch_gamma_markets_raw",
        lambda max_markets=None, page_size=500: [_raw_market()],
    )

    result = fetch_markets_job(str(tmp_path), run_id="unit-test", max_markets=1)

    assert result["market_count"] == 1
    assert result["dim_market_rows"] == 1
    assert result["dim_outcome_rows"] == 2
    assert (tmp_path / "bronze/polymarket/markets_raw/run_id=unit-test/markets.json").exists()
    assert read_parquet_dataset(str(tmp_path / "gold/polymarket/dim_market")).iloc[0]["market_id"] == "101"
    assert set(read_parquet_dataset(str(tmp_path / "gold/polymarket/dim_outcome"))["token_id"]) == {"123", "456"}


def test_fetch_markets_job_can_target_tokens_from_orderfilled_prefix(tmp_path, monkeypatch) -> None:
    paths = LakePaths(str(tmp_path))
    event = decode_orderfilled_log(_v2_log(side=0, token_id=123, maker_amount=400_000, taker_amount=1_000_000))
    assert event is not None
    write_parquet(
        pd.DataFrame([event]),
        paths.orderfilled_raw_parquet(run_id="fills", start_block=88_000_000, end_block=88_000_000),
    )

    seen_tokens = []

    def fake_fetch(token_ids):
        seen_tokens.extend(token_ids)
        return [_raw_market()]

    monkeypatch.setattr("prediction_market.aws_etl.jobs.fetch_gamma_markets_raw_for_tokens", fake_fetch)

    result = fetch_markets_job(
        str(tmp_path),
        run_id="token-markets",
        orderfilled_prefix=str(tmp_path / "bronze/polymarket/orderfilled_raw"),
    )

    assert seen_tokens == ["123"]
    assert result["source"] == "token_ids"
    assert result["token_count"] == 1
    assert result["market_count"] == 1
    assert set(read_parquet_dataset(str(tmp_path / "gold/polymarket/dim_outcome"))["token_id"]) == {"123", "456"}


def test_read_parquet_dataset_can_project_columns(tmp_path) -> None:
    path = tmp_path / "dataset/part.parquet"
    write_parquet(pd.DataFrame([{"token_id": "123", "raw_log": "{}"}]), str(path))

    projected = read_parquet_dataset(str(tmp_path / "dataset"), columns=["token_id"])

    assert list(projected.columns) == ["token_id"]
    assert projected.iloc[0]["token_id"] == "123"


def test_read_parquet_dataset_skips_schema_empty_files_when_projecting(tmp_path) -> None:
    write_parquet(pd.DataFrame(), str(tmp_path / "dataset/empty.parquet"))
    write_parquet(pd.DataFrame([{"token_id": "123", "raw_log": "{}"}]), str(tmp_path / "dataset/part.parquet"))

    projected = read_parquet_dataset(str(tmp_path / "dataset"), columns=["token_id"])

    assert list(projected.columns) == ["token_id"]
    assert projected["token_id"].tolist() == ["123"]


def test_fetch_gamma_markets_raw_for_tokens_batches_repeated_query_params() -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        token_ids = request.url.params.get_list("clob_token_ids")
        assert token_ids == ["123", "456"]
        assert request.url.params["limit"] == "500"
        assert request.url.params["offset"] == "0"
        market = {**_raw_market(), "id": "202"} if request.url.params.get("closed") == "true" else _raw_market()
        return httpx.Response(
            200,
            json=[
                market,
                {**market},
            ],
        )

    markets = fetch_gamma_markets_raw_for_tokens(
        ["123", "456"],
        token_batch_size=50,
        sleep_seconds=0,
        transport=httpx.MockTransport(handler),
    )

    assert len(requests) == 2
    assert [request.url.params.get("closed") for request in requests] == [None, "true"]
    assert [market["id"] for market in markets] == ["101", "202"]


def test_fetch_gamma_markets_raw_for_tokens_paginates_full_pages() -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        offset = int(request.url.params["offset"])
        if offset == 0:
            return httpx.Response(200, json=[{**_raw_market(), "id": "101"}])
        if offset == 1:
            return httpx.Response(200, json=[{**_raw_market(), "id": "202"}])
        return httpx.Response(200, json=[])

    markets = fetch_gamma_markets_raw_for_tokens(
        ["123"],
        token_batch_size=50,
        page_size=1,
        include_closed=False,
        sleep_seconds=0,
        transport=httpx.MockTransport(handler),
    )

    assert [request.url.params["offset"] for request in requests] == ["0", "1", "2"]
    assert [market["id"] for market in markets] == ["101", "202"]


def test_fetch_gamma_markets_raw_for_tokens_splits_server_error_batches() -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        token_ids = request.url.params.get_list("clob_token_ids")
        if len(token_ids) > 1:
            return httpx.Response(500, json={"error": "batch too large"})
        return httpx.Response(200, json=[{**_raw_market(), "id": token_ids[0]}])

    markets = fetch_gamma_markets_raw_for_tokens(
        ["123", "456"],
        token_batch_size=50,
        include_closed=False,
        sleep_seconds=0,
        transport=httpx.MockTransport(handler),
    )

    assert [request.url.params.get_list("clob_token_ids") for request in requests] == [["123", "456"], ["123"], ["456"]]
    assert [market["id"] for market in markets] == ["123", "456"]


def test_normalize_trades_and_daily_jobs_from_local_lake(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "prediction_market.aws_etl.jobs.fetch_gamma_markets_raw",
        lambda max_markets=None, page_size=500: [_raw_market()],
    )
    fetch_markets_job(str(tmp_path), run_id="markets", max_markets=1)

    paths = LakePaths(str(tmp_path))
    event = decode_orderfilled_log(_v2_log(side=0, token_id=123, maker_amount=400_000, taker_amount=1_000_000))
    assert event is not None
    write_parquet(
        pd.DataFrame([event]),
        paths.orderfilled_raw_parquet(run_id="fills", start_block=88_000_000, end_block=88_000_000),
    )

    normalized = normalize_trades_job(str(tmp_path), run_id="normalized")
    daily = build_market_daily_job(str(tmp_path), run_id="daily")

    fact = read_parquet_dataset(str(tmp_path / "gold/polymarket/fact_trades"))
    daily_fact = read_parquet_dataset(str(tmp_path / "gold/polymarket/fact_market_daily"))

    assert normalized["fact_trades_rows"] == 1
    assert daily["fact_market_daily_rows"] == 1
    assert fact.iloc[0]["trade_id"] == "0xhash:3"
    assert fact.iloc[0]["market_id"] == "101"
    assert fact.iloc[0]["outcome_id"] == "123"
    assert fact.iloc[0]["price"] == 0.4
    assert fact.iloc[0]["source_type"] == "historical_backfill"
    assert daily_fact.iloc[0]["daily_volume"] == 0.4
    assert daily_fact.iloc[0]["daily_trade_count"] == 1


def test_normalize_trades_can_scope_orderfilled_prefix(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "prediction_market.aws_etl.jobs.fetch_gamma_markets_raw",
        lambda max_markets=None, page_size=500: [_raw_market()],
    )
    fetch_markets_job(str(tmp_path), run_id="markets", max_markets=1)

    paths = LakePaths(str(tmp_path))
    event = decode_orderfilled_log(_v2_log(side=0, token_id=123, maker_amount=400_000, taker_amount=1_000_000))
    assert event is not None
    write_parquet(
        pd.DataFrame([event]),
        paths.orderfilled_raw_parquet(run_id="fills", start_block=88_000_000, end_block=88_000_000),
    )
    write_parquet(
        pd.DataFrame([event]),
        paths.orderfilled_raw_parquet(run_id="duplicate-fills", start_block=88_000_000, end_block=88_000_000),
    )

    scoped_prefix = tmp_path / "bronze/polymarket/orderfilled_raw/block_range=88000000-88000000/run_id=fills"
    normalized = normalize_trades_job(str(tmp_path), run_id="normalized", orderfilled_prefix=str(scoped_prefix))

    assert normalized["orderfilled_rows"] == 1
    assert normalized["fact_trades_rows"] == 1


def test_normalize_trades_deduplicates_duplicate_orderfilled_inputs(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "prediction_market.aws_etl.jobs.fetch_gamma_markets_raw",
        lambda max_markets=None, page_size=500: [_raw_market()],
    )
    fetch_markets_job(str(tmp_path), run_id="markets", max_markets=1)

    paths = LakePaths(str(tmp_path))
    event = decode_orderfilled_log(_v2_log(side=0, token_id=123, maker_amount=400_000, taker_amount=1_000_000))
    assert event is not None
    for run_id in ("fills", "duplicate-fills"):
        write_parquet(
            pd.DataFrame([event]),
            paths.orderfilled_raw_parquet(run_id=run_id, start_block=88_000_000, end_block=88_000_000),
        )

    normalized = normalize_trades_job(str(tmp_path), run_id="normalized", orderfilled_file_batch_size=1)

    assert normalized["orderfilled_rows"] == 2
    assert normalized["trades_normalized_rows"] == 1
    assert normalized["fact_trades_rows"] == 1


def test_daily_aggregation_deduplicates_fact_trades_by_trade_id() -> None:
    fact_trades = pd.DataFrame(
        [
            _fact_trade("0xhash:3", usd_amount=0.4),
            _fact_trade("0xhash:3", usd_amount=0.4),
        ]
    )

    daily = build_fact_market_daily(fact_trades)

    assert len(daily) == 1
    assert daily.iloc[0]["daily_trade_count"] == 1
    assert daily.iloc[0]["daily_volume"] == 0.4


def test_build_market_daily_job_streams_fact_parts(tmp_path) -> None:
    first = _fact_trade("0xhash:1", usd_amount=0.4)
    second = {
        **_fact_trade("0xhash:2", usd_amount=0.7),
        "timestamp": 1_780_000_100,
        "maker": "0xmaker2",
        "price": 0.7,
    }
    write_parquet(
        pd.DataFrame([first]),
        str(tmp_path / "gold/polymarket/fact_trades/date=2026-06-06/part-000.parquet"),
    )
    write_parquet(
        pd.DataFrame([second]),
        str(tmp_path / "gold/polymarket/fact_trades/date=2026-06-06/part-001.parquet"),
    )

    result = build_market_daily_job(str(tmp_path), run_id="daily", fact_file_batch_size=1)
    daily = read_parquet_dataset(str(tmp_path / "gold/polymarket/fact_market_daily"))

    assert result["fact_trades_rows"] == 2
    assert result["fact_market_daily_rows"] == 1
    assert daily.iloc[0]["daily_trade_count"] == 2
    assert daily.iloc[0]["daily_volume"] == 1.1
    assert daily.iloc[0]["unique_makers"] == 2
    assert daily.iloc[0]["avg_price"] == 0.55
    assert daily.iloc[0]["close_price"] == 0.7


def _raw_market() -> dict:
    return {
        "id": "101",
        "question": "Will it happen?",
        "conditionId": "0xcondition",
        "slug": "will-it-happen",
        "category": "test",
        "outcomes": '["Yes", "No"]',
        "clobTokenIds": '["123", "456"]',
        "volume": "10.5",
        "liquidity": "2.5",
        "active": True,
        "closed": False,
        "startDate": "2026-06-01T00:00:00Z",
        "endDate": "2026-06-10T00:00:00Z",
        "events": [{"id": "9", "slug": "event", "title": "Event title"}],
    }


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


def _fact_trade(trade_id: str, usd_amount: float) -> dict:
    return {
        "trade_id": trade_id,
        "market_id": "101",
        "outcome_id": "123",
        "timestamp": 1_780_000_000,
        "date": "2026-06-06",
        "hour": 0,
        "maker": "0xmaker",
        "taker": "0xtaker",
        "price": 0.4,
        "token_amount": 1.0,
        "usd_amount": usd_amount,
        "maker_direction": "BUY",
        "taker_direction": "SELL",
        "transaction_hash": "0xhash",
        "block_number": 88_000_000,
        "order_hash": "0xorder",
        "fee_amount": 0.0,
        "source_type": "historical_backfill",
        "ingested_at": "2026-06-06T00:00:00Z",
        "raw_payload_path": "s3://bucket/raw",
    }
