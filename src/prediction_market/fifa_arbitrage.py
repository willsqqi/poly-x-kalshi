from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pandas as pd
from rapidfuzz import fuzz

from .collectors import KALSHI_BASE, POLYMARKET_CLOB_BASE, POLYMARKET_GAMMA_BASE
from .utils import best_ask, best_bid, compact_json, parse_json_array, parse_timestamp, to_float, total_size, utc_now_iso

FIFA_KEYWORDS = ("world cup", "world soccer cup", "world soccer", "worldcup", "fifa")
POLYMARKET_EVENT_TAG_SLUGS = ("soccer", "world-cup")
KALSHI_FIFA_SERIES_TICKERS = ("KXWCGAME", "KXWCHOST")
DEFAULT_MAPPING_PATH = "config/fifa_market_mappings.csv"
DEFAULT_OUTPUT_DIR = "data/fifa_arbitrage"
DEFAULT_INTERVAL_SECONDS = 60.0
DEFAULT_MIN_NET_EDGE = 0.02
DEFAULT_SLIPPAGE_BUFFER_PER_LEG = 0.005
DEFAULT_FEE_BUFFER_TOTAL = 0.01
DEFAULT_MIN_DEPTH_PER_LEG = 10.0
DEFAULT_TIMEOUT_SECONDS = 30.0
RETRYABLE_STATUSES = {429, 500, 502, 503, 504}

CANDIDATE_COLUMNS = [
    "run_id",
    "retrieved_at",
    "venue",
    "market_id",
    "ticker_or_slug",
    "title",
    "subtitle",
    "category",
    "status",
    "close_time",
    "outcomes",
    "yes_token_id",
    "no_token_id",
    "rules_text",
    "keyword_hits",
    "raw_payload",
]

APPROVAL_CANDIDATE_COLUMNS = [
    *CANDIDATE_COLUMNS,
    "market_type",
    "event_title",
    "event_date",
    "event_match_key",
    "outcome_label",
    "subject",
    "event_year",
    "event_timeframe",
    "settlement_summary",
    "liquidity_hint",
    "approval_notes",
]

SUGGESTED_MAPPING_COLUMNS = [
    "run_id",
    "suggested_mapping_id",
    "match_score",
    "suggestion_status",
    "market_type",
    "review_notes",
    "mapping_id",
    "event_name",
    "proposition",
    "polymarket_event_title",
    "kalshi_event_title",
    "event_match_key",
    "outcome_label",
    "polymarket_market_id",
    "polymarket_slug",
    "polymarket_title",
    "polymarket_yes_token_id",
    "polymarket_no_token_id",
    "polymarket_yes_outcome",
    "polymarket_no_outcome",
    "polymarket_outcomes",
    "polymarket_settlement_summary",
    "kalshi_ticker",
    "kalshi_title",
    "kalshi_outcomes",
    "kalshi_settlement_summary",
    "draw_handling",
    "extra_time_handling",
    "penalties_handling",
    "settlement_notes",
]

MAPPING_COLUMNS = [
    "mapping_id",
    "status",
    "event_name",
    "proposition",
    "polymarket_market_id",
    "polymarket_slug",
    "polymarket_yes_token_id",
    "polymarket_no_token_id",
    "polymarket_yes_outcome",
    "polymarket_no_outcome",
    "kalshi_ticker",
    "draw_handling",
    "extra_time_handling",
    "penalties_handling",
    "settlement_notes",
    "reviewer",
    "reviewed_at",
    "notes",
]

APPROVED_MAPPING_REQUIRED_COLUMNS = [
    "mapping_id",
    "event_name",
    "proposition",
    "polymarket_yes_token_id",
    "polymarket_no_token_id",
    "kalshi_ticker",
    "draw_handling",
    "extra_time_handling",
    "penalties_handling",
    "settlement_notes",
    "reviewer",
    "reviewed_at",
]

ORDERBOOK_COLUMNS = [
    "run_id",
    "retrieved_at",
    "mapping_id",
    "venue",
    "market_id",
    "yes_bid",
    "yes_ask",
    "no_bid",
    "no_ask",
    "yes_bid_depth",
    "yes_ask_depth",
    "no_bid_depth",
    "no_ask_depth",
    "raw_orderbook",
    "error",
]

ALERT_COLUMNS = [
    "run_id",
    "detected_at",
    "mapping_id",
    "event_name",
    "proposition",
    "direction",
    "leg1_venue",
    "leg1_outcome",
    "leg1_ask",
    "leg1_depth",
    "leg2_venue",
    "leg2_outcome",
    "leg2_ask",
    "leg2_depth",
    "gross_cost",
    "buffered_cost",
    "net_edge",
    "min_depth",
    "min_net_edge",
    "slippage_buffer_per_leg",
    "fee_buffer_total",
    "is_alert",
    "exclusion_reason",
    "draw_handling",
    "extra_time_handling",
    "penalties_handling",
    "settlement_notes",
]

SIGNAL_COLUMNS = [
    "run_id",
    "detected_at",
    "mapping_id",
    "event_name",
    "proposition",
    "direction",
    "signal",
    "is_alert",
    "price_available",
    "liquidity_ok",
    "threshold_ok",
    "gross_cost",
    "buffered_cost",
    "net_edge",
    "min_depth",
    "min_net_edge",
    "exclusion_reason",
    "leg1_venue",
    "leg1_outcome",
    "leg1_ask",
    "leg1_depth",
    "leg2_venue",
    "leg2_outcome",
    "leg2_ask",
    "leg2_depth",
    "draw_handling",
    "extra_time_handling",
    "penalties_handling",
    "settlement_notes",
]

RUN_COLUMNS = [
    "run_id",
    "started_at",
    "finished_at",
    "status",
    "candidate_count",
    "approved_mapping_count",
    "orderbook_count",
    "alert_count",
    "error",
]

MAPPING_SNAPSHOT_COLUMNS = [*MAPPING_COLUMNS, "is_approved", "validation_errors"]
DISCOVERY_REVIEW_TABLES = {"venue_market_candidates", "approval_candidates", "suggested_mappings"}


def fifa_keyword_hits(value: Any) -> list[str]:
    text = _search_text(value)
    return [keyword for keyword in FIFA_KEYWORDS if keyword in text]


def is_fifa_market(value: Any) -> bool:
    return bool(fifa_keyword_hits(value))


def fetch_polymarket_fifa_markets(
    client: httpx.Client,
    max_markets: int = 1_000,
    page_size: int = 500,
    sleep_seconds: float = 0.0,
) -> list[dict[str, Any]]:
    markets: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    inspected = 0
    offset = 0
    while inspected < max_markets:
        limit = min(page_size, max_markets - inspected)
        payload = request_json_with_retry(
            client,
            f"{POLYMARKET_GAMMA_BASE}/markets",
            params={
                "active": "true",
                "closed": "false",
                "limit": limit,
                "offset": offset,
                "order": "volume",
                "ascending": "false",
            },
        )
        batch = _markets_from_payload(payload)
        if not batch:
            break
        inspected += len(batch)
        offset += len(batch)
        for market in batch:
            market_id = str(market.get("conditionId") or market.get("id") or "")
            if market_id in seen_ids:
                continue
            if not _has_two_polymarket_tokens(market):
                continue
            if market.get("active") is not True or market.get("closed") is True:
                continue
            if is_fifa_market(_polymarket_search_payload(market)):
                markets.append(market)
                seen_ids.add(market_id)
        if len(batch) < limit:
            break
        if sleep_seconds:
            time.sleep(sleep_seconds)

    for event in fetch_polymarket_fifa_events(client, max_events=max_markets, page_size=page_size, sleep_seconds=sleep_seconds):
        event_context = _polymarket_event_context(event)
        for market in event.get("markets", []):
            if not isinstance(market, dict):
                continue
            market_id = str(market.get("conditionId") or market.get("id") or "")
            if market_id in seen_ids:
                continue
            if not _has_two_polymarket_tokens(market):
                continue
            if market.get("active") is not True or market.get("closed") is True:
                continue
            enriched_market = {**market, **event_context}
            if is_fifa_market(_polymarket_search_payload(enriched_market)):
                markets.append(enriched_market)
                seen_ids.add(market_id)
    return markets


def fetch_polymarket_fifa_events(
    client: httpx.Client,
    max_events: int = 1_000,
    page_size: int = 200,
    sleep_seconds: float = 0.0,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    seen_slugs: set[str] = set()
    for tag_slug in POLYMARKET_EVENT_TAG_SLUGS:
        inspected = 0
        offset = 0
        while inspected < max_events:
            limit = min(page_size, max_events - inspected)
            payload = request_json_with_retry(
                client,
                f"{POLYMARKET_GAMMA_BASE}/events",
                params={
                    "active": "true",
                    "closed": "false",
                    "limit": limit,
                    "offset": offset,
                    "tag_slug": tag_slug,
                    "order": "volume",
                    "ascending": "false",
                },
            )
            batch = _events_from_payload(payload)
            if not batch:
                break
            inspected += len(batch)
            offset += len(batch)
            for event in batch:
                slug = str(event.get("slug") or event.get("ticker") or event.get("id") or "")
                if not slug or slug in seen_slugs:
                    continue
                if event.get("active") is not True or event.get("closed") is True:
                    continue
                if is_fifa_market(_polymarket_event_search_payload(event)):
                    events.append(event)
                    seen_slugs.add(slug)
            if len(batch) < limit:
                break
            if sleep_seconds:
                time.sleep(sleep_seconds)
    return events


def fetch_kalshi_fifa_markets(
    client: httpx.Client,
    max_markets: int = 1_000,
    page_size: int = 200,
    sleep_seconds: float = 0.0,
) -> list[dict[str, Any]]:
    markets: list[dict[str, Any]] = []
    seen_tickers: set[str] = set()
    inspected = 0
    cursor = ""
    while inspected < max_markets:
        limit = min(page_size, max_markets - inspected)
        params: dict[str, Any] = {"status": "open", "limit": limit}
        if cursor:
            params["cursor"] = cursor
        payload = request_json_with_retry(client, f"{KALSHI_BASE}/markets", params=params)
        batch = _markets_from_payload(payload)
        if not batch:
            break
        inspected += len(batch)
        for market in batch:
            ticker = str(market.get("ticker") or "")
            if not ticker or ticker in seen_tickers:
                continue
            if is_fifa_market(_kalshi_search_payload(market)):
                markets.append(market)
                seen_tickers.add(ticker)
        cursor = str(payload.get("cursor") or "") if isinstance(payload, dict) else ""
        if not cursor or len(batch) < limit:
            break
        if sleep_seconds:
            time.sleep(sleep_seconds)

    for event in fetch_kalshi_fifa_events(client, max_events=max_markets, page_size=min(page_size, 200), sleep_seconds=sleep_seconds):
        event_ticker = str(event.get("event_ticker") or "")
        if not event_ticker:
            continue
        payload = request_json_with_retry(client, f"{KALSHI_BASE}/markets", params={"status": "open", "event_ticker": event_ticker, "limit": page_size})
        for market in _markets_from_payload(payload):
            ticker = str(market.get("ticker") or "")
            if not ticker or ticker in seen_tickers:
                continue
            market["_event_context_title"] = event.get("title", "")
            market["_event_context_ticker"] = event_ticker
            market["_event_context_payload"] = event
            markets.append(market)
            seen_tickers.add(ticker)
    return markets


def fetch_kalshi_fifa_events(
    client: httpx.Client,
    max_events: int = 1_000,
    page_size: int = 200,
    sleep_seconds: float = 0.0,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    seen_event_tickers: set[str] = set()
    query_templates: list[dict[str, Any]] = [{"status": "open"}]
    query_templates.extend({"status": "open", "series_ticker": series_ticker} for series_ticker in KALSHI_FIFA_SERIES_TICKERS)
    for query_template in query_templates:
        inspected = 0
        cursor = ""
        while inspected < max_events:
            limit = min(page_size, max_events - inspected)
            params: dict[str, Any] = {**query_template, "limit": limit}
            if cursor:
                params["cursor"] = cursor
            payload = request_json_with_retry(client, f"{KALSHI_BASE}/events", params=params)
            batch = payload.get("events", []) if isinstance(payload, dict) else []
            if not batch:
                break
            inspected += len(batch)
            for event in batch:
                if not isinstance(event, dict):
                    continue
                event_ticker = str(event.get("event_ticker") or "")
                if not event_ticker or event_ticker in seen_event_tickers:
                    continue
                if is_fifa_market(_kalshi_event_search_payload(event)) or str(event.get("series_ticker") or "") in KALSHI_FIFA_SERIES_TICKERS:
                    events.append(event)
                    seen_event_tickers.add(event_ticker)
            cursor = str(payload.get("cursor") or "") if isinstance(payload, dict) else ""
            if not cursor or len(batch) < limit:
                break
            if sleep_seconds:
                time.sleep(sleep_seconds)
    return events


def normalize_fifa_candidates(
    polymarket_markets: list[dict[str, Any]],
    kalshi_markets: list[dict[str, Any]],
    run_id: str,
    retrieved_at: str | None = None,
) -> pd.DataFrame:
    retrieved_at = retrieved_at or utc_now_iso()
    rows: list[dict[str, Any]] = []
    for market in polymarket_markets:
        outcomes = parse_json_array(market.get("outcomes"))
        token_ids = parse_json_array(market.get("clobTokenIds"))
        rows.append(
            {
                "run_id": run_id,
                "retrieved_at": retrieved_at,
                "venue": "polymarket",
                "market_id": str(market.get("conditionId") or market.get("id") or ""),
                "ticker_or_slug": market.get("slug", ""),
                "title": market.get("question") or market.get("title", ""),
                "subtitle": market.get("description", ""),
                "category": _polymarket_category(market),
                "status": "active" if market.get("active") is True and market.get("closed") is not True else "inactive",
                "close_time": parse_timestamp(market.get("endDate") or market.get("endDateIso")),
                "outcomes": compact_json(outcomes),
                "yes_token_id": str(token_ids[0]) if len(token_ids) > 0 else "",
                "no_token_id": str(token_ids[1]) if len(token_ids) > 1 else "",
                "rules_text": _polymarket_rules_text(market),
                "keyword_hits": ",".join(fifa_keyword_hits(_polymarket_search_payload(market))),
                "raw_payload": compact_json(market),
            }
        )

    for market in kalshi_markets:
        rows.append(
            {
                "run_id": run_id,
                "retrieved_at": retrieved_at,
                "venue": "kalshi",
                "market_id": str(market.get("ticker") or ""),
                "ticker_or_slug": str(market.get("ticker") or ""),
                "title": market.get("title", ""),
                "subtitle": market.get("subtitle") or market.get("yes_sub_title") or "",
                "category": market.get("category", ""),
                "status": market.get("status", ""),
                "close_time": parse_timestamp(market.get("close_time")),
                "outcomes": compact_json(_kalshi_outcomes(market)),
                "yes_token_id": "",
                "no_token_id": "",
                "rules_text": _kalshi_rules_text(market),
                "keyword_hits": ",".join(fifa_keyword_hits(_kalshi_search_payload(market))),
                "raw_payload": compact_json(market),
            }
        )
    return pd.DataFrame(rows, columns=CANDIDATE_COLUMNS)


def build_approval_candidates(candidates: pd.DataFrame) -> pd.DataFrame:
    if candidates.empty:
        return pd.DataFrame(columns=APPROVAL_CANDIDATE_COLUMNS)
    rows: list[dict[str, Any]] = []
    for _, row in candidates.iterrows():
        market_type = classify_market_type(row)
        event_title = extract_event_title(row)
        event_date = extract_event_date(row)
        outcome_label = extract_outcome_label(row)
        subject = extract_market_subject(row)
        settlement_summary = summarize_settlement(row)
        rows.append(
            {
                **{column: row.get(column, "") for column in CANDIDATE_COLUMNS},
                "market_type": market_type,
                "event_title": event_title,
                "event_date": event_date,
                "event_match_key": extract_event_match_key(row, event_title=event_title, event_date=event_date),
                "outcome_label": outcome_label,
                "subject": subject,
                "event_year": extract_event_year(row),
                "event_timeframe": infer_event_timeframe(row),
                "settlement_summary": settlement_summary,
                "liquidity_hint": extract_liquidity_hint(row),
                "approval_notes": approval_notes_for_market_type(market_type),
            }
        )
    return pd.DataFrame(rows, columns=APPROVAL_CANDIDATE_COLUMNS)


def suggest_manual_mappings(approval_candidates: pd.DataFrame, min_score: float = 72.0) -> pd.DataFrame:
    if approval_candidates.empty:
        return pd.DataFrame(columns=SUGGESTED_MAPPING_COLUMNS)
    polymarket = approval_candidates[approval_candidates["venue"] == "polymarket"]
    kalshi = approval_candidates[approval_candidates["venue"] == "kalshi"]
    rows: list[dict[str, Any]] = []
    for _, pm in polymarket.iterrows():
        for _, ks in kalshi.iterrows():
            if not _candidate_types_compatible(pm, ks):
                continue
            score = candidate_match_score(pm, ks)
            if score < min_score:
                continue
            rows.append(_suggested_mapping_row(pm, ks, score))
    frame = pd.DataFrame(rows, columns=SUGGESTED_MAPPING_COLUMNS)
    if frame.empty:
        return frame
    return frame.sort_values(["match_score", "market_type"], ascending=[False, True]).reset_index(drop=True)


def classify_market_type(row: pd.Series | dict[str, Any]) -> str:
    text = _candidate_text(row)
    if "end in a draw" in text or " tie" in text or "winner?" in text:
        return "match_winner"
    if any(term in text for term in ("win?", " win on ", " wins the ")):
        return "match_winner"
    if "both teams to score" in text or "btts" in text:
        return "both_teams_score"
    if any(term in text for term in ("spread", "handicap", "(-", "(+")):
        return "spread"
    if "join" in text:
        return "transfer"
    if "highest-scoring" in text or "highest scoring" in text:
        return "team_stat"
    if any(term in text for term in ("o/u", "over/under", "over ", "under ", "total")):
        return "total"
    if "halftime" in text or "1st half" in text or "first half" in text:
        return "halftime"
    if any(term in text for term in ("golden glove", "assists", "ballon d", "player", "top scorer")):
        return "player_award"
    if any(term in text for term in ("round of", "quarterfinal", "semifinal", "eliminated", "reach")):
        return "advancement"
    if any(term in text for term in ("host", "hosts", "announced as host", "announced as hosts")):
        return "host_country"
    if any(term in text for term in (" vs ", " vs. ")):
        return "match_winner"
    return "other"


def extract_market_subject(row: pd.Series | dict[str, Any]) -> str:
    event_title = extract_event_title(row)
    outcome_label = extract_outcome_label(row)
    if event_title and outcome_label and classify_market_type(row) == "match_winner":
        return f"{event_title}: {outcome_label}"
    raw = _raw_payload(row)
    if isinstance(raw.get("custom_strike"), dict) and raw["custom_strike"].get("Location"):
        return str(raw["custom_strike"]["Location"])
    title = str(_row_get(row, "title") or "")
    for prefix in ("Will ", "will "):
        if title.startswith(prefix):
            title = title[len(prefix) :]
    title = title.replace(" be announced as hosts for the 2038 Men's FIFA World Cup?", "")
    title = title.replace(" be announced as a host for the 2038 Men's FIFA World Cup?", "")
    title = title.replace("?", "")
    return title.strip()


def extract_event_title(row: pd.Series | dict[str, Any]) -> str:
    raw = _raw_payload(row)
    for key in ("_event_context_title", "event_title"):
        value = raw.get(key)
        if value:
            return str(value).strip()
    if isinstance(raw.get("_event_context_payload"), dict) and raw["_event_context_payload"].get("title"):
        return str(raw["_event_context_payload"]["title"]).strip()
    title = str(_row_get(row, "title") or "").strip()
    title = re.sub(r"\s+Winner\?$", "", title, flags=re.IGNORECASE).strip()
    if ":" in title:
        title = title.split(":", 1)[0].strip()
    match = re.search(r"Will\s+(.+?)\s+win\s+on\s+\d{4}-\d{2}-\d{2}\??", title, flags=re.IGNORECASE)
    if match:
        outcome = match.group(1).strip()
        raw_title = str(raw.get("_event_context_title") or "")
        return raw_title or outcome
    match = re.search(r"Will\s+(.+?)\s+end\s+in\s+a\s+draw\??", title, flags=re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return title


def extract_event_date(row: pd.Series | dict[str, Any]) -> str:
    raw = _raw_payload(row)
    for value in (
        raw.get("occurrence_datetime"),
        raw.get("expected_expiration_time"),
        raw.get("_event_context_start_time"),
        raw.get("_event_context_end_date"),
        raw.get("endDate"),
        raw.get("endDateIso"),
        raw.get("close_time"),
        _row_get(row, "close_time"),
    ):
        if _is_blank(value):
            continue
        parsed = parse_timestamp(value)
        if parsed:
            return parsed[:10]
    text = _candidate_text(row)
    match = re.search(r"\b(20\d{2})-(\d{2})-(\d{2})\b", text)
    if match:
        return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
    match = re.search(
        r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+(\d{1,2}),\s*(20\d{2})\b",
        text,
        flags=re.IGNORECASE,
    )
    if match:
        month = _month_number(match.group(0).split()[0])
        if month:
            return f"{match.group(2)}-{month:02d}-{int(match.group(1)):02d}"
    return ""


def extract_event_match_key(row: pd.Series | dict[str, Any], event_title: str | None = None, event_date: str | None = None) -> str:
    event_title = event_title if event_title is not None else extract_event_title(row)
    event_date = event_date if event_date is not None else extract_event_date(row)
    teams = _teams_from_match_title(event_title)
    if len(teams) != 2:
        return ""
    normalized_teams = sorted(_normalize_name(team) for team in teams)
    if not all(normalized_teams):
        return ""
    return "|".join([event_date or "", *normalized_teams])


def extract_outcome_label(row: pd.Series | dict[str, Any]) -> str:
    raw = _raw_payload(row)
    venue = str(_row_get(row, "venue") or "")
    if venue == "kalshi":
        return str(raw.get("yes_sub_title") or raw.get("subtitle") or "").strip()
    title = str(_row_get(row, "title") or "")
    match = re.search(r"Will\s+(.+?)\s+win\s+on\s+\d{4}-\d{2}-\d{2}\??", title, flags=re.IGNORECASE)
    if match:
        return match.group(1).strip()
    if re.search(r"end\s+in\s+a\s+draw", title, flags=re.IGNORECASE):
        return "Tie"
    outcomes = parse_json_array(_row_get(row, "outcomes"))
    if len(outcomes) == 2 and outcomes[0] not in ("Yes", "Over"):
        return str(outcomes[0])
    return ""


def extract_event_year(row: pd.Series | dict[str, Any]) -> str:
    event_date = extract_event_date(row)
    if event_date:
        return event_date[:4]
    text = _candidate_text(row)
    match = re.search(r"\b(20\d{2})\b", text)
    return match.group(1) if match else ""


def infer_event_timeframe(row: pd.Series | dict[str, Any]) -> str:
    market_type = classify_market_type(row)
    text = _candidate_text(row)
    if market_type == "host_country":
        return "host announcement"
    if "halftime" in text or "1st half" in text or "first half" in text:
        return "first half"
    if "extra time" in text:
        return "includes or concerns extra time"
    if "group stage" in text:
        return "group stage"
    if "round of" in text or "quarterfinal" in text or "semifinal" in text:
        return "tournament advancement"
    return "unclear"


def summarize_settlement(row: pd.Series | dict[str, Any], max_chars: int = 500) -> str:
    values = [str(_row_get(row, "title") or ""), str(_row_get(row, "rules_text") or ""), str(_row_get(row, "subtitle") or "")]
    text = " ".join(value.strip() for value in values if value and value.strip())
    text = " ".join(text.split())
    return text[:max_chars]


def extract_liquidity_hint(row: pd.Series | dict[str, Any]) -> str:
    raw = _raw_payload(row)
    hints: list[str] = []
    for key in (
        "liquidity",
        "liquidityNum",
        "volume",
        "volumeNum",
        "volume24hr",
        "volume24hrNum",
        "open_interest",
        "open_interest_dollars",
        "yes_bid",
        "yes_ask",
        "no_bid",
        "no_ask",
        "last_price",
        "lastPrice",
    ):
        value = raw.get(key)
        if value not in (None, "", [], {}):
            hints.append(f"{key}={value}")
    return "; ".join(hints[:8])


def approval_notes_for_market_type(market_type: str) -> str:
    if market_type == "host_country":
        return "Check exact host country/group, year, source agencies, and multi-host resolution."
    if market_type == "match_winner":
        return "Check draw, regulation time, extra time, penalties, postponement/cancel rules."
    if market_type == "advancement":
        return "Check tournament stage, advancement vs match result, and settlement timing."
    if market_type in {"player_award", "team_stat", "transfer"}:
        return "Review full proposition, source, deadline, and whether a Kalshi equivalent really exists."
    if market_type in {"spread", "total", "both_teams_score", "halftime"}:
        return "Do not map to simple winner markets; check threshold, period, void/cancel rules."
    return "Review full rules before approving."


def candidate_match_score(left: pd.Series | dict[str, Any], right: pd.Series | dict[str, Any]) -> float:
    left_event_key = str(_row_get(left, "event_match_key") or "")
    right_event_key = str(_row_get(right, "event_match_key") or "")
    left_outcome = _normalize_name(_row_get(left, "outcome_label"))
    right_outcome = _normalize_name(_row_get(right, "outcome_label"))
    if left_event_key and left_event_key == right_event_key and left_outcome and left_outcome == right_outcome:
        return 100.0
    subject_score = fuzz.token_set_ratio(str(_row_get(left, "subject") or ""), str(_row_get(right, "subject") or ""))
    title_score = fuzz.token_set_ratio(str(_row_get(left, "title") or ""), str(_row_get(right, "title") or ""))
    event_score = fuzz.token_set_ratio(str(_row_get(left, "event_title") or ""), str(_row_get(right, "event_title") or ""))
    outcome_score = fuzz.token_set_ratio(str(_row_get(left, "outcome_label") or ""), str(_row_get(right, "outcome_label") or ""))
    year_score = 100.0 if _row_get(left, "event_year") and _row_get(left, "event_year") == _row_get(right, "event_year") else 0.0
    type_score = 100.0 if _row_get(left, "market_type") == _row_get(right, "market_type") else 0.0
    return float(0.25 * subject_score + 0.20 * event_score + 0.20 * outcome_score + 0.15 * title_score + 0.15 * year_score + 0.05 * type_score)


def load_manual_mappings(path: str | Path = DEFAULT_MAPPING_PATH) -> pd.DataFrame:
    mapping_path = Path(path)
    if not mapping_path.exists():
        return pd.DataFrame(columns=MAPPING_COLUMNS)
    frame = pd.read_csv(mapping_path, dtype=str, keep_default_na=False)
    for column in MAPPING_COLUMNS:
        if column not in frame.columns:
            frame[column] = ""
    return frame[MAPPING_COLUMNS].fillna("")


def validate_manual_mappings(mappings: pd.DataFrame) -> pd.DataFrame:
    if mappings.empty:
        return pd.DataFrame(columns=MAPPING_SNAPSHOT_COLUMNS)
    rows: list[dict[str, Any]] = []
    for _, row in mappings.iterrows():
        status = str(row.get("status", "")).strip().lower()
        errors: list[str] = []
        if status == "approved":
            for column in APPROVED_MAPPING_REQUIRED_COLUMNS:
                if not str(row.get(column, "")).strip():
                    errors.append(f"missing_{column}")
            for column in ("draw_handling", "extra_time_handling", "penalties_handling"):
                if _is_unclear_settlement_note(row.get(column, "")):
                    errors.append(f"unclear_{column}")
        rows.append(
            {
                **{column: row.get(column, "") for column in MAPPING_COLUMNS},
                "is_approved": status == "approved" and not errors,
                "validation_errors": ";".join(errors),
            }
        )
    return pd.DataFrame(rows, columns=MAPPING_SNAPSHOT_COLUMNS)


def approved_mappings(mappings: pd.DataFrame) -> pd.DataFrame:
    validated = validate_manual_mappings(mappings)
    if validated.empty:
        return pd.DataFrame(columns=MAPPING_SNAPSHOT_COLUMNS)
    return validated[validated["is_approved"] == True].reset_index(drop=True)  # noqa: E712


def fetch_mapped_orderbooks(
    client: httpx.Client,
    mappings: pd.DataFrame,
    run_id: str,
    orderbook_depth: int = 100,
    retrieved_at: str | None = None,
) -> pd.DataFrame:
    retrieved_at = retrieved_at or utc_now_iso()
    rows: list[dict[str, Any]] = []
    for _, mapping in mappings.iterrows():
        mapping_id = str(mapping.get("mapping_id", ""))
        rows.append(
            _polymarket_orderbook_row(
                client=client,
                mapping=mapping,
                run_id=run_id,
                retrieved_at=retrieved_at,
            )
        )
        rows.append(
            _kalshi_orderbook_row(
                client=client,
                mapping=mapping,
                run_id=run_id,
                retrieved_at=retrieved_at,
                orderbook_depth=orderbook_depth,
            )
        )
        if not mapping_id:
            rows[-1]["error"] = _append_error(rows[-1].get("error", ""), "missing_mapping_id")
    return pd.DataFrame(rows, columns=ORDERBOOK_COLUMNS)


def score_cross_market_arbitrage(
    mappings: pd.DataFrame,
    orderbooks: pd.DataFrame,
    run_id: str,
    min_net_edge: float = DEFAULT_MIN_NET_EDGE,
    slippage_buffer_per_leg: float = DEFAULT_SLIPPAGE_BUFFER_PER_LEG,
    fee_buffer_total: float = DEFAULT_FEE_BUFFER_TOTAL,
    min_depth_per_leg: float = DEFAULT_MIN_DEPTH_PER_LEG,
    detected_at: str | None = None,
) -> pd.DataFrame:
    detected_at = detected_at or utc_now_iso()
    if mappings.empty:
        return pd.DataFrame(columns=ALERT_COLUMNS)

    rows: list[dict[str, Any]] = []
    for _, mapping in mappings.iterrows():
        mapping_id = str(mapping.get("mapping_id", ""))
        pm = _single_orderbook(orderbooks, mapping_id, "polymarket")
        ks = _single_orderbook(orderbooks, mapping_id, "kalshi")
        rows.append(
            _score_direction(
                mapping=mapping,
                run_id=run_id,
                detected_at=detected_at,
                direction="buy_polymarket_yes_buy_kalshi_no",
                leg1_venue="polymarket",
                leg1_outcome="YES",
                leg1_ask=pm.get("yes_ask"),
                leg1_depth=pm.get("yes_ask_depth"),
                leg2_venue="kalshi",
                leg2_outcome="NO",
                leg2_ask=ks.get("no_ask"),
                leg2_depth=ks.get("no_ask_depth"),
                min_net_edge=min_net_edge,
                slippage_buffer_per_leg=slippage_buffer_per_leg,
                fee_buffer_total=fee_buffer_total,
                min_depth_per_leg=min_depth_per_leg,
            )
        )
        rows.append(
            _score_direction(
                mapping=mapping,
                run_id=run_id,
                detected_at=detected_at,
                direction="buy_kalshi_yes_buy_polymarket_no",
                leg1_venue="kalshi",
                leg1_outcome="YES",
                leg1_ask=ks.get("yes_ask"),
                leg1_depth=ks.get("yes_ask_depth"),
                leg2_venue="polymarket",
                leg2_outcome="NO",
                leg2_ask=pm.get("no_ask"),
                leg2_depth=pm.get("no_ask_depth"),
                min_net_edge=min_net_edge,
                slippage_buffer_per_leg=slippage_buffer_per_leg,
                fee_buffer_total=fee_buffer_total,
                min_depth_per_leg=min_depth_per_leg,
            )
        )
    frame = pd.DataFrame(rows, columns=ALERT_COLUMNS)
    if frame.empty:
        return frame
    frame["_rank"] = frame["is_alert"].map({True: 0, False: 1})
    return frame.sort_values(["_rank", "net_edge"], ascending=[True, False], na_position="last").drop(columns=["_rank"]).reset_index(drop=True)


def build_strategy_signals(alerts: pd.DataFrame) -> pd.DataFrame:
    if alerts.empty:
        return pd.DataFrame(columns=SIGNAL_COLUMNS)

    rows: list[dict[str, Any]] = []
    for _, row in alerts.iterrows():
        leg1_ask = to_float(row.get("leg1_ask"))
        leg2_ask = to_float(row.get("leg2_ask"))
        min_depth = to_float(row.get("min_depth")) or 0.0
        min_net_edge = to_float(row.get("min_net_edge")) or DEFAULT_MIN_NET_EDGE
        net_edge = to_float(row.get("net_edge"))
        exclusion_reason = str(row.get("exclusion_reason") or "")
        price_available = leg1_ask is not None and leg2_ask is not None
        liquidity_ok = price_available and exclusion_reason != "insufficient_depth"
        threshold_ok = net_edge is not None and net_edge >= min_net_edge
        is_alert = bool(row.get("is_alert"))
        if is_alert:
            signal = "alert"
        elif not price_available:
            signal = "blocked_missing_price"
        elif not liquidity_ok:
            signal = "blocked_insufficient_depth"
        elif not threshold_ok:
            signal = "watch_edge_below_threshold"
        else:
            signal = f"blocked_{exclusion_reason or 'unknown'}"

        rows.append(
            {
                "run_id": row.get("run_id", ""),
                "detected_at": row.get("detected_at", ""),
                "mapping_id": row.get("mapping_id", ""),
                "event_name": row.get("event_name", ""),
                "proposition": row.get("proposition", ""),
                "direction": row.get("direction", ""),
                "signal": signal,
                "is_alert": is_alert,
                "price_available": price_available,
                "liquidity_ok": liquidity_ok,
                "threshold_ok": threshold_ok,
                "gross_cost": row.get("gross_cost"),
                "buffered_cost": row.get("buffered_cost"),
                "net_edge": row.get("net_edge"),
                "min_depth": row.get("min_depth"),
                "min_net_edge": row.get("min_net_edge"),
                "exclusion_reason": exclusion_reason,
                "leg1_venue": row.get("leg1_venue", ""),
                "leg1_outcome": row.get("leg1_outcome", ""),
                "leg1_ask": row.get("leg1_ask"),
                "leg1_depth": row.get("leg1_depth"),
                "leg2_venue": row.get("leg2_venue", ""),
                "leg2_outcome": row.get("leg2_outcome", ""),
                "leg2_ask": row.get("leg2_ask"),
                "leg2_depth": row.get("leg2_depth"),
                "draw_handling": row.get("draw_handling", ""),
                "extra_time_handling": row.get("extra_time_handling", ""),
                "penalties_handling": row.get("penalties_handling", ""),
                "settlement_notes": row.get("settlement_notes", ""),
            }
        )
    return pd.DataFrame(rows, columns=SIGNAL_COLUMNS)


def run_fifa_snapshot(
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    mapping_path: str | Path = DEFAULT_MAPPING_PATH,
    run_id: str | None = None,
    market_limit: int = 1_000,
    page_size: int = 500,
    orderbook_depth: int = 100,
    min_net_edge: float = DEFAULT_MIN_NET_EDGE,
    slippage_buffer_per_leg: float = DEFAULT_SLIPPAGE_BUFFER_PER_LEG,
    fee_buffer_total: float = DEFAULT_FEE_BUFFER_TOTAL,
    min_depth_per_leg: float = DEFAULT_MIN_DEPTH_PER_LEG,
    discover: bool = True,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    if client is None:
        with httpx.Client(timeout=DEFAULT_TIMEOUT_SECONDS, headers={"User-Agent": "poly-x-kalshi-fifa-scanner"}) as owned_client:
            return run_fifa_snapshot(
                output_dir=output_dir,
                mapping_path=mapping_path,
                run_id=run_id,
                market_limit=market_limit,
                page_size=page_size,
                orderbook_depth=orderbook_depth,
                min_net_edge=min_net_edge,
                slippage_buffer_per_leg=slippage_buffer_per_leg,
                fee_buffer_total=fee_buffer_total,
                min_depth_per_leg=min_depth_per_leg,
                discover=discover,
                client=owned_client,
            )

    run_id = run_id or _new_run_id()
    started_at = utc_now_iso()
    raw_polymarket_markets: list[dict[str, Any]] = []
    raw_kalshi_markets: list[dict[str, Any]] = []
    candidates = pd.DataFrame(columns=CANDIDATE_COLUMNS)
    if discover:
        raw_polymarket_markets = fetch_polymarket_fifa_markets(client, max_markets=market_limit, page_size=page_size)
        raw_kalshi_markets = fetch_kalshi_fifa_markets(client, max_markets=market_limit, page_size=min(page_size, 200))
        candidates = normalize_fifa_candidates(raw_polymarket_markets, raw_kalshi_markets, run_id=run_id, retrieved_at=started_at)
    approval_candidates = build_approval_candidates(candidates)
    suggested_mappings = suggest_manual_mappings(approval_candidates)

    mappings = load_manual_mappings(mapping_path)
    mapping_snapshot = validate_manual_mappings(mappings)
    eligible_mappings = approved_mappings(mappings)
    orderbooks = fetch_mapped_orderbooks(client, eligible_mappings, run_id=run_id, orderbook_depth=orderbook_depth, retrieved_at=started_at)
    alerts = score_cross_market_arbitrage(
        eligible_mappings,
        orderbooks,
        run_id=run_id,
        min_net_edge=min_net_edge,
        slippage_buffer_per_leg=slippage_buffer_per_leg,
        fee_buffer_total=fee_buffer_total,
        min_depth_per_leg=min_depth_per_leg,
        detected_at=started_at,
    )
    signals = build_strategy_signals(alerts)
    scanner_runs = pd.DataFrame(
        [
            {
                "run_id": run_id,
                "started_at": started_at,
                "finished_at": utc_now_iso(),
                "status": "succeeded",
                "candidate_count": len(candidates),
                "approved_mapping_count": len(eligible_mappings),
                "orderbook_count": len(orderbooks),
                "alert_count": int(alerts["is_alert"].sum()) if "is_alert" in alerts else 0,
                "error": "",
            }
        ],
        columns=RUN_COLUMNS,
    )

    result = {
        "run_id": run_id,
        "raw": {
            "polymarket_markets": raw_polymarket_markets,
            "kalshi_markets": raw_kalshi_markets,
        },
        "tables": {
            "venue_market_candidates": candidates,
            "approval_candidates": approval_candidates,
            "suggested_mappings": suggested_mappings,
            "manual_mappings_snapshot": mapping_snapshot,
            "orderbook_snapshots": orderbooks,
            "arbitrage_alerts": alerts,
            "strategy_signals": signals,
            "scanner_runs": scanner_runs,
        },
    }
    result["written"] = write_fifa_snapshot_artifacts(result, output_dir=output_dir)
    return result


def watch_fifa_arbitrage(
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    mapping_path: str | Path = DEFAULT_MAPPING_PATH,
    interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
    max_ticks: int | None = None,
    market_limit: int = 1_000,
    page_size: int = 500,
    orderbook_depth: int = 100,
    min_net_edge: float = DEFAULT_MIN_NET_EDGE,
    slippage_buffer_per_leg: float = DEFAULT_SLIPPAGE_BUFFER_PER_LEG,
    fee_buffer_total: float = DEFAULT_FEE_BUFFER_TOTAL,
    min_depth_per_leg: float = DEFAULT_MIN_DEPTH_PER_LEG,
    discover: bool = True,
    sleeper: Callable[[float], None] = time.sleep,
    client_factory: Callable[[], httpx.Client] | None = None,
) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    tick = 0
    backoff_seconds = interval_seconds
    while max_ticks is None or tick < max_ticks:
        try:
            factory = client_factory or (
                lambda: httpx.Client(timeout=DEFAULT_TIMEOUT_SECONDS, headers={"User-Agent": "poly-x-kalshi-fifa-scanner"})
            )
            with factory() as client:
                result = run_fifa_snapshot(
                    output_dir=output_dir,
                    mapping_path=mapping_path,
                    market_limit=market_limit,
                    page_size=page_size,
                    orderbook_depth=orderbook_depth,
                    min_net_edge=min_net_edge,
                    slippage_buffer_per_leg=slippage_buffer_per_leg,
                    fee_buffer_total=fee_buffer_total,
                    min_depth_per_leg=min_depth_per_leg,
                    discover=discover,
                    client=client,
                )
            summary = _snapshot_summary(result)
            summaries.append(summary)
            _print_alerts(result["tables"]["arbitrage_alerts"])
            tick += 1
            backoff_seconds = interval_seconds
            if max_ticks is None or tick < max_ticks:
                sleeper(interval_seconds)
        except KeyboardInterrupt:
            print("Stopping FIFA arbitrage watcher.")
            break
        except Exception as exc:  # pragma: no cover - exact exception paths are covered through run recording tests
            run_id = _new_run_id()
            failed_run = pd.DataFrame(
                [
                    {
                        "run_id": run_id,
                        "started_at": utc_now_iso(),
                        "finished_at": utc_now_iso(),
                        "status": "failed",
                        "candidate_count": 0,
                        "approved_mapping_count": 0,
                        "orderbook_count": 0,
                        "alert_count": 0,
                        "error": str(exc),
                    }
                ],
                columns=RUN_COLUMNS,
            )
            append_processed_table("scanner_runs", failed_run, output_dir)
            summaries.append({"run_id": run_id, "status": "failed", "alert_count": 0, "error": str(exc)})
            sleeper(min(backoff_seconds, 300.0))
            backoff_seconds = min(backoff_seconds * 2, 300.0)
            tick += 1
    return summaries


def request_json_with_retry(
    client: httpx.Client,
    url: str,
    params: dict[str, Any] | None = None,
    max_retries: int = 3,
    backoff_seconds: float = 0.5,
    sleeper: Callable[[float], None] = time.sleep,
) -> Any:
    attempt = 0
    while True:
        response = client.get(url, params=params)
        if response.status_code not in RETRYABLE_STATUSES:
            response.raise_for_status()
            return response.json()
        attempt += 1
        if attempt > max_retries:
            response.raise_for_status()
        sleeper(backoff_seconds * (2 ** (attempt - 1)))


def write_fifa_snapshot_artifacts(result: dict[str, Any], output_dir: str | Path = DEFAULT_OUTPUT_DIR) -> dict[str, Any]:
    output_root = Path(output_dir)
    raw_polymarket = output_root / "raw" / "polymarket"
    raw_kalshi = output_root / "raw" / "kalshi"
    alerts_dir = output_root / "alerts"
    for directory in (raw_polymarket, raw_kalshi, output_root / "processed", alerts_dir):
        directory.mkdir(parents=True, exist_ok=True)

    run_id = result["run_id"]
    raw_paths = {
        "polymarket_markets": raw_polymarket / f"markets_{run_id}.json",
        "kalshi_markets": raw_kalshi / f"markets_{run_id}.json",
    }
    raw_paths["polymarket_markets"].write_text(
        json.dumps(result["raw"].get("polymarket_markets", []), indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    raw_paths["kalshi_markets"].write_text(
        json.dumps(result["raw"].get("kalshi_markets", []), indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )

    processed_paths: dict[str, dict[str, Path]] = {}
    latest_processed_paths: dict[str, dict[str, Path]] = {}
    for name, frame in result["tables"].items():
        processed_paths[name] = append_processed_table(name, frame, output_root)
        if name in DISCOVERY_REVIEW_TABLES and frame.empty:
            latest_dir = output_root / "processed" / "latest"
            latest_processed_paths[name] = {
                "parquet": latest_dir / f"{name}.parquet",
                "csv": latest_dir / f"{name}.csv",
            }
            continue
        latest_processed_paths[name] = write_latest_processed_table(name, frame, output_root)

    alert_rows = result["tables"]["arbitrage_alerts"]
    jsonl_path = alerts_dir / "arbitrage_alerts.jsonl"
    actual_alerts = alert_rows[alert_rows["is_alert"] == True] if not alert_rows.empty else alert_rows  # noqa: E712
    if not actual_alerts.empty:
        with jsonl_path.open("a", encoding="utf-8") as handle:
            for record in actual_alerts.to_dict(orient="records"):
                handle.write(json.dumps(record, sort_keys=True, default=str) + "\n")
    return {
        "raw_paths": raw_paths,
        "processed_paths": processed_paths,
        "latest_processed_paths": latest_processed_paths,
        "alert_jsonl": jsonl_path,
    }


def append_processed_table(name: str, frame: pd.DataFrame, output_dir: str | Path = DEFAULT_OUTPUT_DIR) -> dict[str, Path]:
    processed_dir = Path(output_dir) / "processed"
    processed_dir.mkdir(parents=True, exist_ok=True)
    parquet_path = processed_dir / f"{name}.parquet"
    csv_path = processed_dir / f"{name}.csv"
    output = frame.copy()
    if parquet_path.exists():
        try:
            previous = pd.read_parquet(parquet_path)
            output = pd.concat([previous, output], ignore_index=True)
        except Exception:
            pass
    output.to_parquet(parquet_path, index=False)
    output.to_csv(csv_path, index=False)
    return {"parquet": parquet_path, "csv": csv_path}


def write_latest_processed_table(name: str, frame: pd.DataFrame, output_dir: str | Path = DEFAULT_OUTPUT_DIR) -> dict[str, Path]:
    latest_dir = Path(output_dir) / "processed" / "latest"
    latest_dir.mkdir(parents=True, exist_ok=True)
    parquet_path = latest_dir / f"{name}.parquet"
    csv_path = latest_dir / f"{name}.csv"
    frame.to_parquet(parquet_path, index=False)
    frame.to_csv(csv_path, index=False)
    return {"parquet": parquet_path, "csv": csv_path}


def build_snapshot_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one FIFA cross-market arbitrage snapshot")
    _add_common_args(parser)
    parser.add_argument("--run-id")
    parser.add_argument("--no-discovery", action="store_true", help="Skip market discovery and only score approved mappings")
    return parser


def build_watch_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Continuously poll FIFA cross-market arbitrage snapshots")
    _add_common_args(parser)
    parser.add_argument("--interval-seconds", type=float, default=DEFAULT_INTERVAL_SECONDS)
    parser.add_argument("--max-ticks", type=int)
    parser.add_argument("--no-discovery", action="store_true", help="Skip market discovery and only score approved mappings")
    return parser


def snapshot_cli_main(argv: list[str] | None = None, client: httpx.Client | None = None) -> int:
    args = build_snapshot_parser().parse_args(argv)
    result = run_fifa_snapshot(
        output_dir=args.output_dir,
        mapping_path=args.mapping_path,
        run_id=args.run_id,
        market_limit=args.market_limit,
        page_size=args.page_size,
        orderbook_depth=args.orderbook_depth,
        min_net_edge=args.min_net_edge,
        slippage_buffer_per_leg=args.slippage_buffer_per_leg,
        fee_buffer_total=args.fee_buffer_total,
        min_depth_per_leg=args.min_depth_per_leg,
        discover=not args.no_discovery,
        client=client,
    )
    print(json.dumps(_snapshot_summary(result), indent=2, sort_keys=True, default=str))
    _print_alerts(result["tables"]["arbitrage_alerts"])
    return 0


def watch_cli_main(argv: list[str] | None = None) -> int:
    args = build_watch_parser().parse_args(argv)
    summaries = watch_fifa_arbitrage(
        output_dir=args.output_dir,
        mapping_path=args.mapping_path,
        interval_seconds=args.interval_seconds,
        max_ticks=args.max_ticks,
        market_limit=args.market_limit,
        page_size=args.page_size,
        orderbook_depth=args.orderbook_depth,
        min_net_edge=args.min_net_edge,
        slippage_buffer_per_leg=args.slippage_buffer_per_leg,
        fee_buffer_total=args.fee_buffer_total,
        min_depth_per_leg=args.min_depth_per_leg,
        discover=not args.no_discovery,
    )
    print(json.dumps({"ticks": summaries}, indent=2, sort_keys=True, default=str))
    return 0


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--mapping-path", default=DEFAULT_MAPPING_PATH)
    parser.add_argument("--market-limit", type=int, default=1_000)
    parser.add_argument("--page-size", type=int, default=500)
    parser.add_argument("--orderbook-depth", type=int, default=100)
    parser.add_argument("--min-net-edge", type=float, default=DEFAULT_MIN_NET_EDGE)
    parser.add_argument("--slippage-buffer-per-leg", type=float, default=DEFAULT_SLIPPAGE_BUFFER_PER_LEG)
    parser.add_argument("--fee-buffer-total", type=float, default=DEFAULT_FEE_BUFFER_TOTAL)
    parser.add_argument("--min-depth-per-leg", type=float, default=DEFAULT_MIN_DEPTH_PER_LEG)


def _polymarket_orderbook_row(
    client: httpx.Client,
    mapping: pd.Series,
    run_id: str,
    retrieved_at: str,
) -> dict[str, Any]:
    yes_token = str(mapping.get("polymarket_yes_token_id", ""))
    no_token = str(mapping.get("polymarket_no_token_id", ""))
    error = ""
    yes_book: dict[str, Any] = {}
    no_book: dict[str, Any] = {}
    try:
        yes_book = request_json_with_retry(client, f"{POLYMARKET_CLOB_BASE}/book", params={"token_id": yes_token})
    except (httpx.HTTPError, ValueError) as exc:
        error = _append_error(error, f"yes_token_error:{exc}")
    try:
        no_book = request_json_with_retry(client, f"{POLYMARKET_CLOB_BASE}/book", params={"token_id": no_token})
    except (httpx.HTTPError, ValueError) as exc:
        error = _append_error(error, f"no_token_error:{exc}")

    yes_bids = _levels(yes_book, "bids")
    yes_asks = _levels(yes_book, "asks")
    no_bids = _levels(no_book, "bids")
    no_asks = _levels(no_book, "asks")
    return {
        "run_id": run_id,
        "retrieved_at": retrieved_at,
        "mapping_id": mapping.get("mapping_id", ""),
        "venue": "polymarket",
        "market_id": mapping.get("polymarket_market_id") or mapping.get("polymarket_slug") or "",
        "yes_bid": best_bid(yes_bids),
        "yes_ask": best_ask(yes_asks),
        "no_bid": best_bid(no_bids),
        "no_ask": best_ask(no_asks),
        "yes_bid_depth": total_size(yes_bids),
        "yes_ask_depth": total_size(yes_asks),
        "no_bid_depth": total_size(no_bids),
        "no_ask_depth": total_size(no_asks),
        "raw_orderbook": compact_json({"yes": yes_book, "no": no_book}),
        "error": error,
    }


def _kalshi_orderbook_row(
    client: httpx.Client,
    mapping: pd.Series,
    run_id: str,
    retrieved_at: str,
    orderbook_depth: int,
) -> dict[str, Any]:
    ticker = str(mapping.get("kalshi_ticker", ""))
    error = ""
    raw_book: dict[str, Any] = {}
    try:
        raw_book = request_json_with_retry(client, f"{KALSHI_BASE}/markets/{ticker}/orderbook", params={"depth": orderbook_depth})
    except (httpx.HTTPError, ValueError) as exc:
        error = str(exc)
    orderbook = raw_book.get("orderbook_fp") or raw_book.get("orderbook") or {}
    yes_bids = _kalshi_side_levels(orderbook, "yes")
    no_bids = _kalshi_side_levels(orderbook, "no")
    best_yes_bid = best_bid(yes_bids)
    best_no_bid = best_bid(no_bids)
    return {
        "run_id": run_id,
        "retrieved_at": retrieved_at,
        "mapping_id": mapping.get("mapping_id", ""),
        "venue": "kalshi",
        "market_id": ticker,
        "yes_bid": best_yes_bid,
        "yes_ask": _complement(best_no_bid),
        "no_bid": best_no_bid,
        "no_ask": _complement(best_yes_bid),
        "yes_bid_depth": total_size(yes_bids),
        "yes_ask_depth": total_size(no_bids),
        "no_bid_depth": total_size(no_bids),
        "no_ask_depth": total_size(yes_bids),
        "raw_orderbook": compact_json(raw_book),
        "error": error,
    }


def _score_direction(
    mapping: pd.Series,
    run_id: str,
    detected_at: str,
    direction: str,
    leg1_venue: str,
    leg1_outcome: str,
    leg1_ask: Any,
    leg1_depth: Any,
    leg2_venue: str,
    leg2_outcome: str,
    leg2_ask: Any,
    leg2_depth: Any,
    min_net_edge: float,
    slippage_buffer_per_leg: float,
    fee_buffer_total: float,
    min_depth_per_leg: float,
) -> dict[str, Any]:
    leg1_ask_value = to_float(leg1_ask)
    leg2_ask_value = to_float(leg2_ask)
    leg1_depth_value = to_float(leg1_depth) or 0.0
    leg2_depth_value = to_float(leg2_depth) or 0.0
    gross_cost = None
    buffered_cost = None
    net_edge = None
    min_depth = min(leg1_depth_value, leg2_depth_value)
    is_alert = False
    exclusion_reason = ""

    if leg1_ask_value is None or leg2_ask_value is None:
        exclusion_reason = "missing_price"
    else:
        gross_cost = leg1_ask_value + leg2_ask_value
        buffered_cost = gross_cost + (2 * slippage_buffer_per_leg) + fee_buffer_total
        net_edge = 1.0 - buffered_cost
        if min_depth < min_depth_per_leg:
            exclusion_reason = "insufficient_depth"
        elif net_edge < min_net_edge:
            exclusion_reason = "edge_below_threshold"
        else:
            is_alert = True

    return {
        "run_id": run_id,
        "detected_at": detected_at,
        "mapping_id": mapping.get("mapping_id", ""),
        "event_name": mapping.get("event_name", ""),
        "proposition": mapping.get("proposition", ""),
        "direction": direction,
        "leg1_venue": leg1_venue,
        "leg1_outcome": leg1_outcome,
        "leg1_ask": leg1_ask_value,
        "leg1_depth": leg1_depth_value,
        "leg2_venue": leg2_venue,
        "leg2_outcome": leg2_outcome,
        "leg2_ask": leg2_ask_value,
        "leg2_depth": leg2_depth_value,
        "gross_cost": gross_cost,
        "buffered_cost": buffered_cost,
        "net_edge": net_edge,
        "min_depth": min_depth,
        "min_net_edge": min_net_edge,
        "slippage_buffer_per_leg": slippage_buffer_per_leg,
        "fee_buffer_total": fee_buffer_total,
        "is_alert": is_alert,
        "exclusion_reason": exclusion_reason,
        "draw_handling": mapping.get("draw_handling", ""),
        "extra_time_handling": mapping.get("extra_time_handling", ""),
        "penalties_handling": mapping.get("penalties_handling", ""),
        "settlement_notes": mapping.get("settlement_notes", ""),
    }


def _suggested_mapping_row(pm: pd.Series, ks: pd.Series, score: float) -> dict[str, Any]:
    market_type = str(pm.get("market_type") or "")
    mapping_id = f"{_slugify(str(pm.get('ticker_or_slug') or pm.get('market_id')))}__{_slugify(str(ks.get('ticker_or_slug')))}"
    event_name = str(pm.get("event_title") or ks.get("event_title") or "")
    outcome_label = str(pm.get("outcome_label") or ks.get("outcome_label") or "")
    return {
        "run_id": pm.get("run_id", ""),
        "suggested_mapping_id": mapping_id,
        "match_score": round(score, 2),
        "suggestion_status": "review_required",
        "market_type": market_type,
        "review_notes": _suggestion_review_notes(pm, ks),
        "mapping_id": mapping_id,
        "event_name": event_name,
        "proposition": _suggested_proposition(event_name, outcome_label, market_type),
        "polymarket_event_title": pm.get("event_title", ""),
        "kalshi_event_title": ks.get("event_title", ""),
        "event_match_key": pm.get("event_match_key", "") or ks.get("event_match_key", ""),
        "outcome_label": outcome_label,
        "polymarket_market_id": pm.get("market_id", ""),
        "polymarket_slug": pm.get("ticker_or_slug", ""),
        "polymarket_title": pm.get("title", ""),
        "polymarket_yes_token_id": pm.get("yes_token_id", ""),
        "polymarket_no_token_id": pm.get("no_token_id", ""),
        "polymarket_yes_outcome": "Yes",
        "polymarket_no_outcome": "No",
        "polymarket_outcomes": pm.get("outcomes", ""),
        "polymarket_settlement_summary": pm.get("settlement_summary", ""),
        "kalshi_ticker": ks.get("market_id", ""),
        "kalshi_title": ks.get("title", ""),
        "kalshi_outcomes": ks.get("outcomes", ""),
        "kalshi_settlement_summary": ks.get("settlement_summary", ""),
        "draw_handling": _default_draw_handling(market_type),
        "extra_time_handling": _default_extra_time_handling(market_type),
        "penalties_handling": _default_penalties_handling(market_type),
        "settlement_notes": "REVIEW REQUIRED: confirm both markets resolve identically before copying to config/fifa_market_mappings.csv.",
    }


def _suggestion_review_notes(pm: pd.Series, ks: pd.Series) -> str:
    notes = []
    if pm.get("event_match_key") and ks.get("event_match_key") and pm.get("event_match_key") != ks.get("event_match_key"):
        notes.append(f"event mismatch: polymarket={pm.get('event_match_key')} kalshi={ks.get('event_match_key')}")
    if _normalize_name(pm.get("outcome_label")) and _normalize_name(pm.get("outcome_label")) != _normalize_name(ks.get("outcome_label")):
        notes.append(f"outcome mismatch: polymarket={pm.get('outcome_label')} kalshi={ks.get('outcome_label')}")
    if pm.get("event_year") != ks.get("event_year"):
        notes.append(f"year mismatch: polymarket={pm.get('event_year') or 'unknown'} kalshi={ks.get('event_year') or 'unknown'}")
    if pm.get("market_type") != ks.get("market_type"):
        notes.append(f"type mismatch: polymarket={pm.get('market_type')} kalshi={ks.get('market_type')}")
    if pm.get("market_type") == "host_country":
        notes.append("verify host country/group and multi-host handling")
    if pm.get("market_type") == "match_winner":
        notes.append("verify regular-time result, draw/Tie handling, extra time, penalties, and cancellation rules")
    return "; ".join(notes) or "review settlement summaries before approval"


def _default_draw_handling(market_type: str) -> str:
    if market_type == "host_country":
        return "not applicable"
    if market_type == "match_winner":
        return "draw/Tie is a separate outcome; team-winner markets resolve No on draw"
    return "REVIEW REQUIRED"


def _default_extra_time_handling(market_type: str) -> str:
    if market_type == "host_country":
        return "not applicable"
    if market_type == "match_winner":
        return "regular time plus stoppage time only; extra time excluded"
    return "REVIEW REQUIRED"


def _default_penalties_handling(market_type: str) -> str:
    if market_type == "host_country":
        return "not applicable"
    if market_type == "match_winner":
        return "penalties excluded"
    return "REVIEW REQUIRED"


def _single_orderbook(orderbooks: pd.DataFrame, mapping_id: str, venue: str) -> dict[str, Any]:
    if orderbooks.empty:
        return {}
    matched = orderbooks[(orderbooks["mapping_id"] == mapping_id) & (orderbooks["venue"] == venue)]
    if matched.empty:
        return {}
    return matched.iloc[0].to_dict()


def _snapshot_summary(result: dict[str, Any]) -> dict[str, Any]:
    runs = result["tables"]["scanner_runs"]
    run = runs.iloc[-1].to_dict() if not runs.empty else {"run_id": result["run_id"], "status": "unknown"}
    return {
        "run_id": run.get("run_id"),
        "status": run.get("status"),
        "candidate_count": int(run.get("candidate_count") or 0),
        "approved_mapping_count": int(run.get("approved_mapping_count") or 0),
        "orderbook_count": int(run.get("orderbook_count") or 0),
        "alert_count": int(run.get("alert_count") or 0),
    }


def _print_alerts(alerts: pd.DataFrame) -> None:
    if alerts.empty or "is_alert" not in alerts:
        print("No FIFA cross-market alerts.")
        return
    actual = alerts[alerts["is_alert"] == True]  # noqa: E712
    if actual.empty:
        print("No FIFA cross-market alerts.")
        return
    for _, row in actual.iterrows():
        print(
            "ALERT "
            f"{row['mapping_id']} {row['direction']} "
            f"net_edge={row['net_edge']:.4f} "
            f"gross_cost={row['gross_cost']:.4f} "
            f"min_depth={row['min_depth']:.2f}"
        )


def _search_text(value: Any) -> str:
    if isinstance(value, str):
        return value.lower()
    return json.dumps(value, sort_keys=True, default=str).lower()


def _candidate_text(row: pd.Series | dict[str, Any]) -> str:
    return _search_text(
        {
            "title": _row_get(row, "title"),
            "subtitle": _row_get(row, "subtitle"),
            "event_title": _row_get(row, "event_title"),
            "outcome_label": _row_get(row, "outcome_label"),
            "ticker_or_slug": _row_get(row, "ticker_or_slug"),
            "outcomes": _row_get(row, "outcomes"),
            "rules_text": _row_get(row, "rules_text"),
        }
    )


def _row_get(row: pd.Series | dict[str, Any], key: str) -> Any:
    if isinstance(row, pd.Series):
        return row.get(key)
    return row.get(key)


def _raw_payload(row: pd.Series | dict[str, Any]) -> dict[str, Any]:
    value = _row_get(row, "raw_payload")
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            payload = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}
    return {}


def _slugify(value: str) -> str:
    text = value.lower()
    chars = [char if char.isalnum() else "-" for char in text]
    compact = "-".join(part for part in "".join(chars).split("-") if part)
    return compact[:80] or "candidate"


def _candidate_types_compatible(left: pd.Series | dict[str, Any], right: pd.Series | dict[str, Any]) -> bool:
    market_type = str(_row_get(left, "market_type") or "")
    if market_type != str(_row_get(right, "market_type") or ""):
        return False
    left_outcome = _normalize_name(_row_get(left, "outcome_label"))
    right_outcome = _normalize_name(_row_get(right, "outcome_label"))
    if market_type == "match_winner":
        left_event_key = str(_row_get(left, "event_match_key") or "")
        right_event_key = str(_row_get(right, "event_match_key") or "")
        if left_event_key and right_event_key and left_event_key != right_event_key:
            return False
        if left_outcome and right_outcome and left_outcome != right_outcome:
            return False
    return True


def _suggested_proposition(event_name: str, outcome_label: str, market_type: str) -> str:
    if market_type == "match_winner" and outcome_label:
        if _normalize_name(outcome_label) == "tie":
            return f"{event_name} to end in a draw in regular time"
        return f"{outcome_label} to win in regular time"
    return outcome_label or event_name


def _teams_from_match_title(value: Any) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    text = re.sub(r"\s+Winner\?$", "", text, flags=re.IGNORECASE).strip()
    parts = re.split(r"\s+vs\.?\s+", text, maxsplit=1, flags=re.IGNORECASE)
    if len(parts) != 2:
        return []
    return [part.strip(" ?") for part in parts]


def _normalize_name(value: Any) -> str:
    text = str(value or "").lower()
    text = text.replace("&", " and ")
    text = text.replace("draw", "tie")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and pd.isna(value):
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return False


def _month_number(value: str) -> int | None:
    months = {
        "jan": 1,
        "feb": 2,
        "mar": 3,
        "apr": 4,
        "may": 5,
        "jun": 6,
        "jul": 7,
        "aug": 8,
        "sep": 9,
        "oct": 10,
        "nov": 11,
        "dec": 12,
    }
    return months.get(str(value or "")[:3].lower())


def _polymarket_event_context(event: dict[str, Any]) -> dict[str, Any]:
    event_payload = {key: value for key, value in event.items() if key != "markets"}
    return {
        "_event_context_id": event.get("id"),
        "_event_context_slug": event.get("slug"),
        "_event_context_title": event.get("title"),
        "_event_context_start_time": event.get("startTime") or event.get("eventDate") or event.get("startDate"),
        "_event_context_end_date": event.get("endDate"),
        "_event_context_sport": event.get("sport"),
        "_event_context_teams": event.get("teams"),
        "_event_context_payload": event_payload,
    }


def _polymarket_search_payload(market: dict[str, Any]) -> dict[str, Any]:
    return {
        "question": market.get("question"),
        "title": market.get("title"),
        "slug": market.get("slug"),
        "category": market.get("category"),
        "description": market.get("description"),
        "events": market.get("events"),
        "event_context_title": market.get("_event_context_title"),
        "event_context_slug": market.get("_event_context_slug"),
        "event_context_payload": market.get("_event_context_payload"),
        "resolutionSource": market.get("resolutionSource"),
    }


def _polymarket_event_search_payload(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "ticker": event.get("ticker"),
        "slug": event.get("slug"),
        "title": event.get("title"),
        "description": event.get("description"),
        "sport": event.get("sport"),
        "series": event.get("series"),
        "seriesSlug": event.get("seriesSlug"),
        "tags": event.get("tags"),
        "teams": event.get("teams"),
        "eventMetadata": event.get("eventMetadata"),
        "resolutionSource": event.get("resolutionSource"),
    }


def _kalshi_search_payload(market: dict[str, Any]) -> dict[str, Any]:
    return {
        "ticker": market.get("ticker"),
        "event_ticker": market.get("event_ticker"),
        "series_ticker": market.get("series_ticker"),
        "event_context_title": market.get("_event_context_title"),
        "event_context_ticker": market.get("_event_context_ticker"),
        "title": market.get("title"),
        "subtitle": market.get("subtitle"),
        "yes_sub_title": market.get("yes_sub_title"),
        "no_sub_title": market.get("no_sub_title"),
        "category": market.get("category"),
        "product_metadata": market.get("product_metadata"),
        "event_context_payload": market.get("_event_context_payload"),
        "rules_primary": market.get("rules_primary"),
        "rules_secondary": market.get("rules_secondary"),
    }


def _kalshi_event_search_payload(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_ticker": event.get("event_ticker"),
        "series_ticker": event.get("series_ticker"),
        "title": event.get("title"),
        "category": event.get("category"),
        "sub_title": event.get("sub_title"),
        "product_metadata": event.get("product_metadata"),
        "settlement_sources": event.get("settlement_sources"),
    }


def _markets_from_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [market for market in payload if isinstance(market, dict)]
    if isinstance(payload, dict):
        markets = payload.get("markets", [])
        return [market for market in markets if isinstance(market, dict)]
    return []


def _events_from_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [event for event in payload if isinstance(event, dict)]
    if isinstance(payload, dict):
        events = payload.get("events", [])
        return [event for event in events if isinstance(event, dict)]
    return []


def _has_two_polymarket_tokens(market: dict[str, Any]) -> bool:
    return len(parse_json_array(market.get("clobTokenIds"))) >= 2


def _polymarket_category(market: dict[str, Any]) -> str:
    if market.get("category"):
        return str(market["category"])
    events = parse_json_array(market.get("events"))
    for event in events:
        if isinstance(event, dict) and event.get("category"):
            return str(event["category"])
    return ""


def _polymarket_rules_text(market: dict[str, Any]) -> str:
    values = [
        market.get("description"),
        market.get("resolutionSource"),
        market.get("rules"),
        market.get("endDate"),
    ]
    return " ".join(str(value) for value in values if value)


def _kalshi_rules_text(market: dict[str, Any]) -> str:
    values = [
        market.get("rules_primary"),
        market.get("rules_secondary"),
        market.get("settlement_sources"),
        market.get("expiration_value"),
    ]
    return " ".join(str(value) for value in values if value)


def _kalshi_outcomes(market: dict[str, Any]) -> list[str]:
    yes = market.get("yes_sub_title") or "Yes"
    no = market.get("no_sub_title") or "No"
    return [f"Yes: {yes}", f"No: {no}"]


def _is_unclear_settlement_note(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return not text or text in {"unclear", "unknown", "tbd", "na", "n/a", "?"}


def _levels(payload: dict[str, Any], key: str) -> list[Any]:
    levels = payload.get(key)
    return levels if isinstance(levels, list) else []


def _kalshi_side_levels(orderbook: dict[str, Any], side: str) -> list[Any]:
    dollar_key = f"{side}_dollars"
    if isinstance(orderbook.get(dollar_key), list):
        return orderbook[dollar_key]
    raw_levels = orderbook.get(side)
    if not isinstance(raw_levels, list):
        return []
    normalized = []
    for level in raw_levels:
        if isinstance(level, dict):
            price = to_float(level.get("price"))
            size = level.get("size")
            normalized.append({"price": price / 100 if price is not None and price > 1 else price, "size": size})
        elif isinstance(level, (list, tuple)) and level:
            price = to_float(level[0])
            size = level[1] if len(level) > 1 else 0
            normalized.append([price / 100 if price is not None and price > 1 else price, size])
    return normalized


def _complement(price: float | None) -> float | None:
    if price is None:
        return None
    return 1.0 - price


def _append_error(current: str, value: str) -> str:
    return ";".join(part for part in [current, value] if part)


def _new_run_id() -> str:
    return datetime.now(UTC).strftime("fifa-%Y%m%dT%H%M%SZ")


if __name__ == "__main__":
    sys.exit(snapshot_cli_main())
