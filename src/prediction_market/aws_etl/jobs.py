from __future__ import annotations

import json
import os
import time
from collections.abc import Iterable
from typing import Any

import boto3
import httpx
import pandas as pd

from prediction_market.polymarket_onchain import (
    PolygonRpcClient,
    decode_orderfilled_logs,
    fetch_orderfilled_logs_by_range,
)
from prediction_market.utils import utc_now_iso

from .io import (
    filter_by_date,
    list_parquet_objects,
    read_parquet,
    read_parquet_dataset,
    write_json,
    write_parquet,
    write_partitioned_parquet,
)
from .paths import LakePaths, join_uri
from .schemas import FACT_MARKET_DAILY_COLUMNS
from .transforms import build_fact_market_daily, build_fact_trades, build_market_tables

GAMMA_API_URL = "https://gamma-api.polymarket.com"

ORDERFILLED_NORMALIZE_COLUMNS = [
    "transaction_hash",
    "block_number",
    "log_index",
    "timestamp",
    "datetime",
    "contract",
    "contract_generation",
    "order_hash",
    "maker",
    "taker",
    "maker_asset_id",
    "taker_asset_id",
    "side",
    "token_id",
    "maker_amount_filled",
    "taker_amount_filled",
    "maker_fee",
    "taker_fee",
    "protocol_fee",
    "fee",
    "builder",
    "metadata",
]

FACT_DAILY_INPUT_COLUMNS = [
    "trade_id",
    "market_id",
    "date",
    "timestamp",
    "maker",
    "taker",
    "price",
    "usd_amount",
]


def fetch_gamma_markets_raw(
    max_markets: int | None = None,
    page_size: int = 500,
    sleep_seconds: float = 0.2,
    timeout: float = 60.0,
) -> list[dict[str, Any]]:
    markets: list[dict[str, Any]] = []
    offset = 0

    with httpx.Client(timeout=timeout, headers={"User-Agent": "prediction-market-phase1-etl"}) as client:
        while True:
            limit = page_size
            if max_markets is not None:
                remaining = max_markets - len(markets)
                if remaining <= 0:
                    break
                limit = min(limit, remaining)

            response = client.get(
                f"{GAMMA_API_URL}/markets",
                params={"limit": limit, "offset": offset, "order": "createdAt", "ascending": "true"},
            )
            response.raise_for_status()
            payload = response.json()
            batch = payload if isinstance(payload, list) else payload.get("markets", [])
            if not batch:
                break

            markets.extend(batch)
            if len(batch) < limit:
                break
            offset += len(batch)
            if sleep_seconds:
                time.sleep(sleep_seconds)

    return markets


def fetch_gamma_markets_raw_for_tokens(
    token_ids: Iterable[str],
    token_batch_size: int = 50,
    page_size: int = 500,
    include_closed: bool = True,
    sleep_seconds: float = 0.05,
    timeout: float = 60.0,
    transport: httpx.BaseTransport | None = None,
) -> list[dict[str, Any]]:
    markets: list[dict[str, Any]] = []
    seen_market_ids: set[str] = set()
    tokens = sorted({str(token) for token in token_ids if str(token) and str(token) != "0"})
    closed_filters: list[bool | None] = [None]
    if include_closed:
        closed_filters.append(True)

    with httpx.Client(timeout=timeout, headers={"User-Agent": "prediction-market-phase1-etl"}, transport=transport) as client:
        for token_batch in _chunks(tokens, token_batch_size):
            for closed in closed_filters:
                _fetch_gamma_market_pages_for_token_batch(
                    client=client,
                    token_batch=token_batch,
                    page_size=page_size,
                    closed=closed,
                    markets=markets,
                    seen_market_ids=seen_market_ids,
                    sleep_seconds=sleep_seconds,
                )

    return markets


def _fetch_gamma_market_pages_for_token_batch(
    client: httpx.Client,
    token_batch: list[str],
    page_size: int,
    closed: bool | None,
    markets: list[dict[str, Any]],
    seen_market_ids: set[str],
    sleep_seconds: float,
) -> None:
    if page_size <= 0:
        raise ValueError("page_size must be positive")

    offset = 0
    while True:
        params = _gamma_token_query_params(token_batch, page_size=page_size, offset=offset, closed=closed)
        response = client.get(f"{GAMMA_API_URL}/markets", params=params)
        if _should_split_gamma_token_batch(response) and len(token_batch) > 1:
            midpoint = max(1, len(token_batch) // 2)
            for smaller_batch in (token_batch[:midpoint], token_batch[midpoint:]):
                _fetch_gamma_market_pages_for_token_batch(
                    client=client,
                    token_batch=smaller_batch,
                    page_size=page_size,
                    closed=closed,
                    markets=markets,
                    seen_market_ids=seen_market_ids,
                    sleep_seconds=sleep_seconds,
                )
            return

        response.raise_for_status()
        batch = _markets_from_gamma_payload(response.json())
        _append_unique_market_rows(batch, markets, seen_market_ids)
        if sleep_seconds:
            time.sleep(sleep_seconds)
        if len(batch) < page_size:
            break
        offset += len(batch)


def _gamma_token_query_params(
    token_batch: list[str],
    page_size: int,
    offset: int,
    closed: bool | None,
) -> list[tuple[str, str | int]]:
    params: list[tuple[str, str | int]] = [("clob_token_ids", token_id) for token_id in token_batch]
    params.extend([("limit", page_size), ("offset", offset)])
    if closed is not None:
        params.append(("closed", str(closed).lower()))
    return params


def _should_split_gamma_token_batch(response: httpx.Response) -> bool:
    return response.status_code in {414, 422} or response.status_code >= 500


def _markets_from_gamma_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        return payload.get("markets", [])
    return []


def _append_unique_markets(payload: Any, markets: list[dict[str, Any]], seen_market_ids: set[str]) -> None:
    batch = _markets_from_gamma_payload(payload)
    _append_unique_market_rows(batch, markets, seen_market_ids)


def _append_unique_market_rows(
    batch: list[dict[str, Any]],
    markets: list[dict[str, Any]],
    seen_market_ids: set[str],
) -> None:
    for raw_market in batch:
        market_id = str(raw_market.get("id", ""))
        if market_id and market_id not in seen_market_ids:
            markets.append(raw_market)
            seen_market_ids.add(market_id)


def _chunks(items: list[str], size: int) -> list[list[str]]:
    if size <= 0:
        raise ValueError("size must be positive")
    return [items[index : index + size] for index in range(0, len(items), size)]


def extract_token_ids_from_orderfilled(orderfilled_prefix: str) -> list[str]:
    orderfilled = read_parquet_dataset(orderfilled_prefix, columns=["token_id"])
    if orderfilled.empty or "token_id" not in orderfilled.columns:
        return []
    return sorted({str(token) for token in orderfilled["token_id"].dropna() if str(token) and str(token) != "0"})


def fetch_markets_job(
    lake_uri: str,
    run_id: str,
    max_markets: int | None = None,
    page_size: int = 500,
    token_ids: Iterable[str] | None = None,
    orderfilled_prefix: str | None = None,
) -> dict[str, Any]:
    paths = LakePaths(lake_uri)
    ingested_at = utc_now_iso()
    resolved_token_ids = sorted({str(token) for token in (token_ids or []) if str(token) and str(token) != "0"})
    if orderfilled_prefix:
        resolved_token_ids = extract_token_ids_from_orderfilled(orderfilled_prefix)

    if resolved_token_ids:
        raw_markets = fetch_gamma_markets_raw_for_tokens(resolved_token_ids)
        source = "token_ids"
    else:
        raw_markets = fetch_gamma_markets_raw(max_markets=max_markets, page_size=page_size)
        source = "catalog"

    raw_path = paths.markets_raw_json(run_id)
    write_json(raw_markets, raw_path)

    tables = build_market_tables(raw_markets, raw_payload_path=raw_path, ingested_at=ingested_at)
    outputs = {
        "markets_raw": raw_path,
        "markets_normalized": write_parquet(tables["markets_normalized"], paths.silver_markets(run_id)),
        "dim_market": write_parquet(tables["dim_market"], paths.dim_market(run_id)),
        "dim_outcome": write_parquet(tables["dim_outcome"], paths.dim_outcome(run_id)),
    }
    return {
        "job": "fetch_markets",
        "run_id": run_id,
        "source": source,
        "token_count": len(resolved_token_ids),
        "market_count": len(raw_markets),
        "dim_market_rows": len(tables["dim_market"]),
        "dim_outcome_rows": len(tables["dim_outcome"]),
        "outputs": outputs,
    }


def fetch_orderfilled_job(
    lake_uri: str,
    run_id: str,
    start_block: int,
    end_block: int,
    batch_size: int = 100,
    max_events: int | None = None,
    rpc_url: str | None = None,
    rpc_secret_id: str | None = None,
) -> dict[str, Any]:
    paths = LakePaths(lake_uri)
    rpc_urls = resolve_rpc_urls(rpc_url=rpc_url, rpc_secret_id=rpc_secret_id)

    with PolygonRpcClient(rpc_urls=rpc_urls) as rpc:
        raw = fetch_orderfilled_logs_by_range(
            rpc,
            start_block=start_block,
            end_block=end_block,
            batch_size=batch_size,
            max_events=max_events,
        )
        decoded = decode_orderfilled_logs(raw["logs"], rpc=rpc)

    raw_json_path = paths.orderfilled_raw_json(run_id, start_block, end_block)
    raw_parquet_path = paths.orderfilled_raw_parquet(run_id, start_block, end_block)
    write_json(raw, raw_json_path)
    orderfilled = pd.DataFrame(decoded)
    write_parquet(orderfilled, raw_parquet_path)

    failed_ranges = raw.get("failed_ranges", [])
    last_successful_block = end_block if not failed_ranges else min(item["start_block"] for item in failed_ranges) - 1
    checkpoint = {
        "last_successful_block": last_successful_block,
        "requested_start_block": start_block,
        "requested_end_block": end_block,
        "run_id": run_id,
        "updated_at": utc_now_iso(),
        "failed_ranges": failed_ranges,
    }
    checkpoint_path = paths.checkpoint_json()
    write_json(checkpoint, checkpoint_path)

    return {
        "job": "fetch_orderfilled",
        "run_id": run_id,
        "start_block": start_block,
        "end_block": end_block,
        "raw_log_count": len(raw["logs"]),
        "decoded_event_count": len(orderfilled),
        "failed_range_count": len(failed_ranges),
        "outputs": {
            "orderfilled_logs": raw_json_path,
            "orderfilled_raw": raw_parquet_path,
            "checkpoint": checkpoint_path,
        },
    }


def normalize_trades_job(
    lake_uri: str,
    run_id: str,
    date_start: str | None = None,
    date_end: str | None = None,
    orderfilled_prefix: str | None = None,
    dim_market_prefix: str | None = None,
    dim_outcome_prefix: str | None = None,
    orderfilled_file_batch_size: int = 4,
) -> dict[str, Any]:
    paths = LakePaths(lake_uri)
    orderfilled_prefix = orderfilled_prefix or join_uri(paths.bronze_polymarket, "orderfilled_raw")
    dim_market_prefix = dim_market_prefix or join_uri(paths.gold_polymarket, "dim_market")
    dim_outcome_prefix = dim_outcome_prefix or join_uri(paths.gold_polymarket, "dim_outcome")

    dim_market = read_parquet_dataset(dim_market_prefix)
    dim_outcome = read_parquet_dataset(dim_outcome_prefix)
    if not dim_market.empty:
        dim_market = dim_market.drop_duplicates("market_id", keep="last")
    if not dim_outcome.empty:
        dim_outcome = dim_outcome.drop_duplicates("token_id", keep="last")

    total_orderfilled_rows = 0
    total_trades_normalized_rows = 0
    total_fact_trades_rows = 0
    silver_outputs: list[str] = []
    gold_outputs: list[str] = []
    seen_trade_ids: set[str] = set()
    ingested_at = utc_now_iso()

    for index, orderfilled in enumerate(
        _iter_orderfilled_batches(orderfilled_prefix, orderfilled_file_batch_size),
    ):
        total_orderfilled_rows += len(orderfilled)
        if orderfilled.empty:
            continue

        tables = build_fact_trades(
            orderfilled=orderfilled,
            dim_market=dim_market,
            dim_outcome=dim_outcome,
            raw_payload_path=orderfilled_prefix,
            ingested_at=ingested_at,
        )
        trades_normalized = _add_trade_date_columns(tables["trades_normalized"])
        fact_trades = tables["fact_trades"]
        trades_normalized = filter_by_date(trades_normalized, date_start, date_end)
        fact_trades = filter_by_date(fact_trades, date_start, date_end)
        if fact_trades.empty:
            continue

        trade_ids = fact_trades["trade_id"].astype(str)
        keep_mask = ~trade_ids.isin(seen_trade_ids)
        seen_trade_ids.update(trade_ids[keep_mask].tolist())
        fact_trades = fact_trades.loc[keep_mask].reset_index(drop=True)
        trades_normalized = trades_normalized.loc[keep_mask].reset_index(drop=True)
        if fact_trades.empty:
            continue

        part_run_id = f"{run_id}-{index:06d}"
        silver_outputs.extend(
            write_partitioned_parquet(
                trades_normalized,
                paths.silver_trades_base(),
                partition_column="date",
                run_id=part_run_id,
            )
        )
        gold_outputs.extend(
            write_partitioned_parquet(
                fact_trades,
                paths.fact_trades_base(),
                partition_column="date",
                run_id=part_run_id,
            )
        )
        total_trades_normalized_rows += len(trades_normalized)
        total_fact_trades_rows += len(fact_trades)

    if not silver_outputs and not gold_outputs:
        empty_tables = build_fact_trades(
            orderfilled=pd.DataFrame(columns=ORDERFILLED_NORMALIZE_COLUMNS),
            dim_market=dim_market,
            dim_outcome=dim_outcome,
            raw_payload_path=orderfilled_prefix,
            ingested_at=ingested_at,
        )
        silver_outputs = write_partitioned_parquet(
            _add_trade_date_columns(empty_tables["trades_normalized"]),
            paths.silver_trades_base(),
            partition_column="date",
            run_id=run_id,
        )
        gold_outputs = write_partitioned_parquet(
            empty_tables["fact_trades"],
            paths.fact_trades_base(),
            partition_column="date",
            run_id=run_id,
        )

    return {
        "job": "normalize_trades",
        "run_id": run_id,
        "orderfilled_rows": total_orderfilled_rows,
        "trades_normalized_rows": total_trades_normalized_rows,
        "fact_trades_rows": total_fact_trades_rows,
        "outputs": {
            "trades_normalized": silver_outputs,
            "fact_trades": gold_outputs,
        },
    }


def _iter_orderfilled_batches(orderfilled_prefix: str, file_batch_size: int) -> Iterable[pd.DataFrame]:
    paths = list_parquet_objects(orderfilled_prefix)
    if not paths:
        yield pd.DataFrame(columns=ORDERFILLED_NORMALIZE_COLUMNS)
        return

    for path_batch in _chunks(paths, file_batch_size):
        frames = [read_parquet(path, columns=ORDERFILLED_NORMALIZE_COLUMNS) for path in path_batch]
        frames = [frame for frame in frames if not frame.empty]
        if frames:
            yield pd.concat(frames, ignore_index=True)
        else:
            yield pd.DataFrame(columns=ORDERFILLED_NORMALIZE_COLUMNS)


def build_market_daily_job(
    lake_uri: str,
    run_id: str,
    date_start: str | None = None,
    date_end: str | None = None,
    fact_trades_prefix: str | None = None,
    fact_file_batch_size: int = 8,
) -> dict[str, Any]:
    paths = LakePaths(lake_uri)
    fact_trades_prefix = fact_trades_prefix or paths.fact_trades_base()
    daily, fact_trades_rows = _build_fact_market_daily_from_prefix(
        fact_trades_prefix,
        date_start=date_start,
        date_end=date_end,
        file_batch_size=fact_file_batch_size,
    )
    daily = filter_by_date(daily, date_start, date_end)
    outputs = write_partitioned_parquet(
        daily,
        paths.fact_market_daily_base(),
        partition_column="date",
        run_id=run_id,
    )
    return {
        "job": "build_market_daily",
        "run_id": run_id,
        "fact_trades_rows": fact_trades_rows,
        "fact_market_daily_rows": len(daily),
        "outputs": {"fact_market_daily": outputs},
    }


def _build_fact_market_daily_from_prefix(
    fact_trades_prefix: str,
    date_start: str | None,
    date_end: str | None,
    file_batch_size: int,
) -> tuple[pd.DataFrame, int]:
    paths = list_parquet_objects(fact_trades_prefix)
    if not paths:
        return pd.DataFrame(columns=FACT_MARKET_DAILY_COLUMNS), 0

    state: dict[tuple[Any, Any], dict[str, Any]] = {}
    fact_trades_rows = 0
    for path_batch in _chunks(paths, file_batch_size):
        frames = [read_parquet(path, columns=FACT_DAILY_INPUT_COLUMNS) for path in path_batch]
        frames = [frame for frame in frames if not frame.empty]
        if not frames:
            continue
        fact_trades = pd.concat(frames, ignore_index=True)
        fact_trades = filter_by_date(fact_trades, date_start, date_end)
        if fact_trades.empty:
            continue
        if "trade_id" in fact_trades.columns:
            fact_trades = fact_trades.drop_duplicates("trade_id", keep="last")
        fact_trades_rows += len(fact_trades)
        _merge_daily_state(state, fact_trades)

    if not state:
        return pd.DataFrame(columns=FACT_MARKET_DAILY_COLUMNS), fact_trades_rows
    return _daily_state_to_frame(state), fact_trades_rows


def _merge_daily_state(state: dict[tuple[Any, Any], dict[str, Any]], fact_trades: pd.DataFrame) -> None:
    working = fact_trades.copy()
    working["usd_amount"] = pd.to_numeric(working["usd_amount"], errors="coerce").fillna(0.0)
    working["price"] = pd.to_numeric(working["price"], errors="coerce")
    working["timestamp"] = pd.to_numeric(working["timestamp"], errors="coerce")

    for (market_id, date), group in working.groupby(["market_id", "date"], dropna=False):
        key = (market_id, date)
        entry = state.setdefault(
            key,
            {
                "market_id": market_id,
                "date": date,
                "daily_volume": 0.0,
                "daily_trade_count": 0,
                "makers": set(),
                "takers": set(),
                "price_sum": 0.0,
                "price_count": 0,
                "min_price": None,
                "max_price": None,
                "close_sort_key": None,
                "close_price": None,
            },
        )
        prices = group["price"].dropna()
        entry["daily_volume"] += float(group["usd_amount"].sum())
        entry["daily_trade_count"] += int(len(group))
        entry["makers"].update(str(value) for value in group["maker"].dropna())
        entry["takers"].update(str(value) for value in group["taker"].dropna())
        entry["price_sum"] += float(prices.sum()) if not prices.empty else 0.0
        entry["price_count"] += int(prices.count())
        if not prices.empty:
            min_price = float(prices.min())
            max_price = float(prices.max())
            entry["min_price"] = min_price if entry["min_price"] is None else min(entry["min_price"], min_price)
            entry["max_price"] = max_price if entry["max_price"] is None else max(entry["max_price"], max_price)

        close = group.sort_values(["timestamp", "trade_id"], na_position="first").tail(1).iloc[0]
        close_sort_key = (
            float(close["timestamp"]) if pd.notna(close["timestamp"]) else float("-inf"),
            str(close.get("trade_id", "")),
        )
        if entry["close_sort_key"] is None or close_sort_key >= entry["close_sort_key"]:
            entry["close_sort_key"] = close_sort_key
            entry["close_price"] = None if pd.isna(close["price"]) else float(close["price"])


def _daily_state_to_frame(state: dict[tuple[Any, Any], dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for entry in state.values():
        price_count = entry["price_count"]
        rows.append(
            {
                "market_id": entry["market_id"],
                "date": entry["date"],
                "daily_volume": entry["daily_volume"],
                "daily_trade_count": entry["daily_trade_count"],
                "unique_makers": len(entry["makers"]),
                "unique_takers": len(entry["takers"]),
                "avg_price": entry["price_sum"] / price_count if price_count else None,
                "min_price": entry["min_price"],
                "max_price": entry["max_price"],
                "close_price": entry["close_price"],
            }
        )
    return pd.DataFrame(rows, columns=FACT_MARKET_DAILY_COLUMNS)


def resolve_rpc_urls(rpc_url: str | None = None, rpc_secret_id: str | None = None) -> list[str] | None:
    if rpc_url:
        return [rpc_url]
    if os.getenv("POLYGON_RPC_URL"):
        return [os.environ["POLYGON_RPC_URL"]]
    if not rpc_secret_id:
        return None

    secret = boto3.client("secretsmanager").get_secret_value(SecretId=rpc_secret_id)
    value = secret.get("SecretString", "")
    if not value:
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return [value]
    for key in ("POLYGON_RPC_URL", "polygon_rpc_url", "rpc_url"):
        if parsed.get(key):
            return [parsed[key]]
    return None


def _add_trade_date_columns(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        result = frame.copy()
        result["date"] = pd.Series(dtype="object")
        result["hour"] = pd.Series(dtype="Int64")
        return result
    result = frame.copy()
    event_time = pd.to_datetime(result["timestamp"], unit="s", utc=True, errors="coerce")
    result["date"] = event_time.dt.strftime("%Y-%m-%d")
    result["hour"] = event_time.dt.hour.astype("Int64")
    return result
