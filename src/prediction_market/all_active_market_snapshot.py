from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import httpx
import pandas as pd

from .all_open_event_match import (
    _get_json_with_retry,
    fetch_polymarket_open_events,
    is_kalshi_sports_event,
    is_polymarket_sports_event,
)
from .collectors import KALSHI_BASE, POLYMARKET_GAMMA_BASE
from .fifa_arbitrage import (
    _polymarket_event_context,
    build_approval_candidates,
    normalize_market_candidates,
    write_latest_processed_table,
)
from .utils import utc_now_iso


DEFAULT_SOURCE = "data/cross_sports_arbitrage"
EVENT_TABLE = "all_open_events"
RAW_TABLE = "all_active_markets"
CANDIDATE_TABLE = "all_active_market_candidates"
APPROVAL_TABLE = "all_active_approval_candidates"
SUMMARY_TABLE = "all_active_market_snapshot_summary"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch and store all active/open Polymarket and Kalshi markets.")
    parser.add_argument("--source", default=DEFAULT_SOURCE)
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--max-polymarket-markets", type=int, default=0)
    parser.add_argument("--max-kalshi-markets", type=int, default=0)
    parser.add_argument("--max-kalshi-events", type=int, default=0)
    parser.add_argument("--polymarket-page-size", type=int, default=500)
    parser.add_argument("--kalshi-page-size", type=int, default=1000)
    parser.add_argument("--kalshi-event-market-workers", type=int, default=8)
    parser.add_argument("--sports-only", action="store_true", help="Restrict active market snapshots to venue-tagged sports events.")
    parser.add_argument("--sleep-seconds", type=float, default=0.0)
    args = parser.parse_args(argv)

    source: str | Path = args.source if str(args.source).startswith("gs://") else Path(args.source)
    output_arg = args.output_dir or args.source
    output_dir: str | Path = output_arg if str(output_arg).startswith("gs://") else Path(output_arg)

    run_id = f"all-active-markets-{utc_now_iso().replace(':', '').replace('-', '')}"
    retrieved_at = utc_now_iso()
    with httpx.Client(timeout=httpx.Timeout(30.0, connect=10.0), headers={"User-Agent": "poly-kalshi-all-active-market-snapshot"}) as client:
        polymarket_markets = fetch_all_active_polymarket_markets(
            client,
            max_markets=args.max_polymarket_markets,
            page_size=args.polymarket_page_size,
            sports_only=args.sports_only,
            sleep_seconds=args.sleep_seconds,
        )
        kalshi_markets = fetch_all_open_kalshi_markets(
            client,
            max_markets=args.max_kalshi_markets,
            max_events=args.max_kalshi_events,
            page_size=args.kalshi_page_size,
            event_market_workers=args.kalshi_event_market_workers,
            sports_only=args.sports_only,
            sleep_seconds=args.sleep_seconds,
        )

    raw = _raw_market_frame(polymarket_markets, kalshi_markets, run_id=run_id, retrieved_at=retrieved_at)
    candidates = normalize_market_candidates(
        polymarket_markets,
        kalshi_markets,
        run_id=run_id,
        retrieved_at=retrieved_at,
        keywords=(),
    )
    approval_candidates = build_approval_candidates(candidates)
    summary = _summary_frame(raw, candidates, approval_candidates, sports_only=args.sports_only)

    write_latest_processed_table(RAW_TABLE, raw, output_dir)
    write_latest_processed_table(CANDIDATE_TABLE, candidates, output_dir)
    write_latest_processed_table(APPROVAL_TABLE, approval_candidates, output_dir)
    write_latest_processed_table(SUMMARY_TABLE, summary, output_dir)
    print(summary.to_string(index=False), flush=True)
    return 0


def fetch_all_active_polymarket_markets(
    client: httpx.Client,
    *,
    max_markets: int = 0,
    page_size: int = 500,
    sports_only: bool = False,
    sleep_seconds: float = 0.0,
) -> list[dict[str, Any]]:
    markets: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    inspected = 0
    cursor = ""
    page = 0
    page_size = max(1, min(int(page_size), 100))
    sports_event_ids = fetch_polymarket_sports_event_ids(client) if sports_only else set()
    if sports_only:
        print(f"polymarket sports event ids: {len(sports_event_ids):,}", flush=True)
    while max_markets <= 0 or inspected < max_markets:
        limit = _page_limit(page_size, inspected, max_markets)
        if limit <= 0:
            break
        params: dict[str, Any] = {
            "active": "true",
            "closed": "false",
            "limit": limit,
            "order": "volume",
            "ascending": "false",
        }
        if cursor:
            params["after_cursor"] = cursor
        payload = _get_json_with_retry(
            client,
            f"{POLYMARKET_GAMMA_BASE}/markets/keyset",
            params=params,
        )
        batch = payload if isinstance(payload, list) else payload.get("markets", []) if isinstance(payload, dict) else []
        if not batch:
            break
        page += 1
        inspected += len(batch)
        for market in batch:
            if not isinstance(market, dict):
                continue
            market_id = str(market.get("conditionId") or market.get("id") or "")
            if not market_id or market_id in seen_ids:
                continue
            if market.get("active") is not True or market.get("closed") is True:
                continue
            enriched = _enrich_polymarket_market_with_event_context(market)
            if sports_only and not is_polymarket_sports_market(enriched, sports_event_ids=sports_event_ids):
                continue
            markets.append(enriched)
            seen_ids.add(market_id)
        cursor = str(payload.get("next_cursor") or payload.get("cursor") or "") if isinstance(payload, dict) else ""
        if page <= 3 or page % 20 == 0:
            print(f"polymarket markets: pages={page:,} inspected={inspected:,} unique={len(markets):,}", flush=True)
        if not cursor or len(batch) < limit:
            break
        if sleep_seconds:
            time.sleep(sleep_seconds)
    return markets


def fetch_all_open_kalshi_markets(
    client: httpx.Client,
    *,
    max_markets: int = 0,
    max_events: int = 0,
    page_size: int = 1000,
    event_market_workers: int = 8,
    sports_only: bool = False,
    sleep_seconds: float = 0.0,
) -> list[dict[str, Any]]:
    markets: list[dict[str, Any]] = []
    seen_tickers: set[str] = set()
    inspected_markets = 0
    skipped_combos = 0
    events_with_markets = 0
    events = fetch_all_open_kalshi_events(
        client,
        page_size=min(page_size, 200),
        max_events=max_events,
        sports_only=sports_only,
        sleep_seconds=sleep_seconds,
    )
    market_page_size = max(1, min(int(page_size), 1000))
    event_market_workers = max(1, int(event_market_workers))
    if max_markets <= 0 and event_market_workers > 1:
        return _fetch_all_open_kalshi_markets_parallel(
            client,
            events,
            page_size=market_page_size,
            event_market_workers=event_market_workers,
            sleep_seconds=sleep_seconds,
        )

    for event_index, event in enumerate(events, start=1):
        if max_markets > 0 and len(markets) >= max_markets:
            break
        event_ticker = str(event.get("event_ticker") or event.get("ticker") or "")
        if not event_ticker:
            continue
        event_markets = fetch_open_kalshi_event_markets(
            client,
            event_ticker,
            page_size=market_page_size,
            max_markets=0 if max_markets <= 0 else max_markets - len(markets),
            sleep_seconds=sleep_seconds,
        )
        if event_markets:
            events_with_markets += 1
        context = _kalshi_event_context(event)
        for market in event_markets:
            inspected_markets += 1
            if not isinstance(market, dict):
                continue
            ticker = str(market.get("ticker") or "")
            if not ticker or ticker in seen_tickers:
                continue
            if is_kalshi_combo_market(market):
                skipped_combos += 1
                seen_tickers.add(ticker)
                continue
            markets.append({**market, **context})
            seen_tickers.add(ticker)
            if max_markets > 0 and len(markets) >= max_markets:
                break
        _log_kalshi_event_market_progress(
            event_index=event_index,
            event_total=len(events),
            events_with_markets=events_with_markets,
            inspected_markets=inspected_markets,
            standalone_unique=len(markets),
            skipped_combos=skipped_combos,
        )
    return markets


def _fetch_all_open_kalshi_markets_parallel(
    client: httpx.Client,
    events: list[dict[str, Any]],
    *,
    page_size: int,
    event_market_workers: int,
    sleep_seconds: float,
) -> list[dict[str, Any]]:
    markets: list[dict[str, Any]] = []
    seen_tickers: set[str] = set()
    inspected_markets = 0
    skipped_combos = 0
    events_with_markets = 0
    completed_events = 0

    def fetch_event(indexed_event: tuple[int, dict[str, Any]]) -> tuple[int, dict[str, Any], list[dict[str, Any]]]:
        event_index, event = indexed_event
        event_ticker = str(event.get("event_ticker") or event.get("ticker") or "")
        if not event_ticker:
            return event_index, event, []
        event_markets = fetch_open_kalshi_event_markets(
            client,
            event_ticker,
            page_size=page_size,
            max_markets=0,
            sleep_seconds=sleep_seconds,
        )
        return event_index, event, event_markets

    with ThreadPoolExecutor(max_workers=event_market_workers) as executor:
        indexed_events = list(enumerate(events, start=1))
        for _, event, event_markets in executor.map(fetch_event, indexed_events):
            completed_events += 1
            if event_markets:
                events_with_markets += 1
            context = _kalshi_event_context(event)
            for market in event_markets:
                inspected_markets += 1
                if not isinstance(market, dict):
                    continue
                ticker = str(market.get("ticker") or "")
                if not ticker or ticker in seen_tickers:
                    continue
                if is_kalshi_combo_market(market):
                    skipped_combos += 1
                    seen_tickers.add(ticker)
                    continue
                markets.append({**market, **context})
                seen_tickers.add(ticker)
            _log_kalshi_event_market_progress(
                event_index=completed_events,
                event_total=len(events),
                events_with_markets=events_with_markets,
                inspected_markets=inspected_markets,
                standalone_unique=len(markets),
                skipped_combos=skipped_combos,
            )
    return markets


def _log_kalshi_event_market_progress(
    *,
    event_index: int,
    event_total: int,
    events_with_markets: int,
    inspected_markets: int,
    standalone_unique: int,
    skipped_combos: int,
) -> None:
    if event_index <= 3 or event_index % 250 == 0 or event_index == event_total:
        print(
            "kalshi event markets: "
            f"events={event_index:,}/{event_total:,} "
            f"events_with_markets={events_with_markets:,} "
            f"inspected={inspected_markets:,} "
            f"standalone_unique={standalone_unique:,} "
            f"skipped_combos={skipped_combos:,}",
            flush=True,
        )


def fetch_all_open_kalshi_events(
    client: httpx.Client,
    *,
    page_size: int = 200,
    max_events: int = 0,
    sports_only: bool = False,
    sleep_seconds: float = 0.0,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    seen_tickers: set[str] = set()
    cursor = ""
    page = 0
    limit = max(1, min(int(page_size), 200))
    while max_events <= 0 or len(events) < max_events:
        request_limit = _page_limit(limit, len(events), max_events)
        if request_limit <= 0:
            break
        params: dict[str, Any] = {"status": "open", "limit": request_limit}
        if cursor:
            params["cursor"] = cursor
        payload = _get_json_with_retry(client, f"{KALSHI_BASE}/events", params=params)
        batch = payload.get("events", []) if isinstance(payload, dict) else []
        if not batch:
            break
        page += 1
        for event in batch:
            if not isinstance(event, dict):
                continue
            ticker = str(event.get("event_ticker") or event.get("ticker") or "")
            if not ticker or ticker in seen_tickers:
                continue
            if sports_only and not is_kalshi_sports_event(event):
                continue
            events.append(event)
            seen_tickers.add(ticker)
            if max_events > 0 and len(events) >= max_events:
                break
        cursor = str(payload.get("cursor") or "") if isinstance(payload, dict) else ""
        if page <= 3 or page % 20 == 0:
            print(f"kalshi events: pages={page:,} unique={len(events):,}", flush=True)
        if not cursor or len(batch) < request_limit:
            break
        if sleep_seconds:
            time.sleep(sleep_seconds)
    return events


def fetch_open_kalshi_event_markets(
    client: httpx.Client,
    event_ticker: str,
    *,
    page_size: int = 1000,
    max_markets: int = 0,
    sleep_seconds: float = 0.0,
) -> list[dict[str, Any]]:
    markets: list[dict[str, Any]] = []
    cursor = ""
    page = 0
    limit = max(1, min(int(page_size), 1000))
    while max_markets <= 0 or len(markets) < max_markets:
        request_limit = _page_limit(limit, len(markets), max_markets)
        if request_limit <= 0:
            break
        params: dict[str, Any] = {"status": "open", "event_ticker": event_ticker, "limit": request_limit}
        if cursor:
            params["cursor"] = cursor
        payload = _get_json_with_retry(client, f"{KALSHI_BASE}/markets", params=params)
        batch = payload.get("markets", []) if isinstance(payload, dict) else []
        if not batch:
            break
        page += 1
        markets.extend(market for market in batch if isinstance(market, dict))
        cursor = str(payload.get("cursor") or "") if isinstance(payload, dict) else ""
        if not cursor or len(batch) < request_limit:
            break
        if sleep_seconds:
            time.sleep(sleep_seconds)
    return markets


def is_kalshi_combo_market(market: dict[str, Any]) -> bool:
    if market.get("mve_collection_ticker") or market.get("mve_selected_legs"):
        return True
    custom_strike = market.get("custom_strike")
    if isinstance(custom_strike, dict):
        custom_keys = {str(key).lower() for key in custom_strike}
        if {"associated events", "associated markets", "associated market sides"} & custom_keys:
            return True
    elif isinstance(custom_strike, str):
        lowered = custom_strike.lower()
        if "associated events" in lowered or "associated markets" in lowered:
            return True
    return False


def fetch_polymarket_sports_event_ids(client: httpx.Client) -> set[str]:
    return {
        str(event.get("id") or event.get("slug") or "")
        for event in fetch_polymarket_open_events(client, sports_only=True)
        if str(event.get("id") or event.get("slug") or "")
    }


def is_polymarket_sports_market(market: dict[str, Any], *, sports_event_ids: set[str] | None = None) -> bool:
    if sports_event_ids:
        event_id = str(market.get("_event_context_id") or "")
        if event_id and event_id in sports_event_ids:
            return True
        events = market.get("events")
        if isinstance(events, list):
            for event in events:
                if isinstance(event, dict) and str(event.get("id") or event.get("slug") or "") in sports_event_ids:
                    return True
    payload = market.get("_event_context_payload")
    if isinstance(payload, dict) and is_polymarket_sports_event(payload):
        return True
    events = market.get("events")
    if isinstance(events, list):
        return any(isinstance(event, dict) and is_polymarket_sports_event(event) for event in events)
    category = str(market.get("category") or "").replace("|", " ")
    return "sports" in {token.casefold() for token in category.split()}


def _enrich_polymarket_market_with_event_context(market: dict[str, Any]) -> dict[str, Any]:
    events = market.get("events")
    if isinstance(events, list) and events and isinstance(events[0], dict):
        return {**market, **_polymarket_event_context(events[0])}
    return market


def _kalshi_event_context(event: dict[str, Any]) -> dict[str, Any]:
    event_ticker = str(event.get("event_ticker") or event.get("ticker") or "")
    return {
        "_event_context_title": str(event.get("title") or ""),
        "_event_context_ticker": event_ticker,
        "_event_context_payload": event,
    }


def _kalshi_event_contexts(events: pd.DataFrame) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    if events.empty or "venue" not in events.columns:
        return output
    for _, row in events[events["venue"].astype(str).eq("kalshi")].iterrows():
        event_ticker = str(row.get("event_id") or row.get("event_ticker") or "")
        if not event_ticker:
            continue
        payload = _json_payload(row.get("raw_event_payload"))
        output[event_ticker] = {
            "_event_context_title": str(row.get("title") or payload.get("title") or ""),
            "_event_context_ticker": event_ticker,
            "_event_context_payload": payload or {"event_ticker": event_ticker, "title": str(row.get("title") or "")},
        }
    return output


def _raw_market_frame(
    polymarket_markets: list[dict[str, Any]],
    kalshi_markets: list[dict[str, Any]],
    *,
    run_id: str,
    retrieved_at: str,
) -> pd.DataFrame:
    rows: list[dict[str, str]] = []
    for venue, markets in (("polymarket", polymarket_markets), ("kalshi", kalshi_markets)):
        for market in markets:
            market_id = str(market.get("conditionId") or market.get("id") or market.get("ticker") or "")
            event_key = str(market.get("_event_context_id") or market.get("_event_context_ticker") or market.get("event_ticker") or "")
            rows.append(
                {
                    "run_id": run_id,
                    "retrieved_at": retrieved_at,
                    "venue": venue,
                    "market_id": market_id,
                    "ticker_or_slug": str(market.get("slug") or market.get("ticker") or ""),
                    "event_key": event_key,
                    "title": str(market.get("question") or market.get("title") or ""),
                    "status": str(market.get("status") or ("active" if market.get("active") is True and market.get("closed") is not True else "")),
                    "raw_payload": json.dumps(market, sort_keys=True, separators=(",", ":"), default=str),
                }
            )
    return pd.DataFrame(rows)


def _summary_frame(raw: pd.DataFrame, candidates: pd.DataFrame, approval_candidates: pd.DataFrame, *, sports_only: bool = False) -> pd.DataFrame:
    rows = [
        _metric("sports_only", int(bool(sports_only))),
        _metric("raw_market_rows", len(raw)),
        _metric("raw_polymarket_market_rows", int((raw["venue"] == "polymarket").sum()) if not raw.empty else 0),
        _metric("raw_kalshi_market_rows", int((raw["venue"] == "kalshi").sum()) if not raw.empty else 0),
        _metric("raw_unique_polymarket_events", raw[raw["venue"] == "polymarket"]["event_key"].nunique() if not raw.empty else 0),
        _metric("raw_unique_kalshi_events", raw[raw["venue"] == "kalshi"]["event_key"].nunique() if not raw.empty else 0),
        _metric("candidate_rows", len(candidates)),
        _metric("approval_candidate_rows", len(approval_candidates)),
    ]
    if not approval_candidates.empty:
        for venue, count in approval_candidates["venue"].value_counts().items():
            rows.append(_metric(f"approval_candidate_rows:{venue}", int(count)))
        for market_type, count in approval_candidates["market_type"].value_counts().head(20).items():
            rows.append(_metric(f"market_type:{market_type}", int(count)))
    return pd.DataFrame(rows)


def _metric(metric: str, value: int | float) -> dict[str, Any]:
    return {"metric": metric, "value": value}


def _page_limit(page_size: int, inspected: int, max_items: int) -> int:
    page_size = max(1, int(page_size))
    if max_items <= 0:
        return page_size
    return max(0, min(page_size, max_items - inspected))


def _json_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _read_table(path_without_suffix: Path) -> pd.DataFrame:
    parquet = path_without_suffix.with_suffix(".parquet")
    csv = path_without_suffix.with_suffix(".csv")
    if parquet.exists():
        return pd.read_parquet(parquet)
    if csv.exists():
        return pd.read_csv(csv, dtype=str, keep_default_na=False)
    raise FileNotFoundError(f"Expected {parquet} or {csv}")


def _read_optional_table(path_without_suffix: Path) -> pd.DataFrame:
    try:
        return _read_table(path_without_suffix)
    except FileNotFoundError:
        return pd.DataFrame()


if __name__ == "__main__":
    raise SystemExit(main())
