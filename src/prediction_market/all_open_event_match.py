from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pandas as pd
from rapidfuzz import fuzz, process

from .collectors import KALSHI_BASE, POLYMARKET_GAMMA_BASE
from .fifa_arbitrage import (
    DEFAULT_SEMANTIC_CACHE_FLUSH_BATCHES,
    DEFAULT_SEMANTIC_EMBEDDING_DIM,
    DEFAULT_SEMANTIC_EMBEDDING_PROVIDER,
    DEFAULT_SPORTS_OUTPUT_DIR,
    DEFAULT_VERTEX_GEMINI_BATCH_SIZE,
    DEFAULT_VERTEX_GEMINI_BATCH_SLEEP_SECONDS,
    DEFAULT_VERTEX_GEMINI_MAX_RETRIES,
    DEFAULT_VERTEX_GEMINI_RETRY_INITIAL_SECONDS,
    VertexGeminiEmbeddingClient,
    _embed_texts_for_provider,
    _normalize_semantic_provider,
    _semantic_embedding_model_name,
    _stable_text_hash,
    _vector_to_json,
    write_latest_processed_table,
)
from .utils import compact_json, parse_json_array, utc_now_iso


EVENT_COLUMNS = [
    "run_id",
    "retrieved_at",
    "venue",
    "event_id",
    "event_ticker",
    "event_slug",
    "title",
    "subtitle",
    "category",
    "series_ticker",
    "event_date",
    "status",
    "market_count_hint",
    "market_titles_sample",
    "outcomes_sample",
    "settlement_summary",
    "embedding_text",
    "raw_event_payload",
]

EVENT_EMBEDDING_COLUMNS = [
    "run_id",
    "retrieved_at",
    "venue",
    "event_id",
    "event_ticker",
    "event_slug",
    "title",
    "embedding_provider",
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

MATCH_COLUMNS = [
    "run_id",
    "rank",
    "match_strength",
    "combined_score",
    "gemini_embedding_score",
    "title_score",
    "settlement_text_score",
    "settlement_token_overlap",
    "pm_event_id",
    "pm_event_ticker",
    "pm_event_slug",
    "pm_title",
    "pm_subtitle",
    "pm_category",
    "pm_event_date",
    "pm_market_count_hint",
    "pm_market_titles_sample",
    "pm_outcomes_sample",
    "pm_settlement_summary",
    "ks_event_id",
    "ks_event_ticker",
    "ks_event_slug",
    "ks_title",
    "ks_subtitle",
    "ks_category",
    "ks_series_ticker",
    "ks_event_date",
    "ks_market_count_hint",
    "ks_market_titles_sample",
    "ks_outcomes_sample",
    "ks_settlement_summary",
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Match every open Polymarket event to open Kalshi events with Gemini embeddings.")
    parser.add_argument("--output-dir", default=DEFAULT_SPORTS_OUTPUT_DIR)
    parser.add_argument("--run-id", default="")
    parser.add_argument("--polymarket-page-size", type=int, default=100)
    parser.add_argument("--kalshi-page-size", type=int, default=200)
    parser.add_argument("--max-polymarket-events", type=int, default=0)
    parser.add_argument("--max-kalshi-events", type=int, default=0)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--semantic-search-k", type=int, default=50)
    parser.add_argument("--title-search-k", type=int, default=50)
    parser.add_argument("--semantic-embedding-provider", default=DEFAULT_SEMANTIC_EMBEDDING_PROVIDER)
    parser.add_argument("--semantic-embedding-dim", type=int, default=DEFAULT_SEMANTIC_EMBEDDING_DIM)
    parser.add_argument("--semantic-batch-size", type=int, default=DEFAULT_VERTEX_GEMINI_BATCH_SIZE)
    parser.add_argument("--semantic-batch-sleep-seconds", type=float, default=DEFAULT_VERTEX_GEMINI_BATCH_SLEEP_SECONDS)
    parser.add_argument("--semantic-retry-initial-seconds", type=float, default=DEFAULT_VERTEX_GEMINI_RETRY_INITIAL_SECONDS)
    parser.add_argument("--semantic-max-retries", type=int, default=DEFAULT_VERTEX_GEMINI_MAX_RETRIES)
    parser.add_argument("--semantic-cache-flush-batches", type=int, default=DEFAULT_SEMANTIC_CACHE_FLUSH_BATCHES)
    parser.add_argument("--sports-only", action="store_true", help="Restrict the event universe to venue-tagged sports events.")
    parser.add_argument("--skip-fetch", action="store_true", help="Use existing all_open_events table.")
    parser.add_argument("--skip-embeddings", action="store_true", help="Use existing all_open_event_embeddings_gemini2 table.")
    args = parser.parse_args(argv)

    run_id = args.run_id or f"all-open-events-{utc_now_iso().replace(':', '').replace('-', '')}"
    output_dir = Path(args.output_dir)
    latest_dir = output_dir / "processed" / "latest"
    latest_dir.mkdir(parents=True, exist_ok=True)

    if args.skip_fetch:
        events = _read_table(latest_dir / "all_open_events")
        if args.sports_only:
            events = filter_sports_events(events)
    else:
        with httpx.Client(timeout=httpx.Timeout(30.0, connect=10.0), headers={"User-Agent": "poly-kalshi-all-open-event-match"}) as client:
            events = fetch_all_open_events(
                client,
                run_id=run_id,
                polymarket_page_size=args.polymarket_page_size,
                kalshi_page_size=args.kalshi_page_size,
                max_polymarket_events=args.max_polymarket_events,
                max_kalshi_events=args.max_kalshi_events,
                sports_only=args.sports_only,
            )
        write_latest_processed_table("all_open_events", events, output_dir)
        print(_venue_count_message("all_open_events", events), flush=True)

    if args.skip_embeddings:
        embeddings = _read_table(latest_dir / "all_open_event_embeddings_gemini2")
    else:
        embeddings = prepare_event_embeddings(
            events,
            output_dir=output_dir,
            run_id=run_id,
            provider=args.semantic_embedding_provider,
            embedding_dim=args.semantic_embedding_dim,
            vertex_batch_size=args.semantic_batch_size,
            vertex_batch_sleep_seconds=args.semantic_batch_sleep_seconds,
            vertex_retry_initial_seconds=args.semantic_retry_initial_seconds,
            vertex_max_retries=args.semantic_max_retries,
            semantic_cache_flush_batches=args.semantic_cache_flush_batches,
        )
        _write_parquet_only(latest_dir / "all_open_event_embeddings_gemini2.parquet", embeddings)
        print(_venue_count_message("all_open_event_embeddings_gemini2", embeddings), flush=True)

    top_candidates, best_matches = score_all_open_event_matches(
        events,
        embeddings,
        run_id=run_id,
        top_k=args.top_k,
        semantic_search_k=args.semantic_search_k,
        title_search_k=args.title_search_k,
    )
    write_latest_processed_table("all_open_event_top_candidates_gemini2", top_candidates, output_dir)
    write_latest_processed_table("all_open_event_best_matches_gemini2", best_matches, output_dir)
    review_candidates = best_matches[best_matches["match_strength"].isin(["likely_match", "possible_match"])].copy()
    likely = best_matches[best_matches["match_strength"].eq("likely_match")].copy()
    write_latest_processed_table("all_open_event_review_candidates_gemini2", review_candidates, output_dir)
    write_latest_processed_table("all_open_event_likely_matches_gemini2", likely, output_dir)
    print(f"top_candidates={len(top_candidates)}", flush=True)
    print(f"best_matches={len(best_matches)}", flush=True)
    print(best_matches["match_strength"].value_counts().to_string(), flush=True)
    return 0


def fetch_all_open_events(
    client: httpx.Client,
    *,
    run_id: str,
    polymarket_page_size: int = 100,
    kalshi_page_size: int = 200,
    max_polymarket_events: int = 0,
    max_kalshi_events: int = 0,
    sports_only: bool = False,
) -> pd.DataFrame:
    retrieved_at = utc_now_iso()
    rows: list[dict[str, Any]] = []
    polymarket_events = fetch_polymarket_open_events(
        client,
        page_size=polymarket_page_size,
        max_events=max_polymarket_events,
        sports_only=sports_only,
    )
    rows.extend(_polymarket_event_row(event, run_id=run_id, retrieved_at=retrieved_at) for event in polymarket_events)
    kalshi_events = fetch_kalshi_open_events(
        client,
        page_size=kalshi_page_size,
        max_events=max_kalshi_events,
        sports_only=sports_only,
    )
    rows.extend(_kalshi_event_row(event, run_id=run_id, retrieved_at=retrieved_at) for event in kalshi_events)
    return pd.DataFrame(rows, columns=EVENT_COLUMNS).fillna("")


def fetch_polymarket_open_events(
    client: httpx.Client,
    *,
    page_size: int = 100,
    max_events: int = 0,
    sports_only: bool = False,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    cursor = ""
    page = 0
    while max_events <= 0 or len(events) < max_events:
        limit = _page_limit(page_size, len(events), max_events)
        params: dict[str, Any] = {
            "active": "true",
            "closed": "false",
            "limit": limit,
            "order": "volume",
            "ascending": "false",
        }
        if cursor:
            params["after_cursor"] = cursor
        payload = _get_json_with_retry(client, f"{POLYMARKET_GAMMA_BASE}/events/keyset", params=params)
        batch = payload.get("events", []) if isinstance(payload, dict) else []
        if not batch:
            break
        page += 1
        for event in batch:
            if not isinstance(event, dict):
                continue
            event_id = str(event.get("id") or event.get("slug") or "")
            if not event_id or event_id in seen_ids:
                continue
            if event.get("active") is not True or event.get("closed") is True:
                continue
            if sports_only and not is_polymarket_sports_event(event):
                continue
            events.append(event)
            seen_ids.add(event_id)
            if max_events > 0 and len(events) >= max_events:
                break
        cursor = str(payload.get("next_cursor") or payload.get("cursor") or "") if isinstance(payload, dict) else ""
        if page <= 2 or page % 10 == 0:
            print(f"polymarket events: pages={page:,} unique={len(events):,}", flush=True)
        if not cursor or len(batch) < limit:
            break
    return events


def fetch_kalshi_open_events(
    client: httpx.Client,
    *,
    page_size: int = 200,
    max_events: int = 0,
    sports_only: bool = False,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    seen_tickers: set[str] = set()
    cursor = ""
    page = 0
    while max_events <= 0 or len(events) < max_events:
        limit = _page_limit(page_size, len(events), max_events)
        params: dict[str, Any] = {"status": "open", "limit": limit}
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
        if page <= 2 or page % 10 == 0:
            print(f"kalshi events: pages={page:,} unique={len(events):,}", flush=True)
        if not cursor or len(batch) < limit:
            break
    return events


def prepare_event_embeddings(
    events: pd.DataFrame,
    *,
    output_dir: str | Path = DEFAULT_SPORTS_OUTPUT_DIR,
    run_id: str,
    provider: str = DEFAULT_SEMANTIC_EMBEDDING_PROVIDER,
    embedding_dim: int = DEFAULT_SEMANTIC_EMBEDDING_DIM,
    vertex_batch_size: int = DEFAULT_VERTEX_GEMINI_BATCH_SIZE,
    vertex_batch_sleep_seconds: float = DEFAULT_VERTEX_GEMINI_BATCH_SLEEP_SECONDS,
    vertex_retry_initial_seconds: float = DEFAULT_VERTEX_GEMINI_RETRY_INITIAL_SECONDS,
    vertex_max_retries: int = DEFAULT_VERTEX_GEMINI_MAX_RETRIES,
    semantic_cache_flush_batches: int = DEFAULT_SEMANTIC_CACHE_FLUSH_BATCHES,
    embedding_client: Any | None = None,
) -> pd.DataFrame:
    provider = _normalize_semantic_provider(provider)
    if provider == "off" or events.empty:
        return pd.DataFrame(columns=EVENT_EMBEDDING_COLUMNS)
    model_name = _semantic_embedding_model_name(provider)
    latest_dir = Path(output_dir) / "processed" / "latest"
    cache = _ensure_event_embedding_columns(_read_optional_table(latest_dir / "all_open_event_embeddings_gemini2"))
    cache_by_key = {
        str(row.get("embedding_cache_key") or ""): row
        for _, row in cache.iterrows()
        if str(row.get("embedding_cache_key") or "")
    }
    now = utc_now_iso()
    rows: list[dict[str, Any]] = []
    missing: list[tuple[pd.Series, str, str, str, str]] = []
    for _, event in events.fillna("").iterrows():
        text = str(event.get("embedding_text") or "")
        text_hash = _stable_text_hash(text)
        embedding_key = _event_embedding_key(event, text_hash)
        cache_key = "|".join([embedding_key, provider, model_name, str(int(embedding_dim))])
        cached = cache_by_key.get(cache_key)
        if cached is not None and str(cached.get("embedding_vector") or ""):
            rows.append(_event_embedding_row(event, run_id=run_id, provider=provider, model_name=model_name, embedding_dim=embedding_dim, embedding_key=embedding_key, cache_key=cache_key, text_hash=text_hash, text=text, vector=str(cached.get("embedding_vector") or ""), embedded_at=str(cached.get("embedded_at") or now), cache_status="cached", error=""))
        else:
            missing.append((event, text, text_hash, embedding_key, cache_key))

    print(
        f"event embeddings: {len(rows):,} cached, {len(missing):,} new texts, provider={provider}, model={model_name}, dim={embedding_dim}",
        flush=True,
    )
    if not missing:
        return _ensure_event_embedding_columns(pd.DataFrame(rows, columns=EVENT_EMBEDDING_COLUMNS))

    batch_size = max(1, int(vertex_batch_size))
    flush_batches = max(1, int(semantic_cache_flush_batches))
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
    total = len(missing)
    for batch_index, start in enumerate(range(0, total, batch_size), start=1):
        batch = missing[start : start + batch_size]
        vectors = _embed_texts_for_provider(
            provider,
            [item[1] for item in batch],
            embedding_dim=embedding_dim,
            embedding_client=client,
            vertex_batch_size=batch_size,
            vertex_batch_sleep_seconds=0,
            vertex_retry_initial_seconds=vertex_retry_initial_seconds,
            vertex_max_retries=vertex_max_retries,
        )
        if len(vectors) != len(batch):
            raise RuntimeError(f"embedding provider returned {len(vectors)} vectors for {len(batch)} texts")
        for (event, text, text_hash, embedding_key, cache_key), vector in zip(batch, vectors, strict=True):
            rows.append(_event_embedding_row(event, run_id=run_id, provider=provider, model_name=model_name, embedding_dim=embedding_dim, embedding_key=embedding_key, cache_key=cache_key, text_hash=text_hash, text=text, vector=vector, embedded_at=now, cache_status="new", error=""))
        completed = min(start + batch_size, total)
        print(f"event embeddings: embedded {completed:,}/{total:,} new texts", flush=True)
        if completed == total or batch_index % flush_batches == 0:
            frame = _ensure_event_embedding_columns(pd.DataFrame(rows, columns=EVENT_EMBEDDING_COLUMNS))
            _write_parquet_only(latest_dir / "all_open_event_embeddings_gemini2.parquet", frame.drop_duplicates(subset=["embedding_cache_key"], keep="last"))
            print(f"event embeddings: flushed {len(frame):,} rows", flush=True)
        if provider == "vertex-gemini" and vertex_batch_sleep_seconds > 0 and completed < total:
            time.sleep(vertex_batch_sleep_seconds)
    return _ensure_event_embedding_columns(pd.DataFrame(rows, columns=EVENT_EMBEDDING_COLUMNS)).drop_duplicates(subset=["embedding_cache_key"], keep="last").reset_index(drop=True)


def score_all_open_event_matches(
    events: pd.DataFrame,
    embeddings: pd.DataFrame,
    *,
    run_id: str,
    top_k: int = 10,
    semantic_search_k: int = 50,
    title_search_k: int = 50,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    event_lookup = {
        (str(row.get("venue") or ""), str(row.get("event_id") or "")): row
        for _, row in events.fillna("").iterrows()
    }
    pm_embeddings, pm_rows = _embedding_matrix(embeddings, event_lookup, "polymarket")
    ks_embeddings, ks_rows = _embedding_matrix(embeddings, event_lookup, "kalshi")
    if pm_embeddings.size == 0 or ks_embeddings.size == 0:
        empty = pd.DataFrame(columns=MATCH_COLUMNS)
        return empty, empty.copy()

    ks_titles = [str(row.get("title") or "") for row in ks_rows]
    top_rows: list[dict[str, Any]] = []
    best_rows: list[dict[str, Any]] = []
    semantic_take = max(int(top_k), int(semantic_search_k), 1)
    title_take = max(int(title_search_k), 0)
    for pm_index, pm_row in enumerate(pm_rows):
        similarities = ks_embeddings @ pm_embeddings[pm_index]
        take = min(semantic_take, len(similarities))
        semantic_indexes = set(_top_indexes(similarities, take))
        title_indexes: set[int] = set()
        if title_take:
            title_indexes = {
                int(index)
                for _, _, index in process.extract(
                    str(pm_row.get("title") or ""),
                    ks_titles,
                    scorer=fuzz.token_set_ratio,
                    limit=min(title_take, len(ks_titles)),
                )
            }
        candidate_indexes = semantic_indexes | title_indexes
        scored = [
            _match_row(run_id, pm_row, ks_rows[index], rank=0, semantic_score=100.0 * float(similarities[index]))
            for index in candidate_indexes
        ]
        scored.sort(
            key=lambda row: (
                float(row["combined_score"]),
                float(row["gemini_embedding_score"]),
                float(row["title_score"]),
                float(row["settlement_text_score"]),
            ),
            reverse=True,
        )
        for rank, row in enumerate(scored[: max(1, int(top_k))], start=1):
            row["rank"] = rank
            top_rows.append(row)
            if rank == 1:
                best_rows.append(row.copy())
        if (pm_index + 1) % 500 == 0:
            print(f"scored polymarket events: {pm_index + 1:,}/{len(pm_rows):,}", flush=True)

    top_frame = pd.DataFrame(top_rows, columns=MATCH_COLUMNS)
    best_frame = pd.DataFrame(best_rows, columns=MATCH_COLUMNS)
    return top_frame, best_frame


def _polymarket_event_row(event: dict[str, Any], *, run_id: str, retrieved_at: str) -> dict[str, Any]:
    markets = [market for market in event.get("markets") or [] if isinstance(market, dict)]
    active_markets = [market for market in markets if market.get("active") is True and market.get("closed") is not True]
    market_titles = _joined_unique([market.get("question") or market.get("title") or market.get("slug") for market in active_markets], limit=12, max_chars=220)
    outcomes = _joined_unique(_flatten(parse_json_array(market.get("outcomes")) for market in active_markets), limit=20, max_chars=80)
    market_descriptions = _joined_unique([market.get("description") for market in active_markets], limit=5, max_chars=900)
    tags = _joined_unique([tag.get("label") or tag.get("slug") for tag in event.get("tags") or [] if isinstance(tag, dict)], limit=12, max_chars=80)
    settlement_summary = _joined_unique(
        [
            event.get("description"),
            event.get("resolutionSource"),
            market_descriptions,
        ],
        limit=6,
        max_chars=2000,
    )
    row = {
        "run_id": run_id,
        "retrieved_at": retrieved_at,
        "venue": "polymarket",
        "event_id": str(event.get("id") or event.get("slug") or ""),
        "event_ticker": str(event.get("ticker") or ""),
        "event_slug": str(event.get("slug") or ""),
        "title": str(event.get("title") or "").strip(),
        "subtitle": "",
        "category": tags,
        "series_ticker": "",
        "event_date": str(event.get("endDate") or event.get("endDateIso") or ""),
        "status": "active" if event.get("active") is True and event.get("closed") is not True else "inactive",
        "market_count_hint": len(active_markets),
        "market_titles_sample": market_titles,
        "outcomes_sample": outcomes,
        "settlement_summary": settlement_summary,
        "raw_event_payload": compact_json({key: event.get(key) for key in ("id", "ticker", "slug", "title", "description", "resolutionSource", "endDate", "tags")}),
    }
    row["embedding_text"] = _event_embedding_text(row)
    return row


def _kalshi_event_row(event: dict[str, Any], *, run_id: str, retrieved_at: str) -> dict[str, Any]:
    settlement_sources = event.get("settlement_sources") or []
    settlement_source_text = _joined_unique(
        [
            " ".join(str(source.get(key) or "") for key in ("name", "url"))
            for source in settlement_sources
            if isinstance(source, dict)
        ],
        limit=20,
        max_chars=180,
    )
    product_metadata = event.get("product_metadata") or event.get("productMetadata") or ""
    settlement_summary = _joined_unique(
        [
            f"subtitle: {event.get('sub_title') or ''}",
            f"strike_period: {event.get('strike_period') or ''}",
            f"settlement_sources: {settlement_source_text}",
            f"product_metadata: {product_metadata}",
        ],
        limit=8,
        max_chars=2000,
    )
    row = {
        "run_id": run_id,
        "retrieved_at": retrieved_at,
        "venue": "kalshi",
        "event_id": str(event.get("event_ticker") or event.get("ticker") or ""),
        "event_ticker": str(event.get("event_ticker") or event.get("ticker") or ""),
        "event_slug": "",
        "title": str(event.get("title") or "").strip(),
        "subtitle": str(event.get("sub_title") or "").strip(),
        "category": str(event.get("category") or "").strip(),
        "series_ticker": str(event.get("series_ticker") or "").strip(),
        "event_date": str(event.get("last_updated_ts") or ""),
        "status": "open",
        "market_count_hint": 0,
        "market_titles_sample": "",
        "outcomes_sample": "",
        "settlement_summary": settlement_summary,
        "raw_event_payload": compact_json(event),
    }
    row["embedding_text"] = _event_embedding_text(row)
    return row


def filter_sports_events(events: pd.DataFrame) -> pd.DataFrame:
    if events.empty or "venue" not in events.columns:
        return events
    mask = events.apply(_sports_event_row_mask, axis=1)
    return events[mask].reset_index(drop=True)


def _sports_event_row_mask(row: pd.Series) -> bool:
    venue = str(row.get("venue") or "").casefold()
    payload = _json_payload(row.get("raw_event_payload"))
    if venue == "polymarket":
        return is_polymarket_sports_event(payload) or _contains_sports(row.get("category"))
    if venue == "kalshi":
        return is_kalshi_sports_event(payload) or _contains_sports(row.get("category"))
    return False


def is_polymarket_sports_event(event: dict[str, Any]) -> bool:
    return any(
        _is_sports_label(tag.get("label") or tag.get("slug"))
        for tag in event.get("tags") or []
        if isinstance(tag, dict)
    )


def is_kalshi_sports_event(event: dict[str, Any]) -> bool:
    return _is_sports_label(event.get("category"))


def _contains_sports(value: Any) -> bool:
    return "sports" in {token.casefold() for token in str(value or "").replace("|", " ").split()}


def _is_sports_label(value: Any) -> bool:
    return str(value or "").strip().casefold() == "sports"


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


def _event_embedding_text(row: dict[str, Any] | pd.Series) -> str:
    parts = [
        ("venue", _row_get(row, "venue")),
        ("event_title", _row_get(row, "title")),
        ("subtitle", _row_get(row, "subtitle")),
        ("category", _row_get(row, "category")),
        ("series", _row_get(row, "series_ticker")),
        ("event_ticker", _row_get(row, "event_ticker")),
        ("event_slug", _row_get(row, "event_slug")),
        ("event_date", _row_get(row, "event_date")),
        ("market_titles", _row_get(row, "market_titles_sample")),
        ("outcomes", _row_get(row, "outcomes_sample")),
        ("settlement_summary", _row_get(row, "settlement_summary")),
    ]
    return "\n".join(f"{label}: {str(value).strip()}" for label, value in parts if str(value or "").strip())


def _event_embedding_row(
    event: pd.Series,
    *,
    run_id: str,
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
        "retrieved_at": event.get("retrieved_at", ""),
        "venue": event.get("venue", ""),
        "event_id": event.get("event_id", ""),
        "event_ticker": event.get("event_ticker", ""),
        "event_slug": event.get("event_slug", ""),
        "title": event.get("title", ""),
        "embedding_provider": provider,
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


def _match_row(
    run_id: str,
    pm: pd.Series,
    ks: pd.Series,
    *,
    rank: int,
    semantic_score: float,
) -> dict[str, Any]:
    title_score = float(fuzz.token_set_ratio(str(pm.get("title") or ""), str(ks.get("title") or "")))
    settlement_score = float(
        fuzz.token_set_ratio(str(pm.get("settlement_summary") or "")[:2000], str(ks.get("settlement_summary") or "")[:2000])
    )
    token_overlap = _token_overlap(pm.get("settlement_summary"), ks.get("settlement_summary"))
    combined = 0.55 * semantic_score + 0.30 * title_score + 0.12 * settlement_score + 0.03 * token_overlap
    return {
        "run_id": run_id,
        "rank": rank,
        "match_strength": _match_strength(combined, semantic_score, title_score, settlement_score),
        "combined_score": round(float(combined), 2),
        "gemini_embedding_score": round(float(semantic_score), 2),
        "title_score": round(float(title_score), 2),
        "settlement_text_score": round(float(settlement_score), 2),
        "settlement_token_overlap": round(float(token_overlap), 2),
        "pm_event_id": pm.get("event_id", ""),
        "pm_event_ticker": pm.get("event_ticker", ""),
        "pm_event_slug": pm.get("event_slug", ""),
        "pm_title": pm.get("title", ""),
        "pm_subtitle": pm.get("subtitle", ""),
        "pm_category": pm.get("category", ""),
        "pm_event_date": pm.get("event_date", ""),
        "pm_market_count_hint": pm.get("market_count_hint", ""),
        "pm_market_titles_sample": pm.get("market_titles_sample", ""),
        "pm_outcomes_sample": pm.get("outcomes_sample", ""),
        "pm_settlement_summary": pm.get("settlement_summary", ""),
        "ks_event_id": ks.get("event_id", ""),
        "ks_event_ticker": ks.get("event_ticker", ""),
        "ks_event_slug": ks.get("event_slug", ""),
        "ks_title": ks.get("title", ""),
        "ks_subtitle": ks.get("subtitle", ""),
        "ks_category": ks.get("category", ""),
        "ks_series_ticker": ks.get("series_ticker", ""),
        "ks_event_date": ks.get("event_date", ""),
        "ks_market_count_hint": ks.get("market_count_hint", ""),
        "ks_market_titles_sample": ks.get("market_titles_sample", ""),
        "ks_outcomes_sample": ks.get("outcomes_sample", ""),
        "ks_settlement_summary": ks.get("settlement_summary", ""),
    }


def _match_strength(combined: float, semantic: float, title: float, settlement: float) -> str:
    if combined >= 82 and semantic >= 75 and title >= 80 and settlement >= 50:
        return "likely_match"
    if combined >= 72 and (title >= 55 or settlement >= 40):
        return "possible_match"
    return "weak_top_candidate"


def _embedding_matrix(
    embeddings: pd.DataFrame,
    event_lookup: dict[tuple[str, str], pd.Series],
    venue: str,
) -> tuple[np.ndarray, list[pd.Series]]:
    vectors: list[np.ndarray] = []
    rows: list[pd.Series] = []
    for _, embedding in embeddings[embeddings["venue"].eq(venue)].iterrows():
        event_id = str(embedding.get("event_id") or "")
        event = event_lookup.get((venue, event_id))
        if event is None:
            continue
        vector = _parse_vector(embedding.get("embedding_vector"))
        if vector is None:
            continue
        vectors.append(vector.astype(np.float32))
        rows.append(event)
    if not vectors:
        return np.empty((0, 0), dtype=np.float32), []
    return np.vstack(vectors).astype(np.float32), rows


def _parse_vector(value: Any) -> np.ndarray | None:
    if isinstance(value, np.ndarray):
        array = value.astype(float)
    elif isinstance(value, list):
        array = np.array(value, dtype=float)
    else:
        try:
            array = np.array(json.loads(str(value)), dtype=float)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
    norm = float(np.linalg.norm(array))
    if not norm:
        return None
    return array / norm


def _top_indexes(values: np.ndarray, take: int) -> list[int]:
    if take >= len(values):
        return np.argsort(-values).astype(int).tolist()
    partition = np.argpartition(-values, take - 1)[:take]
    return partition[np.argsort(-values[partition])].astype(int).tolist()


def _event_embedding_key(event: pd.Series, text_hash: str) -> str:
    parts = [
        str(event.get("venue") or ""),
        str(event.get("event_id") or ""),
        str(event.get("event_ticker") or ""),
        str(event.get("event_slug") or ""),
        str(text_hash or ""),
    ]
    return "|".join(_slugify(part) for part in parts)


def _token_overlap(left: Any, right: Any) -> float:
    left_tokens = set(_normalize_text(left).split())
    right_tokens = set(_normalize_text(right).split())
    if not left_tokens or not right_tokens:
        return 0.0
    return 100.0 * len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def _normalize_text(value: Any) -> str:
    import re

    text = str(value or "").casefold()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _slugify(value: Any) -> str:
    return _normalize_text(value).replace(" ", "-")


def _page_limit(page_size: int, collected: int, max_items: int) -> int:
    page_size = max(1, int(page_size))
    if max_items <= 0:
        return page_size
    return max(0, min(page_size, max_items - collected))


def _joined_unique(values: list[Any], *, limit: int, max_chars: int) -> str:
    output: list[str] = []
    seen: set[str] = set()
    for raw in values:
        text = str(raw or "").strip()
        if not text or text.casefold() == "nan":
            continue
        key = _normalize_text(text)
        if key in seen:
            continue
        output.append(text[:max_chars])
        seen.add(key)
        if len(output) >= limit:
            break
    return " | ".join(output)


def _flatten(values: Any) -> list[Any]:
    output: list[Any] = []
    for value in values:
        if isinstance(value, list):
            output.extend(value)
        else:
            output.append(value)
    return output


def _row_get(row: dict[str, Any] | pd.Series, key: str) -> Any:
    if isinstance(row, pd.Series):
        return row.get(key)
    return row.get(key)


def _ensure_event_embedding_columns(frame: pd.DataFrame | None) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame(columns=EVENT_EMBEDDING_COLUMNS)
    output = frame.copy()
    for column in EVENT_EMBEDDING_COLUMNS:
        if column not in output.columns:
            output[column] = ""
    return output[EVENT_EMBEDDING_COLUMNS].fillna("")


def _read_table(path_without_suffix: Path) -> pd.DataFrame:
    parquet_path = path_without_suffix.with_suffix(".parquet")
    csv_path = path_without_suffix.with_suffix(".csv")
    if parquet_path.exists():
        return pd.read_parquet(parquet_path)
    if csv_path.exists():
        return pd.read_csv(csv_path, dtype=str, keep_default_na=False)
    raise FileNotFoundError(f"No table found at {parquet_path} or {csv_path}")


def _read_optional_table(path_without_suffix: Path) -> pd.DataFrame:
    try:
        return _read_table(path_without_suffix)
    except FileNotFoundError:
        return pd.DataFrame()


def _write_parquet_only(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)


def _get_json_with_retry(
    client: httpx.Client,
    url: str,
    *,
    params: dict[str, Any] | None,
    max_retries: int = 8,
    backoff_seconds: float = 2.0,
) -> Any:
    attempt = 0
    while True:
        response = client.get(url, params=params)
        if response.status_code < 500 and response.status_code != 429:
            response.raise_for_status()
            return response.json()
        attempt += 1
        if attempt > max_retries:
            response.raise_for_status()
        retry_after = response.headers.get("Retry-After")
        retry_after_seconds = _parse_retry_after_seconds(retry_after)
        sleep_seconds = retry_after_seconds if retry_after_seconds is not None else backoff_seconds * (2 ** (attempt - 1))
        time.sleep(min(sleep_seconds, 120.0))


def _parse_retry_after_seconds(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        seconds = float(value)
    except ValueError:
        return None
    return max(0.0, seconds)


def _venue_count_message(name: str, frame: pd.DataFrame) -> str:
    counts = frame["venue"].value_counts().to_dict() if "venue" in frame.columns else {}
    return f"{name}: rows={len(frame):,} " + " ".join(f"{venue}={count:,}" for venue, count in counts.items())


if __name__ == "__main__":
    raise SystemExit(main())
