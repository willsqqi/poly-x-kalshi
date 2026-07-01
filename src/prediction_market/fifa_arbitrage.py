from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import time
from io import BytesIO
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pandas as pd
from rapidfuzz import fuzz

from .collectors import KALSHI_BASE, POLYMARKET_CLOB_BASE, POLYMARKET_GAMMA_BASE
from .utils import best_ask, best_bid, compact_json, parse_json_array, parse_timestamp, to_float, total_size, utc_now_iso

try:  # pragma: no cover - exercised by integration-style semantic runs
    import numpy as np
except ImportError:  # pragma: no cover - keep the scanner usable without optional numeric acceleration
    np = None

FIFA_KEYWORDS = ("world cup", "world soccer cup", "world soccer", "worldcup", "fifa")
SPORTS_KEYWORDS = (
    "world cup",
    "world soccer cup",
    "world soccer",
    "worldcup",
    "fifa",
    "soccer",
    "football",
    "nfl",
    "nba",
    "wnba",
    "mlb",
    "nhl",
    "hockey",
    "basketball",
    "baseball",
    "tennis",
    "itf",
    "atp",
    "wta",
    "ufc",
    "mma",
    "boxing",
    "golf",
    "pga",
    "pga tour",
    "travelers championship",
    "esports",
    "e-sports",
    "valorant",
    "nascar",
    "formula 1",
    "formula one",
    "f1",
    "grand prix",
    "pole position",
    "qualifying",
    "premier league",
    "champions league",
    "europa league",
    "laliga",
    "serie a",
    "bundesliga",
    "college football",
    "college basketball",
    "march madness",
)
POLYMARKET_EVENT_TAG_SLUGS = ("soccer", "world-cup")
SPORTS_POLYMARKET_EVENT_TAG_SLUGS = (
    "sports",
    "soccer",
    "world-cup",
    "football",
    "nfl",
    "nba",
    "wnba",
    "mlb",
    "nhl",
    "ufc",
    "tennis",
    "itf",
    "atp",
    "wta",
    "wimbledon",
    "golf",
    "pga",
    "pga-tour",
    "esports",
    "valorant",
    "f1",
    "formula-1",
)
KALSHI_FIFA_SERIES_TICKERS = ("KXWCGAME", "KXWCHOST", "KXMENWORLDCUP")
KALSHI_SPORTS_SERIES_TICKERS = (
    "KXWCGAME",
    "KXWCHOST",
    "KXMENWORLDCUP",
    "KXNBA",
    "KXNBAGAME",
    "KXNFL",
    "KXNFLGAME",
    "KXMLB",
    "KXMLBGAME",
    "KXNHL",
    "KXNHLGAME",
    "KXWNBA",
    "KXWNBAGAME",
    "KXUFC",
    "KXITFWMATCH",
    "KXITFMMATCH",
    "KXWTAMATCH",
    "KXATPMATCH",
    "KXPGATOUR",
    "KXVALORANTGAME",
    "KXF1POLE",
)
TEAM_ALIASES_BY_SPORT = {
    "mlb": {
        "a s": "athletics",
        "as": "athletics",
        "athletics": "athletics",
        "oakland": "athletics",
        "oakland athletics": "athletics",
        "arizona": "arizona diamondbacks",
        "atlanta": "atlanta braves",
        "baltimore": "baltimore orioles",
        "boston": "boston red sox",
        "chicago a": "chicago white sox",
        "chicago n": "chicago cubs",
        "cincinnati": "cincinnati reds",
        "cleveland": "cleveland guardians",
        "colorado": "colorado rockies",
        "detroit": "detroit tigers",
        "houston": "houston astros",
        "kansas city": "kansas city royals",
        "los angeles a": "los angeles angels",
        "los angeles d": "los angeles dodgers",
        "miami": "miami marlins",
        "milwaukee": "milwaukee brewers",
        "minnesota": "minnesota twins",
        "new york a": "new york yankees",
        "new york y": "new york yankees",
        "new york yanks": "new york yankees",
        "nyy": "new york yankees",
        "new york n": "new york mets",
        "nym": "new york mets",
        "philadelphia": "philadelphia phillies",
        "pittsburgh": "pittsburgh pirates",
        "san diego": "san diego padres",
        "san francisco": "san francisco giants",
        "sf": "san francisco giants",
        "sfg": "san francisco giants",
        "seattle": "seattle mariners",
        "st louis": "st louis cardinals",
        "st. louis": "st louis cardinals",
        "tampa bay": "tampa bay rays",
        "texas": "texas rangers",
        "toronto": "toronto blue jays",
        "washington": "washington nationals",
    },
    "nfl": {
        "arizona": "arizona cardinals",
        "atlanta": "atlanta falcons",
        "baltimore": "baltimore ravens",
        "buffalo": "buffalo bills",
        "carolina": "carolina panthers",
        "chicago": "chicago bears",
        "cincinnati": "cincinnati bengals",
        "cleveland": "cleveland browns",
        "dallas": "dallas cowboys",
        "denver": "denver broncos",
        "detroit": "detroit lions",
        "green bay": "green bay packers",
        "houston": "houston texans",
        "indianapolis": "indianapolis colts",
        "jacksonville": "jacksonville jaguars",
        "kansas city": "kansas city chiefs",
        "las vegas": "las vegas raiders",
        "los angeles c": "los angeles chargers",
        "los angeles r": "los angeles rams",
        "miami": "miami dolphins",
        "minnesota": "minnesota vikings",
        "new england": "new england patriots",
        "new orleans": "new orleans saints",
        "new york g": "new york giants",
        "new york j": "new york jets",
        "philadelphia": "philadelphia eagles",
        "pittsburgh": "pittsburgh steelers",
        "san francisco": "san francisco 49ers",
        "sf": "san francisco 49ers",
        "sfo": "san francisco 49ers",
        "seattle": "seattle seahawks",
        "tampa bay": "tampa bay buccaneers",
        "tennessee": "tennessee titans",
        "washington": "washington commanders",
    },
    "wnba": {
        "atlanta": "atlanta dream",
        "chicago": "chicago sky",
        "connecticut": "connecticut sun",
        "dallas": "dallas wings",
        "golden state": "golden state valkyries",
        "indiana": "indiana fever",
        "las vegas": "las vegas aces",
        "los angeles": "los angeles sparks",
        "minnesota": "minnesota lynx",
        "new york": "new york liberty",
        "phoenix": "phoenix mercury",
        "portland": "portland fire",
        "seattle": "seattle storm",
        "toronto": "toronto tempo",
        "washington": "washington mystics",
    },
    "nba": {
        "atlanta": "atlanta hawks",
        "boston": "boston celtics",
        "brooklyn": "brooklyn nets",
        "charlotte": "charlotte hornets",
        "chicago": "chicago bulls",
        "cleveland": "cleveland cavaliers",
        "dallas": "dallas mavericks",
        "denver": "denver nuggets",
        "detroit": "detroit pistons",
        "golden state": "golden state warriors",
        "houston": "houston rockets",
        "indiana": "indiana pacers",
        "los angeles c": "los angeles clippers",
        "los angeles l": "los angeles lakers",
        "memphis": "memphis grizzlies",
        "miami": "miami heat",
        "milwaukee": "milwaukee bucks",
        "minnesota": "minnesota timberwolves",
        "new orleans": "new orleans pelicans",
        "new york": "new york knicks",
        "oklahoma city": "oklahoma city thunder",
        "orlando": "orlando magic",
        "philadelphia": "philadelphia 76ers",
        "phoenix": "phoenix suns",
        "portland": "portland trail blazers",
        "sacramento": "sacramento kings",
        "san antonio": "san antonio spurs",
        "toronto": "toronto raptors",
        "utah": "utah jazz",
        "washington": "washington wizards",
    },
    "nhl": {
        "anaheim": "anaheim ducks",
        "boston": "boston bruins",
        "buffalo": "buffalo sabres",
        "calgary": "calgary flames",
        "carolina": "carolina hurricanes",
        "chicago": "chicago blackhawks",
        "colorado": "colorado avalanche",
        "columbus": "columbus blue jackets",
        "dallas": "dallas stars",
        "detroit": "detroit red wings",
        "edmonton": "edmonton oilers",
        "florida": "florida panthers",
        "los angeles": "los angeles kings",
        "minnesota": "minnesota wild",
        "montreal": "montreal canadiens",
        "nashville": "nashville predators",
        "new jersey": "new jersey devils",
        "new york i": "new york islanders",
        "new york r": "new york rangers",
        "ottawa": "ottawa senators",
        "philadelphia": "philadelphia flyers",
        "pittsburgh": "pittsburgh penguins",
        "san jose": "san jose sharks",
        "seattle": "seattle kraken",
        "st louis": "st louis blues",
        "tampa bay": "tampa bay lightning",
        "toronto": "toronto maple leafs",
        "utah": "utah mammoth",
        "vancouver": "vancouver canucks",
        "vegas": "vegas golden knights",
        "washington": "washington capitals",
        "winnipeg": "winnipeg jets",
    },
}
DEFAULT_MAPPING_PATH = "config/fifa_market_mappings.csv"
DEFAULT_SPORTS_MAPPING_PATH = "config/cross_sports_market_mappings.csv"
DEFAULT_OUTPUT_DIR = "data/fifa_arbitrage"
DEFAULT_SPORTS_OUTPUT_DIR = "data/cross_sports_arbitrage"
DEFAULT_INTERVAL_SECONDS = 60.0
DEFAULT_MIN_NET_EDGE = 0.02
DEFAULT_SLIPPAGE_BUFFER_PER_LEG = 0.005
DEFAULT_FEE_BUFFER_TOTAL = 0.01
DEFAULT_MIN_DEPTH_PER_LEG = 10.0
DEFAULT_MARKET_LIMIT = 0
DEFAULT_WATCH_MARKET_LIMIT = 1_000
DEFAULT_TIMEOUT_SECONDS = 30.0
RETRYABLE_STATUSES = {429, 500, 502, 503, 504}
DEFAULT_EMBEDDING_MIN_SCORE = 58.0
DEFAULT_EMBEDDING_TOP_K = 5
EMBEDDING_DIMENSIONS = 1024
EMBEDDING_PREFILTER_LIMIT = 150
DEFAULT_EVENT_SUGGESTION_MIN_SCORE = 55.0
EVENT_FULL_COMPARE_LIMIT = 300
EVENT_SEMANTIC_PREFILTER_LIMIT = 80
EVENT_PAIR_MARKET_TOP_K_PER_PM = 3
EVENT_PAIR_MARKET_EXPANSION_LIMIT = 40
DEFAULT_SEMANTIC_EMBEDDING_PROVIDER = "off"
SEMANTIC_EMBEDDING_PROVIDERS = {"off", "local", "vertex-gemini"}
DEFAULT_SEMANTIC_EMBEDDING_DIM = 3072
DEFAULT_SEMANTIC_TOP_K = 20
DEFAULT_SEMANTIC_MIN_SCORE = 72.0
LOCAL_SEMANTIC_EMBEDDING_MODEL = "local-hashed-token-v1"
VERTEX_GEMINI_EMBEDDING_MODEL = os.getenv("POLY_X_KALSHI_VERTEX_GEMINI_EMBEDDING_MODEL", "gemini-embedding-2")
VERTEX_GEMINI_TASK_TYPE = "SEMANTIC_SIMILARITY"
VERTEX_GEMINI_MODELS_WITHOUT_TASK_TYPE = {"gemini-embedding-2"}
VERTEX_GEMINI_MAX_TEXTS_PER_REQUEST = 250
DEFAULT_VERTEX_GEMINI_BATCH_SIZE = 64
DEFAULT_VERTEX_GEMINI_BATCH_SLEEP_SECONDS = 5.0
DEFAULT_VERTEX_GEMINI_RETRY_INITIAL_SECONDS = 60.0
DEFAULT_VERTEX_GEMINI_RETRY_MAX_SECONDS = 300.0
DEFAULT_VERTEX_GEMINI_MAX_RETRIES = 8
DEFAULT_SEMANTIC_CACHE_FLUSH_BATCHES = 2
DEFAULT_SEMANTIC_MAX_EMBEDDING_TEXTS = 0
AI_PAIR_REVIEW_PROVIDERS = {"off", "vertex-gemini"}
DEFAULT_AI_PAIR_REVIEW_PROVIDER = "off"
DEFAULT_AI_PAIR_REVIEW_MODEL = "gemini-2.0-flash"
DEFAULT_AI_PAIR_REVIEW_LIMIT = 250
DEFAULT_AI_PAIR_REVIEW_MIN_SCORE = 80.0
DEFAULT_AI_PAIR_REVIEW_SLEEP_SECONDS = 0.2
DEFAULT_AI_PAIR_REVIEW_MAX_RETRIES = 3
SEMANTIC_BIGRAM_SEPARATORS = {"vs", "v", "versus", "against"}
EMBEDDING_STOPWORDS = {
    "a",
    "an",
    "and",
    "at",
    "be",
    "for",
    "game",
    "if",
    "in",
    "is",
    "market",
    "match",
    "of",
    "on",
    "or",
    "professional",
    "resolves",
    "the",
    "then",
    "this",
    "to",
    "vs",
    "will",
    "win",
    "winner",
}
SEMANTIC_GENERAL_ALIASES = {
    "bosnia herzegovina": "bosnia and herzegovina",
    "man city": "manchester city",
    "man utd": "manchester united",
    "man u": "manchester united",
    "t1a": "t1 academy",
    "t1 academy": "t1 academy",
    "so sweet": "saigon omega",
    "sosweet": "saigon omega",
    "soswee": "saigon omega",
    "saigon omega": "saigon omega",
}

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
    "embedding_score",
    "lexical_score",
    "combined_score",
    "gemini_embedding_score",
    "semantic_combined_score",
    "semantic_provider",
    "embedding_model",
    "embedding_dim",
    "embedding_text_hash",
    "suggestion_method",
    "suggestion_status",
    "market_type",
    "review_notes",
    "ai_review_provider",
    "ai_review_model",
    "ai_review_status",
    "ai_review_confidence",
    "ai_event_match",
    "ai_market_match",
    "ai_outcome_match",
    "ai_settlement_match",
    "ai_recommendation",
    "ai_review_reason",
    "ai_risk_flags",
    "ai_reviewed_at",
    "ai_raw_response",
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
SUGGESTED_MAPPING_NUMERIC_COLUMNS = {
    "match_score",
    "embedding_score",
    "lexical_score",
    "combined_score",
    "gemini_embedding_score",
    "semantic_combined_score",
    "ai_review_confidence",
}

MARKET_EMBEDDING_COLUMNS = [
    "run_id",
    "retrieved_at",
    "venue",
    "market_id",
    "ticker_or_slug",
    "yes_token_id",
    "outcome_label",
    "market_type",
    "semantic_provider",
    "embedding_model",
    "embedding_dim",
    "embedding_key",
    "embedding_cache_key",
    "embedding_text_hash",
    "embedding_text",
    "embedding_vector",
    "embedded_at",
    "cache_status",
    "error",
]

MAPPING_COLUMNS = [
    "mapping_id",
    "status",
    "lifecycle_status",
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
DISCOVERY_REVIEW_TABLES = {"venue_market_candidates", "approval_candidates", "suggested_mappings", "market_embeddings"}
PRICE_TABLES = {"orderbook_snapshots", "arbitrage_alerts", "strategy_signals"}


def fifa_keyword_hits(value: Any) -> list[str]:
    return keyword_hits(value, FIFA_KEYWORDS)


def sports_keyword_hits(value: Any) -> list[str]:
    return keyword_hits(value, SPORTS_KEYWORDS)


def keyword_hits(value: Any, keywords: tuple[str, ...] = FIFA_KEYWORDS) -> list[str]:
    text = _search_text(value)
    hits: list[str] = []
    for keyword in keywords:
        if _keyword_matches(text, keyword):
            hits.append(keyword)
    return hits


def _keyword_matches(text: str, keyword: str) -> bool:
    clean_keyword = keyword.lower().strip()
    if not clean_keyword:
        return False
    if re.fullmatch(r"[a-z0-9]{1,4}", clean_keyword):
        return re.search(rf"(?<![a-z0-9]){re.escape(clean_keyword)}(?![a-z0-9])", text) is not None
    return clean_keyword in text


def is_fifa_market(value: Any) -> bool:
    return bool(fifa_keyword_hits(value))


def is_sports_market(value: Any) -> bool:
    return bool(sports_keyword_hits(value))


def _passes_keyword_filter(value: Any, keywords: tuple[str, ...]) -> bool:
    return not keywords or bool(keyword_hits(value, keywords))


def _has_market_scan_budget(inspected: int, max_items: int) -> bool:
    return max_items <= 0 or inspected < max_items


def _page_limit(page_size: int, inspected: int, max_items: int) -> int:
    page_size = max(1, int(page_size))
    if max_items <= 0:
        return page_size
    return max(0, min(page_size, max_items - inspected))


def fetch_polymarket_fifa_markets(
    client: httpx.Client,
    max_markets: int = DEFAULT_MARKET_LIMIT,
    page_size: int = 500,
    sleep_seconds: float = 0.0,
) -> list[dict[str, Any]]:
    return fetch_polymarket_markets(
        client,
        max_markets=max_markets,
        page_size=page_size,
        sleep_seconds=sleep_seconds,
        keywords=FIFA_KEYWORDS,
        event_tag_slugs=POLYMARKET_EVENT_TAG_SLUGS,
    )


def fetch_polymarket_sports_markets(
    client: httpx.Client,
    max_markets: int = DEFAULT_MARKET_LIMIT,
    page_size: int = 500,
    sleep_seconds: float = 0.0,
) -> list[dict[str, Any]]:
    return fetch_polymarket_markets(
        client,
        max_markets=max_markets,
        page_size=page_size,
        sleep_seconds=sleep_seconds,
        keywords=SPORTS_KEYWORDS,
        event_tag_slugs=SPORTS_POLYMARKET_EVENT_TAG_SLUGS,
    )


def fetch_polymarket_tags(
    client: httpx.Client,
    max_tags: int = DEFAULT_MARKET_LIMIT,
    page_size: int = 500,
    sleep_seconds: float = 0.0,
) -> list[dict[str, Any]]:
    tags: list[dict[str, Any]] = []
    seen_slugs: set[str] = set()
    inspected = 0
    offset = 0
    while _has_market_scan_budget(inspected, max_tags):
        limit = _page_limit(page_size, inspected, max_tags)
        if limit <= 0:
            break
        payload = request_json_with_retry(
            client,
            f"{POLYMARKET_GAMMA_BASE}/tags",
            params={"limit": limit, "offset": offset},
        )
        batch = _tags_from_payload(payload)
        if not batch:
            break
        inspected += len(batch)
        offset += len(batch)
        for tag in batch:
            slug = str(tag.get("slug") or "").strip()
            if not slug or slug in seen_slugs:
                continue
            tags.append(tag)
            seen_slugs.add(slug)
        if len(batch) < limit:
            break
        if sleep_seconds:
            time.sleep(sleep_seconds)
    return tags


def fetch_polymarket_markets(
    client: httpx.Client,
    max_markets: int = DEFAULT_MARKET_LIMIT,
    page_size: int = 500,
    sleep_seconds: float = 0.0,
    keywords: tuple[str, ...] = FIFA_KEYWORDS,
    event_tag_slugs: tuple[str, ...] = POLYMARKET_EVENT_TAG_SLUGS,
    include_general_search: bool = True,
    discover_event_tags: bool = False,
) -> list[dict[str, Any]]:
    markets: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    markets_by_id: dict[str, dict[str, Any]] = {}
    if include_general_search:
        inspected = 0
        offset = 0
        while _has_market_scan_budget(inspected, max_markets):
            limit = _page_limit(page_size, inspected, max_markets)
            if limit <= 0:
                break
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
                if _passes_keyword_filter(_polymarket_search_payload(market), keywords):
                    markets.append(market)
                    seen_ids.add(market_id)
                    markets_by_id[market_id] = market
            if len(batch) < limit:
                break
            if sleep_seconds:
                time.sleep(sleep_seconds)

    for event in fetch_polymarket_events(
        client,
        max_events=max_markets,
        page_size=page_size,
        sleep_seconds=sleep_seconds,
        keywords=keywords,
        event_tag_slugs=event_tag_slugs,
        discover_tags=discover_event_tags,
    ):
        event_context = _polymarket_event_context(event)
        for market in event.get("markets", []):
            if not isinstance(market, dict):
                continue
            market_id = str(market.get("conditionId") or market.get("id") or "")
            if market_id in seen_ids:
                existing = markets_by_id.get(market_id)
                if existing is not None:
                    existing.update(event_context)
                continue
            if not _has_two_polymarket_tokens(market):
                continue
            if market.get("active") is not True or market.get("closed") is True:
                continue
            enriched_market = {**market, **event_context}
            if _passes_keyword_filter(_polymarket_search_payload(enriched_market), keywords):
                markets.append(enriched_market)
                seen_ids.add(market_id)
                markets_by_id[market_id] = enriched_market
    return markets


def fetch_polymarket_fifa_events(
    client: httpx.Client,
    max_events: int = DEFAULT_MARKET_LIMIT,
    page_size: int = 200,
    sleep_seconds: float = 0.0,
) -> list[dict[str, Any]]:
    return fetch_polymarket_events(
        client,
        max_events=max_events,
        page_size=page_size,
        sleep_seconds=sleep_seconds,
        keywords=FIFA_KEYWORDS,
        event_tag_slugs=POLYMARKET_EVENT_TAG_SLUGS,
    )


def fetch_polymarket_events(
    client: httpx.Client,
    max_events: int = DEFAULT_MARKET_LIMIT,
    page_size: int = 200,
    sleep_seconds: float = 0.0,
    keywords: tuple[str, ...] = FIFA_KEYWORDS,
    event_tag_slugs: tuple[str, ...] = POLYMARKET_EVENT_TAG_SLUGS,
    discover_tags: bool = False,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    seen_slugs: set[str] = set()
    query_tag_slugs = event_tag_slugs or ("",)
    if discover_tags:
        discovered_slugs = tuple(
            str(tag.get("slug") or "").strip()
            for tag in fetch_polymarket_tags(client, max_tags=0, page_size=page_size, sleep_seconds=sleep_seconds)
            if str(tag.get("slug") or "").strip()
        )
        query_tag_slugs = tuple(dict.fromkeys(("", *event_tag_slugs, *discovered_slugs)))
    for tag_slug in query_tag_slugs:
        inspected = 0
        offset = 0
        while _has_market_scan_budget(inspected, max_events):
            limit = _page_limit(page_size, inspected, max_events)
            if limit <= 0:
                break
            params: dict[str, Any] = {
                "active": "true",
                "closed": "false",
                "limit": limit,
                "offset": offset,
                "order": "volume",
                "ascending": "false",
            }
            if tag_slug:
                params["tag_slug"] = tag_slug
            payload = request_json_with_retry(client, f"{POLYMARKET_GAMMA_BASE}/events", params=params)
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
                if _passes_keyword_filter(_polymarket_event_search_payload(event), keywords):
                    events.append(event)
                    seen_slugs.add(slug)
            if len(batch) < limit:
                break
            if sleep_seconds:
                time.sleep(sleep_seconds)
    return events


def fetch_kalshi_fifa_markets(
    client: httpx.Client,
    max_markets: int = DEFAULT_MARKET_LIMIT,
    page_size: int = 200,
    sleep_seconds: float = 0.0,
) -> list[dict[str, Any]]:
    return fetch_kalshi_markets(
        client,
        max_markets=max_markets,
        page_size=page_size,
        sleep_seconds=sleep_seconds,
        keywords=FIFA_KEYWORDS,
        series_tickers=KALSHI_FIFA_SERIES_TICKERS,
    )


def fetch_kalshi_sports_markets(
    client: httpx.Client,
    max_markets: int = DEFAULT_MARKET_LIMIT,
    page_size: int = 200,
    sleep_seconds: float = 0.0,
) -> list[dict[str, Any]]:
    return fetch_kalshi_markets(
        client,
        max_markets=max_markets,
        page_size=page_size,
        sleep_seconds=sleep_seconds,
        keywords=SPORTS_KEYWORDS,
        series_tickers=KALSHI_SPORTS_SERIES_TICKERS,
    )


def fetch_kalshi_markets(
    client: httpx.Client,
    max_markets: int = DEFAULT_MARKET_LIMIT,
    page_size: int = 200,
    sleep_seconds: float = 0.0,
    keywords: tuple[str, ...] = FIFA_KEYWORDS,
    series_tickers: tuple[str, ...] = KALSHI_FIFA_SERIES_TICKERS,
    include_general_search: bool = True,
    expand_event_markets: bool = True,
) -> list[dict[str, Any]]:
    markets: list[dict[str, Any]] = []
    seen_tickers: set[str] = set()
    markets_by_ticker: dict[str, dict[str, Any]] = {}
    tickers_by_event: dict[str, list[str]] = {}
    if include_general_search:
        general_scan_limit = max_markets
        inspected = 0
        cursor = ""
        while _has_market_scan_budget(inspected, general_scan_limit):
            limit = _page_limit(page_size, inspected, general_scan_limit)
            if limit <= 0:
                break
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
                if _passes_keyword_filter(_kalshi_search_payload(market), keywords):
                    markets.append(market)
                    seen_tickers.add(ticker)
                    markets_by_ticker[ticker] = market
                    event_ticker = str(market.get("event_ticker") or "")
                    if event_ticker:
                        tickers_by_event.setdefault(event_ticker, []).append(ticker)
            cursor = str(payload.get("cursor") or "") if isinstance(payload, dict) else ""
            if not cursor or len(batch) < limit:
                break
            if sleep_seconds:
                time.sleep(sleep_seconds)

    for event in fetch_kalshi_events(
        client,
        max_events=max_markets,
        page_size=min(page_size, 200),
        sleep_seconds=sleep_seconds,
        keywords=keywords,
        series_tickers=series_tickers,
        include_general_search=include_general_search,
    ):
        event_ticker = str(event.get("event_ticker") or "")
        if not event_ticker:
            continue
        event_context = {
            "_event_context_title": event.get("title", ""),
            "_event_context_ticker": event_ticker,
            "_event_context_payload": event,
        }
        if not expand_event_markets:
            for ticker in tickers_by_event.get(event_ticker, []):
                existing = markets_by_ticker.get(ticker)
                if existing is not None:
                    existing.update(event_context)
            continue
        for market in _fetch_kalshi_event_markets(client, event_ticker, page_size=page_size, sleep_seconds=sleep_seconds):
            ticker = str(market.get("ticker") or "")
            if not ticker:
                continue
            market.update(event_context)
            if ticker in seen_tickers:
                existing = markets_by_ticker.get(ticker)
                if existing is not None:
                    existing.update(event_context)
                continue
            markets.append(market)
            seen_tickers.add(ticker)
            markets_by_ticker[ticker] = market
            if event_ticker:
                tickers_by_event.setdefault(event_ticker, []).append(ticker)
    return markets


def _fetch_kalshi_event_markets(
    client: httpx.Client,
    event_ticker: str,
    page_size: int = 200,
    sleep_seconds: float = 0.0,
) -> list[dict[str, Any]]:
    markets: list[dict[str, Any]] = []
    cursor = ""
    limit = max(1, min(int(page_size), 200))
    while True:
        params: dict[str, Any] = {"status": "open", "event_ticker": event_ticker, "limit": limit}
        if cursor:
            params["cursor"] = cursor
        payload = request_json_with_retry(client, f"{KALSHI_BASE}/markets", params=params)
        batch = _markets_from_payload(payload)
        if not batch:
            break
        markets.extend(batch)
        cursor = str(payload.get("cursor") or "") if isinstance(payload, dict) else ""
        if not cursor or len(batch) < limit:
            break
        if sleep_seconds:
            time.sleep(sleep_seconds)
    return markets


def fetch_kalshi_fifa_events(
    client: httpx.Client,
    max_events: int = DEFAULT_MARKET_LIMIT,
    page_size: int = 200,
    sleep_seconds: float = 0.0,
) -> list[dict[str, Any]]:
    return fetch_kalshi_events(
        client,
        max_events=max_events,
        page_size=page_size,
        sleep_seconds=sleep_seconds,
        keywords=FIFA_KEYWORDS,
        series_tickers=KALSHI_FIFA_SERIES_TICKERS,
    )


def fetch_kalshi_events(
    client: httpx.Client,
    max_events: int = DEFAULT_MARKET_LIMIT,
    page_size: int = 200,
    sleep_seconds: float = 0.0,
    keywords: tuple[str, ...] = FIFA_KEYWORDS,
    series_tickers: tuple[str, ...] = KALSHI_FIFA_SERIES_TICKERS,
    include_general_search: bool = True,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    seen_event_tickers: set[str] = set()
    query_templates: list[dict[str, Any]] = [{"status": "open"}] if include_general_search else []
    query_templates.extend({"status": "open", "series_ticker": series_ticker} for series_ticker in series_tickers)
    for query_template in query_templates:
        inspected = 0
        cursor = ""
        while _has_market_scan_budget(inspected, max_events):
            limit = _page_limit(page_size, inspected, max_events)
            if limit <= 0:
                break
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
                if _passes_keyword_filter(_kalshi_event_search_payload(event), keywords) or str(event.get("series_ticker") or "") in series_tickers:
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
    return normalize_market_candidates(
        polymarket_markets,
        kalshi_markets,
        run_id=run_id,
        retrieved_at=retrieved_at,
        keywords=FIFA_KEYWORDS,
    )


def normalize_sports_candidates(
    polymarket_markets: list[dict[str, Any]],
    kalshi_markets: list[dict[str, Any]],
    run_id: str,
    retrieved_at: str | None = None,
) -> pd.DataFrame:
    return normalize_market_candidates(
        polymarket_markets,
        kalshi_markets,
        run_id=run_id,
        retrieved_at=retrieved_at,
        keywords=SPORTS_KEYWORDS,
    )


def normalize_market_candidates(
    polymarket_markets: list[dict[str, Any]],
    kalshi_markets: list[dict[str, Any]],
    run_id: str,
    retrieved_at: str | None = None,
    keywords: tuple[str, ...] = FIFA_KEYWORDS,
) -> pd.DataFrame:
    retrieved_at = retrieved_at or utc_now_iso()
    rows: list[dict[str, Any]] = []
    for market in polymarket_markets:
        outcomes = parse_json_array(market.get("outcomes"))
        token_ids = parse_json_array(market.get("clobTokenIds"))
        for oriented_outcomes, oriented_token_ids in _polymarket_candidate_orientations(market, outcomes, token_ids):
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
                    "outcomes": compact_json(oriented_outcomes),
                    "yes_token_id": str(oriented_token_ids[0]) if len(oriented_token_ids) > 0 else "",
                    "no_token_id": str(oriented_token_ids[1]) if len(oriented_token_ids) > 1 else "",
                    "rules_text": _polymarket_rules_text(market),
                    "keyword_hits": ",".join(keyword_hits(_polymarket_search_payload(market), keywords)),
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
                "keyword_hits": ",".join(keyword_hits(_kalshi_search_payload(market), keywords)),
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


def _polymarket_candidate_orientations(market: dict[str, Any], outcomes: list[Any], token_ids: list[Any]) -> list[tuple[list[Any], list[Any]]]:
    if not _should_expand_named_outcome_market(market, outcomes, token_ids):
        return [(outcomes, token_ids)]
    return [
        ([outcomes[0], outcomes[1]], [token_ids[0], token_ids[1]]),
        ([outcomes[1], outcomes[0]], [token_ids[1], token_ids[0]]),
    ]


def _should_expand_named_outcome_market(market: dict[str, Any], outcomes: list[Any], token_ids: list[Any]) -> bool:
    if len(outcomes) != 2 or len(token_ids) != 2:
        return False
    normalized = {_normalize_name(outcome) for outcome in outcomes}
    if normalized & {"yes", "no", "over", "under"}:
        return False
    if any(re.search(r"\b(?:or|tie)\b", outcome) for outcome in normalized):
        return False
    text = _search_text(
        {
            "slug": market.get("slug"),
            "question": market.get("question"),
            "title": market.get("title"),
            "description": market.get("description"),
            "sportsMarketType": market.get("sportsMarketType"),
            "seriesSlug": market.get("seriesSlug"),
        }
    )
    if not any(term in text for term in SPORTS_KEYWORDS + ("moneyline",)):
        return False
    title = str(market.get("question") or market.get("title") or "")
    if not _teams_from_match_title(title):
        return False
    prop_text = _normalize_name(" ".join(str(market.get(key) or "") for key in ("question", "title", "slug", "description")))
    excluded_terms = (
        "spread",
        "handicap",
        "over under",
        "total",
        "first inning",
        "1st inning",
        "first 5",
        "1st 5",
        "extra innings",
        "run scored",
    )
    return not any(term in prop_text for term in excluded_terms)


def suggest_manual_mappings(
    approval_candidates: pd.DataFrame,
    min_score: float = 72.0,
    embedding_min_score: float = DEFAULT_EMBEDDING_MIN_SCORE,
    embedding_top_k: int = DEFAULT_EMBEDDING_TOP_K,
    semantic_embeddings: pd.DataFrame | None = None,
    semantic_min_score: float = DEFAULT_SEMANTIC_MIN_SCORE,
    semantic_top_k: int = DEFAULT_SEMANTIC_TOP_K,
) -> pd.DataFrame:
    if approval_candidates.empty:
        return pd.DataFrame(columns=SUGGESTED_MAPPING_COLUMNS)
    approval_candidates = _suggestion_input_frame(approval_candidates)
    polymarket = approval_candidates[approval_candidates["venue"] == "polymarket"].copy()
    kalshi = approval_candidates[approval_candidates["venue"] == "kalshi"].copy()
    if polymarket.empty or kalshi.empty:
        return pd.DataFrame(columns=SUGGESTED_MAPPING_COLUMNS)
    for frame in (polymarket, kalshi):
        frame["_normalized_outcome"] = frame.apply(_candidate_outcome_key, axis=1)
        frame["_normalized_event_title"] = frame["event_title"].map(_normalize_name)
        frame["_tennis_match_key"] = frame.apply(_tennis_match_key, axis=1)
        frame["_tennis_outcome_key"] = frame.apply(_tennis_outcome_key, axis=1)
        frame["_sport_context"] = frame.apply(_market_sport_context, axis=1)
        frame["_scope_key"] = frame.apply(_event_scope_key, axis=1)
        frame["_timeframe_key"] = frame.apply(_market_timeframe_key, axis=1)
        frame["_local_schedule_date_key"] = frame.apply(_local_schedule_date_key, axis=1)
        frame["_is_bundle_market"] = frame.apply(_looks_like_bundle_market, axis=1)
        frame["_is_exact_score_market"] = frame.apply(_is_exact_score_market, axis=1)
        frame["_is_player_prop_market"] = frame.apply(_is_player_prop_market, axis=1)
        frame["_champion_group_scope_key"] = frame.apply(_champion_group_scope_key, axis=1)
        frame["_unbeaten_champion_scope_key"] = frame.apply(_unbeaten_champion_scope_key, axis=1)
        frame["_embedding_text"] = frame.apply(_candidate_embedding_text, axis=1)
        embedding_texts = [str(value or "") for value in frame["_embedding_text"].tolist()]
        embedding_token_lists = [_embedding_tokens_from_normalized(text) for text in embedding_texts]
        frame["_embedding_token_set"] = [set(tokens) for tokens in embedding_token_lists]
        frame["_important_embedding_token_set"] = [
            _important_embedding_tokens(token_set)
            for token_set in frame["_embedding_token_set"].tolist()
        ]
        frame["_embedding_vector"] = [
            _hashed_embedding_from_tokens(tokens, normalized_text=text)
            for text, tokens in zip(embedding_texts, embedding_token_lists, strict=False)
        ]
        frame["_canonical_embedding_text"] = frame.apply(build_canonical_embedding_text, axis=1)
        frame["_semantic_embedding_text_hash"] = frame["_canonical_embedding_text"].map(_stable_text_hash)
        frame["_semantic_embedding_key"] = frame.apply(_candidate_embedding_key, axis=1)
    semantic_lookup = _semantic_embedding_lookup(semantic_embeddings)
    if semantic_lookup:
        polymarket = polymarket[polymarket.apply(_semantic_row_supported_for_event_matching, axis=1)].copy()
        kalshi = kalshi[kalshi.apply(_semantic_row_supported_for_event_matching, axis=1)].copy()
        if polymarket.empty or kalshi.empty:
            return pd.DataFrame(columns=SUGGESTED_MAPPING_COLUMNS)
        _attach_semantic_vectors(polymarket, semantic_lookup)
        _attach_semantic_vectors(kalshi, semantic_lookup)
    return _event_first_suggest_manual_mappings(
        polymarket,
        kalshi,
        min_score=min_score,
        embedding_min_score=embedding_min_score,
        embedding_top_k=embedding_top_k,
        semantic_min_score=semantic_min_score,
        semantic_top_k=semantic_top_k,
        semantic_enabled=bool(semantic_lookup),
    )


def _legacy_parser_first_suggest_manual_mappings(
    polymarket: pd.DataFrame,
    kalshi: pd.DataFrame,
    *,
    min_score: float,
    embedding_min_score: float,
    embedding_top_k: int,
    semantic_lookup: dict[str, dict[str, Any]],
    semantic_min_score: float,
    semantic_top_k: int,
) -> pd.DataFrame:
    empty_kalshi = kalshi.iloc[0:0]
    kalshi_by_type = {str(market_type or ""): group.copy() for market_type, group in kalshi.groupby("market_type", dropna=False)}
    embedding_index_by_type = {
        market_type: _build_embedding_token_index(group)
        for market_type, group in kalshi_by_type.items()
    }
    kalshi_records_by_index = kalshi.to_dict(orient="index")
    rows: list[dict[str, Any]] = []
    seen_mapping_ids: set[str] = set()
    for _, pm in polymarket.iterrows():
        market_type = str(pm.get("market_type") or "")
        if market_type == "match_winner" and not str(pm.get("_normalized_outcome") or ""):
            continue
        candidates = _prefilter_mapping_candidates(pm, kalshi_by_type.get(market_type, empty_kalshi))
        if len(candidates) > EMBEDDING_PREFILTER_LIMIT:
            candidates = _embedding_prefilter_candidates(
                pm,
                candidates,
                embedding_index_by_type.get(market_type, {}),
            )
        for candidate_index in candidates.index:
            ks = kalshi_records_by_index.get(candidate_index)
            if ks is None:
                continue
            if not _candidate_types_compatible(pm, ks):
                continue
            score = candidate_match_score(pm, ks)
            if score < min_score:
                continue
            row = _suggested_mapping_row(pm, ks, score, lexical_score=score, combined_score=score, suggestion_method="rules")
            seen_mapping_ids.add(str(row["mapping_id"]))
            rows.append(row)

        if embedding_top_k > 0:
            rows.extend(
                _embedding_suggested_mapping_rows(
                    pm,
                    kalshi_by_type,
                    embedding_index_by_type,
                    kalshi_records_by_index,
                    empty_kalshi,
                    min_score=min_score,
                    embedding_min_score=embedding_min_score,
                    top_k=embedding_top_k,
                    seen_mapping_ids=seen_mapping_ids,
                )
            )

        if semantic_top_k <= 0 or not semantic_lookup:
            continue
        rows.extend(
            _semantic_suggested_mapping_rows(
                pm,
                kalshi_by_type,
                embedding_index_by_type,
                kalshi_records_by_index,
                empty_kalshi,
                semantic_min_score=semantic_min_score,
                top_k=semantic_top_k,
                seen_mapping_ids=seen_mapping_ids,
            )
        )
    frame = pd.DataFrame(rows, columns=SUGGESTED_MAPPING_COLUMNS)
    if frame.empty:
        return frame
    for score_column in (
        "match_score",
        "embedding_score",
        "lexical_score",
        "combined_score",
        "gemini_embedding_score",
        "semantic_combined_score",
    ):
        frame[score_column] = pd.to_numeric(frame[score_column], errors="coerce")
    return (
        frame.sort_values(
            ["match_score", "semantic_combined_score", "gemini_embedding_score", "embedding_score", "lexical_score", "market_type"],
            ascending=[False, False, False, False, False, True],
        )
        .drop_duplicates(subset=["mapping_id"], keep="first")
        .reset_index(drop=True)
    )


def _event_first_suggest_manual_mappings(
    polymarket: pd.DataFrame,
    kalshi: pd.DataFrame,
    *,
    min_score: float,
    embedding_min_score: float,
    embedding_top_k: int,
    semantic_min_score: float,
    semantic_top_k: int,
    semantic_enabled: bool,
) -> pd.DataFrame:
    """Suggest pairs by matching event containers before market/outcome rows.

    This path deliberately treats parsed fields such as market_type/outcome/date as
    weak evidence instead of hard gates. The parser is useful context, but manual
    review is the precision layer.
    """
    pm_events = _parserless_event_groups(polymarket)
    ks_events = _parserless_event_groups(kalshi)
    if pm_events.empty or ks_events.empty:
        return pd.DataFrame(columns=SUGGESTED_MAPPING_COLUMNS)

    event_min_score = float(semantic_min_score) if semantic_enabled else min(DEFAULT_EVENT_SUGGESTION_MIN_SCORE, float(min_score), float(semantic_min_score))
    event_top_k = max(1, int(semantic_top_k or 0), int(embedding_top_k or 0), DEFAULT_EMBEDDING_TOP_K)
    rows: list[dict[str, Any]] = []
    seen_mapping_ids: set[str] = set()
    ks_index = _build_event_token_index(ks_events)
    ks_semantic_matrix, ks_semantic_indexes = _event_semantic_matrix(ks_events) if semantic_enabled else (None, [])

    for _, pm_event in pm_events.iterrows():
        event_candidates = None
        if semantic_enabled:
            semantic_indexes = _semantic_event_candidate_indexes(
                pm_event,
                ks_semantic_matrix,
                ks_semantic_indexes,
                limit=max(EVENT_SEMANTIC_PREFILTER_LIMIT, event_top_k * 4),
            )
            if semantic_indexes:
                selected = [index for index in semantic_indexes if index in ks_events.index]
                if selected:
                    event_candidates = ks_events.loc[selected]
        if event_candidates is None:
            event_candidates = _prefilter_event_candidates(pm_event, ks_events, ks_index)
        scored_events: list[tuple[float, dict[str, float], pd.Series]] = []
        for _, ks_event in event_candidates.iterrows():
            if not semantic_enabled and _parserless_event_group_rejection_reason(pm_event, ks_event):
                continue
            score, score_parts = _parserless_event_pair_score(pm_event, ks_event, semantic_enabled=semantic_enabled)
            if score < event_min_score:
                continue
            scored_events.append((score, score_parts, ks_event))
        scored_events.sort(key=lambda item: (item[0], item[1].get("semantic_score", 0.0), item[1].get("lexical_score", 0.0)), reverse=True)

        pm_rows = polymarket.loc[list(pm_event["row_indexes"])]
        for event_score, score_parts, ks_event in scored_events[:event_top_k]:
            ks_rows = kalshi.loc[list(ks_event["row_indexes"])]
            rows.extend(
                _expand_event_pair_to_market_suggestions(
                    pm_event,
                    ks_event,
                    pm_rows,
                    ks_rows,
                    event_score=event_score,
                    event_score_parts=score_parts,
                    seen_mapping_ids=seen_mapping_ids,
                    min_score=float(semantic_min_score if semantic_enabled else 0.0),
                    semantic_enabled=semantic_enabled,
                )
            )

    frame = pd.DataFrame(rows, columns=SUGGESTED_MAPPING_COLUMNS)
    if frame.empty:
        return frame
    for score_column in (
        "match_score",
        "embedding_score",
        "lexical_score",
        "combined_score",
        "gemini_embedding_score",
        "semantic_combined_score",
    ):
        frame[score_column] = pd.to_numeric(frame[score_column], errors="coerce")
    return (
        frame.sort_values(
            ["semantic_combined_score", "match_score", "gemini_embedding_score", "embedding_score", "lexical_score"],
            ascending=[False, False, False, False, False],
        )
        .drop_duplicates(subset=["mapping_id"], keep="first")
        .reset_index(drop=True)
    )


def _parserless_event_groups(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()
    working = frame.copy()
    working["_parserless_event_key"] = working.apply(_parserless_event_key, axis=1)
    rows: list[dict[str, Any]] = []
    for event_key, group in working.groupby("_parserless_event_key", dropna=False, sort=False):
        event_text = _parserless_event_text(group)
        tokens = set(_embedding_tokens(event_text))
        semantic_vectors = [_semantic_vector_from_row(row) for _, row in group.iterrows()]
        semantic_vector = _average_dense_vectors([vector for vector in semantic_vectors if vector])
        rows.append(
            {
                "venue": _first_non_blank(group, "venue"),
                "event_key": str(event_key or ""),
                "event_title": _first_non_blank(group, "event_title") or _first_non_blank(group, "title"),
                "event_date": _first_non_blank(group, "event_date"),
                "event_match_key": _first_non_blank(group, "event_match_key"),
                "event_year": _first_non_blank(group, "event_year"),
                "row_count": int(len(group)),
                "row_indexes": list(group.index),
                "titles_sample": _joined_unique_values(group, "title", limit=12),
                "outcomes_sample": _joined_unique_values(group, "outcome_label", limit=16),
                "tickers_or_slugs": _joined_unique_values(group, "ticker_or_slug", limit=20),
                "settlement_sample": _joined_unique_values(group, "settlement_summary", limit=5),
                "event_text": event_text,
                "event_text_hash": _stable_text_hash(event_text),
                "event_vector": _hashed_text_embedding(event_text),
                "semantic_vector": semantic_vector,
                "semantic_vector_array": _dense_unit_array(semantic_vector),
                "event_token_set": tokens,
                "important_event_token_set": _important_embedding_tokens(tokens),
                "sport_context_set": _non_blank_or_computed_value_set(group, "_sport_context", _market_sport_context),
                "market_type_set": _non_blank_value_set(group, "market_type"),
                "scope_key_set": _non_blank_or_computed_value_set(group, "_scope_key", _event_scope_key),
                "semantic_provider": _first_non_blank(group, "_semantic_provider"),
                "embedding_model": _first_non_blank(group, "_semantic_embedding_model"),
                "embedding_dim": _first_non_blank(group, "_semantic_embedding_dim"),
            }
        )
    return pd.DataFrame(rows)


def _parserless_event_group_rejection_reason(pm_event: pd.Series | dict[str, Any], ks_event: pd.Series | dict[str, Any]) -> str:
    pm_sports = _row_value_set(pm_event, "sport_context_set")
    ks_sports = _row_value_set(ks_event, "sport_context_set")
    if pm_sports and ks_sports and pm_sports.isdisjoint(ks_sports):
        return "excluded_wrong_sport"

    pm_types = _row_value_set(pm_event, "market_type_set")
    ks_types = _row_value_set(ks_event, "market_type_set")
    if pm_types and ks_types and pm_types.isdisjoint(ks_types):
        return "excluded_wrong_market_type"

    return ""


def _parserless_event_key(row: pd.Series | dict[str, Any]) -> str:
    venue = str(_row_get(row, "venue") or "").strip().casefold()
    tennis_key = _tennis_match_key(row)
    if tennis_key:
        return f"{venue}|tennis|{tennis_key}"
    date = str(_row_get(row, "event_date") or "").strip()
    event_title = str(_row_get(row, "event_title") or _row_get(row, "title") or "").strip()
    normalized_title = _normalize_name(event_title)
    if normalized_title:
        return f"{venue}|{date}|{normalized_title}"
    fallback = str(_row_get(row, "ticker_or_slug") or _row_get(row, "market_id") or "").strip()
    return f"{venue}|fallback|{_slugify(fallback)}"


def _parserless_event_text(group: pd.DataFrame) -> str:
    parts = [
        ("event", _first_non_blank(group, "event_title") or _first_non_blank(group, "title")),
        ("date", _first_non_blank(group, "event_date")),
        ("year", _first_non_blank(group, "event_year")),
        ("titles", _joined_unique_values(group, "title", limit=12)),
        ("outcomes", _joined_unique_values(group, "outcome_label", limit=16)),
        ("tickers", _joined_unique_values(group, "ticker_or_slug", limit=20)),
        ("keywords", _joined_unique_values(group, "keyword_hits", limit=8)),
        ("settlement", _joined_unique_values(group, "settlement_summary", limit=5)),
        ("rules", _joined_unique_values(group, "rules_text", limit=3)),
    ]
    return "\n".join(f"{label}: {_semantic_alias_normalize(value)}" for label, value in parts if not _is_blank(value))


def _build_event_token_index(events: pd.DataFrame) -> dict[str, tuple[Any, ...]]:
    index: dict[str, set[Any]] = {}
    if events.empty:
        return {}
    for event_index, event in events.iterrows():
        tokens = event.get("important_event_token_set")
        if not isinstance(tokens, set):
            tokens = _important_embedding_tokens(set(_embedding_tokens(str(event.get("event_text") or ""))))
        for token in tokens:
            index.setdefault(token, set()).add(event_index)
    return {token: tuple(indexes) for token, indexes in index.items()}


def _event_semantic_matrix(events: pd.DataFrame) -> tuple[Any, list[Any]]:
    if np is None or events.empty:
        return None, []
    arrays: list[Any] = []
    indexes: list[Any] = []
    width = 0
    for event_index, event in events.iterrows():
        vector = _dense_value_from_row(event, "semantic_vector_array", "semantic_vector")
        if not _has_dense_value(vector):
            continue
        array = _dense_unit_array(vector)
        if not _has_dense_value(array):
            continue
        if not width:
            width = len(array)
        if len(array) != width:
            continue
        arrays.append(array)
        indexes.append(event_index)
    if not arrays:
        return None, []
    return np.vstack(arrays), indexes


def _semantic_event_candidate_indexes(
    pm_event: pd.Series | dict[str, Any],
    matrix: Any,
    indexes: list[Any],
    *,
    limit: int,
) -> list[Any]:
    if np is None or matrix is None or not indexes:
        return []
    vector = _dense_value_from_row(pm_event, "semantic_vector_array", "semantic_vector")
    if not _has_dense_value(vector):
        return []
    array = _dense_unit_array(vector)
    if not _has_dense_value(array) or len(array) != matrix.shape[1]:
        return []
    scores = matrix @ array
    limit = max(1, min(int(limit), len(indexes)))
    if limit >= len(indexes):
        order = np.argsort(-scores)
    else:
        partition = np.argpartition(-scores, limit - 1)[:limit]
        order = partition[np.argsort(-scores[partition])]
    return [indexes[int(position)] for position in order[:limit]]


def _prefilter_event_candidates(pm_event: pd.Series, kalshi_events: pd.DataFrame, token_index: dict[str, tuple[Any, ...]]) -> pd.DataFrame:
    if kalshi_events.empty or len(kalshi_events) <= EVENT_FULL_COMPARE_LIMIT:
        return kalshi_events
    token_counts: Counter[Any] = Counter()
    tokens = pm_event.get("important_event_token_set")
    if not isinstance(tokens, set):
        tokens = set(_embedding_tokens(str(pm_event.get("event_text") or "")))
    for token in tokens:
        for index in token_index.get(token, ()):
            token_counts[index] += 1
    selected: set[Any] = set()
    ordered_indexes: list[Any] = []
    for index, _ in token_counts.most_common(EVENT_FULL_COMPARE_LIMIT):
        selected.add(index)
        ordered_indexes.append(index)
    event_date = str(pm_event.get("event_date") or "")
    if event_date and "event_date" in kalshi_events.columns:
        for index in kalshi_events[kalshi_events["event_date"].fillna("").astype(str) == event_date].index.tolist():
            if index not in selected:
                selected.add(index)
                ordered_indexes.append(index)
            if len(ordered_indexes) >= EVENT_FULL_COMPARE_LIMIT * 2:
                break
    if ordered_indexes:
        return kalshi_events.loc[[index for index in ordered_indexes if index in kalshi_events.index]]
    return kalshi_events.head(EVENT_FULL_COMPARE_LIMIT)


def _parserless_event_pair_score(
    pm_event: pd.Series | dict[str, Any],
    ks_event: pd.Series | dict[str, Any],
    *,
    semantic_enabled: bool,
) -> tuple[float, dict[str, float]]:
    pm_text = str(_row_get(pm_event, "event_text") or "")
    ks_text = str(_row_get(ks_event, "event_text") or "")
    title_score = float(fuzz.token_set_ratio(str(_row_get(pm_event, "event_title") or ""), str(_row_get(ks_event, "event_title") or "")))
    lexical_score = float(fuzz.token_set_ratio(pm_text[:800], ks_text[:800]))
    local_embedding_score = 100.0 * _embedding_cosine(
        _row_get(pm_event, "event_vector") or _hashed_text_embedding(pm_text),
        _row_get(ks_event, "event_vector") or _hashed_text_embedding(ks_text),
    )
    semantic_score = 0.0
    if semantic_enabled:
        semantic_score = 100.0 * _dense_cosine(
            _dense_value_from_row(pm_event, "semantic_vector_array", "semantic_vector"),
            _dense_value_from_row(ks_event, "semantic_vector_array", "semantic_vector"),
        )
    overlap_score = 100.0 * _token_overlap_score(
        _row_get(pm_event, "important_event_token_set") or set(),
        _row_get(ks_event, "important_event_token_set") or set(),
    )
    date_score = _soft_date_score(pm_event, ks_event)
    if semantic_score:
        combined = 0.78 * semantic_score + 0.08 * local_embedding_score + 0.06 * title_score + 0.04 * lexical_score + 0.02 * overlap_score + 0.02 * date_score
    else:
        combined = 0.34 * local_embedding_score + 0.30 * title_score + 0.22 * lexical_score + 0.10 * overlap_score + 0.04 * date_score
    left_event_key = str(_row_get(pm_event, "event_match_key") or "")
    right_event_key = str(_row_get(ks_event, "event_match_key") or "")
    if not semantic_score and left_event_key and left_event_key == right_event_key:
        combined = max(combined, 96.0)
    compatibility_cap = _parserless_event_pair_compatibility_cap(pm_event, ks_event)
    combined = min(combined, compatibility_cap)
    return float(min(100.0, combined)), {
        "semantic_score": semantic_score,
        "local_embedding_score": local_embedding_score,
        "lexical_score": lexical_score,
        "title_score": title_score,
        "overlap_score": overlap_score,
        "date_score": date_score,
        "compatibility_cap": compatibility_cap,
    }


def _parserless_event_pair_compatibility_cap(pm_event: pd.Series | dict[str, Any], ks_event: pd.Series | dict[str, Any]) -> float:
    cap = 100.0
    pm_sports = _row_value_set(pm_event, "sport_context_set")
    ks_sports = _row_value_set(ks_event, "sport_context_set")
    if pm_sports and ks_sports and pm_sports.isdisjoint(ks_sports):
        cap = min(cap, 65.0)

    pm_types = _row_value_set(pm_event, "market_type_set")
    ks_types = _row_value_set(ks_event, "market_type_set")
    if pm_types and ks_types and pm_types.isdisjoint(ks_types):
        cap = min(cap, 68.0)

    winner_types = {"championship_winner", "pole_position_winner", "host_country"}
    if (pm_types | ks_types) & winner_types:
        pm_scopes = _row_value_set(pm_event, "scope_key_set")
        ks_scopes = _row_value_set(ks_event, "scope_key_set")
        if pm_scopes and ks_scopes and pm_scopes.isdisjoint(ks_scopes):
            cap = min(cap, 68.0)
        pm_year = str(_row_get(pm_event, "event_year") or "")
        ks_year = str(_row_get(ks_event, "event_year") or "")
        if pm_year and ks_year and pm_year != ks_year:
            cap = min(cap, 68.0)

    match_clock_types = {"match_winner", "total", "spread", "set_winner"}
    if (pm_types | ks_types) & match_clock_types:
        if _event_dates_far_apart(str(_row_get(pm_event, "event_date") or ""), str(_row_get(ks_event, "event_date") or "")):
            cap = min(cap, 68.0)
    return cap


def _soft_date_score(left: pd.Series | dict[str, Any], right: pd.Series | dict[str, Any]) -> float:
    left_date = str(_row_get(left, "event_date") or "")
    right_date = str(_row_get(right, "event_date") or "")
    if left_date and right_date:
        return 100.0 if left_date == right_date else 20.0
    left_year = str(_row_get(left, "event_year") or "")
    right_year = str(_row_get(right, "event_year") or "")
    if left_year and right_year:
        return 80.0 if left_year == right_year else 30.0
    return 55.0


def _expand_event_pair_to_market_suggestions(
    pm_event: pd.Series | dict[str, Any],
    ks_event: pd.Series | dict[str, Any],
    pm_rows: pd.DataFrame,
    ks_rows: pd.DataFrame,
    *,
    event_score: float,
    event_score_parts: dict[str, float],
    seen_mapping_ids: set[str],
    min_score: float,
    semantic_enabled: bool,
) -> list[dict[str, Any]]:
    scored: list[tuple[float, float, pd.Series, pd.Series]] = []
    for _, pm in pm_rows.iterrows():
        per_pm: list[tuple[float, float, pd.Series, pd.Series]] = []
        for _, ks in ks_rows.iterrows():
            if _obvious_parserless_pair_rejection_reason(pm, ks, semantic_enabled=semantic_enabled):
                continue
            market_score = _parserless_market_pair_score(pm, ks)
            if semantic_enabled and market_score < min_score:
                continue
            if semantic_enabled:
                combined = min(100.0, 0.40 * event_score + 0.60 * market_score)
            else:
                combined = min(100.0, 0.78 * event_score + 0.22 * market_score)
            left_event_key = str(_row_get(pm, "event_match_key") or "")
            right_event_key = str(_row_get(ks, "event_match_key") or "")
            if not semantic_enabled and left_event_key and left_event_key == right_event_key and _cached_outcomes_compatible(pm, ks):
                combined = 100.0
            if combined < min_score:
                continue
            per_pm.append((combined, market_score, pm, ks))
        per_pm.sort(key=lambda item: (item[0], item[1]), reverse=True)
        scored.extend(per_pm[:EVENT_PAIR_MARKET_TOP_K_PER_PM])
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)

    rows: list[dict[str, Any]] = []
    method = "event_semantic" if event_score_parts.get("semantic_score", 0.0) else "event_embedding"
    for combined, market_score, pm, ks in scored[:EVENT_PAIR_MARKET_EXPANSION_LIMIT]:
        row = _suggested_mapping_row(
            pm,
            ks,
            combined,
            embedding_score=event_score_parts.get("local_embedding_score", ""),
            lexical_score=market_score,
            combined_score=combined,
            gemini_embedding_score=event_score_parts.get("semantic_score", "") if event_score_parts.get("semantic_score") else "",
            semantic_combined_score=event_score,
            semantic_provider=str(_row_get(pm_event, "semantic_provider") or _row_get(ks_event, "semantic_provider") or ""),
            embedding_model=str(_row_get(pm_event, "embedding_model") or _row_get(ks_event, "embedding_model") or ""),
            embedding_dim=str(_row_get(pm_event, "embedding_dim") or _row_get(ks_event, "embedding_dim") or ""),
            embedding_text_hash=str(_row_get(pm_event, "event_text_hash") or ""),
            suggestion_method=method,
        )
        mapping_id = str(row["mapping_id"])
        if mapping_id in seen_mapping_ids:
            continue
        seen_mapping_ids.add(mapping_id)
        row["review_notes"] = _event_first_review_notes(pm_event, ks_event, pm, ks, event_score=event_score, market_score=market_score)
        left_event_key = str(_row_get(pm, "event_match_key") or "")
        right_event_key = str(_row_get(ks, "event_match_key") or "")
        row["event_match_key"] = left_event_key if left_event_key and left_event_key == right_event_key else ""
        rows.append(row)
    return rows


def _obvious_parserless_pair_rejection_reason(
    pm: pd.Series | dict[str, Any],
    ks: pd.Series | dict[str, Any],
    *,
    semantic_enabled: bool = False,
) -> str:
    """Reject only high-confidence mismatches before manual review suggestions.

    The event-first matcher intentionally avoids depending on brittle parsed
    market labels, but some venue facts are reliable enough to prevent noisy
    suggestions: soccer futures should not be paired with Valorant matches, and
    totals should not be paired with match winners.
    """
    if _looks_like_bundle_market(pm) or _looks_like_bundle_market(ks):
        return "excluded_bundle"
    if semantic_enabled:
        return _semantic_market_pair_rejection_reason(pm, ks)

    pm_sport = _cached_sport_context(pm)
    ks_sport = _cached_sport_context(ks)
    if pm_sport and ks_sport and pm_sport != ks_sport:
        return "excluded_wrong_sport"

    pm_type = str(_row_get(pm, "market_type") or "")
    ks_type = str(_row_get(ks, "market_type") or "")
    if pm_type and ks_type and pm_type != ks_type:
        return "excluded_wrong_market_type"

    market_type = pm_type or ks_type
    pm_date = str(_row_get(pm, "event_date") or "")
    ks_date = str(_row_get(ks, "event_date") or "")
    if market_type in {"match_winner", "total", "spread", "set_winner"} and _event_dates_far_apart(pm_date, ks_date):
        return "excluded_wrong_date"
    if market_type == "match_winner":
        reason = _match_winner_pair_gate_reason(pm, ks, strict=False)
        if reason in {
            "excluded_missing_outcome",
            "excluded_wrong_outcome",
            "excluded_wrong_event",
            "excluded_wrong_date",
            "excluded_wrong_timeframe",
            "excluded_wrong_market_type",
        }:
            return reason

    pm_year = str(_row_get(pm, "event_year") or "")
    ks_year = str(_row_get(ks, "event_year") or "")
    if market_type in {"championship_winner", "pole_position_winner", "host_country"} and pm_year and ks_year and pm_year != ks_year:
        return "excluded_wrong_year"

    return ""


def _semantic_market_pair_rejection_reason(left: pd.Series | dict[str, Any], right: pd.Series | dict[str, Any]) -> str:
    left_sport = _cached_sport_context(left)
    right_sport = _cached_sport_context(right)
    if left_sport and right_sport and left_sport != right_sport:
        return "excluded_wrong_sport"

    market_type = str(_row_get(left, "market_type") or "")
    right_market_type = str(_row_get(right, "market_type") or "")
    if market_type and right_market_type and market_type != right_market_type:
        return "excluded_wrong_market_type"
    market_type = market_type or right_market_type
    if market_type not in {"match_winner", "championship_winner", "pole_position_winner", "host_country", "total", "set_winner"}:
        return "excluded_unsupported_semantic_market_type"

    left_date = str(_row_get(left, "event_date") or "")
    right_date = str(_row_get(right, "event_date") or "")
    if market_type in {"match_winner", "total", "spread", "set_winner"} and _event_dates_far_apart(left_date, right_date):
        return "excluded_wrong_date"

    if market_type == "match_winner":
        reason = _match_winner_pair_gate_reason(left, right, strict=False)
        return reason if reason in {"excluded_missing_outcome", "excluded_wrong_outcome", "excluded_wrong_event", "excluded_wrong_date", "excluded_wrong_timeframe", "excluded_wrong_market_type"} else ""

    if market_type in {"championship_winner", "pole_position_winner"}:
        reason = _event_winner_pair_gate_reason(left, right)
        return reason if reason in {"excluded_wrong_outcome", "excluded_wrong_scope", "excluded_wrong_year"} else ""

    if market_type == "host_country":
        reason = _host_country_pair_gate_reason(left, right)
        return reason if reason in {"excluded_wrong_outcome", "excluded_wrong_year"} else ""

    if market_type == "total":
        reason = _total_pair_gate_reason(left, right, strict=False)
        return reason if reason in {"excluded_wrong_total_side", "excluded_wrong_total_line", "excluded_wrong_metric", "excluded_wrong_event"} else ""

    if market_type == "set_winner":
        left_outcome = _cached_candidate_outcome_key(left)
        right_outcome = _cached_candidate_outcome_key(right)
        if left_outcome and right_outcome and not _cached_outcomes_compatible(left, right):
            return "excluded_wrong_outcome"

    return ""


def _semantic_row_supported_for_event_matching(row: pd.Series | dict[str, Any]) -> bool:
    market_type = str(_row_get(row, "market_type") or "")
    if market_type not in {"match_winner", "championship_winner", "pole_position_winner", "host_country", "total", "set_winner"}:
        return False
    if _looks_like_bundle_market(row) or _is_exact_score_market(row) or _is_player_prop_market(row):
        return False
    if market_type == "championship_winner" and (_champion_group_scope_key(row) or _unbeaten_champion_scope_key(row)):
        return False
    return True


def _event_dates_far_apart(left_date: str, right_date: str, *, max_days: int = 1) -> bool:
    if not left_date or not right_date or left_date == right_date:
        return False
    try:
        left = datetime.fromisoformat(left_date[:10]).date()
        right = datetime.fromisoformat(right_date[:10]).date()
    except ValueError:
        return False
    return abs((left - right).days) > max_days


def _parserless_market_pair_score(pm: pd.Series | dict[str, Any], ks: pd.Series | dict[str, Any]) -> float:
    left_event_key = str(_row_get(pm, "event_match_key") or "")
    right_event_key = str(_row_get(ks, "event_match_key") or "")
    if left_event_key and left_event_key == right_event_key and _cached_outcomes_compatible(pm, ks):
        return 100.0
    pm_title = str(_row_get(pm, "title") or "")
    ks_title = str(_row_get(ks, "title") or "")
    pm_outcome = str(_row_get(pm, "outcome_label") or "")
    ks_outcome = str(_row_get(ks, "outcome_label") or "")
    title_score = fuzz.token_set_ratio(pm_title, ks_title)
    outcome_score = fuzz.token_set_ratio(pm_outcome, ks_outcome)
    subject_score = fuzz.token_set_ratio(str(_row_get(pm, "subject") or pm_title), str(_row_get(ks, "subject") or ks_title))
    settlement_score = fuzz.token_set_ratio(str(_row_get(pm, "settlement_summary") or "")[:500], str(_row_get(ks, "settlement_summary") or "")[:500])
    semantic_score = 100.0 * _dense_cosine(_semantic_dense_value_from_row(pm), _semantic_dense_value_from_row(ks))
    if semantic_score:
        return float(0.70 * semantic_score + 0.12 * title_score + 0.10 * subject_score + 0.04 * outcome_score + 0.04 * settlement_score)
    return float(0.34 * title_score + 0.30 * outcome_score + 0.24 * subject_score + 0.12 * settlement_score)


def _event_first_review_notes(
    pm_event: pd.Series | dict[str, Any],
    ks_event: pd.Series | dict[str, Any],
    pm: pd.Series | dict[str, Any],
    ks: pd.Series | dict[str, Any],
    *,
    event_score: float,
    market_score: float,
) -> str:
    notes = [
        "event-first parserless suggestion; parsed market_type/date/outcome were not used as hard gates",
        f"event_score={event_score:.2f}",
        f"market_text_score={market_score:.2f}",
    ]
    pm_type = str(_row_get(pm, "market_type") or "")
    ks_type = str(_row_get(ks, "market_type") or "")
    if pm_type or ks_type:
        notes.append(f"parser_context: polymarket_type={pm_type or 'unknown'} kalshi_type={ks_type or 'unknown'}")
    pm_date = str(_row_get(pm_event, "event_date") or "")
    ks_date = str(_row_get(ks_event, "event_date") or "")
    if pm_date or ks_date:
        notes.append(f"date_context: polymarket={pm_date or 'unknown'} kalshi={ks_date or 'unknown'}")
    notes.append("manual approval must verify same event, market, outcome, draw/overtime/tiebreak, and settlement rules")
    return "; ".join(notes)


def _average_dense_vectors(vectors: list[list[float]]) -> list[float]:
    if not vectors:
        return []
    width = min(len(vector) for vector in vectors if vector)
    if width <= 0:
        return []
    averaged = [sum(float(vector[index]) for vector in vectors if len(vector) >= width) / len(vectors) for index in range(width)]
    norm = math.sqrt(sum(value * value for value in averaged))
    if not norm:
        return averaged
    return [float(value / norm) for value in averaged]


def _first_non_blank(frame: pd.DataFrame, column: str) -> str:
    if column not in frame.columns:
        return ""
    for value in frame[column].tolist():
        if not _is_blank(value):
            return str(value).strip()
    return ""


def _joined_unique_values(frame: pd.DataFrame, column: str, limit: int = 10) -> str:
    if column not in frame.columns:
        return ""
    values: list[str] = []
    seen: set[str] = set()
    for value in frame[column].tolist():
        if _is_blank(value):
            continue
        text = str(value).strip()
        key = text.casefold()
        if key in seen:
            continue
        values.append(text)
        seen.add(key)
        if len(values) >= limit:
            break
    return " | ".join(values)


def _non_blank_value_set(frame: pd.DataFrame, column: str) -> set[str]:
    if column not in frame.columns:
        return set()
    return {
        str(value).strip()
        for value in frame[column].tolist()
        if not _is_blank(value) and str(value).strip()
    }


def _non_blank_or_computed_value_set(frame: pd.DataFrame, column: str, compute: Callable[[pd.Series], str]) -> set[str]:
    values = _non_blank_value_set(frame, column)
    if values:
        return values
    computed: set[str] = set()
    for _, row in frame.iterrows():
        value = str(compute(row) or "").strip()
        if value:
            computed.add(value)
    return computed


def _row_value_set(row: pd.Series | dict[str, Any], column: str) -> set[str]:
    value = _row_get(row, column)
    if isinstance(value, set):
        return {str(item).strip() for item in value if str(item).strip()}
    if isinstance(value, (list, tuple)):
        return {str(item).strip() for item in value if str(item).strip()}
    if _is_blank(value):
        return set()
    return {part.strip() for part in str(value).split("|") if part.strip()}


def _suggestion_input_frame(frame: pd.DataFrame) -> pd.DataFrame:
    output = frame.copy()
    for column in APPROVAL_CANDIDATE_COLUMNS:
        if column not in output.columns:
            output[column] = ""
        else:
            output[column] = output[column].astype("object").fillna("")
    return output[APPROVAL_CANDIDATE_COLUMNS]


def _prefilter_mapping_candidates(pm: pd.Series, kalshi: pd.DataFrame) -> pd.DataFrame:
    market_type = str(pm.get("market_type") or "")
    candidates = kalshi
    if candidates.empty:
        return candidates

    event_key = str(pm.get("event_match_key") or "")
    outcome = str(pm.get("_normalized_outcome") or "")
    event_date = str(pm.get("event_date") or "")
    event_year = str(pm.get("event_year") or "")
    event_title = str(pm.get("_normalized_event_title") or "")

    if market_type == "match_winner":
        if not outcome:
            return candidates.iloc[0:0]
        if event_key and "event_match_key" in candidates:
            exact_candidates = candidates[candidates["event_match_key"] == event_key]
            if not exact_candidates.empty:
                candidates = exact_candidates
            elif _is_tennis_market(pm):
                candidates = _prefilter_tennis_candidates(pm, candidates)
            elif event_title:
                candidates = candidates[candidates["_normalized_event_title"].map(lambda value: fuzz.token_set_ratio(event_title, value) >= 80)]
        elif _is_tennis_market(pm):
            candidates = _prefilter_tennis_candidates(pm, candidates)
        elif event_title:
            if event_date and "event_date" in candidates:
                same_date = candidates[candidates["event_date"] == event_date]
                if not same_date.empty:
                    candidates = same_date
            candidates = candidates[candidates["_normalized_event_title"].map(lambda value: fuzz.token_set_ratio(event_title, value) >= 80)]
        if outcome and "_normalized_outcome" in candidates and not _is_tennis_market(pm):
            candidates = candidates[candidates["_normalized_outcome"] == outcome]
        return candidates

    if market_type in {"championship_winner", "pole_position_winner"}:
        if not outcome:
            return candidates.iloc[0:0]
        sport = _cached_sport_context(pm)
        if sport:
            if "_sport_context" in candidates.columns:
                candidates = candidates[candidates["_sport_context"].fillna("").astype(str) == sport]
            else:
                candidates = candidates[candidates.apply(lambda row: _cached_sport_context(row) == sport, axis=1)]
        scope = _cached_scope_key(pm)
        if scope:
            if "_scope_key" in candidates.columns:
                candidates = candidates[candidates["_scope_key"].fillna("").astype(str) == scope]
            else:
                candidates = candidates[candidates.apply(lambda row: _cached_scope_key(row) == scope, axis=1)]
        if event_year and "event_year" in candidates:
            same_year = candidates[candidates["event_year"] == event_year]
            if not same_year.empty:
                candidates = same_year
        if "_normalized_outcome" in candidates:
            exact = candidates[candidates["_normalized_outcome"] == outcome]
            if not exact.empty:
                candidates = exact
        return candidates

    event_date = str(pm.get("event_date") or "")
    if event_date and "event_date" in candidates:
        same_date = candidates[candidates["event_date"] == event_date]
        if not same_date.empty:
            candidates = same_date
    if event_year and "event_year" in candidates:
        same_year = candidates[candidates["event_year"] == event_year]
        if not same_year.empty:
            candidates = same_year
    if outcome and "_normalized_outcome" in candidates:
        same_outcome = candidates[candidates["_normalized_outcome"] == outcome]
        if not same_outcome.empty:
            candidates = same_outcome
    return candidates


def _embedding_suggested_mapping_rows(
    pm: pd.Series,
    kalshi_by_type: dict[str, pd.DataFrame],
    embedding_index_by_type: dict[str, dict[str, tuple[Any, ...]]],
    kalshi_records_by_index: dict[Any, dict[str, Any]],
    empty_kalshi: pd.DataFrame,
    *,
    min_score: float,
    embedding_min_score: float,
    top_k: int,
    seen_mapping_ids: set[str],
) -> list[dict[str, Any]]:
    market_type = str(pm.get("market_type") or "")
    candidates = _embedding_prefilter_candidates(
        pm,
        kalshi_by_type.get(market_type, empty_kalshi),
        embedding_index_by_type.get(market_type, {}),
    )
    if candidates.empty:
        return []
    pm_vector = pm.get("_embedding_vector") or _hashed_text_embedding(_candidate_embedding_text(pm))
    scored_rows: list[tuple[float, float, float, pd.Series]] = []
    for candidate_index in candidates.index:
        ks = kalshi_records_by_index.get(candidate_index)
        if ks is None:
            continue
        if not _embedding_candidate_types_compatible(pm, ks):
            continue
        lexical_score = _fast_embedding_lexical_score(pm, ks)
        embedding_score = 100.0 * _embedding_cosine(pm_vector, ks.get("_embedding_vector") or _hashed_text_embedding(_candidate_embedding_text(ks)))
        combined_score = _combined_embedding_match_score(embedding_score, lexical_score, pm, ks)
        if combined_score < min_score and embedding_score < embedding_min_score:
            continue
        scored_rows.append((combined_score, embedding_score, lexical_score, ks))
    scored_rows.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)

    rows: list[dict[str, Any]] = []
    for combined_score, embedding_score, lexical_score, ks in scored_rows[:top_k]:
        row = _suggested_mapping_row(
            pm,
            ks,
            combined_score,
            embedding_score=embedding_score,
            lexical_score=lexical_score,
            combined_score=combined_score,
            suggestion_method="embedding",
        )
        mapping_id = str(row["mapping_id"])
        if mapping_id in seen_mapping_ids:
            continue
        seen_mapping_ids.add(mapping_id)
        rows.append(row)
    return rows


def _semantic_suggested_mapping_rows(
    pm: pd.Series,
    kalshi_by_type: dict[str, pd.DataFrame],
    embedding_index_by_type: dict[str, dict[str, tuple[Any, ...]]],
    kalshi_records_by_index: dict[Any, dict[str, Any]],
    empty_kalshi: pd.DataFrame,
    *,
    semantic_min_score: float,
    top_k: int,
    seen_mapping_ids: set[str],
) -> list[dict[str, Any]]:
    market_type = str(pm.get("market_type") or "")
    candidates = _embedding_prefilter_candidates(
        pm,
        kalshi_by_type.get(market_type, empty_kalshi),
        embedding_index_by_type.get(market_type, {}),
    )
    if candidates.empty:
        return []
    pm_vector = _semantic_vector_from_row(pm)
    if not pm_vector:
        return []

    scored_rows: list[tuple[float, float, float, float, dict[str, Any]]] = []
    for candidate_index in candidates.index:
        ks = kalshi_records_by_index.get(candidate_index)
        if ks is None:
            continue
        if not _embedding_candidate_types_compatible(pm, ks):
            continue
        ks_vector = _semantic_vector_from_row(ks)
        if not ks_vector:
            continue
        semantic_score = 100.0 * _dense_cosine(pm_vector, ks_vector)
        lexical_score = _fast_embedding_lexical_score(pm, ks)
        structured_score = _structured_field_score(pm, ks)
        outcome_score = _outcome_match_score(pm, ks)
        combined_score = _semantic_combined_match_score(
            semantic_score=semantic_score,
            lexical_score=lexical_score,
            structured_score=structured_score,
            outcome_score=outcome_score,
        )
        if combined_score < semantic_min_score:
            continue
        scored_rows.append((combined_score, semantic_score, lexical_score, structured_score, ks))

    scored_rows.sort(key=lambda item: (item[0], item[1], item[2], item[3]), reverse=True)
    rows: list[dict[str, Any]] = []
    for combined_score, semantic_score, lexical_score, _, ks in scored_rows[:top_k]:
        semantic_provider = str(pm.get("_semantic_provider") or ks.get("_semantic_provider") or "")
        semantic_model = str(pm.get("_semantic_embedding_model") or ks.get("_semantic_embedding_model") or "")
        semantic_dim = str(pm.get("_semantic_embedding_dim") or ks.get("_semantic_embedding_dim") or "")
        text_hash = str(pm.get("_semantic_embedding_text_hash") or "")
        row = _suggested_mapping_row(
            pm,
            ks,
            combined_score,
            lexical_score=lexical_score,
            combined_score=combined_score,
            suggestion_method="semantic",
            gemini_embedding_score=semantic_score,
            semantic_combined_score=combined_score,
            semantic_provider=semantic_provider,
            embedding_model=semantic_model,
            embedding_dim=semantic_dim,
            embedding_text_hash=text_hash,
        )
        mapping_id = str(row["mapping_id"])
        if mapping_id in seen_mapping_ids:
            continue
        seen_mapping_ids.add(mapping_id)
        rows.append(row)
    return rows


def prepare_market_embeddings(
    approval_candidates: pd.DataFrame,
    *,
    output_dir: str | Path = DEFAULT_SPORTS_OUTPUT_DIR,
    run_id: str = "",
    retrieved_at: str = "",
    provider: str = DEFAULT_SEMANTIC_EMBEDDING_PROVIDER,
    embedding_dim: int = DEFAULT_SEMANTIC_EMBEDDING_DIM,
    vertex_batch_size: int = DEFAULT_VERTEX_GEMINI_BATCH_SIZE,
    vertex_batch_sleep_seconds: float = DEFAULT_VERTEX_GEMINI_BATCH_SLEEP_SECONDS,
    vertex_retry_initial_seconds: float = DEFAULT_VERTEX_GEMINI_RETRY_INITIAL_SECONDS,
    vertex_max_retries: int = DEFAULT_VERTEX_GEMINI_MAX_RETRIES,
    semantic_cache_flush_batches: int = DEFAULT_SEMANTIC_CACHE_FLUSH_BATCHES,
    semantic_max_embedding_texts: int = DEFAULT_SEMANTIC_MAX_EMBEDDING_TEXTS,
    embedding_client: Any | None = None,
    existing_cache: pd.DataFrame | None = None,
) -> pd.DataFrame:
    provider = _normalize_semantic_provider(provider)
    if provider == "off" or approval_candidates.empty:
        return pd.DataFrame(columns=MARKET_EMBEDDING_COLUMNS)
    if embedding_dim <= 0:
        raise ValueError("semantic embedding dimension must be positive")

    candidates = _suggestion_input_frame(approval_candidates)
    cache = _ensure_market_embedding_columns(
        existing_cache if existing_cache is not None else load_market_embedding_cache(output_dir)
    )
    cache_by_key = {
        str(row.get("embedding_cache_key") or ""): row
        for _, row in cache.iterrows()
        if str(row.get("embedding_cache_key") or "")
    }
    now = utc_now_iso()
    model_name = _semantic_embedding_model_name(provider)
    rows: list[dict[str, Any]] = []
    missing: list[tuple[pd.Series, str, str, str]] = []

    for _, candidate in candidates.iterrows():
        text = build_canonical_embedding_text(candidate)
        text_hash = _stable_text_hash(text)
        embedding_key = _candidate_embedding_key(candidate, text_hash)
        cache_key = _embedding_cache_key(embedding_key, provider, model_name, embedding_dim)
        cached = cache_by_key.get(cache_key)
        cached_vector = cached.get("embedding_vector") if cached is not None else ""
        if cached is not None and _has_vector_value(cached_vector):
            rows.append(
                _market_embedding_row(
                    candidate,
                    run_id=run_id,
                    retrieved_at=retrieved_at,
                    provider=provider,
                    model_name=model_name,
                    embedding_dim=embedding_dim,
                    embedding_key=embedding_key,
                    cache_key=cache_key,
                    text_hash=text_hash,
                    text=text,
                    vector=cached_vector,
                    embedded_at=str(cached.get("embedded_at") or now),
                    cache_status="cached",
                    error="",
                )
            )
            continue
        missing.append((candidate, text, text_hash, cache_key))

    cached_count = len(rows)
    missing_count = len(missing)
    if semantic_max_embedding_texts > 0 and missing_count > semantic_max_embedding_texts:
        missing = missing[:semantic_max_embedding_texts]
        _semantic_progress(
            f"Semantic embeddings: limiting new embeddings to {semantic_max_embedding_texts:,} "
            f"of {missing_count:,} cache misses for this run."
        )

    if missing:
        _semantic_progress(
            f"Semantic embeddings: {cached_count:,} cached, {len(missing):,} new texts, "
            f"provider={provider}, dim={embedding_dim}, batch_size={max(1, vertex_batch_size)}."
        )
        rows = _embed_missing_market_embeddings(
            missing,
            rows=rows,
            output_dir=output_dir,
            run_id=run_id,
            retrieved_at=retrieved_at,
            provider=provider,
            model_name=model_name,
            embedding_dim=embedding_dim,
            embedded_at=now,
            vertex_batch_size=vertex_batch_size,
            vertex_batch_sleep_seconds=vertex_batch_sleep_seconds,
            vertex_retry_initial_seconds=vertex_retry_initial_seconds,
            vertex_max_retries=vertex_max_retries,
            semantic_cache_flush_batches=semantic_cache_flush_batches,
            embedding_client=embedding_client,
        )

    if not rows:
        return pd.DataFrame(columns=MARKET_EMBEDDING_COLUMNS)
    frame = pd.DataFrame(rows, columns=MARKET_EMBEDDING_COLUMNS)
    return _ensure_market_embedding_columns(frame).drop_duplicates(subset=["embedding_cache_key"], keep="last").reset_index(drop=True)


def _embed_missing_market_embeddings(
    missing: list[tuple[pd.Series, str, str, str]],
    *,
    rows: list[dict[str, Any]],
    output_dir: str | Path,
    run_id: str,
    retrieved_at: str,
    provider: str,
    model_name: str,
    embedding_dim: int,
    embedded_at: str,
    vertex_batch_size: int,
    vertex_batch_sleep_seconds: float,
    vertex_retry_initial_seconds: float,
    vertex_max_retries: int,
    semantic_cache_flush_batches: int,
    embedding_client: Any | None,
) -> list[dict[str, Any]]:
    batch_size = max(1, vertex_batch_size if provider == "vertex-gemini" else len(missing))
    flush_batches = max(1, semantic_cache_flush_batches)
    total = len(missing)
    client = (
        embedding_client
        if embedding_client is not None
        else (
            VertexGeminiEmbeddingClient(
                batch_size=batch_size,
                batch_sleep_seconds=0,
                retry_initial_seconds=vertex_retry_initial_seconds,
                max_retries=vertex_max_retries,
            )
            if provider == "vertex-gemini"
            else None
        )
    )

    for batch_index, start in enumerate(range(0, total, batch_size), start=1):
        batch = missing[start : start + batch_size]
        texts = [item[1] for item in batch]
        vectors = _embed_texts_for_provider(
            provider,
            texts,
            embedding_dim=embedding_dim,
            embedding_client=client,
            vertex_batch_size=batch_size,
            vertex_batch_sleep_seconds=0,
            vertex_retry_initial_seconds=vertex_retry_initial_seconds,
            vertex_max_retries=vertex_max_retries,
        )
        if len(vectors) != len(batch):
            raise RuntimeError(f"embedding provider returned {len(vectors)} vectors for {len(batch)} texts")
        for (candidate, text, text_hash, cache_key), vector in zip(batch, vectors, strict=True):
            rows.append(
                _market_embedding_row(
                    candidate,
                    run_id=run_id,
                    retrieved_at=retrieved_at,
                    provider=provider,
                    model_name=model_name,
                    embedding_dim=embedding_dim,
                    embedding_key=_candidate_embedding_key(candidate, text_hash),
                    cache_key=cache_key,
                    text_hash=text_hash,
                    text=text,
                    vector=vector,
                    embedded_at=embedded_at,
                    cache_status="new",
                    error="",
                )
            )

        completed = min(start + batch_size, total)
        _semantic_progress(f"Semantic embeddings: embedded {completed:,}/{total:,} new texts.")
        should_flush = completed == total or batch_index % flush_batches == 0
        if should_flush:
            frame = _ensure_market_embedding_columns(pd.DataFrame(rows, columns=MARKET_EMBEDDING_COLUMNS))
            write_market_embedding_cache(frame.drop_duplicates(subset=["embedding_cache_key"], keep="last"), output_dir)
            _semantic_progress(f"Semantic embeddings: flushed cache with {len(frame):,} rows.")
        if provider == "vertex-gemini" and vertex_batch_sleep_seconds > 0 and completed < total:
            time.sleep(vertex_batch_sleep_seconds)
    return rows


def write_market_embedding_cache(frame: pd.DataFrame, output_dir: str | Path = DEFAULT_SPORTS_OUTPUT_DIR) -> dict[str, Any]:
    output = _ensure_market_embedding_columns(frame).drop_duplicates(subset=["embedding_cache_key"], keep="last").reset_index(drop=True)
    if _is_gcs_uri(output_dir):
        bucket_name, prefix = _split_gcs_uri(str(output_dir))
        bucket = _google_storage_client().bucket(bucket_name)
        return _write_gcs_table(bucket, bucket_name, prefix, "processed/latest", "market_embeddings", output)
    return write_latest_processed_table("market_embeddings", output, output_dir)


def _semantic_progress(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def load_market_embedding_cache(output_dir: str | Path = DEFAULT_SPORTS_OUTPUT_DIR) -> pd.DataFrame:
    latest_parquet = "processed/latest/market_embeddings.parquet"
    latest_csv = "processed/latest/market_embeddings.csv"
    if _is_gcs_uri(output_dir):
        root = str(output_dir).rstrip("/")
        for uri in (f"{root}/{latest_parquet}", f"{root}/{latest_csv}"):
            try:
                body = _download_gcs_bytes(uri)
            except FileNotFoundError:
                continue
            if uri.endswith(".parquet"):
                return _ensure_market_embedding_columns(pd.read_parquet(BytesIO(body)))
            return _ensure_market_embedding_columns(pd.read_csv(BytesIO(body), dtype=str, keep_default_na=False))
        return pd.DataFrame(columns=MARKET_EMBEDDING_COLUMNS)

    root = Path(output_dir)
    for path in (root / latest_parquet, root / latest_csv):
        if not path.exists():
            continue
        if path.suffix == ".parquet":
            return _ensure_market_embedding_columns(pd.read_parquet(path))
        return _ensure_market_embedding_columns(pd.read_csv(path, dtype=str, keep_default_na=False))
    return pd.DataFrame(columns=MARKET_EMBEDDING_COLUMNS)


def build_canonical_embedding_text(row: pd.Series | dict[str, Any]) -> str:
    sport = _cached_sport_context(row)
    outcome = _semantic_alias_normalize(_row_get(row, "outcome_label"), sport=sport)
    subject = _semantic_alias_normalize(_row_get(row, "subject"), sport=sport)
    event_title = _semantic_alias_normalize(_row_get(row, "event_title"), sport=sport)
    parts = [
        ("venue", _row_get(row, "venue")),
        ("sport", sport),
        ("league_or_category", _row_get(row, "category")),
        ("market_type", _row_get(row, "market_type")),
        ("event", event_title),
        ("event_date", _row_get(row, "event_date")),
        ("event_year", _row_get(row, "event_year")),
        ("event_scope", _cached_scope_key(row)),
        ("timeframe", _row_get(row, "event_timeframe")),
        ("outcome", outcome),
        ("subject", subject),
        ("title", _semantic_alias_normalize(_row_get(row, "title"), sport=sport)),
        ("subtitle", _semantic_alias_normalize(_row_get(row, "subtitle"), sport=sport)),
        ("ticker_or_slug", _row_get(row, "ticker_or_slug")),
        ("settlement_summary", str(_row_get(row, "settlement_summary") or "")[:240]),
    ]
    return "\n".join(f"{name}: {_normalize_name(value)}" for name, value in parts if not _is_blank(value))


def _market_embedding_row(
    candidate: pd.Series | dict[str, Any],
    *,
    run_id: str,
    retrieved_at: str,
    provider: str,
    model_name: str,
    embedding_dim: int,
    embedding_key: str,
    cache_key: str,
    text_hash: str,
    text: str,
    vector: Any,
    embedded_at: str,
    cache_status: str,
    error: str,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "retrieved_at": retrieved_at,
        "venue": _row_get(candidate, "venue") or "",
        "market_id": _row_get(candidate, "market_id") or "",
        "ticker_or_slug": _row_get(candidate, "ticker_or_slug") or "",
        "yes_token_id": _row_get(candidate, "yes_token_id") or "",
        "outcome_label": _row_get(candidate, "outcome_label") or "",
        "market_type": _row_get(candidate, "market_type") or "",
        "semantic_provider": provider,
        "embedding_model": model_name,
        "embedding_dim": int(embedding_dim),
        "embedding_key": embedding_key,
        "embedding_cache_key": cache_key,
        "embedding_text_hash": text_hash,
        "embedding_text": text,
        "embedding_vector": vector if isinstance(vector, str) else _vector_to_json(vector),
        "embedded_at": embedded_at,
        "cache_status": cache_status,
        "error": error,
    }


def _ensure_market_embedding_columns(frame: pd.DataFrame | None) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame(columns=MARKET_EMBEDDING_COLUMNS)
    output = frame.copy()
    for column in MARKET_EMBEDDING_COLUMNS:
        if column not in output.columns:
            output[column] = ""
    return output[MARKET_EMBEDDING_COLUMNS].fillna("")


def _ensure_suggested_mapping_columns(frame: pd.DataFrame | None) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame(columns=SUGGESTED_MAPPING_COLUMNS)
    output = frame.copy()
    for column in SUGGESTED_MAPPING_COLUMNS:
        if column not in output.columns:
            output[column] = ""
    output = output[SUGGESTED_MAPPING_COLUMNS].copy()
    for column in SUGGESTED_MAPPING_NUMERIC_COLUMNS:
        if column in output.columns:
            output[column] = pd.to_numeric(output[column], errors="coerce")
    string_columns = [column for column in output.columns if column not in SUGGESTED_MAPPING_NUMERIC_COLUMNS]
    output[string_columns] = output[string_columns].fillna("")
    return output


def _embed_texts_for_provider(
    provider: str,
    texts: list[str],
    *,
    embedding_dim: int,
    embedding_client: Any | None = None,
    vertex_batch_size: int = DEFAULT_VERTEX_GEMINI_BATCH_SIZE,
    vertex_batch_sleep_seconds: float = DEFAULT_VERTEX_GEMINI_BATCH_SLEEP_SECONDS,
    vertex_retry_initial_seconds: float = DEFAULT_VERTEX_GEMINI_RETRY_INITIAL_SECONDS,
    vertex_max_retries: int = DEFAULT_VERTEX_GEMINI_MAX_RETRIES,
) -> list[list[float]]:
    provider = _normalize_semantic_provider(provider)
    if provider == "local":
        return [_dense_local_embedding(text, dimensions=embedding_dim) for text in texts]
    if provider == "vertex-gemini":
        client = embedding_client or VertexGeminiEmbeddingClient(
            batch_size=vertex_batch_size,
            batch_sleep_seconds=vertex_batch_sleep_seconds,
            retry_initial_seconds=vertex_retry_initial_seconds,
            max_retries=vertex_max_retries,
        )
        return client.embed_texts(texts, embedding_dim=embedding_dim)
    if provider == "off":
        return []
    raise ValueError(f"Unsupported semantic embedding provider: {provider}")


class VertexGeminiEmbeddingClient:
    def __init__(
        self,
        *,
        model_name: str = VERTEX_GEMINI_EMBEDDING_MODEL,
        task_type: str = VERTEX_GEMINI_TASK_TYPE,
        location: str | None = None,
        project: str | None = None,
        batch_size: int = DEFAULT_VERTEX_GEMINI_BATCH_SIZE,
        batch_sleep_seconds: float = DEFAULT_VERTEX_GEMINI_BATCH_SLEEP_SECONDS,
        retry_initial_seconds: float = DEFAULT_VERTEX_GEMINI_RETRY_INITIAL_SECONDS,
        retry_max_seconds: float = DEFAULT_VERTEX_GEMINI_RETRY_MAX_SECONDS,
        max_retries: int = DEFAULT_VERTEX_GEMINI_MAX_RETRIES,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.model_name = model_name
        self.task_type = task_type
        configured_location = location or os.getenv("GOOGLE_CLOUD_LOCATION") or os.getenv("GOOGLE_CLOUD_REGION")
        if self.model_name in VERTEX_GEMINI_MODELS_WITHOUT_TASK_TYPE and configured_location not in {None, "", "global", "us", "eu"}:
            configured_location = "global"
        self.location = configured_location or ("global" if self.model_name in VERTEX_GEMINI_MODELS_WITHOUT_TASK_TYPE else "us-central1")
        self.project = project or os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCP_PROJECT")
        if self.model_name in VERTEX_GEMINI_MODELS_WITHOUT_TASK_TYPE:
            self.batch_size = min(max(1, batch_size), 16)
            self.batch_sleep_seconds = 0.0
        else:
            self.batch_size = min(max(1, batch_size), VERTEX_GEMINI_MAX_TEXTS_PER_REQUEST)
            self.batch_sleep_seconds = max(0.0, float(batch_sleep_seconds))
        self.retry_initial_seconds = max(0.0, float(retry_initial_seconds))
        self.retry_max_seconds = max(self.retry_initial_seconds, float(retry_max_seconds))
        self.max_retries = max_retries
        self.sleeper = sleeper
        self._model: Any | None = None
        self._genai_client: Any | None = None
        self._genai_types: Any | None = None

    def embed_texts(self, texts: list[str], *, embedding_dim: int) -> list[list[float]]:
        if not texts:
            return []
        if self.model_name in VERTEX_GEMINI_MODELS_WITHOUT_TASK_TYPE:
            return self._embed_texts_with_genai(texts, embedding_dim=embedding_dim)
        model, text_embedding_input = self._load_model()
        output: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start : start + self.batch_size]
            inputs = [text_embedding_input(text, self.task_type) if self.task_type else text_embedding_input(text) for text in batch]
            output.extend(self._get_embeddings_with_retry(model, inputs, embedding_dim=embedding_dim))
            if self.batch_sleep_seconds and start + self.batch_size < len(texts):
                self.sleeper(self.batch_sleep_seconds)
        return output

    def _embed_texts_with_genai(self, texts: list[str], *, embedding_dim: int) -> list[list[float]]:
        client, types_module = self._load_genai_client()
        output: list[list[float]] = []
        config = types_module.EmbedContentConfig(outputDimensionality=embedding_dim)
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start : start + self.batch_size]
            contents = [types_module.Content(parts=[types_module.Part(text=text)]) for text in batch]
            output.extend(self._get_genai_embeddings_batch(client, contents, config=config))
            if self.batch_sleep_seconds and start + self.batch_size < len(texts):
                self.sleeper(self.batch_sleep_seconds)
        return output

    def _load_genai_client(self) -> tuple[Any, Any]:
        if self._genai_client is not None and self._genai_types is not None:
            return self._genai_client, self._genai_types
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:  # pragma: no cover - exercised when optional dependency is absent
            raise RuntimeError('Install the GCP extra first: pip install -e ".[gcp]"') from exc
        self._genai_client = genai.Client(vertexai=True, project=self.project, location=self.location)
        self._genai_types = types
        return self._genai_client, self._genai_types

    def _load_model(self) -> tuple[Any, Any]:
        if self._model is not None:
            from vertexai.language_models import TextEmbeddingInput

            return self._model, TextEmbeddingInput
        try:
            import vertexai
            from vertexai.language_models import TextEmbeddingInput, TextEmbeddingModel
        except ImportError as exc:  # pragma: no cover - exercised when optional dependency is absent
            raise RuntimeError('Install the GCP extra first: pip install -e ".[gcp]"') from exc
        vertexai.init(project=self.project, location=self.location)
        self._model = TextEmbeddingModel.from_pretrained(self.model_name)
        return self._model, TextEmbeddingInput

    def _get_embeddings_with_retry(self, model: Any, inputs: list[Any], *, embedding_dim: int) -> list[list[float]]:
        attempt = 0
        while True:
            try:
                results = model.get_embeddings(inputs, output_dimensionality=embedding_dim)
                return [list(result.values) for result in results]
            except Exception as exc:
                attempt += 1
                if attempt > self.max_retries:
                    raise
                self.sleeper(self._retry_sleep_seconds(exc, attempt))

    def _get_genai_embeddings_batch(self, client: Any, contents: list[Any], *, config: Any) -> list[list[float]]:
        if len(contents) == 1:
            return [self._get_genai_embedding_with_retry(client, contents[0], config=config)]
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=self.batch_size) as executor:
            return list(executor.map(lambda content: self._get_genai_embedding_with_retry(client, content, config=config), contents))

    def _get_genai_embedding_with_retry(self, client: Any, content: Any, *, config: Any) -> list[float]:
        attempt = 0
        while True:
            try:
                response = client.models.embed_content(model=self.model_name, contents=content, config=config)
                embeddings = getattr(response, "embeddings", None)
                if embeddings is None and getattr(response, "embedding", None) is not None:
                    embeddings = [response.embedding]
                if not embeddings:
                    raise RuntimeError("embedding provider returned no vectors")
                return list(embeddings[0].values)
            except Exception as exc:
                attempt += 1
                if attempt > self.max_retries:
                    raise
                self.sleeper(self._retry_sleep_seconds(exc, attempt))

    def _retry_sleep_seconds(self, exc: Exception, attempt: int) -> float:
        if _is_quota_exception(exc):
            return min(self.retry_max_seconds, self.retry_initial_seconds * (2 ** (attempt - 1)))
        return min(30.0, 0.5 * (2 ** (attempt - 1)))


def review_suggested_mappings_with_ai(
    suggestions: pd.DataFrame,
    *,
    provider: str = DEFAULT_AI_PAIR_REVIEW_PROVIDER,
    model_name: str = DEFAULT_AI_PAIR_REVIEW_MODEL,
    limit: int = DEFAULT_AI_PAIR_REVIEW_LIMIT,
    min_score: float = DEFAULT_AI_PAIR_REVIEW_MIN_SCORE,
    reviewed_at: str | None = None,
    review_client: Any | None = None,
) -> pd.DataFrame:
    provider = _normalize_ai_pair_review_provider(provider)
    output = _ensure_suggested_mapping_columns(suggestions)
    if provider == "off" or output.empty:
        return output
    if provider != "vertex-gemini":
        raise ValueError(f"Unsupported AI pair review provider: {provider}")

    reviewed_at = reviewed_at or utc_now_iso()
    client = review_client or VertexGeminiPairReviewClient(model_name=model_name)
    candidates = output.copy()
    score_series = candidates["match_score"] if "match_score" in candidates.columns else pd.Series(0, index=candidates.index)
    candidates["_ai_sort_score"] = pd.to_numeric(score_series, errors="coerce").fillna(0.0)
    candidates = candidates[candidates["_ai_sort_score"] >= min_score]
    if candidates.empty:
        return output
    for column in ("semantic_combined_score", "embedding_score", "lexical_score"):
        if column in candidates.columns:
            candidates[column] = pd.to_numeric(candidates[column], errors="coerce").fillna(0.0)
    sort_columns = [column for column in ("_ai_sort_score", "semantic_combined_score", "embedding_score", "lexical_score") if column in candidates.columns]
    candidates = candidates.sort_values(sort_columns, ascending=[False] * len(sort_columns), na_position="last")

    seen: set[str] = set()
    selected_indexes: list[Any] = []
    for index, row in candidates.iterrows():
        mapping_id = str(row.get("mapping_id") or row.get("suggested_mapping_id") or "")
        if not mapping_id or mapping_id in seen:
            continue
        seen.add(mapping_id)
        selected_indexes.append(index)
        if limit > 0 and len(selected_indexes) >= limit:
            break

    for completed, index in enumerate(selected_indexes, start=1):
        row = output.loc[index]
        try:
            review = client.review_pair(row)
        except Exception as exc:  # keep discovery useful even if one AI call fails
            review = {
                "status": "error",
                "confidence": 0,
                "event_match": "uncertain",
                "market_match": "uncertain",
                "outcome_match": "uncertain",
                "settlement_match": "uncertain",
                "recommendation": "needs_review",
                "reason": f"AI review failed: {exc}",
                "risk_flags": ["ai_error"],
                "raw_response": "",
            }
        output.loc[index, "ai_review_provider"] = provider
        output.loc[index, "ai_review_model"] = model_name
        output.loc[index, "ai_review_status"] = _normalize_ai_review_status(review.get("status"))
        output.loc[index, "ai_review_confidence"] = _clamp_confidence(review.get("confidence"))
        output.loc[index, "ai_event_match"] = _normalize_ai_match_flag(review.get("event_match"))
        output.loc[index, "ai_market_match"] = _normalize_ai_match_flag(review.get("market_match"))
        output.loc[index, "ai_outcome_match"] = _normalize_ai_match_flag(review.get("outcome_match"))
        output.loc[index, "ai_settlement_match"] = _normalize_ai_match_flag(review.get("settlement_match"))
        output.loc[index, "ai_recommendation"] = _normalize_ai_recommendation(review.get("recommendation"))
        output.loc[index, "ai_review_reason"] = str(review.get("reason") or "")[:2000]
        output.loc[index, "ai_risk_flags"] = _join_ai_risk_flags(review.get("risk_flags"))
        output.loc[index, "ai_reviewed_at"] = reviewed_at
        output.loc[index, "ai_raw_response"] = str(review.get("raw_response") or "")[:4000]
        if completed % 25 == 0 or completed == len(selected_indexes):
            print(f"AI pair review: reviewed {completed:,}/{len(selected_indexes):,} suggestions.", file=sys.stderr, flush=True)
    return _ensure_suggested_mapping_columns(output)


class VertexGeminiPairReviewClient:
    def __init__(
        self,
        *,
        model_name: str = DEFAULT_AI_PAIR_REVIEW_MODEL,
        location: str | None = None,
        project: str | None = None,
        sleep_seconds: float = DEFAULT_AI_PAIR_REVIEW_SLEEP_SECONDS,
        max_retries: int = DEFAULT_AI_PAIR_REVIEW_MAX_RETRIES,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.model_name = model_name
        self.location = location or os.getenv("GOOGLE_CLOUD_LOCATION") or os.getenv("GOOGLE_CLOUD_REGION") or "us-central1"
        self.project = project or os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCP_PROJECT")
        self.sleep_seconds = max(0.0, float(sleep_seconds))
        self.max_retries = max(0, int(max_retries))
        self.sleeper = sleeper
        self._model: Any | None = None

    def review_pair(self, row: pd.Series | dict[str, Any]) -> dict[str, Any]:
        raw = self._generate_with_retry(_ai_pair_review_prompt(row))
        parsed = _parse_ai_review_response(raw)
        parsed["raw_response"] = raw
        if self.sleep_seconds:
            self.sleeper(self.sleep_seconds)
        return parsed

    def _load_model(self) -> Any:
        if self._model is not None:
            return self._model
        try:
            import vertexai
            from vertexai.generative_models import GenerativeModel
        except ImportError as exc:  # pragma: no cover - exercised when optional dependency is absent
            raise RuntimeError('Install the GCP extra first: pip install -e ".[gcp]"') from exc
        vertexai.init(project=self.project, location=self.location)
        self._model = GenerativeModel(self.model_name)
        return self._model

    def _generate_with_retry(self, prompt: str) -> str:
        model = self._load_model()
        attempt = 0
        while True:
            try:
                return self._generate(model, prompt)
            except Exception as exc:
                attempt += 1
                if attempt > self.max_retries:
                    raise
                sleep_seconds = 10.0 if _is_quota_exception(exc) else min(10.0, 0.5 * (2 ** (attempt - 1)))
                self.sleeper(sleep_seconds)

    def _generate(self, model: Any, prompt: str) -> str:
        try:
            from vertexai.generative_models import GenerationConfig

            response = model.generate_content(
                prompt,
                generation_config=GenerationConfig(temperature=0, response_mime_type="application/json"),
            )
        except TypeError:
            response = model.generate_content(prompt)
        text = getattr(response, "text", None)
        if text:
            return str(text)
        candidates = getattr(response, "candidates", None) or []
        parts: list[str] = []
        for candidate in candidates:
            content = getattr(candidate, "content", None)
            for part in getattr(content, "parts", []) or []:
                value = getattr(part, "text", None)
                if value:
                    parts.append(str(value))
        return "\n".join(parts)


def _ai_pair_review_prompt(row: pd.Series | dict[str, Any]) -> str:
    evidence = {
        "instruction": (
            "Review whether a Polymarket market/outcome and a Kalshi market/outcome are exact contract-level equivalents. "
            "Use only the venue-provided titles, outcomes, rules, dates, and settlement/source text below. "
            "Do not approve trading. Prefer not_equivalent or uncertainty if one contract is broader/narrower, "
            "uses a different metric, threshold, date window, source, action, or settlement condition."
        ),
        "required_json_schema": {
            "status": "equivalent | not_equivalent | uncertain",
            "confidence": "integer 0-100",
            "event_match": "yes | no | uncertain",
            "market_match": "yes | no | uncertain",
            "outcome_match": "yes | no | uncertain",
            "settlement_match": "yes | no | uncertain",
            "recommendation": "approve_candidate | reject_candidate | needs_review",
            "risk_flags": ["short snake_case strings"],
            "reason": "one concise paragraph",
        },
        "review_checks": [
            "same real-world subject/entity/participants",
            "same event, date, deadline, period, or measurement window",
            "same proposition/action/metric, not merely related wording",
            "same threshold, bracket, direction, and outcome side",
            "same settlement source or compatible authoritative source",
            "same cancellation/postponement/void/other handling when visible",
            "reject if one side is scheduled vs held, invited vs accepted/joined, emergency-only vs any occurrence, total metric vs pure/subset metric, or binary deadline vs broader when/bracket market",
        ],
        "polymarket": {
            "event_title": _row_get(row, "polymarket_event_title"),
            "market_title": _row_get(row, "polymarket_title"),
            "outcome": _row_get(row, "polymarket_yes_outcome") or _row_get(row, "outcome_label"),
            "other_outcome": _row_get(row, "polymarket_no_outcome"),
            "market_id": _row_get(row, "polymarket_market_id"),
            "slug": _row_get(row, "polymarket_slug"),
            "settlement_summary": _row_get(row, "polymarket_settlement_summary"),
        },
        "kalshi": {
            "event_title": _row_get(row, "kalshi_event_title"),
            "market_title": _row_get(row, "kalshi_title"),
            "outcome": _row_get(row, "outcome_label"),
            "ticker": _row_get(row, "kalshi_ticker"),
            "settlement_summary": _row_get(row, "kalshi_settlement_summary"),
        },
        "current_system_suggestion": {
            "event_name": _row_get(row, "event_name"),
            "proposition": _row_get(row, "proposition"),
            "market_type": _row_get(row, "market_type"),
            "score": _row_get(row, "match_score"),
            "review_notes": _row_get(row, "review_notes"),
            "draw_handling": _row_get(row, "draw_handling"),
            "extra_time_handling": _row_get(row, "extra_time_handling"),
            "penalties_handling": _row_get(row, "penalties_handling"),
        },
    }
    return "Return only valid JSON matching required_json_schema. Never include markdown.\n\n" + json.dumps(evidence, ensure_ascii=False, default=str)


def _parse_ai_review_response(raw: str) -> dict[str, Any]:
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        parsed = json.loads(match.group(0)) if match else {}
    if not isinstance(parsed, dict):
        parsed = {}
    return {
        "status": _normalize_ai_review_status(parsed.get("status")),
        "confidence": _clamp_confidence(parsed.get("confidence")),
        "event_match": _normalize_ai_match_flag(parsed.get("event_match")),
        "market_match": _normalize_ai_match_flag(parsed.get("market_match")),
        "outcome_match": _normalize_ai_match_flag(parsed.get("outcome_match")),
        "settlement_match": _normalize_ai_match_flag(parsed.get("settlement_match")),
        "recommendation": _normalize_ai_recommendation(parsed.get("recommendation")),
        "risk_flags": parsed.get("risk_flags") if isinstance(parsed.get("risk_flags"), list) else [],
        "reason": str(parsed.get("reason") or ""),
    }


def _normalize_ai_pair_review_provider(provider: str | None) -> str:
    value = str(provider or DEFAULT_AI_PAIR_REVIEW_PROVIDER).strip().casefold()
    if value not in AI_PAIR_REVIEW_PROVIDERS:
        raise ValueError(f"Unsupported AI pair review provider: {provider}")
    return value


def _normalize_ai_review_status(value: Any) -> str:
    normalized = str(value or "").strip().casefold().replace("-", "_")
    if normalized in {"match", "same", "equivalent", "yes"}:
        return "equivalent"
    if normalized in {"mismatch", "different", "not_equivalent", "not equivalent", "no", "reject"}:
        return "not_equivalent"
    if normalized == "error":
        return "error"
    return "uncertain"


def _normalize_ai_match_flag(value: Any) -> str:
    normalized = str(value or "").strip().casefold().replace("-", "_")
    if normalized in {"yes", "true", "match", "same", "equivalent"}:
        return "yes"
    if normalized in {"no", "false", "mismatch", "different", "not_equivalent"}:
        return "no"
    return "uncertain"


def _normalize_ai_recommendation(value: Any) -> str:
    normalized = str(value or "").strip().casefold().replace("-", "_").replace(" ", "_")
    if normalized in {"approve", "approved", "approve_candidate"}:
        return "approve_candidate"
    if normalized in {"reject", "rejected", "reject_candidate"}:
        return "reject_candidate"
    return "needs_review"


def _clamp_confidence(value: Any) -> int:
    try:
        numeric = int(round(float(value)))
    except (TypeError, ValueError):
        return 0
    return max(0, min(100, numeric))


def _join_ai_risk_flags(value: Any) -> str:
    if isinstance(value, str):
        return value[:1000]
    if not isinstance(value, list):
        return ""
    flags = []
    for item in value:
        token = re.sub(r"[^a-z0-9_]+", "_", str(item or "").strip().casefold()).strip("_")
        if token:
            flags.append(token)
    return ",".join(dict.fromkeys(flags))[:1000]


def _is_quota_exception(exc: Exception) -> bool:
    text = str(exc).casefold()
    status_code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    return status_code == 429 or "429" in text or "quota exceeded" in text or "too many requests" in text


def _semantic_embedding_lookup(frame: pd.DataFrame | None) -> dict[str, dict[str, Any]]:
    if frame is None or frame.empty:
        return {}
    lookup: dict[str, dict[str, Any]] = {}
    cache = _ensure_market_embedding_columns(frame)
    for _, row in cache.iterrows():
        embedding_key = str(row.get("embedding_key") or "")
        vector = _vector_from_value(row.get("embedding_vector"))
        if not embedding_key or not vector:
            continue
        lookup[embedding_key] = {
            "vector": vector,
            "provider": str(row.get("semantic_provider") or ""),
            "model": str(row.get("embedding_model") or ""),
            "dim": str(row.get("embedding_dim") or ""),
            "text_hash": str(row.get("embedding_text_hash") or ""),
        }
    return lookup


def _attach_semantic_vectors(frame: pd.DataFrame, lookup: dict[str, dict[str, Any]]) -> None:
    vectors: list[list[float]] = []
    vector_arrays: list[Any] = []
    providers: list[str] = []
    models: list[str] = []
    dims: list[str] = []
    for _, row in frame.iterrows():
        embedding_key = str(row.get("_semantic_embedding_key") or "")
        match = lookup.get(embedding_key, {})
        vector = match.get("vector", [])
        vectors.append(vector)
        vector_arrays.append(_dense_unit_array(vector))
        providers.append(str(match.get("provider") or ""))
        models.append(str(match.get("model") or ""))
        dims.append(str(match.get("dim") or ""))
    frame["_semantic_vector"] = vectors
    frame["_semantic_vector_array"] = vector_arrays
    frame["_semantic_provider"] = providers
    frame["_semantic_embedding_model"] = models
    frame["_semantic_embedding_dim"] = dims


def _semantic_vector_from_row(row: pd.Series | dict[str, Any]) -> list[float]:
    return _vector_from_value(_row_get(row, "_semantic_vector"))


def _semantic_dense_value_from_row(row: pd.Series | dict[str, Any]) -> Any:
    vector_array = _row_get(row, "_semantic_vector_array")
    if _has_dense_value(vector_array):
        return vector_array
    return _semantic_vector_from_row(row)


def _semantic_combined_match_score(
    *,
    semantic_score: float,
    lexical_score: float,
    structured_score: float,
    outcome_score: float,
) -> float:
    return float(min(100.0, 0.75 * semantic_score + 0.10 * lexical_score + 0.10 * structured_score + 0.05 * outcome_score))


def _structured_field_score(left: pd.Series | dict[str, Any], right: pd.Series | dict[str, Any]) -> float:
    checks = [
        bool(_row_get(left, "market_type") and _row_get(left, "market_type") == _row_get(right, "market_type")),
        bool(_cached_sport_context(left) and _cached_sport_context(left) == _cached_sport_context(right)),
        bool(_row_get(left, "event_date") and _row_get(left, "event_date") == _row_get(right, "event_date")),
        bool(_cached_scope_key(left) and _cached_scope_key(left) == _cached_scope_key(right)),
        bool(_row_get(left, "event_year") and _row_get(left, "event_year") == _row_get(right, "event_year")),
    ]
    applicable = [
        bool(_row_get(left, "market_type") or _row_get(right, "market_type")),
        bool(_cached_sport_context(left) or _cached_sport_context(right)),
        bool(_row_get(left, "event_date") or _row_get(right, "event_date")),
        bool(_cached_scope_key(left) or _cached_scope_key(right)),
        bool(_row_get(left, "event_year") or _row_get(right, "event_year")),
    ]
    total = sum(applicable)
    if not total:
        return 0.0
    return 100.0 * sum(check for check, use in zip(checks, applicable, strict=True) if use) / total


def _outcome_match_score(left: pd.Series | dict[str, Any], right: pd.Series | dict[str, Any]) -> float:
    if _cached_outcomes_compatible(left, right):
        return 100.0
    return float(fuzz.token_set_ratio(_cached_candidate_outcome_key(left), _cached_candidate_outcome_key(right)))


def _candidate_embedding_key(row: pd.Series | dict[str, Any], text_hash: str | None = None) -> str:
    text_hash = text_hash or _stable_text_hash(build_canonical_embedding_text(row))
    return "|".join([_candidate_embedding_identity_key(row), _slugify(str(text_hash or ""))])


def _candidate_embedding_identity_key(row: pd.Series | dict[str, Any]) -> str:
    parts = [
        _row_get(row, "venue"),
        _row_get(row, "market_id"),
        _row_get(row, "ticker_or_slug"),
        _row_get(row, "yes_token_id"),
        _semantic_alias_normalize(_row_get(row, "outcome_label"), sport=_cached_sport_context(row)),
    ]
    return "|".join(_slugify(str(part or "")) for part in parts)


def _embedding_cache_key(embedding_key: str, provider: str, model_name: str, embedding_dim: int) -> str:
    return "|".join([embedding_key, _slugify(provider), _slugify(model_name), str(int(embedding_dim))])


def _stable_text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _semantic_embedding_model_name(provider: str) -> str:
    provider = _normalize_semantic_provider(provider)
    if provider == "local":
        return LOCAL_SEMANTIC_EMBEDDING_MODEL
    if provider == "vertex-gemini":
        return VERTEX_GEMINI_EMBEDDING_MODEL
    return ""


def _normalize_semantic_provider(provider: str | None) -> str:
    value = str(provider or DEFAULT_SEMANTIC_EMBEDDING_PROVIDER).strip().casefold()
    if value not in SEMANTIC_EMBEDDING_PROVIDERS:
        raise ValueError(f"semantic embedding provider must be one of {sorted(SEMANTIC_EMBEDDING_PROVIDERS)}")
    return value


def _semantic_alias_normalize(value: Any, *, sport: str = "") -> str:
    normalized = _normalize_name(value)
    if not normalized:
        return ""
    aliases = {**SEMANTIC_GENERAL_ALIASES, **TEAM_ALIASES_BY_SPORT.get(sport, {})}
    tokens = normalized.split()
    output: list[str] = []
    index = 0
    while index < len(tokens):
        replacement = ""
        consumed = 0
        for width in (3, 2, 1):
            phrase = " ".join(tokens[index : index + width])
            if phrase in aliases:
                replacement = aliases[phrase]
                consumed = width
                break
        if replacement:
            output.extend(replacement.split())
            index += consumed
        else:
            output.append(tokens[index])
            index += 1
    return " ".join(output)


def _dense_local_embedding(text: str, dimensions: int = DEFAULT_SEMANTIC_EMBEDDING_DIM) -> list[float]:
    normalized = _normalize_name(text)
    tokens = _embedding_tokens_from_normalized(normalized)
    weights: Counter[int] = Counter()
    for token in tokens:
        weights[_hash_embedding_feature(token, dimensions)] += 2.0 if "_" in token else 1.0
    norm = math.sqrt(sum(value * value for value in weights.values()))
    vector = [0.0] * dimensions
    if not norm:
        return vector
    for index, value in weights.items():
        vector[index] = float(value / norm)
    return vector


def _dense_value_from_row(row: pd.Series | dict[str, Any], array_key: str, vector_key: str) -> Any:
    vector_array = _row_get(row, array_key)
    if _has_dense_value(vector_array):
        return vector_array
    vector = _row_get(row, vector_key)
    return vector if _has_dense_value(vector) else []


def _dense_unit_array(vector: Any) -> Any:
    if np is None or not _has_dense_value(vector):
        return None
    array = vector if isinstance(vector, np.ndarray) else np.asarray(vector, dtype=float)
    if array.size == 0:
        return None
    norm = float(np.linalg.norm(array))
    if not norm:
        return array
    return array / norm


def _has_dense_value(value: Any) -> bool:
    if value is None:
        return False
    if np is not None and isinstance(value, np.ndarray):
        return bool(value.size)
    if isinstance(value, (list, tuple)):
        return bool(value)
    return False


def _dense_cosine(left: Any, right: Any) -> float:
    if not _has_dense_value(left) or not _has_dense_value(right):
        return 0.0
    limit = min(len(left), len(right))
    if limit == 0:
        return 0.0
    if np is not None and limit >= 64:
        left_array = left[:limit] if isinstance(left, np.ndarray) else np.asarray(left[:limit], dtype=float)
        right_array = right[:limit] if isinstance(right, np.ndarray) else np.asarray(right[:limit], dtype=float)
        left_norm = float(np.linalg.norm(left_array))
        right_norm = float(np.linalg.norm(right_array))
        if not left_norm or not right_norm:
            return 0.0
        return float(np.dot(left_array, right_array) / (left_norm * right_norm))
    dot = sum(float(left[index]) * float(right[index]) for index in range(limit))
    left_norm = math.sqrt(sum(float(value) * float(value) for value in left[:limit]))
    right_norm = math.sqrt(sum(float(value) * float(value) for value in right[:limit]))
    if not left_norm or not right_norm:
        return 0.0
    return float(dot / (left_norm * right_norm))


def _vector_to_json(vector: list[float]) -> str:
    return json.dumps([round(float(value), 8) for value in vector], separators=(",", ":"))


def _has_vector_value(value: Any) -> bool:
    if isinstance(value, (list, tuple)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip()
        return text.startswith("[") and text.endswith("]")
    return False


def _vector_from_value(value: Any) -> list[float]:
    if isinstance(value, list):
        return [float(item) for item in value]
    if isinstance(value, tuple):
        return [float(item) for item in value]
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        if isinstance(parsed, list):
            return [float(item) for item in parsed]
    return []


def _embedding_prefilter_candidates(pm: pd.Series, kalshi: pd.DataFrame, embedding_index: dict[str, tuple[Any, ...]]) -> pd.DataFrame:
    if kalshi.empty:
        return kalshi
    if len(kalshi) > EMBEDDING_PREFILTER_LIMIT:
        return _embedding_prefilter_candidates_by_index(pm, kalshi, embedding_index)
    candidates = kalshi.copy()
    sport = _cached_sport_context(pm)
    if sport:
        same_sport = candidates[candidates["_sport_context"].fillna("").astype(str) == sport] if "_sport_context" in candidates.columns else candidates[candidates.apply(lambda row: _cached_sport_context(row) == sport, axis=1)]
        if not same_sport.empty:
            candidates = same_sport

    event_date = str(pm.get("event_date") or "")
    if event_date and "event_date" in candidates.columns:
        same_date = candidates[candidates["event_date"].fillna("").astype(str) == event_date]
        if not same_date.empty:
            candidates = same_date

    event_year = str(pm.get("event_year") or "")
    if event_year and "event_year" in candidates.columns and str(pm.get("market_type") or "") != "match_winner":
        same_year = candidates[candidates["event_year"].fillna("").astype(str) == event_year]
        if not same_year.empty:
            candidates = same_year

    scope = _cached_scope_key(pm)
    if scope and str(pm.get("market_type") or "") in {"championship_winner", "pole_position_winner"}:
        same_scope = candidates[candidates["_scope_key"].fillna("").astype(str) == scope] if "_scope_key" in candidates.columns else candidates[candidates.apply(lambda row: _cached_scope_key(row) == scope, axis=1)]
        if not same_scope.empty:
            candidates = same_scope

    if len(candidates) <= EMBEDDING_PREFILTER_LIMIT:
        return candidates

    return _embedding_prefilter_candidates_by_index(pm, candidates, embedding_index)


def _embedding_prefilter_candidates_by_index(
    pm: pd.Series,
    candidates: pd.DataFrame,
    embedding_index: dict[str, tuple[Any, ...]],
) -> pd.DataFrame:
    allowed_indexes = set(candidates.index)
    token_counts: Counter[Any] = Counter()
    for token in _cached_important_embedding_token_set(pm):
        for index in embedding_index.get(token, ()):
            if index in allowed_indexes:
                token_counts[index] += 1
    if token_counts:
        top_indexes = [index for index, _ in token_counts.most_common(EMBEDDING_PREFILTER_LIMIT)]
        return candidates.loc[top_indexes]
    return candidates.head(EMBEDDING_PREFILTER_LIMIT)


def _combined_embedding_match_score(
    embedding_score: float,
    lexical_score: float,
    pm: pd.Series | dict[str, Any],
    ks: pd.Series | dict[str, Any],
) -> float:
    score = 0.58 * embedding_score + 0.42 * lexical_score
    if _row_get(pm, "market_type") and _row_get(pm, "market_type") == _row_get(ks, "market_type"):
        score += 6.0
    if _row_get(pm, "event_date") and _row_get(pm, "event_date") == _row_get(ks, "event_date"):
        score += 12.0
    if _cached_sport_context(pm) and _cached_sport_context(pm) == _cached_sport_context(ks):
        score += 8.0
    if _cached_candidate_outcome_key(pm) and _cached_outcomes_compatible(pm, ks):
        score += 18.0
    if _cached_scope_key(pm) and _cached_scope_key(pm) == _cached_scope_key(ks):
        score += 5.0
    return float(min(score, 100.0))


def _embedding_candidate_types_compatible(left: pd.Series | dict[str, Any], right: pd.Series | dict[str, Any]) -> bool:
    return _suggestion_pair_gate_reason(left, right, strict=True) == ""


def _fast_embedding_lexical_score(left: pd.Series | dict[str, Any], right: pd.Series | dict[str, Any]) -> float:
    subject_score = fuzz.token_set_ratio(str(_row_get(left, "subject") or ""), str(_row_get(right, "subject") or ""))
    title_score = fuzz.token_set_ratio(str(_row_get(left, "title") or ""), str(_row_get(right, "title") or ""))
    event_score = fuzz.token_set_ratio(str(_row_get(left, "event_title") or ""), str(_row_get(right, "event_title") or ""))
    outcome_score = fuzz.token_set_ratio(_cached_candidate_outcome_key(left), _cached_candidate_outcome_key(right))
    year_score = 100.0 if _row_get(left, "event_year") and _row_get(left, "event_year") == _row_get(right, "event_year") else 0.0
    type_score = 100.0 if _row_get(left, "market_type") == _row_get(right, "market_type") else 0.0
    return float(0.25 * subject_score + 0.20 * event_score + 0.20 * outcome_score + 0.15 * title_score + 0.15 * year_score + 0.05 * type_score)


def _cached_candidate_outcome_key(row: pd.Series | dict[str, Any]) -> str:
    if _row_has_key(row, "_normalized_outcome"):
        return str(_row_get(row, "_normalized_outcome") or "")
    return _candidate_outcome_key(row)


def _cached_outcomes_compatible(left: pd.Series | dict[str, Any], right: pd.Series | dict[str, Any]) -> bool:
    left_key = _cached_candidate_outcome_key(left)
    right_key = _cached_candidate_outcome_key(right)
    if not left_key or not right_key:
        return False
    if left_key == right_key:
        return True
    sport = _cached_sport_context(left) or _cached_sport_context(right)
    if sport in {"golf", "f1"}:
        return _competitor_name_compatible(_row_get(left, "outcome_label"), _row_get(right, "outcome_label"))
    if sport in {"tennis", "valorant"}:
        return _person_or_team_name_compatible(_row_get(left, "outcome_label"), _row_get(right, "outcome_label"))
    return False


def _candidate_embedding_text(row: pd.Series | dict[str, Any]) -> str:
    parts = [
        _row_get(row, "market_type"),
        _cached_sport_context(row),
        _row_get(row, "event_title"),
        _row_get(row, "title"),
        _row_get(row, "subtitle"),
        _row_get(row, "outcome_label"),
        _row_get(row, "subject"),
        _row_get(row, "event_date"),
        _row_get(row, "event_year"),
        _cached_scope_key(row),
        _row_get(row, "category"),
        _row_get(row, "keyword_hits"),
        str(_row_get(row, "settlement_summary") or "")[:180],
    ]
    return _normalize_name(" ".join(str(part or "") for part in parts))


def _embedding_token_set(row: pd.Series | dict[str, Any]) -> set[str]:
    return set(_embedding_tokens(_candidate_embedding_text(row)))


def _cached_embedding_token_set(row: pd.Series | dict[str, Any]) -> set[str]:
    cached = _row_get(row, "_embedding_token_set")
    if isinstance(cached, set):
        return cached
    if isinstance(cached, (list, tuple)):
        return set(str(value) for value in cached)
    return _embedding_token_set(row)


def _cached_important_embedding_token_set(row: pd.Series | dict[str, Any]) -> set[str]:
    cached = _row_get(row, "_important_embedding_token_set")
    if isinstance(cached, set):
        return cached
    if isinstance(cached, (list, tuple)):
        return set(str(value) for value in cached)
    return _important_embedding_tokens(_cached_embedding_token_set(row))


def _build_embedding_token_index(frame: pd.DataFrame) -> dict[str, tuple[Any, ...]]:
    index: dict[str, set[Any]] = {}
    if frame.empty:
        return {}
    for row_index, row in frame.iterrows():
        for token in _cached_important_embedding_token_set(row):
            index.setdefault(token, set()).add(row_index)
    return {token: tuple(indexes) for token, indexes in index.items()}


def _important_embedding_tokens(tokens: set[str]) -> set[str]:
    output: set[str] = set()
    for token in tokens:
        if not token or token in EMBEDDING_STOPWORDS:
            continue
        compact = token.replace("_", "")
        if len(compact) < 3:
            continue
        if compact.isdigit():
            continue
        output.add(token)
    return output


def _cached_sport_context(row: pd.Series | dict[str, Any]) -> str:
    if _row_has_key(row, "_sport_context"):
        return str(_row_get(row, "_sport_context") or "")
    cached = str(_row_get(row, "_sport_context") or "")
    return cached or _market_sport_context(row)


def _cached_scope_key(row: pd.Series | dict[str, Any]) -> str:
    if _row_has_key(row, "_scope_key"):
        return _cached_string_value(row, "_scope_key")
    cached = _cached_string_value(row, "_scope_key")
    return cached or _event_scope_key(row)


def _cached_string_value(row: pd.Series | dict[str, Any], key: str) -> str:
    value = _row_get(row, key)
    return "" if _is_blank(value) else str(value)


def _cached_bool_value(row: pd.Series | dict[str, Any], key: str) -> bool:
    value = _row_get(row, key)
    if _is_blank(value):
        return False
    if isinstance(value, str):
        return value.strip().casefold() in {"1", "true", "yes"}
    return bool(value)


def _row_has_key(row: pd.Series | dict[str, Any], key: str) -> bool:
    if isinstance(row, pd.Series):
        return key in row.index
    return key in row


def _embedding_tokens(text: str) -> list[str]:
    normalized = _normalize_name(text)
    return _embedding_tokens_from_normalized(normalized)


def _embedding_tokens_from_normalized(normalized: str) -> list[str]:
    raw_tokens = [token for token in normalized.split() if token]
    tokens = [token for token in raw_tokens if token not in EMBEDDING_STOPWORDS]
    expanded = list(tokens)
    for chunk in _bigram_chunks(raw_tokens):
        usable = [token for token in chunk if token not in EMBEDDING_STOPWORDS]
        expanded.extend(f"{left}_{right}" for left, right in zip(usable, usable[1:], strict=False))
    return expanded


def _bigram_chunks(tokens: list[str]) -> list[list[str]]:
    chunks: list[list[str]] = []
    current: list[str] = []
    for token in tokens:
        if token in SEMANTIC_BIGRAM_SEPARATORS:
            if current:
                chunks.append(current)
            current = []
            continue
        current.append(token)
    if current:
        chunks.append(current)
    return chunks


def _hashed_text_embedding(text: str, dimensions: int = EMBEDDING_DIMENSIONS) -> dict[int, float]:
    normalized = _normalize_name(text)
    return _hashed_embedding_from_tokens(_embedding_tokens_from_normalized(normalized), normalized_text=normalized, dimensions=dimensions)


def _hashed_embedding_from_tokens(
    tokens: list[str],
    *,
    normalized_text: str,
    dimensions: int = EMBEDDING_DIMENSIONS,
) -> dict[int, float]:
    weights: Counter[int] = Counter()
    for token in tokens:
        weights[_hash_embedding_feature(token, dimensions)] += 2.0 if "_" in token else 1.0
    norm = math.sqrt(sum(value * value for value in weights.values()))
    if not norm:
        return {}
    return {index: value / norm for index, value in weights.items()}


def _hash_embedding_feature(feature: str, dimensions: int) -> int:
    digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, byteorder="big") % dimensions


def _embedding_cosine(left: dict[int, float], right: dict[int, float]) -> float:
    if not left or not right:
        return 0.0
    if len(left) > len(right):
        left, right = right, left
    return float(sum(value * right.get(index, 0.0) for index, value in left.items()))


def _token_overlap_score(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / math.sqrt(len(left) * len(right))


def classify_market_type(row: pd.Series | dict[str, Any]) -> str:
    text = _candidate_text(row)
    primary_text = _primary_market_text(row)
    if re.search(r"\bset\s+\d+\s+winner\b", text) or "set winner:" in text:
        return "set_winner"
    if re.search(r"\bmap\s+\d+\s+winner\b", text) or "map winner" in text:
        return "set_winner"
    if "pole position" in text or "fastest valid qualifying lap" in text:
        return "pole_position_winner"
    if _is_transfer_market_text(text):
        return "transfer"
    if _champion_group_scope_key(row) or _unbeaten_champion_scope_key(row) or _is_exact_score_market(row) or _is_player_prop_market(row):
        return "other"
    if _is_non_winner_prop_market_text(primary_text):
        return "other"
    if _is_tournament_winner_market_text(primary_text):
        return "championship_winner"
    if any(term in text for term in ("highest-scoring", "highest scoring")):
        return "team_stat"
    if any(
        term in text
        for term in (
            "highest batting average",
            "highest home runs",
            "highest rbis",
            "highest stolen bases",
            "highest era",
            "highest saves",
            "highest strikeouts",
            "highest rebounds per game",
            "highest points per game",
            "highest blocks per game",
            "highest steals per game",
            "highest three point percentage",
            "highest field goal percentage",
            "highest free throw percentage",
            "strike out the most batters",
            "record the most assists",
            "most assists",
            "most strikeouts",
            "most home runs",
            "most rbis",
            "most stolen bases",
            "most intentional walks",
            "most triples",
            "most doubles",
        )
    ):
        return "player_award"
    if any(
        term in text
        for term in (
            "golden glove",
            "golden boot",
            "silver boot",
            "platinum glove",
            "hank aaron award",
            "outstanding designated hitter award",
            "calder trophy",
            "rookie of the year",
            "manager of the year",
            "most valuable player",
            " mvp",
            "cy young",
            "assists per game",
            "ballon d",
            "top scorer",
            "goalscorer",
            "player of the",
            "defender of the year",
            "goalkeeper of the year",
            "coach of the year",
            " era leader",
            " lead the mlb in era",
            " lead the league in",
            " lead the nfl in",
            " lead the nba in",
            " lead the wnba in",
            " lead the nhl in",
            " lead the mlb in",
        )
    ):
        return "player_award"
    if any(term in text for term in ("eliminated", "advance to", "advancing", "reach the", "qualify for")):
        return "advancement"
    if any(term in text for term in ("host", "hosts", "announced as host", "announced as hosts")):
        return "host_country"
    if "highest constructor score" in text or "win the most series" in text or "most series wins" in text:
        return "team_stat"
    if re.search(r"\btop\s+(?:5|10|20|40)\b", text) or "make the cut" in text or "miss the cut" in text:
        return "other"
    if _is_championship_market_text(primary_text):
        return "championship_winner"
    if "both teams to score" in text or "btts" in text:
        return "both_teams_score"
    if any(term in text for term in ("spread", "handicap", "(-", "(+")):
        return "spread"
    if _is_total_market_text(text):
        return "total"
    if "halftime" in text or "1st half" in text or "first half" in text:
        return "halftime"
    if any(term in text for term in ("first inning", "1st inning", "nrfi", "yrfi", "extra innings", "run scored")):
        return "other"
    if "end in a draw" in text or " tie" in text or "winner?" in text:
        return "match_winner"
    if any(term in text for term in ("win?", " win on ", " wins the ")):
        return "match_winner"
    if _teams_from_match_title(extract_event_title(row)):
        return "match_winner"
    if any(term in text for term in (" vs ", " vs. ")):
        return "match_winner"
    return "other"


def extract_market_subject(row: pd.Series | dict[str, Any]) -> str:
    event_title = extract_event_title(row)
    outcome_label = extract_outcome_label(row)
    if event_title and outcome_label and classify_market_type(row) in {"match_winner", "championship_winner", "pole_position_winner"}:
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
            return _tennis_match_title_from_text(value) or str(value).strip()
    if isinstance(raw.get("_event_context_payload"), dict) and raw["_event_context_payload"].get("title"):
        value = str(raw["_event_context_payload"]["title"]).strip()
        return _tennis_match_title_from_text(value) or value
    title = str(_row_get(row, "title") or "").strip()
    title = re.sub(r"\s+Winner\?$", "", title, flags=re.IGNORECASE).strip()
    tennis_match = _tennis_match_title_from_text(title)
    if tennis_match:
        return tennis_match
    if ":" in title:
        prefix, suffix = [part.strip() for part in title.split(":", 1)]
        if _should_split_title_prefix(prefix, suffix):
            title = suffix
    event_winner_title = _championship_event_title_from_text(title)
    if event_winner_title:
        return event_winner_title
    game_match = re.search(
        r"^Will\s+.+?\s+win\s+the\s+(.+?)\s+(?:valorant|counter[- ]?strike|league\s+of\s+legends)?\s*(?:match|game)\??$",
        title,
        flags=re.IGNORECASE,
    )
    if game_match and _teams_from_match_title(game_match.group(1)):
        return game_match.group(1).strip()
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
        raw.get("gameStartTime"),
        raw.get("startTime"),
        _date_from_ticker_text(
            " ".join(
                str(value or "")
                for value in (
                    raw.get("event_ticker"),
                    raw.get("_event_context_ticker"),
                    raw.get("ticker"),
                    _row_get(row, "ticker_or_slug"),
                )
            )
        ),
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


def _date_from_ticker_text(value: Any) -> str:
    text = str(value or "").upper()
    match = re.search(r"(?:^|-)(\d{2})(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)(\d{1,2})(?:\d{0,4})", text)
    if not match:
        return ""
    year = 2000 + int(match.group(1))
    month = _month_number(match.group(2))
    day = int(match.group(3))
    if not month or not 1 <= day <= 31:
        return ""
    return f"{year:04d}-{month:02d}-{day:02d}"


def extract_event_match_key(row: pd.Series | dict[str, Any], event_title: str | None = None, event_date: str | None = None) -> str:
    event_title = event_title if event_title is not None else extract_event_title(row)
    event_date = event_date if event_date is not None else extract_event_date(row)
    teams = _teams_from_match_title(event_title)
    if len(teams) != 2:
        return ""
    normalized_teams = sorted(_canonical_team_name(team, row) for team in teams)
    if not all(normalized_teams):
        return ""
    return "|".join([event_date or "", *normalized_teams])


def extract_outcome_label(row: pd.Series | dict[str, Any]) -> str:
    raw = _raw_payload(row)
    venue = str(_row_get(row, "venue") or "")
    if venue == "kalshi":
        return str(raw.get("yes_sub_title") or raw.get("subtitle") or "").strip()
    title = str(_row_get(row, "title") or "")
    match = re.search(r"^(.+?)\s+winning\s+after\s+\d+\s+innings\??$", title, flags=re.IGNORECASE)
    if match:
        return match.group(1).strip()
    if re.search(r"\btied\s+after\s+\d+\s+innings\b", title, flags=re.IGNORECASE):
        return "Tie"
    match = re.search(r"Will\s+(.+?)\s+win\s+on\s+\d{4}-\d{2}-\d{2}\??", title, flags=re.IGNORECASE)
    if match:
        return match.group(1).strip()
    if re.search(r"end\s+in\s+a\s+draw", title, flags=re.IGNORECASE):
        return "Tie"
    host = re.search(r"^Will\s+(.+?)\s+(?:host\b|be\s+announced\s+as\s+(?:a\s+)?hosts?\b)", title, flags=re.IGNORECASE)
    if host:
        return host.group(1).strip()
    championship = _championship_outcome_from_title(title)
    if championship:
        return championship
    event_winner = _event_winner_outcome_from_title(title)
    if event_winner:
        return event_winner
    outcomes = parse_json_array(_row_get(row, "outcomes"))
    if len(outcomes) == 2 and outcomes[0] not in ("Yes", "Over"):
        return str(outcomes[0])
    return ""


def extract_event_year(row: pd.Series | dict[str, Any]) -> str:
    if classify_market_type(row) == "championship_winner":
        text = " ".join(
            str(value or "")
            for value in (
                _row_get(row, "title"),
                _row_get(row, "event_title"),
                _row_get(row, "ticker_or_slug"),
                _row_get(row, "rules_text"),
            )
        )
        match = re.search(r"\b(20\d{2})(?:\s*[-/]\s*\d{2})?\b", text)
        if match:
            return match.group(1)
    event_date = extract_event_date(row)
    if event_date:
        return event_date[:4]
    text = _candidate_text(row)
    match = re.search(r"\b(20\d{2})\b", text)
    return match.group(1) if match else ""


def infer_event_timeframe(row: pd.Series | dict[str, Any]) -> str:
    market_type = classify_market_type(row)
    text = _candidate_text(row)
    if re.search(r"\b(?:first|1st)\s*5\b", text) or "first five" in text or re.search(r"\bafter\s+5\s+innings\b", text):
        return "first 5 innings"
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
    if market_type == "pole_position_winner":
        return "Check exact race weekend, qualifying session, valid-lap definition, and driver naming."
    if market_type == "advancement":
        return "Check tournament stage, advancement vs match result, and settlement timing."
    if market_type in {"player_award", "team_stat", "transfer"}:
        return "Review full proposition, source, deadline, and whether a Kalshi equivalent really exists."
    if market_type in {"spread", "total", "both_teams_score", "halftime", "set_winner"}:
        return "Do not map to simple winner markets; check threshold, period, void/cancel rules."
    return "Review full rules before approving."


def candidate_match_score(left: pd.Series | dict[str, Any], right: pd.Series | dict[str, Any]) -> float:
    left_event_key = str(_row_get(left, "event_match_key") or "")
    right_event_key = str(_row_get(right, "event_match_key") or "")
    left_outcome = _cached_candidate_outcome_key(left)
    right_outcome = _cached_candidate_outcome_key(right)
    if (
        _row_get(left, "market_type") in {"championship_winner", "pole_position_winner"}
        and _row_get(left, "market_type") == _row_get(right, "market_type")
        and left_outcome
        and _cached_outcomes_compatible(left, right)
        and _cached_sport_context(left) == _cached_sport_context(right)
    ):
        year_score = 100.0 if _row_get(left, "event_year") and _row_get(left, "event_year") == _row_get(right, "event_year") else 70.0
        scope_score = 100.0 if _cached_scope_key(left) and _cached_scope_key(left) == _cached_scope_key(right) else 60.0
        title_score = fuzz.token_set_ratio(str(_row_get(left, "event_title") or ""), str(_row_get(right, "event_title") or ""))
        return float(0.55 * 100.0 + 0.20 * scope_score + 0.15 * year_score + 0.10 * title_score)
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
    if _is_gcs_uri(path):
        try:
            frame = pd.read_csv(BytesIO(_download_gcs_bytes(str(path))), dtype=str, keep_default_na=False)
        except FileNotFoundError:
            return pd.DataFrame(columns=MAPPING_COLUMNS)
    else:
        mapping_path = Path(path)
        if not mapping_path.exists():
            return pd.DataFrame(columns=MAPPING_COLUMNS)
        frame = pd.read_csv(mapping_path, dtype=str, keep_default_na=False)
    for column in MAPPING_COLUMNS:
        if column not in frame.columns:
            frame[column] = ""
    frame["lifecycle_status"] = frame["lifecycle_status"].fillna("").replace("", "active")
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
    approved = validated[validated["is_approved"] == True].copy()  # noqa: E712
    if "lifecycle_status" in approved.columns:
        lifecycle = approved["lifecycle_status"].fillna("").astype(str).str.strip().str.lower()
        approved = approved[lifecycle.isin(("", "active", "open", "trading"))]
    return approved.reset_index(drop=True)


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
    market_limit: int = DEFAULT_MARKET_LIMIT,
    page_size: int = 500,
    orderbook_depth: int = 100,
    min_net_edge: float = DEFAULT_MIN_NET_EDGE,
    slippage_buffer_per_leg: float = DEFAULT_SLIPPAGE_BUFFER_PER_LEG,
    fee_buffer_total: float = DEFAULT_FEE_BUFFER_TOTAL,
    min_depth_per_leg: float = DEFAULT_MIN_DEPTH_PER_LEG,
    discover: bool = True,
    discovery_only: bool = False,
    include_general_search: bool = True,
    all_active_markets: bool = False,
    embedding_min_score: float = DEFAULT_EMBEDDING_MIN_SCORE,
    embedding_top_k: int = DEFAULT_EMBEDDING_TOP_K,
    semantic_embedding_provider: str = DEFAULT_SEMANTIC_EMBEDDING_PROVIDER,
    semantic_embedding_dim: int = DEFAULT_SEMANTIC_EMBEDDING_DIM,
    semantic_top_k: int = DEFAULT_SEMANTIC_TOP_K,
    semantic_min_score: float = DEFAULT_SEMANTIC_MIN_SCORE,
    semantic_batch_size: int = DEFAULT_VERTEX_GEMINI_BATCH_SIZE,
    semantic_batch_sleep_seconds: float = DEFAULT_VERTEX_GEMINI_BATCH_SLEEP_SECONDS,
    semantic_retry_initial_seconds: float = DEFAULT_VERTEX_GEMINI_RETRY_INITIAL_SECONDS,
    semantic_max_retries: int = DEFAULT_VERTEX_GEMINI_MAX_RETRIES,
    semantic_cache_flush_batches: int = DEFAULT_SEMANTIC_CACHE_FLUSH_BATCHES,
    semantic_max_embedding_texts: int = DEFAULT_SEMANTIC_MAX_EMBEDDING_TEXTS,
    semantic_embedding_client: Any | None = None,
    ai_pair_review_provider: str = DEFAULT_AI_PAIR_REVIEW_PROVIDER,
    ai_pair_review_model: str = DEFAULT_AI_PAIR_REVIEW_MODEL,
    ai_pair_review_limit: int = DEFAULT_AI_PAIR_REVIEW_LIMIT,
    ai_pair_review_min_score: float = DEFAULT_AI_PAIR_REVIEW_MIN_SCORE,
    ai_pair_review_client: Any | None = None,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    return run_market_snapshot(
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
        discovery_only=discovery_only,
        include_general_search=include_general_search,
        all_active_markets=all_active_markets,
        embedding_min_score=embedding_min_score,
        embedding_top_k=embedding_top_k,
        semantic_embedding_provider=semantic_embedding_provider,
        semantic_embedding_dim=semantic_embedding_dim,
        semantic_top_k=semantic_top_k,
        semantic_min_score=semantic_min_score,
        semantic_batch_size=semantic_batch_size,
        semantic_batch_sleep_seconds=semantic_batch_sleep_seconds,
        semantic_retry_initial_seconds=semantic_retry_initial_seconds,
        semantic_max_retries=semantic_max_retries,
        semantic_cache_flush_batches=semantic_cache_flush_batches,
        semantic_max_embedding_texts=semantic_max_embedding_texts,
        semantic_embedding_client=semantic_embedding_client,
        ai_pair_review_provider=ai_pair_review_provider,
        ai_pair_review_model=ai_pair_review_model,
        ai_pair_review_limit=ai_pair_review_limit,
        ai_pair_review_min_score=ai_pair_review_min_score,
        ai_pair_review_client=ai_pair_review_client,
        client=client,
        scanner_label="FIFA",
        run_id_prefix="fifa",
        keywords=FIFA_KEYWORDS,
        polymarket_event_tag_slugs=POLYMARKET_EVENT_TAG_SLUGS,
        kalshi_series_tickers=KALSHI_FIFA_SERIES_TICKERS,
    )


def run_sports_snapshot(
    output_dir: str | Path = DEFAULT_SPORTS_OUTPUT_DIR,
    mapping_path: str | Path = DEFAULT_SPORTS_MAPPING_PATH,
    run_id: str | None = None,
    market_limit: int = DEFAULT_MARKET_LIMIT,
    page_size: int = 500,
    orderbook_depth: int = 100,
    min_net_edge: float = DEFAULT_MIN_NET_EDGE,
    slippage_buffer_per_leg: float = DEFAULT_SLIPPAGE_BUFFER_PER_LEG,
    fee_buffer_total: float = DEFAULT_FEE_BUFFER_TOTAL,
    min_depth_per_leg: float = DEFAULT_MIN_DEPTH_PER_LEG,
    discover: bool = True,
    discovery_only: bool = False,
    include_general_search: bool = True,
    all_active_markets: bool = False,
    embedding_min_score: float = DEFAULT_EMBEDDING_MIN_SCORE,
    embedding_top_k: int = DEFAULT_EMBEDDING_TOP_K,
    semantic_embedding_provider: str = DEFAULT_SEMANTIC_EMBEDDING_PROVIDER,
    semantic_embedding_dim: int = DEFAULT_SEMANTIC_EMBEDDING_DIM,
    semantic_top_k: int = DEFAULT_SEMANTIC_TOP_K,
    semantic_min_score: float = DEFAULT_SEMANTIC_MIN_SCORE,
    semantic_batch_size: int = DEFAULT_VERTEX_GEMINI_BATCH_SIZE,
    semantic_batch_sleep_seconds: float = DEFAULT_VERTEX_GEMINI_BATCH_SLEEP_SECONDS,
    semantic_retry_initial_seconds: float = DEFAULT_VERTEX_GEMINI_RETRY_INITIAL_SECONDS,
    semantic_max_retries: int = DEFAULT_VERTEX_GEMINI_MAX_RETRIES,
    semantic_cache_flush_batches: int = DEFAULT_SEMANTIC_CACHE_FLUSH_BATCHES,
    semantic_max_embedding_texts: int = DEFAULT_SEMANTIC_MAX_EMBEDDING_TEXTS,
    semantic_embedding_client: Any | None = None,
    ai_pair_review_provider: str = DEFAULT_AI_PAIR_REVIEW_PROVIDER,
    ai_pair_review_model: str = DEFAULT_AI_PAIR_REVIEW_MODEL,
    ai_pair_review_limit: int = DEFAULT_AI_PAIR_REVIEW_LIMIT,
    ai_pair_review_min_score: float = DEFAULT_AI_PAIR_REVIEW_MIN_SCORE,
    ai_pair_review_client: Any | None = None,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    return run_market_snapshot(
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
        discovery_only=discovery_only,
        include_general_search=include_general_search,
        all_active_markets=all_active_markets,
        embedding_min_score=embedding_min_score,
        embedding_top_k=embedding_top_k,
        semantic_embedding_provider=semantic_embedding_provider,
        semantic_embedding_dim=semantic_embedding_dim,
        semantic_top_k=semantic_top_k,
        semantic_min_score=semantic_min_score,
        semantic_batch_size=semantic_batch_size,
        semantic_batch_sleep_seconds=semantic_batch_sleep_seconds,
        semantic_retry_initial_seconds=semantic_retry_initial_seconds,
        semantic_max_retries=semantic_max_retries,
        semantic_cache_flush_batches=semantic_cache_flush_batches,
        semantic_max_embedding_texts=semantic_max_embedding_texts,
        semantic_embedding_client=semantic_embedding_client,
        ai_pair_review_provider=ai_pair_review_provider,
        ai_pair_review_model=ai_pair_review_model,
        ai_pair_review_limit=ai_pair_review_limit,
        ai_pair_review_min_score=ai_pair_review_min_score,
        ai_pair_review_client=ai_pair_review_client,
        client=client,
        scanner_label="sports",
        run_id_prefix="sports",
        keywords=SPORTS_KEYWORDS,
        polymarket_event_tag_slugs=SPORTS_POLYMARKET_EVENT_TAG_SLUGS,
        kalshi_series_tickers=KALSHI_SPORTS_SERIES_TICKERS,
    )


def run_market_snapshot(
    output_dir: str | Path,
    mapping_path: str | Path,
    run_id: str | None = None,
    market_limit: int = DEFAULT_MARKET_LIMIT,
    page_size: int = 500,
    orderbook_depth: int = 100,
    min_net_edge: float = DEFAULT_MIN_NET_EDGE,
    slippage_buffer_per_leg: float = DEFAULT_SLIPPAGE_BUFFER_PER_LEG,
    fee_buffer_total: float = DEFAULT_FEE_BUFFER_TOTAL,
    min_depth_per_leg: float = DEFAULT_MIN_DEPTH_PER_LEG,
    discover: bool = True,
    discovery_only: bool = False,
    include_general_search: bool = True,
    all_active_markets: bool = False,
    embedding_min_score: float = DEFAULT_EMBEDDING_MIN_SCORE,
    embedding_top_k: int = DEFAULT_EMBEDDING_TOP_K,
    semantic_embedding_provider: str = DEFAULT_SEMANTIC_EMBEDDING_PROVIDER,
    semantic_embedding_dim: int = DEFAULT_SEMANTIC_EMBEDDING_DIM,
    semantic_top_k: int = DEFAULT_SEMANTIC_TOP_K,
    semantic_min_score: float = DEFAULT_SEMANTIC_MIN_SCORE,
    semantic_batch_size: int = DEFAULT_VERTEX_GEMINI_BATCH_SIZE,
    semantic_batch_sleep_seconds: float = DEFAULT_VERTEX_GEMINI_BATCH_SLEEP_SECONDS,
    semantic_retry_initial_seconds: float = DEFAULT_VERTEX_GEMINI_RETRY_INITIAL_SECONDS,
    semantic_max_retries: int = DEFAULT_VERTEX_GEMINI_MAX_RETRIES,
    semantic_cache_flush_batches: int = DEFAULT_SEMANTIC_CACHE_FLUSH_BATCHES,
    semantic_max_embedding_texts: int = DEFAULT_SEMANTIC_MAX_EMBEDDING_TEXTS,
    semantic_embedding_client: Any | None = None,
    ai_pair_review_provider: str = DEFAULT_AI_PAIR_REVIEW_PROVIDER,
    ai_pair_review_model: str = DEFAULT_AI_PAIR_REVIEW_MODEL,
    ai_pair_review_limit: int = DEFAULT_AI_PAIR_REVIEW_LIMIT,
    ai_pair_review_min_score: float = DEFAULT_AI_PAIR_REVIEW_MIN_SCORE,
    ai_pair_review_client: Any | None = None,
    client: httpx.Client | None = None,
    scanner_label: str = "FIFA",
    run_id_prefix: str = "fifa",
    keywords: tuple[str, ...] = FIFA_KEYWORDS,
    polymarket_event_tag_slugs: tuple[str, ...] = POLYMARKET_EVENT_TAG_SLUGS,
    kalshi_series_tickers: tuple[str, ...] = KALSHI_FIFA_SERIES_TICKERS,
) -> dict[str, Any]:
    if client is None:
        with httpx.Client(timeout=DEFAULT_TIMEOUT_SECONDS, headers={"User-Agent": f"poly-x-kalshi-{scanner_label.lower()}-scanner"}) as owned_client:
            return run_market_snapshot(
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
                discovery_only=discovery_only,
                include_general_search=include_general_search,
                all_active_markets=all_active_markets,
                embedding_min_score=embedding_min_score,
                embedding_top_k=embedding_top_k,
                semantic_embedding_provider=semantic_embedding_provider,
                semantic_embedding_dim=semantic_embedding_dim,
                semantic_top_k=semantic_top_k,
                semantic_min_score=semantic_min_score,
                semantic_batch_size=semantic_batch_size,
                semantic_batch_sleep_seconds=semantic_batch_sleep_seconds,
                semantic_retry_initial_seconds=semantic_retry_initial_seconds,
                semantic_max_retries=semantic_max_retries,
                semantic_cache_flush_batches=semantic_cache_flush_batches,
                semantic_max_embedding_texts=semantic_max_embedding_texts,
                semantic_embedding_client=semantic_embedding_client,
                ai_pair_review_provider=ai_pair_review_provider,
                ai_pair_review_model=ai_pair_review_model,
                ai_pair_review_limit=ai_pair_review_limit,
                ai_pair_review_min_score=ai_pair_review_min_score,
                ai_pair_review_client=ai_pair_review_client,
                client=owned_client,
                scanner_label=scanner_label,
                run_id_prefix=run_id_prefix,
                keywords=keywords,
                polymarket_event_tag_slugs=polymarket_event_tag_slugs,
                kalshi_series_tickers=kalshi_series_tickers,
            )

    run_id = run_id or _new_run_id(run_id_prefix)
    started_at = utc_now_iso()
    raw_polymarket_markets: list[dict[str, Any]] = []
    raw_kalshi_markets: list[dict[str, Any]] = []
    candidates = pd.DataFrame(columns=CANDIDATE_COLUMNS)
    if discover:
        discovery_keywords = () if all_active_markets else keywords
        discovery_event_tag_slugs = ("", *polymarket_event_tag_slugs) if all_active_markets else polymarket_event_tag_slugs
        discovery_kalshi_series_tickers = () if all_active_markets else kalshi_series_tickers
        if semantic_embedding_provider != "off":
            market_limit_label = "all" if market_limit <= 0 else f"{market_limit:,}"
            scope_label = "all active markets" if all_active_markets else "configured discovery scope"
            _semantic_progress(
                f"{scanner_label} discovery: scanning active markets "
                f"(scope={scope_label}, market_limit={market_limit_label}, page_size={page_size})."
            )
        raw_polymarket_markets = fetch_polymarket_markets(
            client,
            max_markets=market_limit,
            page_size=page_size,
            keywords=discovery_keywords,
            event_tag_slugs=discovery_event_tag_slugs,
            include_general_search=include_general_search,
            discover_event_tags=all_active_markets,
        )
        raw_kalshi_markets = fetch_kalshi_markets(
            client,
            max_markets=market_limit,
            page_size=min(page_size, 200),
            keywords=discovery_keywords,
            series_tickers=discovery_kalshi_series_tickers,
            include_general_search=include_general_search,
            expand_event_markets=not all_active_markets,
        )
        if semantic_embedding_provider != "off":
            _semantic_progress(
                f"{scanner_label} discovery: {len(raw_polymarket_markets):,} Polymarket and "
                f"{len(raw_kalshi_markets):,} Kalshi markets matched discovery keywords."
            )
        candidates = normalize_market_candidates(
            raw_polymarket_markets,
            raw_kalshi_markets,
            run_id=run_id,
            retrieved_at=started_at,
            keywords=discovery_keywords,
        )
    approval_candidates = build_approval_candidates(candidates)
    if semantic_embedding_provider != "off":
        _semantic_progress(f"{scanner_label} discovery: {len(approval_candidates):,} approval candidate rows.")
    market_embeddings = prepare_market_embeddings(
        approval_candidates,
        output_dir=output_dir,
        run_id=run_id,
        retrieved_at=started_at,
        provider=semantic_embedding_provider,
        embedding_dim=semantic_embedding_dim,
        vertex_batch_size=semantic_batch_size,
        vertex_batch_sleep_seconds=semantic_batch_sleep_seconds,
        vertex_retry_initial_seconds=semantic_retry_initial_seconds,
        vertex_max_retries=semantic_max_retries,
        semantic_cache_flush_batches=semantic_cache_flush_batches,
        semantic_max_embedding_texts=semantic_max_embedding_texts,
        embedding_client=semantic_embedding_client,
    )
    if semantic_embedding_provider != "off":
        _semantic_progress(f"{scanner_label} semantic: generating suggested mappings.")
    suggested_mappings = suggest_manual_mappings(
        approval_candidates,
        embedding_min_score=embedding_min_score,
        embedding_top_k=embedding_top_k,
        semantic_embeddings=market_embeddings,
        semantic_min_score=semantic_min_score,
        semantic_top_k=semantic_top_k,
    )
    if semantic_embedding_provider != "off":
        _semantic_progress(f"{scanner_label} semantic: {len(suggested_mappings):,} suggested mappings generated.")
    suggested_mappings = review_suggested_mappings_with_ai(
        suggested_mappings,
        provider=ai_pair_review_provider,
        model_name=ai_pair_review_model,
        limit=ai_pair_review_limit,
        min_score=ai_pair_review_min_score,
        reviewed_at=started_at,
        review_client=ai_pair_review_client,
    )

    mappings = load_manual_mappings(mapping_path)
    mapping_snapshot = validate_manual_mappings(mappings)
    eligible_mappings = approved_mappings(mappings)
    if discovery_only:
        orderbooks = pd.DataFrame(columns=ORDERBOOK_COLUMNS)
        alerts = pd.DataFrame(columns=ALERT_COLUMNS)
        signals = pd.DataFrame(columns=SIGNAL_COLUMNS)
    else:
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
            "market_embeddings": market_embeddings,
            "suggested_mappings": suggested_mappings,
            "manual_mappings_snapshot": mapping_snapshot,
            "orderbook_snapshots": orderbooks,
            "arbitrage_alerts": alerts,
            "strategy_signals": signals,
            "scanner_runs": scanner_runs,
        },
    }
    if discovery_only:
        result["skip_latest_tables"] = PRICE_TABLES
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
    return watch_market_arbitrage(
        output_dir=output_dir,
        mapping_path=mapping_path,
        interval_seconds=interval_seconds,
        max_ticks=max_ticks,
        market_limit=market_limit,
        page_size=page_size,
        orderbook_depth=orderbook_depth,
        min_net_edge=min_net_edge,
        slippage_buffer_per_leg=slippage_buffer_per_leg,
        fee_buffer_total=fee_buffer_total,
        min_depth_per_leg=min_depth_per_leg,
        discover=discover,
        sleeper=sleeper,
        client_factory=client_factory,
        scanner_label="FIFA",
        snapshot_runner=run_fifa_snapshot,
    )


def watch_sports_arbitrage(
    output_dir: str | Path = DEFAULT_SPORTS_OUTPUT_DIR,
    mapping_path: str | Path = DEFAULT_SPORTS_MAPPING_PATH,
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
    return watch_market_arbitrage(
        output_dir=output_dir,
        mapping_path=mapping_path,
        interval_seconds=interval_seconds,
        max_ticks=max_ticks,
        market_limit=market_limit,
        page_size=page_size,
        orderbook_depth=orderbook_depth,
        min_net_edge=min_net_edge,
        slippage_buffer_per_leg=slippage_buffer_per_leg,
        fee_buffer_total=fee_buffer_total,
        min_depth_per_leg=min_depth_per_leg,
        discover=discover,
        sleeper=sleeper,
        client_factory=client_factory,
        scanner_label="sports",
        snapshot_runner=run_sports_snapshot,
    )


def watch_market_arbitrage(
    output_dir: str | Path,
    mapping_path: str | Path,
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
    scanner_label: str = "FIFA",
    snapshot_runner: Callable[..., dict[str, Any]] = run_fifa_snapshot,
) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    tick = 0
    backoff_seconds = interval_seconds
    while max_ticks is None or tick < max_ticks:
        try:
            factory = client_factory or (
                lambda: httpx.Client(timeout=DEFAULT_TIMEOUT_SECONDS, headers={"User-Agent": f"poly-x-kalshi-{scanner_label.lower()}-scanner"})
            )
            with factory() as client:
                result = snapshot_runner(
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
            _print_alerts(result["tables"]["arbitrage_alerts"], scanner_label=scanner_label)
            tick += 1
            backoff_seconds = interval_seconds
            if max_ticks is None or tick < max_ticks:
                sleeper(interval_seconds)
        except KeyboardInterrupt:
            print(f"Stopping {scanner_label} arbitrage watcher.")
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
    if _is_gcs_uri(output_dir):
        return write_fifa_snapshot_artifacts_gcs(result, output_dir)

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
    skip_latest_tables = set(result.get("skip_latest_tables", []))
    for name, frame in result["tables"].items():
        processed_paths[name] = append_processed_table(name, frame, output_root)
        if name in skip_latest_tables:
            latest_processed_paths[name] = {}
            continue
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


def write_fifa_snapshot_artifacts_gcs(result: dict[str, Any], output_dir: str | Path = DEFAULT_OUTPUT_DIR) -> dict[str, Any]:
    bucket_name, prefix = _split_gcs_uri(str(output_dir))
    storage = _google_storage_client()
    bucket = storage.bucket(bucket_name)
    run_id = result["run_id"]

    raw_paths = {
        "polymarket_markets": _gcs_uri(bucket_name, prefix, f"raw/polymarket/markets_{run_id}.json"),
        "kalshi_markets": _gcs_uri(bucket_name, prefix, f"raw/kalshi/markets_{run_id}.json"),
    }
    _upload_gcs_text(
        bucket,
        _gcs_blob_name(prefix, f"raw/polymarket/markets_{run_id}.json"),
        json.dumps(result["raw"].get("polymarket_markets", []), indent=2, sort_keys=True, default=str),
        content_type="application/json",
    )
    _upload_gcs_text(
        bucket,
        _gcs_blob_name(prefix, f"raw/kalshi/markets_{run_id}.json"),
        json.dumps(result["raw"].get("kalshi_markets", []), indent=2, sort_keys=True, default=str),
        content_type="application/json",
    )

    processed_paths: dict[str, dict[str, str]] = {}
    latest_processed_paths: dict[str, dict[str, str]] = {}
    skip_latest_tables = set(result.get("skip_latest_tables", []))
    for name, frame in result["tables"].items():
        run_prefix = f"processed/{name}/run_id={run_id}"
        processed_paths[name] = _write_gcs_table(bucket, bucket_name, prefix, run_prefix, name, frame)
        if name in skip_latest_tables:
            latest_processed_paths[name] = {}
            continue
        if name in DISCOVERY_REVIEW_TABLES and frame.empty:
            latest_processed_paths[name] = {
                "parquet": _gcs_uri(bucket_name, prefix, f"processed/latest/{name}.parquet"),
                "csv": _gcs_uri(bucket_name, prefix, f"processed/latest/{name}.csv"),
            }
            continue
        latest_processed_paths[name] = _write_gcs_table(bucket, bucket_name, prefix, "processed/latest", name, frame)

    alert_rows = result["tables"]["arbitrage_alerts"]
    actual_alerts = alert_rows[alert_rows["is_alert"] == True] if not alert_rows.empty else alert_rows  # noqa: E712
    alert_jsonl = _gcs_uri(bucket_name, prefix, f"alerts/run_id={run_id}/arbitrage_alerts.jsonl")
    if not actual_alerts.empty:
        body = "\n".join(json.dumps(record, sort_keys=True, default=str) for record in actual_alerts.to_dict(orient="records")) + "\n"
        _upload_gcs_text(
            bucket,
            _gcs_blob_name(prefix, f"alerts/run_id={run_id}/arbitrage_alerts.jsonl"),
            body,
            content_type="application/jsonl",
        )
    return {
        "raw_paths": raw_paths,
        "processed_paths": processed_paths,
        "latest_processed_paths": latest_processed_paths,
        "alert_jsonl": alert_jsonl,
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


def _write_gcs_table(
    bucket: Any,
    bucket_name: str,
    prefix: str,
    table_prefix: str,
    name: str,
    frame: pd.DataFrame,
) -> dict[str, str]:
    parquet_path = f"{table_prefix}/{name}.parquet"
    csv_path = f"{table_prefix}/{name}.csv"
    _upload_gcs_bytes(
        bucket,
        _gcs_blob_name(prefix, parquet_path),
        _dataframe_to_parquet_bytes(frame),
        content_type="application/octet-stream",
    )
    _upload_gcs_text(
        bucket,
        _gcs_blob_name(prefix, csv_path),
        frame.to_csv(index=False),
        content_type="text/csv",
    )
    return {
        "parquet": _gcs_uri(bucket_name, prefix, parquet_path),
        "csv": _gcs_uri(bucket_name, prefix, csv_path),
    }


def _dataframe_to_parquet_bytes(frame: pd.DataFrame) -> bytes:
    buffer = BytesIO()
    frame.to_parquet(buffer, index=False)
    return buffer.getvalue()


def _is_gcs_uri(value: str | Path) -> bool:
    return str(value).startswith("gs://")


def _split_gcs_uri(uri: str) -> tuple[str, str]:
    if not uri.startswith("gs://"):
        raise ValueError(f"Expected gs:// URI, got {uri}")
    remainder = uri[5:]
    bucket, _, prefix = remainder.partition("/")
    if not bucket:
        raise ValueError(f"GCS URI is missing bucket name: {uri}")
    return bucket, prefix.strip("/")


def _gcs_blob_name(prefix: str, path: str) -> str:
    clean_path = path.strip("/")
    return f"{prefix.strip('/')}/{clean_path}" if prefix.strip("/") else clean_path


def _gcs_uri(bucket_name: str, prefix: str, path: str) -> str:
    return f"gs://{bucket_name}/{_gcs_blob_name(prefix, path)}"


def _google_storage_client() -> Any:
    try:
        from google.cloud import storage
    except ImportError as exc:  # pragma: no cover - covered by deployment image dependency
        raise RuntimeError("GCS output requires installing the gcp extra: pip install '.[gcp]'") from exc
    return storage.Client()


def _upload_gcs_text(bucket: Any, blob_name: str, body: str, content_type: str) -> None:
    bucket.blob(blob_name).upload_from_string(body, content_type=content_type)


def _upload_gcs_bytes(bucket: Any, blob_name: str, body: bytes, content_type: str) -> None:
    bucket.blob(blob_name).upload_from_string(body, content_type=content_type)


def _download_gcs_bytes(uri: str) -> bytes:
    try:
        from google.api_core.exceptions import NotFound
    except ImportError as exc:  # pragma: no cover - exercised only without optional gcp extra
        raise RuntimeError("GCS mapping reads require installing the gcp extra: pip install '.[gcp]'") from exc

    bucket_name, blob_name = _split_gcs_uri(uri)
    try:
        return _google_storage_client().bucket(bucket_name).blob(blob_name).download_as_bytes()
    except NotFound as exc:
        raise FileNotFoundError(uri) from exc


def write_latest_processed_table(name: str, frame: pd.DataFrame, output_dir: str | Path = DEFAULT_OUTPUT_DIR) -> dict[str, Any]:
    if _is_gcs_uri(output_dir):
        bucket_name, prefix = _split_gcs_uri(str(output_dir))
        bucket = _google_storage_client().bucket(bucket_name)
        return _write_gcs_table(bucket, bucket_name, prefix, "processed/latest", name, frame)

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
    parser.add_argument("--discovery-only", action="store_true", help="Refresh discovery/review tables without pulling orderbooks or scoring signals")
    parser.add_argument("--no-general-search", action="store_true", help="Skip broad all-market search and only query configured venue sports tags/series")
    parser.add_argument("--all-active-markets", action="store_true", help="Discover every active/open market from both venues instead of filtering to configured keywords, tags, or series")
    parser.add_argument("--embedding-min-score", type=float, default=DEFAULT_EMBEDDING_MIN_SCORE, help="Minimum vector similarity score for embedding-generated suggestions")
    parser.add_argument("--embedding-top-k", type=int, default=DEFAULT_EMBEDDING_TOP_K, help="Maximum embedding suggestions to keep per Polymarket row")
    _add_semantic_args(parser)
    _add_ai_pair_review_args(parser)
    return parser


def build_sports_snapshot_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one cross-sports Polymarket/Kalshi arbitrage snapshot")
    _add_common_args(parser, default_output_dir=DEFAULT_SPORTS_OUTPUT_DIR, default_mapping_path=DEFAULT_SPORTS_MAPPING_PATH)
    parser.add_argument("--run-id")
    parser.add_argument("--no-discovery", action="store_true", help="Skip market discovery and only score approved mappings")
    parser.add_argument("--discovery-only", action="store_true", help="Refresh discovery/review tables without pulling orderbooks or scoring signals")
    parser.add_argument("--no-general-search", action="store_true", help="Skip broad all-market search and only query configured venue sports tags/series")
    parser.add_argument("--all-active-markets", action="store_true", help="Discover every active/open market from both venues instead of filtering to configured sports keywords, tags, or series")
    parser.add_argument("--embedding-min-score", type=float, default=DEFAULT_EMBEDDING_MIN_SCORE, help="Minimum vector similarity score for embedding-generated suggestions")
    parser.add_argument("--embedding-top-k", type=int, default=DEFAULT_EMBEDDING_TOP_K, help="Maximum embedding suggestions to keep per Polymarket row")
    _add_semantic_args(parser)
    _add_ai_pair_review_args(parser)
    return parser


def _add_semantic_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--semantic-embedding-provider",
        choices=sorted(SEMANTIC_EMBEDDING_PROVIDERS),
        default=DEFAULT_SEMANTIC_EMBEDDING_PROVIDER,
        help="Optional second-stage semantic embedding provider for review suggestions",
    )
    parser.add_argument("--semantic-embedding-dim", type=int, default=DEFAULT_SEMANTIC_EMBEDDING_DIM, help="Semantic embedding output dimension")
    parser.add_argument("--semantic-top-k", type=int, default=DEFAULT_SEMANTIC_TOP_K, help="Maximum semantic suggestions to keep per Polymarket row")
    parser.add_argument("--semantic-min-score", type=float, default=DEFAULT_SEMANTIC_MIN_SCORE, help="Minimum semantic combined score for suggested pairs")
    parser.add_argument("--semantic-batch-size", type=int, default=DEFAULT_VERTEX_GEMINI_BATCH_SIZE, help="Vertex Gemini texts per embedding request when semantic provider is vertex-gemini")
    parser.add_argument("--semantic-batch-sleep-seconds", type=float, default=DEFAULT_VERTEX_GEMINI_BATCH_SLEEP_SECONDS, help="Sleep between Vertex Gemini embedding batches to stay under token-per-minute quota")
    parser.add_argument("--semantic-retry-initial-seconds", type=float, default=DEFAULT_VERTEX_GEMINI_RETRY_INITIAL_SECONDS, help="Initial sleep after Vertex Gemini quota errors")
    parser.add_argument("--semantic-max-retries", type=int, default=DEFAULT_VERTEX_GEMINI_MAX_RETRIES, help="Maximum retry attempts per Vertex Gemini embedding batch")
    parser.add_argument("--semantic-cache-flush-batches", type=int, default=DEFAULT_SEMANTIC_CACHE_FLUSH_BATCHES, help="Flush market embedding cache every N semantic batches")
    parser.add_argument("--semantic-max-embedding-texts", type=int, default=DEFAULT_SEMANTIC_MAX_EMBEDDING_TEXTS, help="Maximum new uncached market texts to embed in this run; 0 means unlimited")


def _add_ai_pair_review_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--ai-pair-review-provider",
        choices=sorted(AI_PAIR_REVIEW_PROVIDERS),
        default=DEFAULT_AI_PAIR_REVIEW_PROVIDER,
        help="Optional LLM reviewer for generated pair suggestions; never auto-approves mappings",
    )
    parser.add_argument(
        "--ai-pair-review-model",
        default=DEFAULT_AI_PAIR_REVIEW_MODEL,
        help="Vertex Gemini model used when --ai-pair-review-provider=vertex-gemini",
    )
    parser.add_argument(
        "--ai-pair-review-limit",
        type=int,
        default=DEFAULT_AI_PAIR_REVIEW_LIMIT,
        help="Maximum suggestions to send to the AI reviewer in one discovery run; 0 means no cap",
    )
    parser.add_argument(
        "--ai-pair-review-min-score",
        type=float,
        default=DEFAULT_AI_PAIR_REVIEW_MIN_SCORE,
        help="Minimum generated match score required before a suggestion is sent to the AI reviewer",
    )


def build_watch_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Continuously poll FIFA cross-market arbitrage snapshots")
    _add_common_args(parser, default_market_limit=DEFAULT_WATCH_MARKET_LIMIT)
    parser.add_argument("--interval-seconds", type=float, default=DEFAULT_INTERVAL_SECONDS)
    parser.add_argument("--max-ticks", type=int)
    parser.add_argument("--no-discovery", action="store_true", help="Skip market discovery and only score approved mappings")
    return parser


def build_sports_watch_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Continuously poll cross-sports Polymarket/Kalshi arbitrage snapshots")
    _add_common_args(
        parser,
        default_output_dir=DEFAULT_SPORTS_OUTPUT_DIR,
        default_mapping_path=DEFAULT_SPORTS_MAPPING_PATH,
        default_market_limit=DEFAULT_WATCH_MARKET_LIMIT,
    )
    parser.add_argument("--interval-seconds", type=float, default=DEFAULT_INTERVAL_SECONDS)
    parser.add_argument("--max-ticks", type=int)
    parser.add_argument("--no-discovery", action="store_true", help="Skip market discovery and only score approved mappings")
    return parser


def snapshot_cli_main(argv: list[str] | None = None, client: httpx.Client | None = None) -> int:
    parser = build_snapshot_parser()
    args = parser.parse_args(argv)
    if args.discovery_only and args.no_discovery:
        parser.error("--discovery-only cannot be combined with --no-discovery")
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
        discovery_only=args.discovery_only,
        include_general_search=not args.no_general_search,
        all_active_markets=args.all_active_markets,
        embedding_min_score=args.embedding_min_score,
        embedding_top_k=args.embedding_top_k,
        semantic_embedding_provider=args.semantic_embedding_provider,
        semantic_embedding_dim=args.semantic_embedding_dim,
        semantic_top_k=args.semantic_top_k,
        semantic_min_score=args.semantic_min_score,
        semantic_batch_size=args.semantic_batch_size,
        semantic_batch_sleep_seconds=args.semantic_batch_sleep_seconds,
        semantic_retry_initial_seconds=args.semantic_retry_initial_seconds,
        semantic_max_retries=args.semantic_max_retries,
        semantic_cache_flush_batches=args.semantic_cache_flush_batches,
        semantic_max_embedding_texts=args.semantic_max_embedding_texts,
        ai_pair_review_provider=args.ai_pair_review_provider,
        ai_pair_review_model=args.ai_pair_review_model,
        ai_pair_review_limit=args.ai_pair_review_limit,
        ai_pair_review_min_score=args.ai_pair_review_min_score,
        client=client,
    )
    print(json.dumps(_snapshot_summary(result), indent=2, sort_keys=True, default=str))
    _print_alerts(result["tables"]["arbitrage_alerts"], scanner_label="FIFA")
    return 0


def sports_snapshot_cli_main(argv: list[str] | None = None, client: httpx.Client | None = None) -> int:
    parser = build_sports_snapshot_parser()
    args = parser.parse_args(argv)
    if args.discovery_only and args.no_discovery:
        parser.error("--discovery-only cannot be combined with --no-discovery")
    result = run_sports_snapshot(
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
        discovery_only=args.discovery_only,
        include_general_search=not args.no_general_search,
        all_active_markets=args.all_active_markets,
        embedding_min_score=args.embedding_min_score,
        embedding_top_k=args.embedding_top_k,
        semantic_embedding_provider=args.semantic_embedding_provider,
        semantic_embedding_dim=args.semantic_embedding_dim,
        semantic_top_k=args.semantic_top_k,
        semantic_min_score=args.semantic_min_score,
        semantic_batch_size=args.semantic_batch_size,
        semantic_batch_sleep_seconds=args.semantic_batch_sleep_seconds,
        semantic_retry_initial_seconds=args.semantic_retry_initial_seconds,
        semantic_max_retries=args.semantic_max_retries,
        semantic_cache_flush_batches=args.semantic_cache_flush_batches,
        semantic_max_embedding_texts=args.semantic_max_embedding_texts,
        ai_pair_review_provider=args.ai_pair_review_provider,
        ai_pair_review_model=args.ai_pair_review_model,
        ai_pair_review_limit=args.ai_pair_review_limit,
        ai_pair_review_min_score=args.ai_pair_review_min_score,
        client=client,
    )
    print(json.dumps(_snapshot_summary(result), indent=2, sort_keys=True, default=str))
    _print_alerts(result["tables"]["arbitrage_alerts"], scanner_label="sports")
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


def sports_watch_cli_main(argv: list[str] | None = None) -> int:
    args = build_sports_watch_parser().parse_args(argv)
    summaries = watch_sports_arbitrage(
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


def _add_common_args(
    parser: argparse.ArgumentParser,
    default_output_dir: str = DEFAULT_OUTPUT_DIR,
    default_mapping_path: str = DEFAULT_MAPPING_PATH,
    default_market_limit: int = DEFAULT_MARKET_LIMIT,
) -> None:
    parser.add_argument("--output-dir", default=default_output_dir)
    parser.add_argument("--mapping-path", default=default_mapping_path)
    parser.add_argument(
        "--market-limit",
        type=int,
        default=default_market_limit,
        help="Maximum active markets/events to scan per venue query; 0 exhausts pagination",
    )
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


def _suggested_mapping_row(
    pm: pd.Series,
    ks: pd.Series,
    score: float,
    *,
    embedding_score: float | str = "",
    lexical_score: float | str = "",
    combined_score: float | str = "",
    gemini_embedding_score: float | str = "",
    semantic_combined_score: float | str = "",
    semantic_provider: str = "",
    embedding_model: str = "",
    embedding_dim: str | int = "",
    embedding_text_hash: str = "",
    suggestion_method: str = "rules",
) -> dict[str, Any]:
    market_type = str(pm.get("market_type") or "")
    mapping_id = f"{_slugify(str(pm.get('ticker_or_slug') or pm.get('market_id')))}__{_slugify(str(ks.get('ticker_or_slug')))}"
    event_name = str(pm.get("event_title") or ks.get("event_title") or "")
    outcome_label = str(pm.get("outcome_label") or ks.get("outcome_label") or "")
    pm_outcomes = parse_json_array(pm.get("outcomes"))
    return {
        "run_id": pm.get("run_id", ""),
        "suggested_mapping_id": mapping_id,
        "match_score": round(score, 2),
        "embedding_score": round(float(embedding_score), 2) if embedding_score != "" else "",
        "lexical_score": round(float(lexical_score), 2) if lexical_score != "" else "",
        "combined_score": round(float(combined_score), 2) if combined_score != "" else round(score, 2),
        "gemini_embedding_score": round(float(gemini_embedding_score), 2) if gemini_embedding_score != "" else "",
        "semantic_combined_score": round(float(semantic_combined_score), 2) if semantic_combined_score != "" else "",
        "semantic_provider": semantic_provider,
        "embedding_model": embedding_model,
        "embedding_dim": embedding_dim,
        "embedding_text_hash": embedding_text_hash,
        "suggestion_method": suggestion_method,
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
        "polymarket_yes_outcome": str(pm_outcomes[0]) if len(pm_outcomes) > 0 else "Yes",
        "polymarket_no_outcome": str(pm_outcomes[1]) if len(pm_outcomes) > 1 else "No",
        "polymarket_outcomes": pm.get("outcomes", ""),
        "polymarket_settlement_summary": pm.get("settlement_summary", ""),
        "kalshi_ticker": ks.get("market_id", ""),
        "kalshi_title": ks.get("title", ""),
        "kalshi_outcomes": ks.get("outcomes", ""),
        "kalshi_settlement_summary": ks.get("settlement_summary", ""),
        "draw_handling": _default_draw_handling(market_type, pm, ks),
        "extra_time_handling": _default_extra_time_handling(market_type, pm, ks),
        "penalties_handling": _default_penalties_handling(market_type, pm, ks),
        "settlement_notes": "REVIEW REQUIRED: confirm both markets resolve identically before copying to config/fifa_market_mappings.csv.",
    }


def _suggestion_review_notes(pm: pd.Series, ks: pd.Series) -> str:
    notes = []
    if pm.get("event_match_key") and ks.get("event_match_key") and pm.get("event_match_key") != ks.get("event_match_key"):
        notes.append(f"event mismatch: polymarket={pm.get('event_match_key')} kalshi={ks.get('event_match_key')}")
    pm_outcome_key = _candidate_outcome_key(pm)
    ks_outcome_key = _candidate_outcome_key(ks)
    if pm_outcome_key and ks_outcome_key and pm_outcome_key != ks_outcome_key and not _outcomes_compatible(pm, ks):
        notes.append(f"outcome mismatch: polymarket={pm.get('outcome_label')} kalshi={ks.get('outcome_label')}")
    if pm.get("event_year") != ks.get("event_year"):
        notes.append(f"year mismatch: polymarket={pm.get('event_year') or 'unknown'} kalshi={ks.get('event_year') or 'unknown'}")
    if pm.get("market_type") != ks.get("market_type"):
        notes.append(f"type mismatch: polymarket={pm.get('market_type')} kalshi={ks.get('market_type')}")
    if pm.get("market_type") == "host_country":
        notes.append("verify host country/group and multi-host handling")
    if pm.get("market_type") == "match_winner":
        notes.append("verify regular-time result, draw/Tie handling, extra time, penalties, and cancellation rules")
    if pm.get("market_type") == "championship_winner":
        notes.append("verify tournament/event scope, field, official winner definition, and dead-heat/void rules")
    if pm.get("market_type") == "pole_position_winner":
        notes.append("verify qualifying session, fastest valid lap definition, and driver naming")
    return "; ".join(notes) or "review settlement summaries before approval"


def _default_draw_handling(market_type: str, *rows: pd.Series | dict[str, Any]) -> str:
    if market_type == "host_country":
        return "not applicable"
    if market_type in {"championship_winner", "pole_position_winner"}:
        return "not applicable; single-event competitor winner market"
    if _is_tennis_market(*rows):
        return "not applicable; tennis markets resolve 50-50 if the match is not played or no winner is determined under venue rules"
    if market_type == "match_winner":
        sport = next((_market_sport_context(row) for row in rows if _market_sport_context(row)), "")
        if sport == "mlb":
            return "no standard draw; verify venue cancellation/tie handling for rare suspended or canceled games"
        if sport in {"nba", "wnba", "nhl", "nfl"}:
            return "not applicable; league game winner markets include overtime until a winner is determined"
        return "draw/Tie is a separate outcome; team-winner markets resolve No on draw"
    return "REVIEW REQUIRED"


def _default_extra_time_handling(market_type: str, *rows: pd.Series | dict[str, Any]) -> str:
    if market_type == "host_country":
        return "not applicable"
    if market_type in {"championship_winner", "pole_position_winner"}:
        return "not applicable; event winner rather than match-clock market"
    if _is_tennis_market(*rows):
        return "not applicable; tennis match winner/advancement after play begins"
    if market_type == "match_winner":
        sport = next((_market_sport_context(row) for row in rows if _market_sport_context(row)), "")
        if sport == "mlb":
            return "extra innings included if needed for the official game winner"
        if sport in {"nba", "wnba", "nhl", "nfl"}:
            return "overtime included if needed for the official game winner"
        return "regular time plus stoppage time only; extra time excluded"
    return "REVIEW REQUIRED"


def _default_penalties_handling(market_type: str, *rows: pd.Series | dict[str, Any]) -> str:
    if market_type == "host_country":
        return "not applicable"
    if market_type in {"championship_winner", "pole_position_winner"}:
        return "not applicable"
    if _is_tennis_market(*rows):
        return "not applicable"
    if market_type == "match_winner":
        sport = next((_market_sport_context(row) for row in rows if _market_sport_context(row)), "")
        if sport in {"mlb", "nba", "wnba", "nhl", "nfl"}:
            return "not applicable"
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
    suggestions = result["tables"].get("suggested_mappings", pd.DataFrame())
    embeddings = result["tables"].get("market_embeddings", pd.DataFrame())
    ai_review_count = 0
    ai_equivalent_count = 0
    if not suggestions.empty and "ai_review_status" in suggestions.columns:
        statuses = suggestions["ai_review_status"].fillna("").astype(str).str.strip()
        ai_review_count = int((statuses != "").sum())
        ai_equivalent_count = int((statuses == "equivalent").sum())
    return {
        "run_id": run.get("run_id"),
        "status": run.get("status"),
        "candidate_count": int(run.get("candidate_count") or 0),
        "suggested_mapping_count": len(suggestions),
        "ai_review_count": ai_review_count,
        "ai_equivalent_count": ai_equivalent_count,
        "embedding_count": len(embeddings),
        "approved_mapping_count": int(run.get("approved_mapping_count") or 0),
        "orderbook_count": int(run.get("orderbook_count") or 0),
        "alert_count": int(run.get("alert_count") or 0),
    }


def _print_alerts(alerts: pd.DataFrame, scanner_label: str = "FIFA") -> None:
    if alerts.empty or "is_alert" not in alerts:
        print(f"No {scanner_label} cross-market alerts.")
        return
    actual = alerts[alerts["is_alert"] == True]  # noqa: E712
    if actual.empty:
        print(f"No {scanner_label} cross-market alerts.")
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
            "settlement_summary": _row_get(row, "settlement_summary"),
        }
    )


def _primary_market_text(row: pd.Series | dict[str, Any]) -> str:
    return _normalize_name(
        " ".join(
            str(value or "")
            for value in (
                _row_get(row, "title"),
                _row_get(row, "event_title"),
                _row_get(row, "outcome_label"),
                _row_get(row, "ticker_or_slug"),
            )
        )
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
    return _suggestion_pair_gate_reason(left, right, strict=False) == ""


def _suggestion_pair_gate_reason(
    left: pd.Series | dict[str, Any],
    right: pd.Series | dict[str, Any],
    *,
    strict: bool = False,
) -> str:
    market_type = str(_row_get(left, "market_type") or "")
    right_market_type = str(_row_get(right, "market_type") or "")
    if market_type != right_market_type:
        return "excluded_wrong_market_type"
    if _looks_like_bundle_market(left) or _looks_like_bundle_market(right):
        return "excluded_bundle"
    left_sport = _cached_sport_context(left)
    right_sport = _cached_sport_context(right)
    if left_sport and right_sport and left_sport != right_sport:
        return "excluded_wrong_sport"
    if strict and (not left_sport or not right_sport):
        return "excluded_missing_sport"
    left_date = str(_row_get(left, "event_date") or "")
    right_date = str(_row_get(right, "event_date") or "")
    if market_type in {"match_winner", "total", "spread", "set_winner"} and left_date and right_date and left_date != right_date:
        return "excluded_wrong_date"
    if market_type == "match_winner":
        return _match_winner_pair_gate_reason(left, right, strict=strict)
    if market_type in {"championship_winner", "pole_position_winner"}:
        return _event_winner_pair_gate_reason(left, right)
    if market_type == "total":
        return _total_pair_gate_reason(left, right, strict=strict)
    if market_type == "host_country":
        return _host_country_pair_gate_reason(left, right)
    return "excluded_unsupported_semantic_market_type"


def _host_country_pair_gate_reason(left: pd.Series | dict[str, Any], right: pd.Series | dict[str, Any]) -> str:
    left_outcome = _cached_candidate_outcome_key(left)
    right_outcome = _cached_candidate_outcome_key(right)
    if not left_outcome or not right_outcome:
        return "excluded_missing_outcome"
    if left_outcome != right_outcome and not _cached_outcomes_compatible(left, right):
        return "excluded_wrong_outcome"
    left_year = str(_row_get(left, "event_year") or "")
    right_year = str(_row_get(right, "event_year") or "")
    if left_year and right_year and left_year != right_year:
        return "excluded_wrong_year"
    return ""


def _match_winner_pair_gate_reason(
    left: pd.Series | dict[str, Any],
    right: pd.Series | dict[str, Any],
    *,
    strict: bool,
) -> str:
    left_timeframe = _market_timeframe_key(left)
    right_timeframe = _market_timeframe_key(right)
    if (left_timeframe or right_timeframe) and left_timeframe != right_timeframe:
        return "excluded_wrong_timeframe"
    left_local_date = _local_schedule_date_key(left)
    right_local_date = _local_schedule_date_key(right)
    if _should_gate_match_local_date(left, right) and left_local_date and right_local_date and left_local_date != right_local_date:
        return "excluded_wrong_date"
    if _is_exact_score_market(left) or _is_exact_score_market(right) or _is_player_prop_market(left) or _is_player_prop_market(right):
        return "excluded_wrong_market_type"

    left_outcome = _cached_candidate_outcome_key(left)
    right_outcome = _cached_candidate_outcome_key(right)
    if not left_outcome or not right_outcome:
        return "excluded_missing_outcome"
    if not _cached_outcomes_compatible(left, right):
        return "excluded_wrong_outcome"
    if _is_tennis_market(left, right):
        return "" if _tennis_match_rows_compatible(left, right) else "excluded_wrong_event"

    left_event_key = str(_row_get(left, "event_match_key") or "")
    right_event_key = str(_row_get(right, "event_match_key") or "")
    if left_event_key and right_event_key:
        if left_event_key == right_event_key:
            return ""
        if not _event_titles_compatible(left, right, min_score=88.0):
            return "excluded_wrong_event"
    else:
        left_teams = _event_team_key(left)
        right_teams = _event_team_key(right)
        if left_teams and right_teams:
            if left_teams != right_teams:
                return "excluded_wrong_event"
        elif strict:
            return "excluded_missing_participants"
        elif not _event_start_times_compatible(left, right):
            return "excluded_wrong_event"

    if strict and not _event_titles_compatible(left, right, min_score=75.0):
        return "excluded_wrong_event"
    return ""


def _event_winner_pair_gate_reason(left: pd.Series | dict[str, Any], right: pd.Series | dict[str, Any]) -> str:
    left_special_scope = _champion_group_scope_key(left) or _unbeaten_champion_scope_key(left)
    right_special_scope = _champion_group_scope_key(right) or _unbeaten_champion_scope_key(right)
    if (left_special_scope or right_special_scope) and left_special_scope != right_special_scope:
        return "excluded_wrong_scope"

    left_outcome = _cached_candidate_outcome_key(left)
    right_outcome = _cached_candidate_outcome_key(right)
    if not left_outcome or not right_outcome:
        return "excluded_missing_outcome"
    if not _cached_outcomes_compatible(left, right):
        return "excluded_wrong_outcome"
    left_scope = _cached_scope_key(left)
    right_scope = _cached_scope_key(right)
    if (left_scope or right_scope) and left_scope != right_scope:
        return "excluded_wrong_scope"
    left_year = str(_row_get(left, "event_year") or "")
    right_year = str(_row_get(right, "event_year") or "")
    if left_year and right_year and left_year != right_year:
        return "excluded_wrong_year"
    return ""


def _total_pair_gate_reason(
    left: pd.Series | dict[str, Any],
    right: pd.Series | dict[str, Any],
    *,
    strict: bool,
) -> str:
    left_spec = _total_market_spec(left)
    right_spec = _total_market_spec(right)
    if strict and (not left_spec["side"] or not right_spec["side"]):
        return "excluded_missing_total_side"
    if left_spec["side"] and right_spec["side"] and left_spec["side"] != right_spec["side"]:
        return "excluded_wrong_total_side"
    if not left_spec["line"] or not right_spec["line"] or left_spec["line"] != right_spec["line"]:
        return "excluded_wrong_total_line"
    if not left_spec["metric"] or not right_spec["metric"] or left_spec["metric"] != right_spec["metric"]:
        return "excluded_wrong_metric"
    left_event_key = str(_row_get(left, "event_match_key") or "")
    right_event_key = str(_row_get(right, "event_match_key") or "")
    if left_event_key and right_event_key and left_event_key != right_event_key:
        return "excluded_wrong_event"
    left_teams = _event_team_key(left)
    right_teams = _event_team_key(right)
    if left_teams and right_teams and left_teams != right_teams:
        return "excluded_wrong_event"
    if strict and not left_teams and not right_teams:
        return "excluded_missing_participants"
    if not _event_start_times_compatible(left, right):
        return "excluded_wrong_event"
    return ""


def _suggested_proposition(event_name: str, outcome_label: str, market_type: str) -> str:
    if market_type == "match_winner" and outcome_label:
        if _normalize_name(outcome_label) == "tie":
            return f"{event_name} to end in a draw in regular time"
        return f"{outcome_label} to win in regular time"
    if market_type == "pole_position_winner" and outcome_label:
        return f"{outcome_label} to take pole position"
    return outcome_label or event_name


def _total_market_spec(row: pd.Series | dict[str, Any]) -> dict[str, str]:
    raw_text = " ".join(
        str(value or "")
        for value in (
            _row_get(row, "outcome_label"),
            _row_get(row, "title"),
            _row_get(row, "subject"),
            _row_get(row, "event_title"),
            _row_get(row, "settlement_summary"),
        )
    )
    raw_lower = raw_text.lower()
    text = _normalize_name(
        " ".join(
            str(value or "")
            for value in (
                _row_get(row, "outcome_label"),
                _row_get(row, "title"),
                _row_get(row, "subject"),
                _row_get(row, "event_title"),
                _row_get(row, "settlement_summary"),
            )
        )
    )
    side_match = re.search(r"\b(over|under)\b", raw_lower)
    side = side_match.group(1) if side_match else ""
    if not side:
        outcomes = parse_json_array(_row_get(row, "outcomes"))
        first_outcome = _normalize_name(outcomes[0]) if outcomes else ""
        if first_outcome in {"over", "under"}:
            side = first_outcome
    line_match = re.search(r"\b(?:over|under|o/u|o\s*/\s*u|total)\s+(\d+(?:\.\d+|pt\d+)?)\b", raw_lower)
    if not line_match:
        line_match = re.search(r"\b(\d+\.\d+)\b", raw_lower)
    return {
        "side": side,
        "line": line_match.group(1).replace("pt", ".") if line_match else "",
        "metric": _total_metric_key(row, text),
    }


def _total_metric_key(row: pd.Series | dict[str, Any], normalized_text: str | None = None) -> str:
    text = normalized_text or _normalize_name(_candidate_text(row))
    if any(term in f" {text} " for term in (" set ", " sets ", " total sets ", " match sets ")) or _is_tennis_market(row):
        return "sets"
    if any(term in f" {text} " for term in (" kill ", " kills ", " total kills ")):
        return "kills"
    if any(term in f" {text} " for term in (" goal ", " goals ", " goals scored ", " total goals ")):
        return "goals"
    if any(term in f" {text} " for term in (" run ", " runs ", " total runs ")):
        return "runs"
    if any(term in f" {text} " for term in (" point ", " points ", " total points ")):
        return "points"
    if any(term in f" {text} " for term in (" map ", " maps ", " total maps ")):
        return "maps"
    if any(term in f" {text} " for term in (" round ", " rounds ", " total rounds ")):
        return "rounds"
    sport = _cached_sport_context(row)
    if sport == "tennis":
        return "sets"
    if sport in {"soccer", "nhl"}:
        return "goals"
    if sport == "mlb":
        return "runs"
    if sport in {"nba", "wnba", "nfl"}:
        return "points"
    return ""


def _looks_like_bundle_market(row: pd.Series | dict[str, Any]) -> bool:
    if _row_has_key(row, "_is_bundle_market"):
        return _cached_bool_value(row, "_is_bundle_market")
    raw_text = " ".join(
        str(value or "")
        for value in (
            _row_get(row, "title"),
            _row_get(row, "subtitle"),
            _row_get(row, "subject"),
            _row_get(row, "outcomes"),
        )
    ).lower()
    bundle_legs = re.findall(r"(?:^|,)\s*(?:yes|no)\s+[a-z0-9]", raw_text)
    return len(bundle_legs) >= 2


def _event_titles_compatible(left: pd.Series | dict[str, Any], right: pd.Series | dict[str, Any], min_score: float = 80.0) -> bool:
    left_title = str(_row_get(left, "event_title") or "")
    right_title = str(_row_get(right, "event_title") or "")
    if not left_title or not right_title:
        return False
    left_date = str(_row_get(left, "event_date") or "")
    right_date = str(_row_get(right, "event_date") or "")
    if left_date and right_date and left_date != right_date:
        return False
    left_teams = _event_team_key(left)
    right_teams = _event_team_key(right)
    if left_teams and left_teams == right_teams:
        return True
    return fuzz.token_set_ratio(left_title, right_title) >= min_score


def _event_start_times_compatible(left: pd.Series | dict[str, Any], right: pd.Series | dict[str, Any], max_seconds: int = 3 * 60 * 60) -> bool:
    left_time = _event_start_datetime(left)
    right_time = _event_start_datetime(right)
    if left_time is None or right_time is None:
        return True
    return abs((left_time - right_time).total_seconds()) <= max_seconds


def _event_start_datetime(row: pd.Series | dict[str, Any]) -> datetime | None:
    raw = _raw_payload(row)
    for value in (
        raw.get("occurrence_datetime"),
        raw.get("_event_context_start_time"),
        raw.get("gameStartTime"),
        raw.get("startTime"),
        raw.get("eventDate"),
        raw.get("startDate"),
    ):
        if _is_blank(value):
            continue
        parsed = parse_timestamp(value)
        if parsed:
            try:
                return datetime.fromisoformat(parsed.replace("Z", "+00:00"))
            except ValueError:
                return None
    return None


def _prefilter_tennis_candidates(pm: pd.Series, candidates: pd.DataFrame) -> pd.DataFrame:
    if candidates.empty:
        return candidates
    match_key = str(pm.get("_tennis_match_key") or _tennis_match_key(pm))
    outcome_key = str(pm.get("_tennis_outcome_key") or _tennis_outcome_key(pm))
    if match_key and "_tennis_match_key" in candidates.columns:
        candidates = candidates[candidates["_tennis_match_key"] == match_key]
    if outcome_key and "_tennis_outcome_key" in candidates.columns:
        candidates = candidates[candidates["_tennis_outcome_key"] == outcome_key]
    return candidates


def _tennis_match_key(row: pd.Series | dict[str, Any]) -> str:
    if not _is_tennis_market(row):
        return ""
    event_date = str(_row_get(row, "event_date") or "")
    teams = _teams_from_match_title(_row_get(row, "event_title"))
    if len(teams) != 2:
        return ""
    side_keys = sorted(_tennis_side_key(team) for team in teams)
    if not all(side_keys):
        return ""
    return "|".join([event_date, *side_keys])


def _tennis_outcome_key(row: pd.Series | dict[str, Any]) -> str:
    if str(_row_get(row, "market_type") or "") != "match_winner" or not _is_tennis_market(row):
        return ""
    return _tennis_side_key(_row_get(row, "outcome_label"))


def _tennis_side_key(value: Any) -> str:
    normalized = _normalize_name(value)
    if not normalized:
        return ""
    sides = [
        _last_name_or_team_part(side.strip())
        for side in re.split(r"\s*/\s*", str(value or ""))
        if side.strip()
    ]
    if sides:
        return "/".join(_normalize_name(side) for side in sides if _normalize_name(side))
    return _last_name_or_team_part(normalized)


def _tennis_match_rows_compatible(left: pd.Series | dict[str, Any], right: pd.Series | dict[str, Any]) -> bool:
    left_date = str(_row_get(left, "event_date") or "")
    right_date = str(_row_get(right, "event_date") or "")
    if left_date and right_date and left_date != right_date:
        return False

    left_title = str(_row_get(left, "event_title") or "")
    right_title = str(_row_get(right, "event_title") or "")
    if not _tennis_match_titles_compatible(left_title, right_title):
        return False

    left_outcome = str(_row_get(left, "outcome_label") or "")
    right_outcome = str(_row_get(right, "outcome_label") or "")
    return _person_or_team_name_compatible(left_outcome, right_outcome)


def _tennis_match_titles_compatible(left_title: Any, right_title: Any) -> bool:
    left_teams = _teams_from_match_title(left_title)
    right_teams = _teams_from_match_title(right_title)
    if len(left_teams) != 2 or len(right_teams) != 2:
        return False
    same_order = _person_or_team_name_compatible(left_teams[0], right_teams[0]) and _person_or_team_name_compatible(left_teams[1], right_teams[1])
    reverse_order = _person_or_team_name_compatible(left_teams[0], right_teams[1]) and _person_or_team_name_compatible(left_teams[1], right_teams[0])
    return same_order or reverse_order


def _person_or_team_name_compatible(left: Any, right: Any) -> bool:
    left_norm = _normalize_name(left)
    right_norm = _normalize_name(right)
    if not left_norm or not right_norm:
        return False
    if left_norm == right_norm:
        return True
    if left_norm in right_norm.split() or right_norm in left_norm.split():
        return True
    left_compact = left_norm.replace(" ", "")
    right_compact = right_norm.replace(" ", "")
    if min(len(left_compact), len(right_compact)) >= 3 and (
        left_compact.startswith(right_compact) or right_compact.startswith(left_compact)
    ):
        return True
    left_last = _last_name_or_team_part(left_norm)
    right_last = _last_name_or_team_part(right_norm)
    return bool(left_last and right_last and left_last == right_last)


def _competitor_name_compatible(left: Any, right: Any) -> bool:
    left_norm = _normalize_name(left)
    right_norm = _normalize_name(right)
    if not left_norm or not right_norm:
        return False
    if left_norm == right_norm:
        return True
    left_tokens = left_norm.split()
    right_tokens = right_norm.split()
    if len(left_tokens) == 1 and left_tokens[0] == right_tokens[-1]:
        return True
    if len(right_tokens) == 1 and right_tokens[0] == left_tokens[-1]:
        return True
    return fuzz.token_set_ratio(left_norm, right_norm) >= 92


def _last_name_or_team_part(value: str) -> str:
    parts = value.split()
    if not parts:
        return ""
    if "/" in value:
        return value
    return parts[-1]


def _is_total_market_text(text: str) -> bool:
    return bool(
        re.search(r"\b(?:o/u|over/under|total)\b", text)
        or re.search(r"\b(?:over|under)\s+\d+(?:\.\d+|pt\d+)?\b", text)
    )


def _is_non_winner_prop_market_text(text: str) -> bool:
    normalized = _normalize_name(text)
    if not normalized:
        return False
    if re.search(r"\b(?:announcers?|commentators?|casters?)\s+say\b", normalized):
        return True
    if re.search(r"\bwill\s+.+\s+say\s+", normalized):
        return True
    if any(
        term in f" {normalized} "
        for term in (
            " both teams slay ",
            " both teams destroy ",
            " quadra kill ",
            " penta kill ",
            " baron nashor ",
            " slay a dragon ",
            " destroy inhibitors ",
            " beat roshan ",
            " ends in daytime ",
            " ends in nighttime ",
            " end in daytime ",
            " end in nighttime ",
            " completed match ",
            " fight to go the distance ",
            " fight be won by ko or tko ",
            " fight be won by submission ",
            " won by ko or tko ",
            " won by submission ",
            " win by ko or tko ",
            " win by submission ",
            " week 1 starting qb ",
            " week one starting qb ",
            " starting qb ",
            " starting quarterback ",
        )
    ):
        return True
    return False


def _is_championship_market_text(text: str) -> bool:
    return any(
        term in text
        for term in (
            " champion",
            " championship",
            " finals",
            " world series",
            " stanley cup",
            " big game",
        )
    )


def _is_transfer_market_text(text: str) -> bool:
    normalized = _normalize_name(text)
    return bool(
        re.search(r"\bjoin(?:s|ed|ing)?\b", normalized)
        or re.search(r"\btransfer(?:s|red|ring)?\b", normalized)
        or re.search(r"\bsign(?:s|ed|ing)?\s+for\b", normalized)
        or re.search(r"\bsigned\s+by\b", normalized)
    )


def _is_tournament_winner_market_text(text: str) -> bool:
    normalized = _normalize_name(text)
    winner_patterns = (
        r"\b(?:fifa\s+)?world cup\s+winner\b",
        r"\bworld soccer cup\s+winner\b",
        r"\b(?:men s|women s)\s+(?:wimbledon|us open|australian open|french open)\s+winner\b",
        r"\bbe\s+the\s+(?:20\d{2}\s+)?(?:(?:men s|women s)\s+)?(?:wimbledon|us open|australian open|french open)\s+winner\b",
        r"\b(?:win|wins)\s+the\s+20\d{2}(?:[-/]\d{2})?\s+(?:afc|nfc|al|nl)\s+(?:east|west|north|south|central)\b",
        r"\b(?:win|wins)\s+the\s+(?:20\d{2}\s+)?(?:(?:men s|women s)\s+)?(?:fifa\s+)?world cup\b",
        r"\b(?:win|wins)\s+the\s+(?:20\d{2}\s+)?(?:fifa\s+)?(?:(?:men s|women s)\s+)?world cup\b",
        r"\b(?:win|wins)\s+the\s+(?:20\d{2}\s+)?world soccer cup\b",
        r"\b(?:win|wins)\s+(?:the\s+)?msi\s+20\d{2}\b",
        r"\b(?:win|wins)\s+(?:the\s+)?(?:north america ace stage\s+\d+|emea challengers stage|challengers\s+20\d{2}:?\s+[^?]{0,80})\b",
        r"\b(?:msi|challengers)\s+20\d{2}\b[^?]{0,80}\bwinner\b",
        r"\b(?:championship|tournament|classic|open|grand prix|world series|stanley cup|wimbledon)\s+winner\b",
        r"\b(?:win|wins)\s+the\s+(?:20\d{2}\s+)?(?:men s|women s)?\s*[^?]{0,80}\b(?:championship|tournament|classic|open|grand prix|world series|stanley cup)\b",
    )
    return any(re.search(pattern, normalized) for pattern in winner_patterns)


def _market_timeframe_key(row: pd.Series | dict[str, Any]) -> str:
    if _row_has_key(row, "_timeframe_key"):
        return _cached_string_value(row, "_timeframe_key")
    cached = _normalize_name(_row_get(row, "event_timeframe"))
    if cached in {"first 5 innings", "first half"}:
        return cached
    text = _candidate_text(row)
    if re.search(r"\b(?:first|1st)\s*5\b", text) or "first five" in text or re.search(r"\bafter\s+5\s+innings\b", text):
        return "first 5 innings"
    return ""


def _should_gate_match_local_date(left: pd.Series | dict[str, Any], right: pd.Series | dict[str, Any]) -> bool:
    sport = _cached_sport_context(left) or _cached_sport_context(right)
    return sport == "mlb"


def _local_schedule_date_key(row: pd.Series | dict[str, Any]) -> str:
    if _row_has_key(row, "_local_schedule_date_key"):
        return _cached_string_value(row, "_local_schedule_date_key")
    raw = _raw_payload(row)
    text = " ".join(
        str(value or "")
        for value in (
            _row_get(row, "ticker_or_slug"),
            _row_get(row, "market_id"),
            raw.get("ticker"),
            raw.get("event_ticker"),
            raw.get("_event_context_ticker"),
        )
    )
    slug_date = re.search(r"\b(20\d{2})-(\d{2})-(\d{2})\b", text)
    if slug_date:
        return f"{slug_date.group(1)}-{slug_date.group(2)}-{slug_date.group(3)}"
    return _date_from_ticker_text(text)


def _champion_group_scope_key(row: pd.Series | dict[str, Any]) -> str:
    if _row_has_key(row, "_champion_group_scope_key"):
        return _cached_string_value(row, "_champion_group_scope_key")
    raw = _raw_payload(row)
    text = _normalize_name(
        " ".join(
            str(value or "")
            for value in (
                _row_get(row, "event_title"),
                _row_get(row, "title"),
                _row_get(row, "subject"),
                _row_get(row, "ticker_or_slug"),
                _row_get(row, "settlement_summary"),
                raw.get("_event_context_title"),
                raw.get("title"),
            )
        )
    )
    group_match = re.search(r"\bgroup\s+([a-l])\b", text)
    if not group_match:
        return ""
    if "champion" not in text or ("nation" not in text and "country" not in text and "team" not in text):
        return ""
    if not any(term in text for term in ("world cup", "world soccer cup", "fifa")):
        return ""
    return f"soccer_world_cup_group_{group_match.group(1)}"


def _unbeaten_champion_scope_key(row: pd.Series | dict[str, Any]) -> str:
    if _row_has_key(row, "_unbeaten_champion_scope_key"):
        return _cached_string_value(row, "_unbeaten_champion_scope_key")
    text = _normalize_name(
        " ".join(
            str(value or "")
            for value in (
                _row_get(row, "event_title"),
                _row_get(row, "title"),
                _row_get(row, "subject"),
                _row_get(row, "ticker_or_slug"),
                _row_get(row, "settlement_summary"),
            )
        )
    )
    if "unbeaten champion" not in text:
        return ""
    if not any(term in text for term in ("world cup", "world soccer cup", "fifa")):
        return ""
    return "soccer_world_cup_unbeaten_champion"


def _is_exact_score_market(row: pd.Series | dict[str, Any]) -> bool:
    if _row_has_key(row, "_is_exact_score_market"):
        return _cached_bool_value(row, "_is_exact_score_market")
    text = _normalize_name(
        " ".join(
            str(value or "")
            for value in (
                _row_get(row, "event_title"),
                _row_get(row, "title"),
                _row_get(row, "subject"),
                _row_get(row, "ticker_or_slug"),
                _row_get(row, "settlement_summary"),
            )
        )
    )
    return "exact score" in text or bool(re.search(r"\bcorrect score\b", text))


def _is_player_prop_market(row: pd.Series | dict[str, Any]) -> bool:
    if _row_has_key(row, "_is_player_prop_market"):
        return _cached_bool_value(row, "_is_player_prop_market")
    text = _normalize_name(
        " ".join(
            str(value or "")
            for value in (
                _row_get(row, "event_title"),
                _row_get(row, "title"),
                _row_get(row, "subject"),
                _row_get(row, "ticker_or_slug"),
                _row_get(row, "settlement_summary"),
            )
        )
    )
    if "player props" in text or "player prop" in text:
        return True
    if re.search(
        r"\b\d+\s*(?:(?:plus|\+)\s+)?(?:goals?|shots?|shots?\s+on\s+target|saves?|passes?|tackles?|cards?|fouls?|rebounds?|assists?|points?)\b",
        text,
    ):
        return True
    return False


def _event_scope_key(row: pd.Series | dict[str, Any]) -> str:
    championship = _championship_scope_key(row)
    if championship:
        return championship
    sport = _market_sport_context(row)
    raw = _raw_payload(row)
    event_title = _normalize_name(
        " ".join(
            str(value or "")
            for value in (
                _row_get(row, "event_title"),
                raw.get("_event_context_title"),
                raw.get("title"),
                raw.get("_event_context_payload", {}).get("title") if isinstance(raw.get("_event_context_payload"), dict) else "",
            )
        )
    )
    text = _normalize_name(
        " ".join(
            str(value or "")
            for value in (
                event_title,
                _row_get(row, "title"),
                _row_get(row, "ticker_or_slug"),
                raw.get("event_ticker"),
                raw.get("series_ticker"),
                raw.get("ticker"),
            )
        )
    )
    if sport == "golf":
        source = _winner_event_title_scope_source(event_title or text)
        match = re.search(r"\b([a-z0-9 ]+? championship)\b", source)
        if match:
            return f"golf_{_slugify(match.group(1))}"
    if sport == "f1":
        match = re.search(r"\b([a-z0-9 ]+? grand prix)\b", event_title or text)
        if match and ("pole" in text or "qualifying" in text):
            session = "pole"
            if "sprint qualifying" in text:
                session = "sprint_qualifying_pole"
            return f"f1_{_slugify(match.group(1))}_{session}"
    return ""


def _championship_scope_key(row: pd.Series | dict[str, Any]) -> str:
    sport = _market_sport_context(row)
    raw = _raw_payload(row)
    text = " ".join(
        [
            _candidate_text(row),
            _search_text(
                {
                    "series_ticker": raw.get("series_ticker"),
                    "event_ticker": raw.get("event_ticker"),
                    "ticker": raw.get("ticker"),
                }
            ),
        ]
    )
    normalized = _normalize_name(text)
    if not sport:
        if "mlb" in normalized or "baseball" in normalized or "world series" in normalized:
            sport = "mlb"
        elif "nba" in normalized or "basketball" in normalized:
            sport = "nba"
        elif "nfl" in normalized or "football" in normalized:
            sport = "nfl"
        elif "nhl" in normalized or "hockey" in normalized:
            sport = "nhl"

    if sport == "mlb":
        if "american league championship" in normalized or " alcs " in f" {normalized} ":
            return "mlb_alcs"
        if "national league championship" in normalized or " nlcs " in f" {normalized} ":
            return "mlb_nlcs"
        if any(term in normalized for term in ("world series", "pro baseball championship", "kxmlb")):
            return "mlb_world_series"
    if sport == "nba":
        if "conference finals" in normalized or "conference championship" in normalized:
            return "nba_conference_finals"
        if any(term in normalized for term in ("nba finals", "pro basketball finals", "kxnba")):
            return "nba_finals"
    if sport == "wnba":
        if any(term in normalized for term in ("wnba finals", "women s pro basketball championship", "kxwnba")):
            return "wnba_finals"
    if sport == "nhl":
        if any(term in normalized for term in ("stanley cup", "kxnhl")):
            return "nhl_stanley_cup"
    if sport == "nfl":
        if "afc championship" in normalized:
            return "nfl_afc_championship"
        if "nfc championship" in normalized:
            return "nfl_nfc_championship"
        if any(term in normalized for term in ("big game", "super bowl", "pro football championship", "kxnfl")):
            return "nfl_big_game"
    if sport == "soccer":
        if any(term in normalized for term in ("fifa world cup", "world soccer cup", " world cup ", "kxwc")):
            return "soccer_world_cup"
    if sport == "golf":
        match = re.search(r"\b([a-z0-9 ]+? (?:championship|classic|open|invitational|masters))\b", _winner_event_title_scope_source(normalized))
        if match:
            return f"golf_{_slugify(match.group(1))}"
    return ""


def _winner_event_title_scope_source(value: str) -> str:
    text = _normalize_name(value)
    if " pga tour " in f" {text} ":
        text = text.replace("pga tour", " ")
    text = re.sub(r"\b(?:winner|market|event title|title)\b", " ", text)
    text = " ".join(text.split())
    if " championship" in text:
        parts = text.split(" championship", 1)
        prefix_words = parts[0].split()
        if len(prefix_words) > 3:
            prefix_words = prefix_words[-3:]
        return " ".join([*prefix_words, "championship"])
    return text


def _championship_outcome_from_title(value: Any) -> str:
    title = str(value or "").strip()
    if not title:
        return ""
    patterns = (
        r"^Will\s+(.+?)\s+win\s+the\s+20\d{2}(?:[-/]\d{2})?\s+(?:AFC|NFC|AL|NL)\s+(?:East|West|North|South|Central)\b",
        r"^Will\s+(.+?)\s+win\s+the\s+20\d{2}(?:[-/]\d{2})?\s+.+?(?:Finals|Championship|Classic|Invitational|Masters|World Series|Stanley Cup|World Cup|World Soccer Cup)",
        r"^Will\s+(.+?)\s+win\s+.+?(?:Finals|Championship|Classic|Invitational|Masters|World Series|Stanley Cup|World Cup|World Soccer Cup)",
        r"^Will\s+(.+?)\s+win\s+(?:the\s+)?(?:MSI\s+20\d{2}|North America ACE Stage\s+\d+|EMEA Challengers Stage|Challengers\s+20\d{2}:?\s+[^?]+)\??$",
        r"^Will\s+(.+?)\s+be\s+the\s+(?:20\d{2}\s+)?(?:(?:Men(?:['’])?s|Women(?:['’])?s)\s+)?(?:Wimbledon|US Open|U\\.S\\. Open|Australian Open|French Open)\s+winner",
        r"^Will\s+(.+?)\s+be\s+.+?\bchampion",
    )
    for pattern in patterns:
        match = re.search(pattern, title, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return ""


def _championship_event_title_from_text(value: Any) -> str:
    title = str(value or "").strip(" ?")
    if not title:
        return ""
    match = re.search(
        r"\bwin\s+the\s+((?:20\d{2}(?:[-/]\d{2})?\s+)?(?:(?:Men's|Women's)\s+)?(?:FIFA\s+)?(?:World Cup|World Soccer Cup))\b",
        title,
        flags=re.IGNORECASE,
    )
    if match:
        return f"{match.group(1).strip()} Winner"
    match = re.search(
        r"\bwin\s+the\s+((?:20\d{2}(?:[-/]\d{2})?\s+)?(?:FIFA\s+)?(?:(?:Men's|Women's)\s+)?(?:World Cup|World Soccer Cup))\b",
        title,
        flags=re.IGNORECASE,
    )
    if match:
        return f"{match.group(1).strip()} Winner"
    match = re.search(
        r"\bwin\s+(?:the\s+)?((?:20\d{2}(?:[-/]\d{2})?\s+)?(?:World Series|Stanley Cup|[^?]+? Championship|[^?]+? Finals))\b",
        title,
        flags=re.IGNORECASE,
    )
    if match:
        return f"{match.group(1).strip()} Winner"
    return ""


def _event_winner_outcome_from_title(value: Any) -> str:
    title = str(value or "").strip()
    if not title:
        return ""
    patterns = (
        r"^Will\s+(.+?)\s+achieve\s+pole\s+position\b",
        r"^Will\s+(.+?)\s+get\s+pole\s+position\b",
        r"^Will\s+(.+?)\s+set\s+the\s+fastest\s+valid\s+qualifying\s+lap\s+time\b",
    )
    for pattern in patterns:
        match = re.search(pattern, title, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return ""


def _tennis_match_title_from_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    match = re.search(r"\bwin\s+the\s+(.+?)\s*:\s*.+?\bmatch\??$", text, flags=re.IGNORECASE)
    if match and _teams_from_match_title(match.group(1)):
        return match.group(1).strip()
    if ":" in text:
        prefix, suffix = [part.strip() for part in text.split(":", 1)]
        if _teams_from_match_title(prefix):
            return prefix
        if _should_split_title_prefix(prefix, suffix) and _teams_from_match_title(suffix):
            return suffix
    return ""


def _teams_from_match_title(value: Any) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    text = re.sub(r"\s+Winner\?$", "", text, flags=re.IGNORECASE).strip()
    if ":" in text:
        prefix, suffix = [part.strip() for part in text.split(":", 1)]
        if _should_split_title_prefix(prefix, suffix):
            text = suffix
    text = re.sub(r"\s+-\s+.*$", "", text).strip()
    text = re.sub(r"\s*\([^)]*\)", "", text).strip()
    parts = re.split(r"\s+(?:vs\.?|v\.?|versus|against|@)\s+", text, maxsplit=1, flags=re.IGNORECASE)
    if len(parts) != 2:
        return []
    return [_clean_match_part(part) for part in parts]


def _clean_match_part(value: Any) -> str:
    text = str(value or "").strip(" ?")
    text = re.sub(r"\s*\([^)]*\)", "", text).strip()
    text = re.sub(
        r"\s+(?:valorant|counter strike|league of legends|professional|pro)?\s*(?:game|match|winner|market)\??$",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip()
    return text


def _should_split_title_prefix(prefix: str, suffix: str) -> bool:
    if re.search(r"\s+(?:vs\.?|v\.?|versus|against|@)\s+", prefix, flags=re.IGNORECASE):
        return False
    if not re.search(r"\s+(?:vs\.?|v\.?|versus|against|@)\s+", suffix, flags=re.IGNORECASE):
        return False
    normalized_prefix = _normalize_name(prefix)
    known_prefix_terms = (
        "itf",
        "atp",
        "wta",
        "valorant",
        "counter strike",
        "league of legends",
        "nba",
        "wnba",
        "mlb",
        "nhl",
        "nfl",
        "soccer",
        "football",
    )
    return len(normalized_prefix.split()) >= 2 or any(term in normalized_prefix for term in known_prefix_terms)


def _is_tennis_market(*rows: pd.Series | dict[str, Any]) -> bool:
    if any(_cached_sport_context(row) == "tennis" for row in rows if row is not None):
        return True
    text = " ".join(_candidate_text(row) for row in rows if row is not None)
    return any(term in text for term in ("tennis", " itf", " atp", " wta", "wimbledon"))


def _outcomes_compatible(left: pd.Series | dict[str, Any], right: pd.Series | dict[str, Any]) -> bool:
    left_key = _candidate_outcome_key(left)
    right_key = _candidate_outcome_key(right)
    if not left_key or not right_key:
        return False
    if left_key == right_key:
        return True
    sport = _cached_sport_context(left) or _cached_sport_context(right)
    if sport in {"golf", "f1"}:
        return _competitor_name_compatible(_row_get(left, "outcome_label"), _row_get(right, "outcome_label"))
    if sport in {"tennis", "valorant"}:
        return _person_or_team_name_compatible(_row_get(left, "outcome_label"), _row_get(right, "outcome_label"))
    return False


def _market_sport_context(row: pd.Series | dict[str, Any] | None) -> str:
    if row is None:
        return ""
    raw = _raw_payload(row)
    direct_sport = _normalize_name(raw.get("_event_context_sport") or raw.get("category") or _row_get(row, "sport") or _row_get(row, "category"))
    if direct_sport in {"soccer", "mlb", "nfl", "wnba", "nba", "nhl", "golf", "valorant", "f1", "tennis"}:
        return direct_sport
    if direct_sport in {"baseball"}:
        return "mlb"
    if direct_sport in {"football", "world soccer"}:
        return "soccer"
    if direct_sport in {"formula 1", "formula one"}:
        return "f1"
    text = " ".join(
        [
            _candidate_text(row),
            _search_text(
                {
                    "series_ticker": raw.get("series_ticker"),
                    "event_ticker": raw.get("event_ticker"),
                    "ticker": raw.get("ticker"),
                    "sport": raw.get("_event_context_sport"),
                    "seriesSlug": raw.get("seriesSlug"),
                    "category": raw.get("category") or _row_get(row, "category"),
                    "sport_column": _row_get(row, "sport"),
                    "keyword_hits": _row_get(row, "keyword_hits"),
                    "event_context_payload": raw.get("_event_context_payload"),
                }
            ),
        ]
    )
    normalized_text = f" {_normalize_name(text)} "
    if any(term in text for term in ("kxwcgame", "kxwchost", " world cup", " world soccer", " soccer", " fifa")):
        return "soccer"
    if any(term in text for term in ("kxmlbgame", " kxmlb", " mlb", " baseball", "world series")):
        return "mlb"
    if any(term in text for term in ("kxnflgame", " kxnfl", " nfl", " football", "pro football")):
        return "nfl"
    if any(term in text for term in ("kxwnbagame", "kxwnba", " wnba", "women s pro basketball", "women's pro basketball")):
        return "wnba"
    if any(term in text for term in ("kxnbagame", " kxnba", " nba", "pro basketball")):
        return "nba"
    if any(term in text for term in ("kxnhlgame", " kxnhl", " nhl", "hockey", "stanley cup")):
        return "nhl"
    if any(term in normalized_text for term in (" tennis ", " atp ", " wta ", " itf ", " wimbledon ", " us open tennis ", " u s open tennis ")):
        return "tennis"
    if any(term in text for term in ("kxpgatour", " pga", "pga tour", " pga-tour", " golf", "travelers championship")):
        return "golf"
    if any(term in text for term in ("kxvalorantgame", " valorant", " esports", "e-sports")):
        return "valorant"
    if any(term in text for term in ("kxf1pole", " formula 1", "formula one", " f1", " grand prix", "pole position")):
        return "f1"
    return ""


def _canonical_team_name(value: Any, row: pd.Series | dict[str, Any] | None = None) -> str:
    normalized = _normalize_name(value)
    if not normalized:
        return ""
    aliases = {**SEMANTIC_GENERAL_ALIASES, **TEAM_ALIASES_BY_SPORT.get(_market_sport_context(row), {})}
    return aliases.get(normalized, normalized)


def _candidate_outcome_key(row: pd.Series | dict[str, Any]) -> str:
    implied = _title_implied_winner_outcome(row)
    if implied:
        return _canonical_team_name(implied, row)
    outcome = _row_get(row, "outcome_label")
    if _is_tennis_market(row):
        return _tennis_side_key(outcome)
    return _canonical_team_name(outcome, row)


def _title_implied_winner_outcome(row: pd.Series | dict[str, Any]) -> str:
    market_type = str(_row_get(row, "market_type") or "")
    if market_type not in {"championship_winner", "pole_position_winner"}:
        return ""
    title = str(_row_get(row, "title") or "")
    if market_type == "pole_position_winner":
        return _event_winner_outcome_from_title(title)
    return _championship_outcome_from_title(title)


def _event_team_key(row: pd.Series | dict[str, Any]) -> tuple[str, ...]:
    teams = _teams_from_match_title(_row_get(row, "event_title"))
    if len(teams) != 2:
        return ()
    normalized = sorted(_canonical_team_name(team, row) for team in teams)
    if not all(normalized):
        return ()
    return tuple(normalized)


def _normalize_name(value: Any) -> str:
    text = str(value or "").lower()
    text = text.replace("&", " and ")
    text = text.replace("draw", "tie")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    normalized = " ".join(text.split())
    return re.sub(r"^the\s+", "", normalized)


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


def _tags_from_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [tag for tag in payload if isinstance(tag, dict)]
    if isinstance(payload, dict):
        tags = payload.get("tags", [])
        return [tag for tag in tags if isinstance(tag, dict)]
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


def _new_run_id(prefix: str = "fifa") -> str:
    safe_prefix = _slugify(prefix) or "run"
    return f"{safe_prefix}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"


if __name__ == "__main__":
    sys.exit(snapshot_cli_main())
