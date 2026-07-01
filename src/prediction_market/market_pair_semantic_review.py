from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .fifa_arbitrage import (
    DEFAULT_SEMANTIC_EMBEDDING_DIM,
    SUGGESTED_MAPPING_COLUMNS,
    VertexGeminiEmbeddingClient,
    VertexGeminiPairReviewClient,
    _dense_local_embedding,
    _ensure_suggested_mapping_columns,
    _stable_text_hash,
    _vector_from_value,
    _vector_to_json,
    review_suggested_mappings_with_ai,
    write_latest_processed_table,
)
from .utils import utc_now_iso


DEFAULT_SOURCE = "data/cross_sports_arbitrage"
ACTIVE_APPROVAL_TABLE = "all_active_approval_candidates"
EVENT_PAIR_TABLE = "all_open_event_possible_match_validity_gemini25"
EMBEDDING_TABLE = "market_pair_venue_fact_embeddings"
CANDIDATE_TABLE = "market_pair_gemini_candidates"
REVIEWED_TABLE = "market_pair_gemini_reviewed"
SUMMARY_TABLE = "market_pair_gemini_summary"
DEFAULT_COVERAGE_TABLE = "market_pair_gemini_coverage"
DEFAULT_TEXT_REVIEW_MODEL = "gemini-2.5-flash"
DEFAULT_TOP_K = 1
DEFAULT_MIN_SCORE = 0.0

EMBEDDING_COLUMNS = [
    "run_id",
    "retrieved_at",
    "venue",
    "event_key",
    "market_key",
    "market_id",
    "ticker_or_slug",
    "yes_token_id",
    "outcome_label",
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Score market pairs inside approved event pairs with Gemini embeddings and Gemini text review.",
    )
    parser.add_argument("--source", default=DEFAULT_SOURCE)
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--active-approval-table", default=ACTIVE_APPROVAL_TABLE)
    parser.add_argument("--event-pair-table", default=EVENT_PAIR_TABLE)
    parser.add_argument("--embedding-output-name", default=EMBEDDING_TABLE)
    parser.add_argument("--candidate-output-name", default=CANDIDATE_TABLE)
    parser.add_argument("--reviewed-output-name", default=REVIEWED_TABLE)
    parser.add_argument("--summary-output-name", default=SUMMARY_TABLE)
    parser.add_argument("--coverage-output-name", default=DEFAULT_COVERAGE_TABLE)
    parser.add_argument("--embedding-provider", choices=["vertex-gemini", "local"], default="vertex-gemini")
    parser.add_argument("--embedding-dim", type=int, default=DEFAULT_SEMANTIC_EMBEDDING_DIM)
    parser.add_argument("--embedding-batch-size", type=int, default=16)
    parser.add_argument("--embedding-cache-flush-batches", type=int, default=25)
    parser.add_argument("--max-new-embeddings", type=int, default=0)
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    parser.add_argument("--min-score", type=float, default=DEFAULT_MIN_SCORE)
    parser.add_argument("--max-event-pairs", type=int, default=0)
    parser.add_argument("--ai-review-provider", choices=["off", "vertex-gemini"], default="off")
    parser.add_argument("--ai-review-model", default=DEFAULT_TEXT_REVIEW_MODEL)
    parser.add_argument("--ai-review-location", default="global")
    parser.add_argument("--review-limit", type=int, default=250)
    parser.add_argument("--review-min-score", type=float, default=DEFAULT_MIN_SCORE)
    args = parser.parse_args(argv)

    source = Path(args.source)
    output_dir = Path(args.output_dir) if args.output_dir else source
    latest_dir = source / "processed" / "latest"
    active = _read_table(latest_dir / args.active_approval_table)
    event_pairs = _valid_event_pairs(_read_table(latest_dir / args.event_pair_table))
    if args.max_event_pairs > 0:
        event_pairs = event_pairs.head(args.max_event_pairs).copy()

    run_id = f"market-pair-semantic-{utc_now_iso().replace(':', '').replace('-', '')}"
    retrieved_at = utc_now_iso()
    market_facts = build_market_fact_rows(active, event_pairs)
    embedding_cache = prepare_venue_fact_embeddings(
        market_facts,
        output_dir=output_dir,
        table_name=args.embedding_output_name,
        run_id=run_id,
        retrieved_at=retrieved_at,
        provider=args.embedding_provider,
        embedding_dim=args.embedding_dim,
        batch_size=args.embedding_batch_size,
        flush_batches=args.embedding_cache_flush_batches,
        max_new_embeddings=args.max_new_embeddings,
    )
    candidates, coverage = score_market_pair_candidates(
        market_facts,
        event_pairs,
        embedding_cache,
        run_id=run_id,
        top_k=args.top_k,
        min_score=args.min_score,
    )
    reviewed = review_suggested_mappings_with_ai(
        candidates,
        provider=args.ai_review_provider,
        model_name=args.ai_review_model,
        limit=args.review_limit,
        min_score=args.review_min_score,
        review_client=(
            VertexGeminiPairReviewClient(model_name=args.ai_review_model, location=args.ai_review_location)
            if args.ai_review_provider == "vertex-gemini"
            else None
        ),
    )
    summary = _summary_frame(
        event_pairs=event_pairs,
        market_facts=market_facts,
        embeddings=embedding_cache,
        candidates=candidates,
        reviewed=reviewed,
        coverage=coverage,
    )

    write_latest_processed_table(args.embedding_output_name, embedding_cache, output_dir)
    write_latest_processed_table(args.candidate_output_name, candidates, output_dir)
    write_latest_processed_table(args.reviewed_output_name, reviewed, output_dir)
    write_latest_processed_table(args.summary_output_name, summary, output_dir)
    write_latest_processed_table(args.coverage_output_name, coverage, output_dir)
    print(summary.to_string(index=False), flush=True)
    return 0


def build_market_fact_rows(active_candidates: pd.DataFrame, event_pairs: pd.DataFrame) -> pd.DataFrame:
    if active_candidates.empty or event_pairs.empty:
        return pd.DataFrame(columns=[*active_candidates.columns, "_event_key", "_market_key", "_fact_text", "_fact_text_hash"])
    frame = active_candidates.fillna("").copy()
    frame["_event_key"] = frame.apply(_native_event_key, axis=1)
    frame["_market_key"] = frame.apply(_market_key, axis=1)
    frame["_outcome_side"] = frame.apply(_outcome_side, axis=1)
    frame["_fact_text"] = frame.apply(build_venue_fact_text, axis=1)
    frame["_fact_text_hash"] = frame["_fact_text"].map(_stable_text_hash)
    pm_keys = set(event_pairs["pm_event_key"].astype(str))
    ks_keys = set(event_pairs["ks_event_key"].astype(str))
    pm = frame[frame["venue"].astype(str).str.casefold().eq("polymarket") & frame["_event_key"].astype(str).isin(pm_keys)]
    ks = frame[frame["venue"].astype(str).str.casefold().eq("kalshi") & frame["_event_key"].astype(str).isin(ks_keys)]
    return pd.concat([pm, ks], ignore_index=True).drop_duplicates(subset=["_market_key"], keep="last")


def build_venue_fact_text(row: pd.Series | dict[str, Any]) -> str:
    payload = _json_payload(_row_get(row, "raw_payload"))
    raw_parts = _raw_venue_fact_parts(payload)
    parts = [
        ("venue", _row_get(row, "venue")),
        ("event_title", _row_get(row, "event_title")),
        ("market_title", _row_get(row, "title")),
        ("outcome_or_side", _row_get(row, "_outcome_side")),
        ("all_outcomes", _row_get(row, "outcomes")),
        ("rules_or_description", _row_get(row, "rules_text")),
        ("settlement_summary", _row_get(row, "settlement_summary")),
        ("close_or_expiration", _close_or_expiration_text(row, payload)),
        ("source_or_resolution", raw_parts.get("source_or_resolution", "")),
        ("venue_category", _row_get(row, "category")),
        ("venue_identifier", _row_get(row, "ticker_or_slug") or _row_get(row, "market_id")),
    ]
    return "\n".join(f"{label}: {str(value).strip()}" for label, value in parts if not _is_blank(value))


def prepare_venue_fact_embeddings(
    market_facts: pd.DataFrame,
    *,
    output_dir: str | Path,
    table_name: str = EMBEDDING_TABLE,
    run_id: str,
    retrieved_at: str,
    provider: str,
    embedding_dim: int,
    batch_size: int,
    flush_batches: int,
    max_new_embeddings: int = 0,
) -> pd.DataFrame:
    cache = _ensure_embedding_columns(_read_optional_table(Path(output_dir) / "processed" / "latest" / table_name))
    cache_by_key = {
        str(row.get("embedding_cache_key") or ""): row
        for _, row in cache.iterrows()
        if str(row.get("embedding_cache_key") or "")
    }
    model_name = _embedding_model_name(provider)
    rows: list[dict[str, Any]] = []
    missing: list[tuple[pd.Series, str, str, str]] = []
    for _, fact in market_facts.iterrows():
        text = str(fact.get("_fact_text") or "")
        text_hash = str(fact.get("_fact_text_hash") or _stable_text_hash(text))
        embedding_key = _embedding_key(fact, text_hash)
        cache_key = _embedding_cache_key(embedding_key, provider, model_name, embedding_dim)
        cached = cache_by_key.get(cache_key)
        if cached is not None and _vector_from_value(cached.get("embedding_vector")):
            rows.append(_embedding_row(fact, run_id=run_id, retrieved_at=retrieved_at, provider=provider, model_name=model_name, embedding_dim=embedding_dim, embedding_key=embedding_key, cache_key=cache_key, text_hash=text_hash, text=text, vector=cached.get("embedding_vector"), embedded_at=str(cached.get("embedded_at") or ""), cache_status="cached", error=""))
        else:
            missing.append((fact, text, text_hash, cache_key))
    if max_new_embeddings > 0 and len(missing) > max_new_embeddings:
        missing = missing[:max_new_embeddings]
        print(f"Market semantic embeddings: limiting new embeddings to {len(missing):,}.", flush=True)
    if missing:
        print(
            f"Market semantic embeddings: {len(rows):,} cached, {len(missing):,} new texts, provider={provider}.",
            flush=True,
        )
        rows.extend(
            _embed_missing_rows(
                missing,
                output_dir=output_dir,
                seed_rows=rows,
                run_id=run_id,
                retrieved_at=retrieved_at,
                provider=provider,
                model_name=model_name,
                embedding_dim=embedding_dim,
                batch_size=batch_size,
                flush_batches=flush_batches,
                table_name=table_name,
            )
        )
    output = _ensure_embedding_columns(pd.DataFrame(rows, columns=EMBEDDING_COLUMNS))
    return output.drop_duplicates(subset=["embedding_cache_key"], keep="last").reset_index(drop=True)


def score_market_pair_candidates(
    market_facts: pd.DataFrame,
    event_pairs: pd.DataFrame,
    embeddings: pd.DataFrame,
    *,
    run_id: str,
    top_k: int = 5,
    min_score: float = 0.0,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if market_facts.empty or event_pairs.empty or embeddings.empty:
        return pd.DataFrame(columns=SUGGESTED_MAPPING_COLUMNS), pd.DataFrame()
    vectors = _embedding_vector_lookup(embeddings)
    facts = market_facts.copy()
    facts["_embedding_key"] = facts.apply(lambda row: _embedding_key(row, str(row.get("_fact_text_hash") or "")), axis=1)
    facts["_embedding_vector"] = facts["_embedding_key"].map(vectors)
    facts = facts[facts["_embedding_vector"].map(lambda value: isinstance(value, list) and bool(value))].copy()
    pm_by_event = {str(key): group.copy() for key, group in facts[facts["venue"].eq("polymarket")].groupby("_event_key", dropna=False)}
    ks_by_event = {str(key): group.copy() for key, group in facts[facts["venue"].eq("kalshi")].groupby("_event_key", dropna=False)}
    rows: list[dict[str, Any]] = []
    coverage_rows: list[dict[str, Any]] = []
    top_k = max(1, int(top_k))
    for _, pair in event_pairs.iterrows():
        pm_event_key = str(pair.get("pm_event_key") or "")
        ks_event_key = str(pair.get("ks_event_key") or "")
        pm_rows = pm_by_event.get(pm_event_key, pd.DataFrame())
        ks_rows = ks_by_event.get(ks_event_key, pd.DataFrame())
        if pm_rows.empty or ks_rows.empty:
            coverage_rows.append(
                {
                    "pm_event_key": pm_event_key,
                    "ks_event_key": ks_event_key,
                    "pm_event_title": str(pair.get("pm_title") or ""),
                    "ks_event_title": str(pair.get("ks_title") or ""),
                    "pm_market_rows": int(len(pm_rows)),
                    "ks_market_rows": int(len(ks_rows)),
                    "candidate_rows": 0,
                }
            )
            continue
        pm_records = [row for _, row in pm_rows.iterrows()]
        ks_records = [row for _, row in ks_rows.iterrows()]
        pm_matrix = _normalized_matrix([row.get("_embedding_vector") or [] for row in pm_records])
        ks_matrix = _normalized_matrix([row.get("_embedding_vector") or [] for row in ks_records])
        score_matrix = pm_matrix @ ks_matrix.T * 100.0
        pair_candidate_count = 0
        for pm_index, pm in enumerate(pm_records):
            scores = score_matrix[pm_index]
            if top_k >= len(scores):
                candidate_indices = np.argsort(-scores)
            else:
                partial = np.argpartition(-scores, top_k - 1)[:top_k]
                candidate_indices = partial[np.argsort(-scores[partial])]
            for rank, ks_index in enumerate(candidate_indices, start=1):
                score = float(scores[int(ks_index)])
                if score < min_score:
                    continue
                rows.append(_suggested_row_from_facts(pm, ks_records[int(ks_index)], score=score, rank=rank, run_id=run_id))
                pair_candidate_count += 1
        coverage_rows.append(
            {
                "pm_event_key": pm_event_key,
                "ks_event_key": ks_event_key,
                "pm_event_title": str(pair.get("pm_title") or ""),
                "ks_event_title": str(pair.get("ks_title") or ""),
                "pm_market_rows": int(len(pm_rows)),
                "ks_market_rows": int(len(ks_rows)),
                "candidate_rows": pair_candidate_count,
            }
        )
    candidates = _ensure_suggested_mapping_columns(pd.DataFrame(rows, columns=SUGGESTED_MAPPING_COLUMNS))
    coverage = pd.DataFrame(coverage_rows)
    return candidates, coverage


def _embed_missing_rows(
    missing: list[tuple[pd.Series, str, str, str]],
    *,
    output_dir: str | Path,
    seed_rows: list[dict[str, Any]],
    run_id: str,
    retrieved_at: str,
    provider: str,
    model_name: str,
    embedding_dim: int,
    batch_size: int,
    flush_batches: int,
    table_name: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    client = VertexGeminiEmbeddingClient(batch_size=max(1, int(batch_size))) if provider == "vertex-gemini" else None
    batch_size = max(1, int(batch_size))
    flush_batches = max(1, int(flush_batches))
    embedded_at = utc_now_iso()
    total = len(missing)
    for batch_index, start in enumerate(range(0, total, batch_size), start=1):
        batch = missing[start : start + batch_size]
        texts = [item[1] for item in batch]
        if provider == "vertex-gemini":
            vectors = client.embed_texts(texts, embedding_dim=embedding_dim) if client else []
        else:
            vectors = [_dense_local_embedding(text, dimensions=embedding_dim) for text in texts]
        if len(vectors) != len(batch):
            raise RuntimeError(f"embedding provider returned {len(vectors)} vectors for {len(batch)} texts")
        for (fact, text, text_hash, cache_key), vector in zip(batch, vectors, strict=True):
            rows.append(_embedding_row(fact, run_id=run_id, retrieved_at=retrieved_at, provider=provider, model_name=model_name, embedding_dim=embedding_dim, embedding_key=_embedding_key(fact, text_hash), cache_key=cache_key, text_hash=text_hash, text=text, vector=vector, embedded_at=embedded_at, cache_status="new", error=""))
        completed = min(start + batch_size, total)
        print(f"Market semantic embeddings: embedded {completed:,}/{total:,} new texts.", flush=True)
        if completed == total or batch_index % flush_batches == 0:
            frame = _ensure_embedding_columns(pd.DataFrame([*seed_rows, *rows], columns=EMBEDDING_COLUMNS))
            write_latest_processed_table(table_name, frame.drop_duplicates(subset=["embedding_cache_key"], keep="last"), output_dir)
    return rows


def _suggested_row_from_facts(pm: pd.Series, ks: pd.Series, *, score: float, rank: int, run_id: str) -> dict[str, Any]:
    mapping_id = f"{_slug(str(pm.get('_market_key') or ''))}__{_slug(str(ks.get('_market_key') or ''))}"
    event_name = str(pm.get("event_title") or ks.get("event_title") or "")
    outcome_label = str(pm.get("_outcome_side") or ks.get("_outcome_side") or "")
    pm_market_type = str(pm.get("market_type") or "").strip()
    ks_market_type = str(ks.get("market_type") or "").strip()
    market_type = pm_market_type if pm_market_type == ks_market_type else f"{pm_market_type or 'unknown'}->{ks_market_type or 'unknown'}"
    rounded = round(float(score), 2)
    return {
        "run_id": run_id,
        "suggested_mapping_id": mapping_id,
        "match_score": rounded,
        "embedding_score": rounded,
        "lexical_score": "",
        "combined_score": rounded,
        "gemini_embedding_score": rounded,
        "semantic_combined_score": rounded,
        "semantic_provider": "vertex-gemini",
        "embedding_model": "",
        "embedding_dim": "",
        "embedding_text_hash": f"{pm.get('_fact_text_hash')}|{ks.get('_fact_text_hash')}",
        "suggestion_method": "event_scoped_gemini_embedding_venue_facts",
        "suggestion_status": "manual_approval_required",
        "market_type": market_type,
        "review_notes": f"rank={rank}; event-scoped top-1 venue-fact retrieval; no score threshold unless --min-score is set; settlement fallback clauses are not treated as automatic rejection.",
        "mapping_id": mapping_id,
        "event_name": event_name,
        "proposition": str(pm.get("title") or ""),
        "polymarket_event_title": pm.get("event_title", ""),
        "kalshi_event_title": ks.get("event_title", ""),
        "event_match_key": f"{pm.get('_event_key')}__{ks.get('_event_key')}",
        "outcome_label": outcome_label,
        "polymarket_market_id": pm.get("market_id", ""),
        "polymarket_slug": pm.get("ticker_or_slug", ""),
        "polymarket_title": pm.get("title", ""),
        "polymarket_yes_token_id": pm.get("yes_token_id", ""),
        "polymarket_no_token_id": pm.get("no_token_id", ""),
        "polymarket_yes_outcome": pm.get("_outcome_side", "") or "Yes",
        "polymarket_no_outcome": _second_outcome(pm.get("outcomes")),
        "polymarket_outcomes": pm.get("outcomes", ""),
        "polymarket_settlement_summary": pm.get("settlement_summary", "") or pm.get("rules_text", ""),
        "kalshi_ticker": ks.get("market_id", "") or ks.get("ticker_or_slug", ""),
        "kalshi_title": ks.get("title", ""),
        "kalshi_outcomes": ks.get("outcomes", ""),
        "kalshi_settlement_summary": ks.get("settlement_summary", "") or ks.get("rules_text", ""),
        "draw_handling": "",
        "extra_time_handling": "",
        "penalties_handling": "",
        "settlement_notes": "Loose market counterpart candidate; verify same market/outcome side and reject obvious market-type mismatches. Venue-specific fallback differences may remain.",
    }


def _summary_frame(
    *,
    event_pairs: pd.DataFrame,
    market_facts: pd.DataFrame,
    embeddings: pd.DataFrame,
    candidates: pd.DataFrame,
    reviewed: pd.DataFrame,
    coverage: pd.DataFrame,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    pairs_with_both_market_sides = 0
    if not coverage.empty and {"pm_market_rows", "ks_market_rows"}.issubset(coverage.columns):
        pairs_with_both_market_sides = int((coverage["pm_market_rows"].astype(int).gt(0) & coverage["ks_market_rows"].astype(int).gt(0)).sum())
    rows.append(_metric("approved_event_pairs", len(event_pairs)))
    rows.append(_metric("approved_event_pairs_iterated", len(coverage), len(event_pairs)))
    rows.append(_metric("approved_event_pairs_with_market_rows_on_both_sides", pairs_with_both_market_sides, len(event_pairs)))
    rows.append(_metric("scoped_market_fact_rows", len(market_facts)))
    if not market_facts.empty:
        rows.append(_metric("scoped_polymarket_fact_rows", int(market_facts["venue"].eq("polymarket").sum())))
        rows.append(_metric("scoped_kalshi_fact_rows", int(market_facts["venue"].eq("kalshi").sum())))
    rows.append(_metric("embedding_rows", len(embeddings), len(market_facts)))
    rows.append(_metric("candidate_rows", len(candidates)))
    reviewed_count = int(reviewed["ai_review_status"].astype(str).ne("").sum()) if "ai_review_status" in reviewed else 0
    rows.append(_metric("ai_reviewed_rows", reviewed_count, len(candidates)))
    if "ai_review_status" in reviewed:
        for status, count in reviewed[reviewed["ai_review_status"].astype(str).ne("")]["ai_review_status"].value_counts().items():
            rows.append(_metric(f"ai_review_status:{status}", int(count), reviewed_count))
    if "ai_recommendation" in reviewed:
        for recommendation, count in reviewed[reviewed["ai_recommendation"].astype(str).ne("")]["ai_recommendation"].value_counts().items():
            rows.append(_metric(f"ai_recommendation:{recommendation}", int(count), reviewed_count))
    return pd.DataFrame(rows, columns=["metric", "value", "denominator", "pct"])


def _valid_event_pairs(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=["pm_event_key", "ks_event_key", "pm_title", "ks_title"])
    data = frame.fillna("").copy()
    if "verdict" in data.columns:
        data = data[data["verdict"].astype(str).str.casefold().eq("valid")].copy()
        data["pm_event_key"] = data["pm_event_id"].astype(str)
        data["ks_event_key"] = data["ks_event_id"].astype(str)
        data["pm_title"] = data.get("pm_title", "")
        data["ks_title"] = data.get("ks_title", "")
    else:
        data = data[data["status"].astype(str).str.casefold().eq("approved")].copy()
        data["pm_event_key"] = data["source_event_key"].astype(str)
        data["ks_event_key"] = data["other_event_key"].astype(str)
        data["pm_title"] = data.get("source_event_title", "")
        data["ks_title"] = data.get("other_event_title", "")
    return data[["pm_event_key", "ks_event_key", "pm_title", "ks_title"]].drop_duplicates().reset_index(drop=True)


def _native_event_key(row: pd.Series | dict[str, Any]) -> str:
    payload = _json_payload(_row_get(row, "raw_payload"))
    venue = str(_row_get(row, "venue") or "").strip().casefold()
    if venue == "polymarket":
        return str(payload.get("_event_context_id") or "")
    if venue == "kalshi":
        return str(payload.get("_event_context_ticker") or payload.get("event_ticker") or "")
    return ""


def _market_key(row: pd.Series | dict[str, Any]) -> str:
    venue = str(_row_get(row, "venue") or "")
    market_id = str(_row_get(row, "market_id") or "")
    slug = str(_row_get(row, "ticker_or_slug") or "")
    yes_token_id = str(_row_get(row, "yes_token_id") or "")
    outcome = str(_row_get(row, "outcome_label") or _outcome_side(row) or "")
    return "|".join([venue, market_id or slug, yes_token_id, outcome])


def _outcome_side(row: pd.Series | dict[str, Any]) -> str:
    outcome = str(_row_get(row, "outcome_label") or "").strip()
    if outcome:
        return outcome
    outcomes = _parse_json_array(_row_get(row, "outcomes"))
    if outcomes:
        return str(outcomes[0])
    return "Yes"


def _second_outcome(value: Any) -> str:
    outcomes = _parse_json_array(value)
    return str(outcomes[1]) if len(outcomes) > 1 else "No"


def _raw_venue_fact_parts(payload: dict[str, Any]) -> dict[str, str]:
    values: list[str] = []
    for key in ("resolutionSource", "rules_primary", "rules_secondary", "early_close_condition"):
        if not _is_blank(payload.get(key)):
            values.append(f"{key}: {payload.get(key)}")
    settlement_sources = payload.get("settlement_sources")
    if settlement_sources:
        values.append(f"settlement_sources: {settlement_sources}")
    product_metadata = payload.get("product_metadata")
    if product_metadata:
        values.append(f"product_metadata: {product_metadata}")
    return {"source_or_resolution": " | ".join(values)}


def _close_or_expiration_text(row: pd.Series | dict[str, Any], payload: dict[str, Any]) -> str:
    values = []
    for key in ("close_time", "endDate", "expiration_time", "expected_expiration_time", "latest_expiration_time"):
        value = _row_get(row, key) if key == "close_time" else payload.get(key)
        if not _is_blank(value):
            values.append(f"{key}: {value}")
    return " | ".join(values)


def _embedding_row(
    fact: pd.Series | dict[str, Any],
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
        "venue": _row_get(fact, "venue") or "",
        "event_key": _row_get(fact, "_event_key") or "",
        "market_key": _row_get(fact, "_market_key") or "",
        "market_id": _row_get(fact, "market_id") or "",
        "ticker_or_slug": _row_get(fact, "ticker_or_slug") or "",
        "yes_token_id": _row_get(fact, "yes_token_id") or "",
        "outcome_label": _row_get(fact, "_outcome_side") or "",
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


def _embedding_vector_lookup(frame: pd.DataFrame) -> dict[str, list[float]]:
    output: dict[str, list[float]] = {}
    for _, row in frame.iterrows():
        key = str(row.get("embedding_key") or "")
        vector = _vector_from_value(row.get("embedding_vector"))
        if key and vector:
            output[key] = vector
    return output


def _embedding_key(row: pd.Series | dict[str, Any], text_hash: str) -> str:
    return "|".join([str(_row_get(row, "_market_key") or _market_key(row)), str(text_hash or "")])


def _embedding_cache_key(embedding_key: str, provider: str, model_name: str, embedding_dim: int) -> str:
    return "|".join([embedding_key, provider, model_name, str(int(embedding_dim))])


def _embedding_model_name(provider: str) -> str:
    return "gemini-embedding-2" if provider == "vertex-gemini" else "local-hashed-token-v1"


def _cosine(left: list[float], right: list[float]) -> float:
    limit = min(len(left), len(right))
    if limit == 0:
        return 0.0
    dot = 0.0
    left_norm = 0.0
    right_norm = 0.0
    for index in range(limit):
        left_value = float(left[index])
        right_value = float(right[index])
        dot += left_value * right_value
        left_norm += left_value * left_value
        right_norm += right_value * right_value
    if not left_norm or not right_norm:
        return 0.0
    return dot / math.sqrt(left_norm * right_norm)


def _normalized_matrix(vectors: list[list[float]]) -> np.ndarray:
    if not vectors:
        return np.empty((0, 0), dtype=np.float32)
    matrix = np.asarray(vectors, dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms


def _ensure_embedding_columns(frame: pd.DataFrame | None) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame(columns=EMBEDDING_COLUMNS)
    output = frame.copy()
    for column in EMBEDDING_COLUMNS:
        if column not in output.columns:
            output[column] = ""
    return output[EMBEDDING_COLUMNS].fillna("")


def _metric(metric: str, value: int | float, denominator: int | float | None = None) -> dict[str, Any]:
    if denominator in (None, "", 0):
        return {"metric": metric, "value": value, "denominator": "", "pct": ""}
    return {"metric": metric, "value": value, "denominator": str(denominator), "pct": f"{100.0 * float(value) / float(denominator):.2f}%"}


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


def _parse_json_array(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _row_get(row: pd.Series | dict[str, Any], key: str) -> Any:
    return row.get(key, "") if isinstance(row, dict) else row.get(key, "")


def _is_blank(value: Any) -> bool:
    return value is None or str(value).strip() == ""


def _slug(value: str) -> str:
    text = "".join(ch.lower() if ch.isalnum() else "-" for ch in value)
    return "-".join(part for part in text.split("-") if part)[:180]


if __name__ == "__main__":
    raise SystemExit(main())
