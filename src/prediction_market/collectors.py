from __future__ import annotations

from typing import Any

import httpx

from .utils import parse_json_array, utc_now_iso

POLYMARKET_GAMMA_BASE = "https://gamma-api.polymarket.com"
POLYMARKET_CLOB_BASE = "https://clob.polymarket.com"
POLYMARKET_DATA_BASE = "https://data-api.polymarket.com"
KALSHI_BASE = "https://external-api.kalshi.com/trade-api/v2"
DEFAULT_TIMEOUT_SECONDS = 30.0


def _get_json(client: httpx.Client, url: str, params: dict[str, Any] | None = None) -> Any:
    response = client.get(url, params=params)
    response.raise_for_status()
    return response.json()


def fetch_polymarket_markets(client: httpx.Client, limit: int = 10) -> list[dict[str, Any]]:
    request_limit = max(limit * 3, limit)
    payload = _get_json(
        client,
        f"{POLYMARKET_GAMMA_BASE}/markets",
        params={"active": "true", "closed": "false", "limit": request_limit},
    )
    markets = payload if isinstance(payload, list) else payload.get("markets", [])
    candidates = [
        market
        for market in markets
        if market.get("active") is True
        and market.get("closed") is False
        and parse_json_array(market.get("clobTokenIds"))
    ]
    return candidates[:limit]


def fetch_polymarket_orderbook(client: httpx.Client, token_id: str) -> dict[str, Any]:
    return _get_json(client, f"{POLYMARKET_CLOB_BASE}/book", params={"token_id": token_id})


def fetch_polymarket_orderbooks(
    client: httpx.Client,
    markets: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for market in markets:
        market_id = market.get("conditionId") or market.get("id")
        outcomes = parse_json_array(market.get("outcomes"))
        token_ids = parse_json_array(market.get("clobTokenIds"))
        for index, token_id in enumerate(token_ids[:2]):
            retrieved_at = utc_now_iso()
            outcome = outcomes[index] if index < len(outcomes) else str(index)
            record = {
                "platform": "polymarket",
                "market_id": market_id,
                "token_id": str(token_id),
                "token_index": index,
                "outcome": outcome,
                "retrieved_at": retrieved_at,
            }
            try:
                record["payload"] = fetch_polymarket_orderbook(client, str(token_id))
            except (httpx.HTTPError, ValueError) as exc:
                record["payload"] = {}
                record["error"] = str(exc)
            records.append(record)
    return records


def fetch_polymarket_trades(
    client: httpx.Client,
    markets: list[dict[str, Any]],
    limit_per_market: int = 100,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for market in markets:
        market_id = market.get("conditionId") or market.get("id")
        if not market_id:
            continue
        retrieved_at = utc_now_iso()
        record = {
            "platform": "polymarket",
            "market_id": market_id,
            "retrieved_at": retrieved_at,
        }
        try:
            record["payload"] = _get_json(
                client,
                f"{POLYMARKET_DATA_BASE}/trades",
                params={"market": market_id, "limit": limit_per_market},
            )
        except (httpx.HTTPError, ValueError) as exc:
            record["payload"] = []
            record["error"] = str(exc)
        records.append(record)
    return records


def fetch_kalshi_markets(client: httpx.Client, limit: int = 10) -> list[dict[str, Any]]:
    payload = _get_json(
        client,
        f"{KALSHI_BASE}/markets",
        params={"status": "open", "limit": max(limit, 1)},
    )
    markets = payload if isinstance(payload, list) else payload.get("markets", [])
    candidates = [market for market in markets if market.get("ticker")]
    return candidates[:limit]


def fetch_kalshi_orderbook(client: httpx.Client, ticker: str, depth: int = 100) -> dict[str, Any]:
    return _get_json(client, f"{KALSHI_BASE}/markets/{ticker}/orderbook", params={"depth": depth})


def fetch_kalshi_orderbooks(
    client: httpx.Client,
    markets: list[dict[str, Any]],
    depth: int = 100,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for market in markets:
        ticker = market.get("ticker")
        if not ticker:
            continue
        record = {
            "platform": "kalshi",
            "market_id": ticker,
            "retrieved_at": utc_now_iso(),
        }
        try:
            record["payload"] = fetch_kalshi_orderbook(client, ticker, depth=depth)
        except (httpx.HTTPError, ValueError) as exc:
            record["payload"] = {"orderbook_fp": {"yes_dollars": [], "no_dollars": []}}
            record["error"] = str(exc)
        records.append(record)
    return records


def fetch_kalshi_trades(
    client: httpx.Client,
    markets: list[dict[str, Any]],
    limit_per_market: int = 100,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for market in markets:
        ticker = market.get("ticker")
        if not ticker:
            continue
        record = {
            "platform": "kalshi",
            "market_id": ticker,
            "retrieved_at": utc_now_iso(),
        }
        try:
            payload = _get_json(
                client,
                f"{KALSHI_BASE}/markets/trades",
                params={"ticker": ticker, "limit": limit_per_market},
            )
            record["payload"] = payload.get("trades", []) if isinstance(payload, dict) else payload
            if isinstance(payload, dict):
                record["cursor"] = payload.get("cursor", "")
        except (httpx.HTTPError, ValueError) as exc:
            record["payload"] = []
            record["error"] = str(exc)
        records.append(record)
    return records


def collect_snapshot(
    market_limit: int = 10,
    trades_limit: int = 100,
    orderbook_depth: int = 100,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    if client is None:
        with httpx.Client(timeout=DEFAULT_TIMEOUT_SECONDS) as owned_client:
            return collect_snapshot(market_limit, trades_limit, orderbook_depth, owned_client)

    retrieved_at = utc_now_iso()
    polymarket_markets = fetch_polymarket_markets(client, limit=market_limit)
    kalshi_markets = fetch_kalshi_markets(client, limit=market_limit)

    return {
        "retrieved_at": retrieved_at,
        "polymarket_markets": polymarket_markets,
        "kalshi_markets": kalshi_markets,
        "polymarket_orderbooks": fetch_polymarket_orderbooks(client, polymarket_markets),
        "kalshi_orderbooks": fetch_kalshi_orderbooks(client, kalshi_markets, depth=orderbook_depth),
        "polymarket_trades": fetch_polymarket_trades(client, polymarket_markets, limit_per_market=trades_limit),
        "kalshi_trades": fetch_kalshi_trades(client, kalshi_markets, limit_per_market=trades_limit),
    }
