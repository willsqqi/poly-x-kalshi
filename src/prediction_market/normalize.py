from __future__ import annotations

from collections import defaultdict
from typing import Any

import pandas as pd

from .utils import (
    best_ask,
    best_bid,
    compact_json,
    complement,
    parse_json_array,
    parse_timestamp,
    subtract,
    to_float,
    total_size,
)

MARKET_COLUMNS = [
    "platform",
    "market_id",
    "ticker_or_slug",
    "title",
    "category",
    "status",
    "close_time",
    "outcomes",
    "volume",
    "liquidity",
    "raw_payload",
]

ORDERBOOK_COLUMNS = [
    "platform",
    "market_id",
    "timestamp",
    "best_yes_bid",
    "best_yes_ask",
    "best_no_bid",
    "best_no_ask",
    "spread",
    "bid_depth",
    "ask_depth",
    "depth_json",
]

TRADE_COLUMNS = [
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


def normalize_markets(
    polymarket_markets: list[dict[str, Any]],
    kalshi_markets: list[dict[str, Any]],
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []

    for market in polymarket_markets:
        event = _first_dict(parse_json_array(market.get("events")))
        rows.append(
            {
                "platform": "polymarket",
                "market_id": market.get("conditionId") or market.get("id"),
                "ticker_or_slug": market.get("slug"),
                "title": market.get("question") or market.get("title"),
                "category": market.get("category") or event.get("category"),
                "status": _polymarket_status(market),
                "close_time": parse_timestamp(market.get("endDate") or market.get("endDateIso")),
                "outcomes": parse_json_array(market.get("outcomes")),
                "volume": to_float(market.get("volumeNum") or market.get("volume")),
                "liquidity": to_float(market.get("liquidityNum") or market.get("liquidity")),
                "raw_payload": compact_json(market),
            }
        )

    for market in kalshi_markets:
        rows.append(
            {
                "platform": "kalshi",
                "market_id": market.get("ticker"),
                "ticker_or_slug": market.get("ticker"),
                "title": market.get("title"),
                "category": market.get("category"),
                "status": market.get("status"),
                "close_time": parse_timestamp(market.get("close_time")),
                "outcomes": _kalshi_outcomes(market),
                "volume": to_float(market.get("volume_fp")),
                "liquidity": to_float(market.get("liquidity_dollars")),
                "raw_payload": compact_json(market),
            }
        )

    return pd.DataFrame(rows, columns=MARKET_COLUMNS)


def normalize_orderbooks(
    polymarket_orderbooks: list[dict[str, Any]],
    kalshi_orderbooks: list[dict[str, Any]],
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    rows.extend(_normalize_polymarket_orderbooks(polymarket_orderbooks))
    rows.extend(_normalize_kalshi_orderbooks(kalshi_orderbooks))
    return pd.DataFrame(rows, columns=ORDERBOOK_COLUMNS)


def normalize_trades(
    polymarket_trades: list[dict[str, Any]],
    kalshi_trades: list[dict[str, Any]],
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []

    for group in polymarket_trades:
        for trade in _trade_payloads(group):
            price = to_float(trade.get("price"))
            outcome = trade.get("outcome")
            rows.append(
                {
                    "platform": "polymarket",
                    "market_id": trade.get("conditionId") or group.get("market_id"),
                    "trade_id": trade.get("transactionHash") or _fallback_trade_id(group, trade),
                    "timestamp": parse_timestamp(trade.get("timestamp")),
                    "outcome": outcome,
                    "side": trade.get("side"),
                    "price": price,
                    "yes_price": _polymarket_yes_price(outcome, price),
                    "size": to_float(trade.get("size")),
                    "raw_payload": compact_json(trade),
                }
            )

    for group in kalshi_trades:
        for trade in _trade_payloads(group):
            yes_price = to_float(trade.get("yes_price_dollars"))
            rows.append(
                {
                    "platform": "kalshi",
                    "market_id": trade.get("ticker") or group.get("market_id"),
                    "trade_id": trade.get("trade_id") or _fallback_trade_id(group, trade),
                    "timestamp": parse_timestamp(trade.get("created_time")),
                    "outcome": trade.get("taker_outcome_side"),
                    "side": trade.get("taker_side"),
                    "price": yes_price,
                    "yes_price": yes_price,
                    "size": to_float(trade.get("count_fp")),
                    "raw_payload": compact_json(trade),
                }
            )

    return pd.DataFrame(rows, columns=TRADE_COLUMNS)


def normalize_snapshot(raw_data: dict[str, Any]) -> dict[str, pd.DataFrame]:
    return {
        "markets": normalize_markets(raw_data.get("polymarket_markets", []), raw_data.get("kalshi_markets", [])),
        "orderbook_snapshots": normalize_orderbooks(
            raw_data.get("polymarket_orderbooks", []),
            raw_data.get("kalshi_orderbooks", []),
        ),
        "trades": normalize_trades(raw_data.get("polymarket_trades", []), raw_data.get("kalshi_trades", [])),
    }


def _normalize_polymarket_orderbooks(orderbooks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_market: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in orderbooks:
        if entry.get("market_id"):
            by_market[str(entry["market_id"])].append(entry)

    rows: list[dict[str, Any]] = []
    for market_id, entries in by_market.items():
        values: dict[str, dict[str, Any]] = {
            "yes": {"best_bid": None, "best_ask": None, "bid_depth": 0.0, "ask_depth": 0.0},
            "no": {"best_bid": None, "best_ask": None, "bid_depth": 0.0, "ask_depth": 0.0},
        }
        timestamps: list[str] = []

        for entry in entries:
            payload = entry.get("payload") or {}
            outcome_key = _outcome_key(entry.get("outcome"), entry.get("token_index"))
            bids = payload.get("bids") if isinstance(payload.get("bids"), list) else []
            asks = payload.get("asks") if isinstance(payload.get("asks"), list) else []
            values[outcome_key] = {
                "best_bid": best_bid(bids),
                "best_ask": best_ask(asks),
                "bid_depth": total_size(bids),
                "ask_depth": total_size(asks),
            }
            timestamp = parse_timestamp(payload.get("timestamp")) or entry.get("retrieved_at")
            if timestamp:
                timestamps.append(timestamp)

        best_yes_bid = values["yes"]["best_bid"]
        best_yes_ask = values["yes"]["best_ask"]
        rows.append(
            {
                "platform": "polymarket",
                "market_id": market_id,
                "timestamp": timestamps[0] if timestamps else None,
                "best_yes_bid": best_yes_bid,
                "best_yes_ask": best_yes_ask,
                "best_no_bid": values["no"]["best_bid"],
                "best_no_ask": values["no"]["best_ask"],
                "spread": subtract(best_yes_ask, best_yes_bid),
                "bid_depth": values["yes"]["bid_depth"],
                "ask_depth": values["yes"]["ask_depth"],
                "depth_json": compact_json(entries),
            }
        )

    return rows


def _normalize_kalshi_orderbooks(orderbooks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for entry in orderbooks:
        payload = entry.get("payload") or {}
        orderbook = payload.get("orderbook_fp") or payload.get("orderbook") or {}
        yes_bids = orderbook.get("yes_dollars") if isinstance(orderbook.get("yes_dollars"), list) else []
        no_bids = orderbook.get("no_dollars") if isinstance(orderbook.get("no_dollars"), list) else []

        best_yes_bid = best_bid(yes_bids)
        best_no_bid = best_bid(no_bids)
        best_yes_ask = complement(best_no_bid)
        best_no_ask = complement(best_yes_bid)

        rows.append(
            {
                "platform": "kalshi",
                "market_id": entry.get("market_id"),
                "timestamp": entry.get("retrieved_at"),
                "best_yes_bid": best_yes_bid,
                "best_yes_ask": best_yes_ask,
                "best_no_bid": best_no_bid,
                "best_no_ask": best_no_ask,
                "spread": subtract(best_yes_ask, best_yes_bid),
                "bid_depth": total_size(yes_bids),
                "ask_depth": total_size(no_bids),
                "depth_json": compact_json(payload),
            }
        )
    return rows


def _polymarket_status(market: dict[str, Any]) -> str:
    if market.get("closed") is True:
        return "closed"
    if market.get("active") is True:
        return "active"
    return "inactive"


def _kalshi_outcomes(market: dict[str, Any]) -> list[str]:
    yes = market.get("yes_sub_title") or "Yes"
    no = market.get("no_sub_title") or "No"
    return [f"Yes: {yes}", f"No: {no}"]


def _first_dict(values: list[Any]) -> dict[str, Any]:
    for value in values:
        if isinstance(value, dict):
            return value
    return {}


def _outcome_key(outcome: Any, token_index: Any) -> str:
    text = str(outcome or "").strip().lower()
    if text == "yes" or text.startswith("yes"):
        return "yes"
    if text == "no" or text.startswith("no"):
        return "no"
    return "yes" if token_index == 0 else "no"


def _trade_payloads(group: dict[str, Any]) -> list[dict[str, Any]]:
    payload = group.get("payload", [])
    if isinstance(payload, list):
        return [trade for trade in payload if isinstance(trade, dict)]
    if isinstance(payload, dict):
        for key in ("data", "trades"):
            value = payload.get(key)
            if isinstance(value, list):
                return [trade for trade in value if isinstance(trade, dict)]
    return []


def _polymarket_yes_price(outcome: Any, price: float | None) -> float | None:
    if price is None:
        return None
    text = str(outcome or "").strip().lower()
    if text == "yes" or text.startswith("yes"):
        return price
    if text == "no" or text.startswith("no"):
        return 1.0 - price
    return None


def _fallback_trade_id(group: dict[str, Any], trade: dict[str, Any]) -> str:
    return compact_json(
        {
            "market_id": group.get("market_id"),
            "timestamp": trade.get("timestamp") or trade.get("created_time"),
            "price": trade.get("price") or trade.get("yes_price_dollars"),
            "size": trade.get("size") or trade.get("count_fp"),
        }
    )
