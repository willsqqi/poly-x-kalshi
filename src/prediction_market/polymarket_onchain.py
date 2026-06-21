from __future__ import annotations

import json
import os
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import httpx
import pandas as pd

from .utils import compact_json, parse_json_array, parse_timestamp, to_float, utc_now_iso

POLYMARKET_GAMMA_BASE = "https://gamma-api.polymarket.com"

POLYGON_RPC_URLS = (
    "https://polygon-bor-rpc.publicnode.com",
    "https://polygon.drpc.org",
)

V1_CTF_EXCHANGE = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
V1_NEGRISK_CTF_EXCHANGE = "0xC5d563A36AE78145C45a50134d48A1215220f80a"
V2_CTF_EXCHANGE = "0xE111180000d2663C0091e4f400237545B87B996B"
V2_NEGRISK_CTF_EXCHANGE = "0xe2222d279d744050d28e00520010520000310F59"

ORDER_FILLED_TOPIC_V1 = "0xd0a08e8c493f9c94f29311604c9de1b4e8c8d4c06bd0c789af57f2d65bfec0f6"
ORDER_FILLED_TOPIC_V2 = "0xd543adfd945773f1a62f74f0ee55a5e3b9b1a28262980ba90b1a89f2ea84d8ee"

USDC_ASSET_ID = "0"
USDC_DECIMALS = 10**6

CONTRACTS = {
    V1_CTF_EXCHANGE.lower(): {"name": "CTF_EXCHANGE", "generation": "v1"},
    V1_NEGRISK_CTF_EXCHANGE.lower(): {"name": "NEGRISK_CTF_EXCHANGE", "generation": "v1"},
    V2_CTF_EXCHANGE.lower(): {"name": "CTF_EXCHANGE_V2", "generation": "v2"},
    V2_NEGRISK_CTF_EXCHANGE.lower(): {"name": "NEGRISK_CTF_EXCHANGE_V2", "generation": "v2"},
}

CONTRACT_ADDRESSES = tuple(CONTRACTS)
ORDER_FILLED_TOPICS = (ORDER_FILLED_TOPIC_V1, ORDER_FILLED_TOPIC_V2)
EXCHANGE_TAKER_ADDRESSES = set(CONTRACTS)

ORDERFILLED_COLUMNS = [
    "transaction_hash",
    "block_number",
    "log_index",
    "timestamp",
    "datetime",
    "contract",
    "contract_generation",
    "event_name",
    "order_hash",
    "maker",
    "taker",
    "maker_asset_id",
    "taker_asset_id",
    "side",
    "side_label",
    "token_id",
    "maker_amount_filled",
    "taker_amount_filled",
    "maker_fee",
    "taker_fee",
    "protocol_fee",
    "fee",
    "builder",
    "metadata",
    "raw_log",
]

MARKET_MAPPING_COLUMNS = [
    "token_id",
    "market_id",
    "condition_id",
    "side",
    "answer",
    "question",
    "event_id",
    "event_slug",
    "event_title",
    "slug",
    "active",
    "closed",
    "end_date",
    "raw_payload",
]

TRADES_COLUMNS = [
    "timestamp",
    "datetime",
    "block_number",
    "transaction_hash",
    "log_index",
    "contract",
    "contract_generation",
    "event_id",
    "event_slug",
    "event_title",
    "market_id",
    "condition_id",
    "question",
    "nonusdc_side",
    "answer",
    "maker",
    "taker",
    "maker_asset",
    "taker_asset",
    "maker_direction",
    "taker_direction",
    "price",
    "usd_amount",
    "token_amount",
    "fee_amount",
    "asset_id",
    "order_hash",
    "builder",
    "metadata",
]

QUANT_COLUMNS = TRADES_COLUMNS

USERS_COLUMNS = [
    "timestamp",
    "datetime",
    "block_number",
    "transaction_hash",
    "event_id",
    "market_id",
    "condition_id",
    "user",
    "role",
    "price",
    "token_amount",
    "usd_amount",
]


def default_rpc_urls() -> list[str]:
    env_url = os.getenv("POLYGON_RPC_URL")
    if env_url:
        return [env_url]
    return list(POLYGON_RPC_URLS)


class PolygonRpcClient:
    def __init__(
        self,
        rpc_urls: Iterable[str] | None = None,
        timeout: float = 20.0,
        max_retries: int = 2,
        request_delay: float = 0.15,
        transport: httpx.BaseTransport | None = None,
    ):
        self.rpc_urls = list(rpc_urls or default_rpc_urls())
        self.timeout = timeout
        self.max_retries = max_retries
        self.request_delay = request_delay
        self.client = httpx.Client(timeout=timeout, transport=transport)
        self._active_rpc_url: str | None = None

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> "PolygonRpcClient":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    @property
    def active_rpc_url(self) -> str | None:
        return self._active_rpc_url

    def call(self, method: str, params: list[Any] | None = None) -> Any:
        last_error: Exception | str | None = None
        urls = [self._active_rpc_url] if self._active_rpc_url else []
        urls.extend(url for url in self.rpc_urls if url not in urls)

        for url in urls:
            if not url:
                continue
            for attempt in range(self.max_retries):
                try:
                    response = self.client.post(
                        url,
                        json={"jsonrpc": "2.0", "method": method, "params": params or [], "id": 1},
                    )
                    response.raise_for_status()
                    payload = response.json()
                    if "error" in payload:
                        last_error = payload["error"]
                        break
                    self._active_rpc_url = url
                    return payload.get("result")
                except (httpx.HTTPError, ValueError) as exc:
                    last_error = exc
                    if attempt + 1 < self.max_retries:
                        time.sleep(self.request_delay * (attempt + 1))

        raise RuntimeError(f"Polygon RPC request failed for {method}: {last_error}")

    def latest_block(self) -> int:
        return hex_to_int(self.call("eth_blockNumber"))

    def get_logs(self, start_block: int, end_block: int) -> list[dict[str, Any]]:
        params = [
            {
                "fromBlock": hex(start_block),
                "toBlock": hex(end_block),
                "address": list(CONTRACT_ADDRESSES),
                "topics": [list(ORDER_FILLED_TOPICS)],
            }
        ]
        return self.call("eth_getLogs", params) or []

    def get_block_timestamp(self, block_number: int) -> int:
        block = self.call("eth_getBlockByNumber", [hex(block_number), False])
        if not isinstance(block, dict):
            raise RuntimeError(f"Missing block payload for block {block_number}")
        return hex_to_int(block.get("timestamp"))


def fetch_recent_orderfilled_logs(
    rpc: PolygonRpcClient,
    lookback_blocks: int = 25,
    batch_size: int = 5,
    max_events: int = 1_000,
) -> dict[str, Any]:
    latest = rpc.latest_block()
    start = max(1, latest - lookback_blocks + 1)
    logs: list[dict[str, Any]] = []
    failed_ranges: list[dict[str, Any]] = []

    current = start
    while current <= latest and len(logs) < max_events:
        batch_end = min(current + batch_size - 1, latest)
        try:
            batch_logs = rpc.get_logs(current, batch_end)
        except RuntimeError as exc:
            failed_ranges.append({"start_block": current, "end_block": batch_end, "error": str(exc)})
            current = batch_end + 1
            continue

        remaining = max_events - len(logs)
        logs.extend(batch_logs[:remaining])
        current = batch_end + 1

    return {
        "retrieved_at": utc_now_iso(),
        "rpc_url": rpc.active_rpc_url,
        "start_block": start,
        "end_block": latest,
        "lookback_blocks": lookback_blocks,
        "batch_size": batch_size,
        "max_events": max_events,
        "truncated": len(logs) >= max_events,
        "failed_ranges": failed_ranges,
        "logs": logs,
    }


def fetch_orderfilled_logs_by_range(
    rpc: PolygonRpcClient,
    start_block: int,
    end_block: int,
    batch_size: int = 100,
    max_events: int | None = None,
) -> dict[str, Any]:
    if start_block < 1:
        raise ValueError("start_block must be >= 1")
    if end_block < start_block:
        raise ValueError("end_block must be >= start_block")
    if batch_size < 1:
        raise ValueError("batch_size must be >= 1")

    logs: list[dict[str, Any]] = []
    failed_ranges: list[dict[str, Any]] = []
    current = start_block

    while current <= end_block:
        batch_end = min(current + batch_size - 1, end_block)
        try:
            batch_logs = rpc.get_logs(current, batch_end)
        except RuntimeError as exc:
            failed_ranges.append({"start_block": current, "end_block": batch_end, "error": str(exc)})
            current = batch_end + 1
            continue

        if max_events is None:
            logs.extend(batch_logs)
        else:
            remaining = max_events - len(logs)
            logs.extend(batch_logs[:remaining])
            if len(logs) >= max_events:
                break
        current = batch_end + 1

    return {
        "retrieved_at": utc_now_iso(),
        "rpc_url": rpc.active_rpc_url,
        "start_block": start_block,
        "end_block": end_block,
        "batch_size": batch_size,
        "max_events": max_events,
        "truncated": max_events is not None and len(logs) >= max_events,
        "failed_ranges": failed_ranges,
        "logs": logs,
    }


def decode_orderfilled_logs(logs: list[dict[str, Any]], rpc: PolygonRpcClient | None = None) -> list[dict[str, Any]]:
    timestamp_cache: dict[int, int] = {}
    decoded: list[dict[str, Any]] = []

    for log in logs:
        event = decode_orderfilled_log(log)
        if event is None:
            continue

        if event["timestamp"] is None and rpc is not None:
            block_number = event["block_number"]
            if block_number not in timestamp_cache:
                timestamp_cache[block_number] = rpc.get_block_timestamp(block_number)
            event["timestamp"] = timestamp_cache[block_number]
            event["datetime"] = parse_timestamp(event["timestamp"])

        decoded.append(event)

    return decoded


def decode_orderfilled_log(log: dict[str, Any]) -> dict[str, Any] | None:
    topics = log.get("topics") or []
    if not topics:
        return None

    topic0 = topics[0].lower()
    address = str(log.get("address", "")).lower()
    contract_info = CONTRACTS.get(address, {"name": "UNKNOWN", "generation": "unknown"})
    base = {
        "transaction_hash": log.get("transactionHash", ""),
        "block_number": hex_to_int(log.get("blockNumber")),
        "log_index": hex_to_int(log.get("logIndex")),
        "timestamp": _log_timestamp(log),
        "contract": contract_info["name"],
        "contract_generation": contract_info["generation"],
        "event_name": "OrderFilled",
        "order_hash": topics[1] if len(topics) > 1 else "",
        "maker": topic_to_address(topics[2]) if len(topics) > 2 else "",
        "taker": topic_to_address(topics[3]) if len(topics) > 3 else "",
        "maker_asset_id": "",
        "taker_asset_id": "",
        "side": None,
        "side_label": "",
        "token_id": "",
        "maker_amount_filled": 0,
        "taker_amount_filled": 0,
        "maker_fee": 0,
        "taker_fee": 0,
        "protocol_fee": 0,
        "fee": 0,
        "builder": "",
        "metadata": "",
        "raw_log": compact_json(log),
    }
    base["datetime"] = parse_timestamp(base["timestamp"])

    chunks = decode_uint_chunks(log.get("data", ""))

    if topic0 == ORDER_FILLED_TOPIC_V1:
        base.update(
            {
                "maker_asset_id": str(chunks[0] if len(chunks) > 0 else 0),
                "taker_asset_id": str(chunks[1] if len(chunks) > 1 else 0),
                "maker_amount_filled": chunks[2] if len(chunks) > 2 else 0,
                "taker_amount_filled": chunks[3] if len(chunks) > 3 else 0,
                "maker_fee": chunks[4] if len(chunks) > 4 else 0,
                "taker_fee": chunks[5] if len(chunks) > 5 else 0,
                "protocol_fee": chunks[6] if len(chunks) > 6 else 0,
            }
        )
        base["token_id"] = base["maker_asset_id"] if base["maker_asset_id"] != USDC_ASSET_ID else base["taker_asset_id"]
        return base

    if topic0 == ORDER_FILLED_TOPIC_V2:
        side = chunks[0] if len(chunks) > 0 else None
        token_id = str(chunks[1] if len(chunks) > 1 else "")
        if side == 0:
            maker_asset_id = USDC_ASSET_ID
            taker_asset_id = token_id
            side_label = "BUY"
        elif side == 1:
            maker_asset_id = token_id
            taker_asset_id = USDC_ASSET_ID
            side_label = "SELL"
        else:
            maker_asset_id = ""
            taker_asset_id = ""
            side_label = ""

        base.update(
            {
                "maker_asset_id": maker_asset_id,
                "taker_asset_id": taker_asset_id,
                "side": side,
                "side_label": side_label,
                "token_id": token_id,
                "maker_amount_filled": chunks[2] if len(chunks) > 2 else 0,
                "taker_amount_filled": chunks[3] if len(chunks) > 3 else 0,
                "fee": chunks[4] if len(chunks) > 4 else 0,
                "builder": "0x" + _data_chunk(log.get("data", ""), 5),
                "metadata": "0x" + _data_chunk(log.get("data", ""), 6),
            }
        )
        return base

    return None


def fetch_gamma_markets_for_tokens(
    token_ids: Iterable[str],
    timeout: float = 30.0,
    sleep_seconds: float = 0.2,
    transport: httpx.BaseTransport | None = None,
) -> list[dict[str, Any]]:
    markets: list[dict[str, Any]] = []
    seen_market_ids: set[str] = set()

    with httpx.Client(timeout=timeout, transport=transport) as client:
        for token_id in sorted({str(token) for token in token_ids if str(token) and str(token) != USDC_ASSET_ID}):
            response = client.get(f"{POLYMARKET_GAMMA_BASE}/markets", params={"clob_token_ids": token_id})
            response.raise_for_status()
            payload = response.json()
            rows = payload if isinstance(payload, list) else payload.get("markets", [])
            for raw_market in rows:
                market = parse_gamma_market(raw_market)
                market_id = market.get("id", "")
                if market_id and market_id not in seen_market_ids:
                    markets.append(market)
                    seen_market_ids.add(market_id)
            if sleep_seconds:
                time.sleep(sleep_seconds)

    return markets


def parse_gamma_market(raw: dict[str, Any]) -> dict[str, Any]:
    outcomes = parse_json_array(raw.get("outcomes"))
    clob_tokens = parse_json_array(raw.get("clobTokenIds"))
    outcome_prices = parse_json_array(raw.get("outcomePrices"))
    events = raw.get("events", [])
    event_info = events[0] if isinstance(events, list) and events and isinstance(events[0], dict) else {}

    return {
        "id": str(raw.get("id", "")),
        "question": raw.get("question") or raw.get("title") or "",
        "answer1": outcomes[0] if len(outcomes) > 0 else "",
        "answer2": outcomes[1] if len(outcomes) > 1 else "",
        "token1": str(clob_tokens[0]) if len(clob_tokens) > 0 else "",
        "token2": str(clob_tokens[1]) if len(clob_tokens) > 1 else "",
        "condition_id": raw.get("conditionId", ""),
        "neg_risk": bool(raw.get("negRiskAugmented", False) or raw.get("negRisk", False)),
        "slug": raw.get("slug", ""),
        "category": raw.get("category") or raw.get("groupItemTitle") or "",
        "volume": raw.get("volume", ""),
        "liquidity": raw.get("liquidity", ""),
        "start_date": raw.get("startDate", ""),
        "created_at": raw.get("createdAt", ""),
        "updated_at": raw.get("updatedAt", ""),
        "closed": bool(raw.get("closed", False)),
        "active": bool(raw.get("active", True)),
        "archived": bool(raw.get("archived", False)),
        "end_date": raw.get("endDate", ""),
        "outcome_prices": str(outcome_prices) if outcome_prices else "[]",
        "event_id": event_info.get("id", ""),
        "event_slug": event_info.get("slug", ""),
        "event_title": event_info.get("title", ""),
        "raw_payload": compact_json(raw),
    }


def build_token_mapping(markets: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    mapping: dict[str, dict[str, Any]] = {}
    for market in markets:
        for side, token_key, answer_key in (("token1", "token1", "answer1"), ("token2", "token2", "answer2")):
            token = str(market.get(token_key, ""))
            if not token:
                continue
            mapping[token] = {
                "token_id": token,
                "market_id": str(market.get("id", "")),
                "condition_id": str(market.get("condition_id", "")),
                "side": side,
                "answer": str(market.get(answer_key, "")),
                "question": str(market.get("question", ""))[:250],
                "event_id": str(market.get("event_id", "")),
                "event_slug": str(market.get("event_slug", "")),
                "event_title": str(market.get("event_title", ""))[:250],
                "slug": str(market.get("slug", "")),
                "active": market.get("active"),
                "closed": market.get("closed"),
                "end_date": market.get("end_date"),
                "raw_payload": market.get("raw_payload", "{}"),
            }
    return mapping


def token_mapping_to_frame(token_mapping: dict[str, dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(list(token_mapping.values()), columns=MARKET_MAPPING_COLUMNS)


def extract_trades(events: list[dict[str, Any]], token_mapping: dict[str, dict[str, Any]] | None = None) -> pd.DataFrame:
    token_mapping = token_mapping or {}
    trades: list[dict[str, Any]] = []
    for event in events:
        trade = _parse_trade(event, token_mapping)
        if trade:
            trades.append(trade)
    return pd.DataFrame(trades, columns=TRADES_COLUMNS)


def clean_quant_df(trades_df: pd.DataFrame) -> pd.DataFrame:
    if trades_df.empty:
        return pd.DataFrame(columns=QUANT_COLUMNS)

    df = trades_df.copy()
    df = df[df["price"].notna()]
    df = df[~df["taker"].fillna("").str.lower().isin(EXCHANGE_TAKER_ADDRESSES)]
    if df.empty:
        return pd.DataFrame(columns=QUANT_COLUMNS)

    is_token2 = df["nonusdc_side"] == "token2"
    df.loc[is_token2, "price"] = 1 - df.loc[is_token2, "price"]
    df.loc[is_token2, "nonusdc_side"] = "token1"
    return df.reset_index(drop=True).reindex(columns=QUANT_COLUMNS)


def clean_users_df(trades_df: pd.DataFrame) -> pd.DataFrame:
    if trades_df.empty:
        return pd.DataFrame(columns=USERS_COLUMNS)

    df = trades_df.copy()
    df = df[df["price"].notna()]
    df = df[~df["taker"].fillna("").str.lower().isin(EXCHANGE_TAKER_ADDRESSES)]
    if df.empty:
        return pd.DataFrame(columns=USERS_COLUMNS)

    df = df.reset_index(drop=True)
    df["_original_order"] = df.index
    common_cols = [
        "timestamp",
        "datetime",
        "block_number",
        "transaction_hash",
        "event_id",
        "market_id",
        "condition_id",
        "_original_order",
        "nonusdc_side",
        "price",
        "token_amount",
        "usd_amount",
    ]

    maker_df = df[common_cols + ["maker", "maker_direction"]].copy()
    maker_df = maker_df.rename(columns={"maker": "user", "maker_direction": "direction"})
    maker_df["role"] = "maker"
    maker_df["_sub_order"] = 0

    taker_df = df[common_cols + ["taker", "taker_direction"]].copy()
    taker_df = taker_df.rename(columns={"taker": "user", "taker_direction": "direction"})
    taker_df["role"] = "taker"
    taker_df["_sub_order"] = 1

    result = pd.concat([maker_df, taker_df], ignore_index=True)
    is_token2 = result["nonusdc_side"] == "token2"
    result.loc[is_token2, "price"] = 1 - result.loc[is_token2, "price"]

    is_sell = result["direction"] == "SELL"
    result.loc[is_sell, "token_amount"] = -result.loc[is_sell, "token_amount"]
    result["direction"] = "BUY"

    result = result.sort_values(["_original_order", "_sub_order"])
    return result[USERS_COLUMNS].reset_index(drop=True)


def collect_onchain_sample(
    lookback_blocks: int = 25,
    batch_size: int = 5,
    max_events: int = 1_000,
    rpc_urls: Iterable[str] | None = None,
) -> dict[str, Any]:
    with PolygonRpcClient(rpc_urls=rpc_urls) as rpc:
        raw = fetch_recent_orderfilled_logs(
            rpc,
            lookback_blocks=lookback_blocks,
            batch_size=batch_size,
            max_events=max_events,
        )
        events = decode_orderfilled_logs(raw["logs"], rpc=rpc)

    token_ids = sorted({event.get("token_id", "") for event in events if event.get("token_id")})
    markets = fetch_gamma_markets_for_tokens(token_ids)
    token_mapping = build_token_mapping(markets)
    trades = extract_trades(events, token_mapping)
    quant = clean_quant_df(trades)
    users = clean_users_df(trades)

    return {
        "summary": {
            **{key: value for key, value in raw.items() if key != "logs"},
            "decoded_events": len(events),
            "unique_token_ids": len(token_ids),
            "matched_markets": len(markets),
            "trades_rows": len(trades),
            "quant_rows": len(quant),
            "users_rows": len(users),
        },
        "raw_logs": raw["logs"],
        "events": pd.DataFrame(events, columns=ORDERFILLED_COLUMNS),
        "markets": markets,
        "token_mapping": token_mapping_to_frame(token_mapping),
        "trades": trades,
        "quant": quant,
        "users": users,
    }


def save_onchain_sample(sample: dict[str, Any], output_dir: str | Path) -> dict[str, Any]:
    root = Path(output_dir)
    raw_dir = root / "raw"
    processed_dir = root / "processed"
    latest_dir = root / "latest_result"
    for directory in (raw_dir, processed_dir, latest_dir):
        directory.mkdir(parents=True, exist_ok=True)

    paths: dict[str, Any] = {"raw": {}, "processed": {}, "latest_result": {}}

    paths["raw"]["summary"] = raw_dir / "summary.json"
    paths["raw"]["summary"].write_text(
        json.dumps(sample.get("summary", {}), indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )

    paths["raw"]["orderfilled_logs"] = raw_dir / "orderfilled_logs.json"
    paths["raw"]["orderfilled_logs"].write_text(
        json.dumps(sample.get("raw_logs", []), indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )

    paths["raw"]["gamma_markets"] = raw_dir / "gamma_markets.json"
    paths["raw"]["gamma_markets"].write_text(
        json.dumps(sample.get("markets", []), indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )

    table_names = ["events", "token_mapping", "trades", "quant", "users"]
    parquet_names = {
        "events": "orderfilled.parquet",
        "token_mapping": "market_token_mapping.parquet",
        "trades": "trades.parquet",
        "quant": "quant.parquet",
        "users": "users.parquet",
    }
    csv_names = {
        "events": "orderfilled.csv",
        "token_mapping": "market_token_mapping.csv",
        "trades": "trades.csv",
        "quant": "quant.csv",
        "users": "users.csv",
    }

    for name in table_names:
        frame = sample.get(name)
        if frame is None:
            frame = pd.DataFrame()
        parquet_path = processed_dir / parquet_names[name]
        csv_path = latest_dir / csv_names[name]
        frame.to_parquet(parquet_path, index=False)
        frame.tail(1_000).to_csv(csv_path, index=False)
        paths["processed"][name] = parquet_path
        paths["latest_result"][name] = csv_path

    return paths


def run_and_save_onchain_sample(
    output_dir: str | Path,
    lookback_blocks: int = 25,
    batch_size: int = 5,
    max_events: int = 1_000,
    rpc_urls: Iterable[str] | None = None,
) -> dict[str, Any]:
    sample = collect_onchain_sample(
        lookback_blocks=lookback_blocks,
        batch_size=batch_size,
        max_events=max_events,
        rpc_urls=rpc_urls,
    )
    paths = save_onchain_sample(sample, output_dir)
    return {"sample": sample, "paths": paths}


def _parse_trade(event: dict[str, Any], token_mapping: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    token_id = str(event.get("token_id") or "")
    if not token_id or token_id == USDC_ASSET_ID:
        return None

    maker_asset_id = str(event.get("maker_asset_id", ""))
    taker_asset_id = str(event.get("taker_asset_id", ""))
    maker_amount = to_float(event.get("maker_amount_filled")) or 0.0
    taker_amount = to_float(event.get("taker_amount_filled")) or 0.0

    if event.get("contract_generation") == "v2":
        side = event.get("side")
        if side == 0:
            usdc_amount_raw = maker_amount
            token_amount_raw = taker_amount
            maker_direction, taker_direction = "BUY", "SELL"
            maker_asset, taker_asset = "USDC", "token"
        elif side == 1:
            usdc_amount_raw = taker_amount
            token_amount_raw = maker_amount
            maker_direction, taker_direction = "SELL", "BUY"
            maker_asset, taker_asset = "token", "USDC"
        else:
            return None
        fee_raw = to_float(event.get("fee")) or 0.0
    else:
        if maker_asset_id != USDC_ASSET_ID:
            token_id = maker_asset_id
            usdc_amount_raw = taker_amount
            token_amount_raw = maker_amount
            maker_direction, taker_direction = "SELL", "BUY"
            maker_asset, taker_asset = "token", "USDC"
        elif taker_asset_id != USDC_ASSET_ID:
            token_id = taker_asset_id
            usdc_amount_raw = maker_amount
            token_amount_raw = taker_amount
            maker_direction, taker_direction = "BUY", "SELL"
            maker_asset, taker_asset = "USDC", "token"
        else:
            return None
        fee_raw = (
            (to_float(event.get("maker_fee")) or 0.0)
            + (to_float(event.get("taker_fee")) or 0.0)
            + (to_float(event.get("protocol_fee")) or 0.0)
        )

    usd_amount = usdc_amount_raw / USDC_DECIMALS
    token_amount = token_amount_raw / USDC_DECIMALS
    price = usd_amount / token_amount if token_amount > 0 else None
    market_info = token_mapping.get(token_id, {})
    nonusdc_side = str(market_info.get("side", ""))

    if nonusdc_side:
        if maker_asset == "token":
            maker_asset = nonusdc_side
        if taker_asset == "token":
            taker_asset = nonusdc_side

    return {
        "timestamp": event.get("timestamp"),
        "datetime": event.get("datetime"),
        "block_number": event.get("block_number"),
        "transaction_hash": event.get("transaction_hash"),
        "log_index": event.get("log_index"),
        "contract": event.get("contract", ""),
        "contract_generation": event.get("contract_generation", ""),
        "event_id": market_info.get("event_id", ""),
        "event_slug": market_info.get("event_slug", ""),
        "event_title": market_info.get("event_title", ""),
        "market_id": market_info.get("market_id", ""),
        "condition_id": market_info.get("condition_id", ""),
        "question": market_info.get("question", ""),
        "nonusdc_side": nonusdc_side,
        "answer": market_info.get("answer", ""),
        "maker": event.get("maker", ""),
        "taker": event.get("taker", ""),
        "maker_asset": maker_asset,
        "taker_asset": taker_asset,
        "maker_direction": maker_direction,
        "taker_direction": taker_direction,
        "price": round(price, 6) if price is not None else None,
        "usd_amount": round(usd_amount, 6),
        "token_amount": round(token_amount, 6),
        "fee_amount": round(fee_raw / USDC_DECIMALS, 6),
        "asset_id": token_id,
        "order_hash": event.get("order_hash", ""),
        "builder": event.get("builder", ""),
        "metadata": event.get("metadata", ""),
    }


def hex_to_int(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value, 16) if value.startswith("0x") else int(value)
    return int(value)


def topic_to_address(topic: str) -> str:
    clean = topic.lower().replace("0x", "").zfill(64)
    return "0x" + clean[-40:]


def decode_uint_chunks(data: Any) -> list[int]:
    clean = str(data or "").replace("0x", "")
    if not clean:
        return []
    chunks = [clean[index : index + 64] for index in range(0, len(clean), 64)]
    return [int(chunk.ljust(64, "0"), 16) for chunk in chunks if chunk]


def _data_chunk(data: Any, index: int) -> str:
    clean = str(data or "").replace("0x", "")
    start = index * 64
    chunk = clean[start : start + 64]
    return chunk.ljust(64, "0") if chunk else "0" * 64


def _log_timestamp(log: dict[str, Any]) -> int | None:
    for key in ("blockTimestamp", "timeStamp", "timestamp"):
        value = log.get(key)
        if value is None:
            continue
        try:
            return hex_to_int(value)
        except (TypeError, ValueError):
            parsed = parse_timestamp(value)
            if parsed:
                return int(pd.Timestamp(parsed).timestamp())
    return None
