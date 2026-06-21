from __future__ import annotations

import datetime as dt
import json
from typing import Any


def utc_now_iso() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_json_array(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            return [stripped]
        if isinstance(parsed, list):
            return parsed
        return [parsed]
    return [value]


def to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped or stripped.lower() in {"none", "null", "nan"}:
            return None
        try:
            return float(stripped)
        except ValueError:
            return None
    return None


def parse_timestamp(value: Any) -> str | None:
    if value is None:
        return None

    numeric = to_float(value)
    if numeric is not None:
        seconds = numeric / 1000 if numeric > 10_000_000_000 else numeric
        return dt.datetime.fromtimestamp(seconds, dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    if isinstance(value, str):
        candidate = value.strip()
        if not candidate:
            return None
        if candidate.endswith("Z"):
            candidate = candidate[:-1] + "+00:00"
        try:
            parsed = dt.datetime.fromisoformat(candidate)
        except ValueError:
            return value
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.UTC)
        return parsed.astimezone(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    return None


def compact_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def level_price(level: Any) -> float | None:
    if isinstance(level, dict):
        return to_float(level.get("price"))
    if isinstance(level, (list, tuple)) and level:
        return to_float(level[0])
    return None


def level_size(level: Any) -> float:
    if isinstance(level, dict):
        return to_float(level.get("size")) or 0.0
    if isinstance(level, (list, tuple)) and len(level) > 1:
        return to_float(level[1]) or 0.0
    return 0.0


def best_bid(levels: list[Any]) -> float | None:
    prices = [price for price in (level_price(level) for level in levels) if price is not None]
    return max(prices) if prices else None


def best_ask(levels: list[Any]) -> float | None:
    prices = [price for price in (level_price(level) for level in levels) if price is not None]
    return min(prices) if prices else None


def total_size(levels: list[Any]) -> float:
    return sum(level_size(level) for level in levels)


def subtract(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return left - right


def complement(price: float | None) -> float | None:
    if price is None:
        return None
    return 1.0 - price
