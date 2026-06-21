from __future__ import annotations

from typing import Any

import pandas as pd

from prediction_market.polymarket_onchain import build_token_mapping, extract_trades, parse_gamma_market
from prediction_market.utils import parse_timestamp, to_float, utc_now_iso

from .schemas import DIM_MARKET_COLUMNS, DIM_OUTCOME_COLUMNS, FACT_MARKET_DAILY_COLUMNS, FACT_TRADES_COLUMNS


def parse_market_payloads(raw_markets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [parse_gamma_market(raw_market) for raw_market in raw_markets]


def build_market_tables(
    raw_markets: list[dict[str, Any]],
    raw_payload_path: str,
    ingested_at: str | None = None,
) -> dict[str, pd.DataFrame]:
    ingested_at = ingested_at or utc_now_iso()
    markets = parse_market_payloads(raw_markets)
    markets_normalized = pd.DataFrame(markets)

    dim_market_rows = []
    dim_outcome_rows = []
    for market in markets:
        market_id = str(market.get("id", ""))
        status = _market_status(market)
        dim_market_rows.append(
            {
                "market_id": market_id,
                "question": market.get("question", ""),
                "slug": market.get("slug", ""),
                "category": market.get("category", ""),
                "status": status,
                "open_time": parse_timestamp(market.get("start_date") or market.get("created_at")),
                "close_time": parse_timestamp(market.get("end_date")),
                "resolution_time": None,
                "condition_id": market.get("condition_id", ""),
                "volume": to_float(market.get("volume")),
                "liquidity": to_float(market.get("liquidity")),
                "raw_payload_path": raw_payload_path,
                "ingested_at": ingested_at,
            }
        )

        for index, (token_key, answer_key) in enumerate((("token1", "answer1"), ("token2", "answer2"))):
            token_id = str(market.get(token_key, ""))
            if not token_id:
                continue
            dim_outcome_rows.append(
                {
                    "outcome_id": token_id,
                    "market_id": market_id,
                    "outcome_name": market.get(answer_key, ""),
                    "outcome_index": index,
                    "token_id": token_id,
                    "side": token_key,
                    "ingested_at": ingested_at,
                }
            )

    return {
        "markets_normalized": markets_normalized,
        "dim_market": pd.DataFrame(dim_market_rows, columns=DIM_MARKET_COLUMNS),
        "dim_outcome": pd.DataFrame(dim_outcome_rows, columns=DIM_OUTCOME_COLUMNS),
    }


def build_token_mapping_from_dims(dim_market: pd.DataFrame, dim_outcome: pd.DataFrame) -> dict[str, dict[str, Any]]:
    if dim_market.empty or dim_outcome.empty:
        return {}

    markets_by_id = dim_market.drop_duplicates("market_id", keep="last").set_index("market_id").to_dict("index")
    rows: list[dict[str, Any]] = []
    for _, outcome in dim_outcome.drop_duplicates("token_id", keep="last").iterrows():
        market_id = str(outcome.get("market_id", ""))
        market = markets_by_id.get(market_id, {})
        rows.append(
            {
                "id": market_id,
                "condition_id": market.get("condition_id", ""),
                "question": market.get("question", ""),
                "event_id": "",
                "event_slug": "",
                "event_title": "",
                "slug": market.get("slug", ""),
                "active": market.get("status") == "active",
                "closed": market.get("status") == "closed",
                "end_date": market.get("close_time"),
                "token1": str(outcome["token_id"]) if outcome.get("side") == "token1" else "",
                "token2": str(outcome["token_id"]) if outcome.get("side") == "token2" else "",
                "answer1": str(outcome.get("outcome_name", "")) if outcome.get("side") == "token1" else "",
                "answer2": str(outcome.get("outcome_name", "")) if outcome.get("side") == "token2" else "",
                "raw_payload": "{}",
            }
        )

    # Merge one-token pseudo markets back into the mapping shape expected by build_token_mapping.
    mapping: dict[str, dict[str, Any]] = {}
    for row in rows:
        side = "token1" if row.get("token1") else "token2"
        token_id = row.get(side, "")
        answer = row.get("answer1" if side == "token1" else "answer2", "")
        if token_id:
            mapping[str(token_id)] = {
                "token_id": str(token_id),
                "market_id": str(row.get("id", "")),
                "condition_id": str(row.get("condition_id", "")),
                "side": side,
                "answer": str(answer),
                "question": str(row.get("question", ""))[:250],
                "event_id": "",
                "event_slug": "",
                "event_title": "",
                "slug": str(row.get("slug", "")),
                "active": row.get("active"),
                "closed": row.get("closed"),
                "end_date": row.get("end_date"),
                "raw_payload": "{}",
            }
    return mapping


def build_fact_trades(
    orderfilled: pd.DataFrame,
    dim_market: pd.DataFrame,
    dim_outcome: pd.DataFrame,
    raw_payload_path: str,
    ingested_at: str | None = None,
) -> dict[str, pd.DataFrame]:
    ingested_at = ingested_at or utc_now_iso()
    token_mapping = build_token_mapping_from_dims(dim_market, dim_outcome)
    trades_normalized = extract_trades(orderfilled.to_dict("records"), token_mapping)
    if trades_normalized.empty:
        return {
            "trades_normalized": trades_normalized,
            "fact_trades": pd.DataFrame(columns=FACT_TRADES_COLUMNS),
        }
    trades_normalized = trades_normalized.drop_duplicates(
        ["transaction_hash", "log_index"],
        keep="last",
    ).reset_index(drop=True)

    fact = trades_normalized.copy()
    fact["trade_id"] = fact["transaction_hash"].astype(str) + ":" + fact["log_index"].astype(str)
    fact["outcome_id"] = fact["asset_id"].astype(str)
    event_time = pd.to_datetime(fact["timestamp"], unit="s", utc=True, errors="coerce")
    fact["date"] = event_time.dt.strftime("%Y-%m-%d")
    fact["hour"] = event_time.dt.hour.astype("Int64")
    fact["source_type"] = "historical_backfill"
    fact["ingested_at"] = ingested_at
    fact["raw_payload_path"] = raw_payload_path

    fact = fact[
        [
            "trade_id",
            "market_id",
            "outcome_id",
            "timestamp",
            "date",
            "hour",
            "maker",
            "taker",
            "price",
            "token_amount",
            "usd_amount",
            "maker_direction",
            "taker_direction",
            "transaction_hash",
            "block_number",
            "order_hash",
            "fee_amount",
            "source_type",
            "ingested_at",
            "raw_payload_path",
        ]
    ]
    return {
        "trades_normalized": trades_normalized,
        "fact_trades": fact.reindex(columns=FACT_TRADES_COLUMNS),
    }


def build_fact_market_daily(fact_trades: pd.DataFrame) -> pd.DataFrame:
    if fact_trades.empty:
        return pd.DataFrame(columns=FACT_MARKET_DAILY_COLUMNS)

    sortable = fact_trades.copy()
    if "trade_id" in sortable.columns:
        sortable = sortable.drop_duplicates("trade_id", keep="last")
    sortable = sortable.sort_values(["market_id", "date", "timestamp", "trade_id"])
    grouped = sortable.groupby(["market_id", "date"], dropna=False)
    aggregates = grouped.agg(
        daily_volume=("usd_amount", "sum"),
        daily_trade_count=("trade_id", "count"),
        unique_makers=("maker", pd.Series.nunique),
        unique_takers=("taker", pd.Series.nunique),
        avg_price=("price", "mean"),
        min_price=("price", "min"),
        max_price=("price", "max"),
    ).reset_index()

    close_price = grouped.tail(1)[["market_id", "date", "price"]].rename(columns={"price": "close_price"})
    result = aggregates.merge(close_price, on=["market_id", "date"], how="left")
    return result.reindex(columns=FACT_MARKET_DAILY_COLUMNS)


def _market_status(market: dict[str, Any]) -> str:
    if market.get("closed"):
        return "closed"
    if market.get("archived"):
        return "archived"
    if market.get("active"):
        return "active"
    return "inactive"
