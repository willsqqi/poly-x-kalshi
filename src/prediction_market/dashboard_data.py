from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

import pandas as pd


DASHBOARD_TABLES = (
    "scanner_runs",
    "manual_mappings_snapshot",
    "orderbook_snapshots",
    "strategy_signals",
    "arbitrage_alerts",
    "approval_candidates",
    "suggested_mappings",
    "event_top_matches_gemini2",
    "market_embeddings",
    "reliable_pairs_for_monitoring",
    "pair_reliability_review",
)

HISTORICAL_TABLES = (
    "scanner_runs",
    "orderbook_snapshots",
    "strategy_signals",
    "arbitrage_alerts",
)

DEFAULT_SENSITIVITY_TOTAL_BUFFERS = (0.0, 0.01, 0.02, 0.03, 0.05)
DEFAULT_SENSITIVITY_MIN_EDGES = (0.0, 0.01, 0.02, 0.03)
DEFAULT_SENSITIVITY_MIN_DEPTHS = (0.0, 10.0, 100.0, 1000.0)


@dataclass(frozen=True)
class TableLoadResult:
    name: str
    frame: pd.DataFrame
    source_path: str | None
    error: str | None = None


def is_gcs_uri(value: str | Path) -> bool:
    return str(value).startswith("gs://")


def split_gcs_uri(uri: str) -> tuple[str, str]:
    if not uri.startswith("gs://"):
        raise ValueError(f"Not a GCS URI: {uri}")
    remainder = uri.removeprefix("gs://")
    bucket, _, prefix = remainder.partition("/")
    if not bucket:
        raise ValueError(f"GCS URI is missing a bucket: {uri}")
    return bucket, prefix.strip("/")


def table_candidates(source: str | Path, table_name: str, *, prefer_history: bool = False) -> list[str]:
    source_text = str(source).rstrip("/")
    if is_gcs_uri(source_text):
        historical = [
            f"{source_text}/processed/{table_name}.parquet",
            f"{source_text}/processed/{table_name}.csv",
        ]
        latest = [
            f"{source_text}/processed/latest/{table_name}.parquet",
            f"{source_text}/processed/latest/{table_name}.csv",
        ]
        return historical + latest if prefer_history else latest + historical

    root = Path(source_text)
    latest = [
        str(root / "processed" / "latest" / f"{table_name}.parquet"),
        str(root / "processed" / "latest" / f"{table_name}.csv"),
    ]
    historical = [
        str(root / "processed" / f"{table_name}.parquet"),
        str(root / "processed" / f"{table_name}.csv"),
    ]
    return historical + latest if prefer_history else latest + historical


def latest_table_candidates(source: str | Path, table_name: str) -> list[str]:
    return table_candidates(source, table_name)


def read_dashboard_table(source: str | Path, table_name: str, *, prefer_history: bool = False) -> TableLoadResult:
    errors: list[str] = []
    for path in table_candidates(source, table_name, prefer_history=prefer_history):
        try:
            frame = _read_table_path(path)
        except FileNotFoundError:
            continue
        except Exception as exc:  # pragma: no cover - defensive for malformed remote objects
            errors.append(f"{path}: {exc}")
            continue
        return TableLoadResult(name=table_name, frame=frame, source_path=path)
    return TableLoadResult(
        name=table_name,
        frame=pd.DataFrame(),
        source_path=None,
        error="; ".join(errors) if errors else "table not found",
    )


def load_dashboard_tables(
    source: str | Path,
    table_names: tuple[str, ...] = DASHBOARD_TABLES,
    *,
    prefer_history: bool = False,
) -> dict[str, TableLoadResult]:
    return {
        name: read_dashboard_table(source, name, prefer_history=prefer_history and name in HISTORICAL_TABLES)
        for name in table_names
    }


def summarize_dashboard(tables: dict[str, TableLoadResult]) -> dict[str, Any]:
    runs = _frame(tables, "scanner_runs")
    mappings = _frame(tables, "manual_mappings_snapshot")
    orderbooks = _frame(tables, "orderbook_snapshots")
    signals = _frame(tables, "strategy_signals")
    alerts = _frame(tables, "arbitrage_alerts")
    candidates = _frame(tables, "approval_candidates")
    suggestions = _frame(tables, "suggested_mappings")

    scored = _coerce_bool_column(signals, "is_alert")
    pair_recommendations = build_pair_recommendations(scored)
    alert_rows = scored[scored["is_alert"] == True] if "is_alert" in scored.columns else scored.iloc[0:0]  # noqa: E712

    latest_run = runs.sort_values("finished_at").tail(1).to_dict("records")[0] if not runs.empty and "finished_at" in runs.columns else {}
    approved_count = _approved_mapping_count(mappings)

    near_misses = scored.copy()
    if "net_edge" in near_misses.columns:
        near_misses["net_edge"] = pd.to_numeric(near_misses["net_edge"], errors="coerce")
        near_misses = near_misses.sort_values("net_edge", ascending=False)

    return {
        "run_count": len(runs),
        "latest_run": latest_run,
        "approved_mapping_count": approved_count,
        "candidate_count": len(candidates),
        "suggested_mapping_count": len(suggestions),
        "orderbook_count": len(orderbooks),
        "signal_count": len(scored),
        "alert_count": len(alert_rows),
        "near_misses": near_misses.head(25),
        "exclusion_counts": _value_counts(scored, "exclusion_reason"),
        "venue_counts": _value_counts(orderbooks, "venue"),
        "viability_summary": build_viability_summary(scored),
        "pair_viability": build_pair_viability(scored),
        "sensitivity_grid": build_sensitivity_grid(scored),
        "pair_recommendations": pair_recommendations,
        "coverage_funnel": build_coverage_funnel(candidates, suggestions, mappings, pair_recommendations),
        "keyword_coverage": build_keyword_coverage(candidates),
        "run_trend": build_run_trend(runs),
    }


def build_coverage_funnel(
    candidates: pd.DataFrame,
    suggestions: pd.DataFrame,
    mappings: pd.DataFrame,
    recommendations: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Summarize discovery-to-monitoring coverage by market type."""
    columns = [
        "market_type",
        "candidate_count",
        "polymarket_candidates",
        "kalshi_candidates",
        "suggested_mapping_count",
        "approved_mapping_count",
        "monitor_count",
        "watch_count",
        "pause_count",
        "needs_more_data_count",
        "suggestion_rate",
        "approval_rate",
    ]
    if candidates.empty and suggestions.empty and mappings.empty:
        return pd.DataFrame(columns=columns)

    candidate_counts = _candidate_market_type_counts(candidates)
    suggestion_counts = _market_type_mapping_counts(suggestions)
    approved_counts = _approved_market_type_counts(mappings, suggestions)
    recommendation_counts = _recommendation_market_type_counts(recommendations, suggestions)

    market_types = sorted(
        set(candidate_counts.index)
        | set(suggestion_counts.index)
        | set(approved_counts.index)
        | set(recommendation_counts.index)
    )
    rows: list[dict[str, Any]] = []
    for market_type in market_types:
        candidate_row = candidate_counts.loc[market_type] if market_type in candidate_counts.index else pd.Series(dtype=float)
        suggested_count = int(suggestion_counts.get(market_type, 0))
        approved_count = int(approved_counts.get(market_type, 0))
        recommendation_row = (
            recommendation_counts.loc[market_type]
            if market_type in recommendation_counts.index
            else pd.Series(dtype=float)
        )
        candidate_count = int(candidate_row.get("candidate_count", 0))
        rows.append(
            {
                "market_type": market_type,
                "candidate_count": candidate_count,
                "polymarket_candidates": int(candidate_row.get("polymarket_candidates", 0)),
                "kalshi_candidates": int(candidate_row.get("kalshi_candidates", 0)),
                "suggested_mapping_count": suggested_count,
                "approved_mapping_count": approved_count,
                "monitor_count": int(recommendation_row.get("monitor", 0)),
                "watch_count": int(recommendation_row.get("watch", 0)),
                "pause_count": int(recommendation_row.get("pause", 0)),
                "needs_more_data_count": int(recommendation_row.get("needs_more_data", 0)),
                "suggestion_rate": suggested_count / candidate_count if candidate_count else 0.0,
                "approval_rate": approved_count / suggested_count if suggested_count else 0.0,
            }
        )
    return pd.DataFrame(rows, columns=columns).sort_values(
        ["candidate_count", "suggested_mapping_count", "approved_mapping_count"],
        ascending=[False, False, False],
    )


def build_keyword_coverage(candidates: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "keyword",
        "candidate_count",
        "polymarket_candidates",
        "kalshi_candidates",
        "match_winner_candidates",
        "total_candidates",
        "spread_candidates",
        "other_candidates",
    ]
    if candidates.empty or "keyword_hits" not in candidates.columns:
        return pd.DataFrame(columns=columns)

    rows: list[dict[str, Any]] = []
    for index, row in candidates.iterrows():
        keywords = _split_keywords(row.get("keyword_hits"))
        if not keywords:
            keywords = ["missing"]
        for keyword in keywords:
            rows.append(
                {
                    "_candidate_index": index,
                    "keyword": keyword,
                    "venue": _normalize_label(row.get("venue"), "unknown"),
                    "market_type": _normalize_label(row.get("market_type"), "unknown"),
                }
            )
    exploded = pd.DataFrame(rows)
    if exploded.empty:
        return pd.DataFrame(columns=columns)

    output_rows: list[dict[str, Any]] = []
    for keyword, group in exploded.groupby("keyword", dropna=False):
        venue_counts = group["venue"].value_counts()
        market_type_counts = group["market_type"].value_counts()
        total = int(group["_candidate_index"].nunique())
        typed_total = int(
            market_type_counts.get("match_winner", 0)
            + market_type_counts.get("total", 0)
            + market_type_counts.get("spread", 0)
        )
        output_rows.append(
            {
                "keyword": keyword,
                "candidate_count": total,
                "polymarket_candidates": int(venue_counts.get("polymarket", 0)),
                "kalshi_candidates": int(venue_counts.get("kalshi", 0)),
                "match_winner_candidates": int(market_type_counts.get("match_winner", 0)),
                "total_candidates": int(market_type_counts.get("total", 0)),
                "spread_candidates": int(market_type_counts.get("spread", 0)),
                "other_candidates": max(total - typed_total, 0),
            }
        )
    return pd.DataFrame(output_rows, columns=columns).sort_values("candidate_count", ascending=False)


def build_viability_summary(signals: pd.DataFrame) -> dict[str, Any]:
    scored = _prepare_signal_frame(signals)
    if scored.empty:
        return {
            "snapshot_count": 0,
            "pair_count": 0,
            "direction_count": 0,
            "alert_count": 0,
            "alert_rate": 0.0,
            "positive_edge_count": 0,
            "positive_edge_rate": 0.0,
            "best_net_edge": None,
            "median_net_edge": None,
            "p95_net_edge": None,
            "liquidity_ok_rate": None,
            "price_available_rate": None,
        }

    net_edge = pd.to_numeric(scored.get("net_edge"), errors="coerce") if "net_edge" in scored.columns else pd.Series(dtype=float)
    alert_count = int(scored["is_alert"].sum()) if "is_alert" in scored.columns else 0
    positive_edge_count = int((net_edge > 0).sum()) if not net_edge.empty else 0

    return {
        "snapshot_count": scored["run_id"].nunique() if "run_id" in scored.columns else 0,
        "pair_count": scored["mapping_id"].nunique() if "mapping_id" in scored.columns else 0,
        "direction_count": len(scored),
        "alert_count": alert_count,
        "alert_rate": alert_count / len(scored) if len(scored) else 0.0,
        "positive_edge_count": positive_edge_count,
        "positive_edge_rate": positive_edge_count / len(scored) if len(scored) else 0.0,
        "best_net_edge": _nullable_float(net_edge.max()) if not net_edge.empty else None,
        "median_net_edge": _nullable_float(net_edge.median()) if not net_edge.empty else None,
        "p95_net_edge": _nullable_float(net_edge.quantile(0.95)) if not net_edge.empty else None,
        "liquidity_ok_rate": _bool_rate(scored, "liquidity_ok"),
        "price_available_rate": _bool_rate(scored, "price_available"),
    }


def build_pair_viability(signals: pd.DataFrame) -> pd.DataFrame:
    scored = _prepare_signal_frame(signals)
    if scored.empty or "mapping_id" not in scored.columns:
        return pd.DataFrame(
            columns=[
                "mapping_id",
                "event_name",
                "proposition",
                "observations",
                "snapshots",
                "alert_count",
                "positive_edge_count",
                "best_net_edge",
                "median_net_edge",
                "latest_net_edge",
                "latest_exclusion_reason",
                "worst_min_depth",
            ]
        )

    scored["net_edge"] = pd.to_numeric(scored.get("net_edge"), errors="coerce")
    scored["min_depth"] = pd.to_numeric(scored.get("min_depth"), errors="coerce") if "min_depth" in scored.columns else pd.NA
    if "detected_at" in scored.columns:
        scored["detected_at"] = pd.to_datetime(scored["detected_at"], errors="coerce")
        scored = scored.sort_values(["mapping_id", "detected_at"])

    group = scored.groupby("mapping_id", dropna=False)
    output = group.agg(
        event_name=("event_name", "last") if "event_name" in scored.columns else ("mapping_id", "last"),
        proposition=("proposition", "last") if "proposition" in scored.columns else ("mapping_id", "last"),
        observations=("mapping_id", "size"),
        snapshots=("run_id", "nunique") if "run_id" in scored.columns else ("mapping_id", "size"),
        alert_count=("is_alert", "sum") if "is_alert" in scored.columns else ("mapping_id", "size"),
        positive_edge_count=("net_edge", lambda value: int((value > 0).sum())),
        best_net_edge=("net_edge", "max"),
        median_net_edge=("net_edge", "median"),
        latest_net_edge=("net_edge", "last"),
        latest_exclusion_reason=("exclusion_reason", "last") if "exclusion_reason" in scored.columns else ("mapping_id", "last"),
        worst_min_depth=("min_depth", "min"),
    ).reset_index()
    output["alert_rate"] = output["alert_count"] / output["observations"]
    output["positive_edge_rate"] = output["positive_edge_count"] / output["observations"]
    return output.sort_values(["best_net_edge", "positive_edge_count"], ascending=[False, False])


def build_sensitivity_grid(
    signals: pd.DataFrame,
    *,
    total_buffers: tuple[float, ...] = DEFAULT_SENSITIVITY_TOTAL_BUFFERS,
    min_net_edges: tuple[float, ...] = DEFAULT_SENSITIVITY_MIN_EDGES,
    min_depths: tuple[float, ...] = DEFAULT_SENSITIVITY_MIN_DEPTHS,
) -> pd.DataFrame:
    scored = _prepare_signal_frame(signals)
    if scored.empty or "gross_cost" not in scored.columns:
        return pd.DataFrame(
            columns=[
                "total_buffer",
                "min_net_edge",
                "min_depth",
                "eligible_signals",
                "eligible_rate",
                "eligible_pairs",
                "price_rows",
                "best_repriced_net_edge",
            ]
        )

    gross_cost = pd.to_numeric(scored["gross_cost"], errors="coerce")
    min_depth_series = (
        pd.to_numeric(scored["min_depth"], errors="coerce")
        if "min_depth" in scored.columns
        else pd.Series(0.0, index=scored.index)
    )
    price_mask = gross_cost.notna()
    price_rows = int(price_mask.sum())
    rows: list[dict[str, Any]] = []
    for total_buffer in total_buffers:
        repriced_net_edge = 1.0 - gross_cost - total_buffer
        for min_net_edge in min_net_edges:
            threshold_mask = repriced_net_edge >= min_net_edge
            for min_depth in min_depths:
                eligible_mask = price_mask & threshold_mask & (min_depth_series >= min_depth)
                eligible = scored[eligible_mask]
                rows.append(
                    {
                        "total_buffer": total_buffer,
                        "min_net_edge": min_net_edge,
                        "min_depth": min_depth,
                        "eligible_signals": int(eligible_mask.sum()),
                        "eligible_rate": float(eligible_mask.sum() / price_rows) if price_rows else 0.0,
                        "eligible_pairs": int(eligible["mapping_id"].nunique()) if "mapping_id" in eligible.columns else 0,
                        "price_rows": price_rows,
                        "best_repriced_net_edge": _nullable_float(repriced_net_edge[price_mask].max()) if price_rows else None,
                    }
                )
    return pd.DataFrame(rows).sort_values(
        ["eligible_signals", "total_buffer", "min_net_edge", "min_depth"],
        ascending=[False, True, True, True],
    )


def build_pair_recommendations(signals: pd.DataFrame) -> pd.DataFrame:
    pair_viability = build_pair_viability(signals)
    if pair_viability.empty:
        return pd.DataFrame(
            columns=[
                "mapping_id",
                "event_name",
                "proposition",
                "recommendation",
                "reason",
                "confidence",
                "snapshots",
                "alert_rate",
                "positive_edge_rate",
                "best_net_edge",
                "worst_min_depth",
            ]
        )

    output = pair_viability.copy()
    recommendations = output.apply(_recommend_pair_action, axis=1, result_type="expand")
    output = pd.concat([output, recommendations], axis=1)
    order = {"monitor": 0, "watch": 1, "pause": 2, "needs_more_data": 3}
    output["_recommendation_rank"] = output["recommendation"].map(order).fillna(99)
    return (
        output.sort_values(["_recommendation_rank", "best_net_edge", "positive_edge_rate"], ascending=[True, False, False])
        .drop(columns=["_recommendation_rank"])
        .reset_index(drop=True)
    )


def build_run_trend(runs: pd.DataFrame) -> pd.DataFrame:
    if runs.empty:
        return pd.DataFrame()
    output = runs.copy()
    for column in ("started_at", "finished_at"):
        if column in output.columns:
            output[column] = pd.to_datetime(output[column], errors="coerce")
    for column in ("candidate_count", "approved_mapping_count", "orderbook_count", "alert_count"):
        if column in output.columns:
            output[column] = pd.to_numeric(output[column], errors="coerce")
    if "finished_at" in output.columns:
        output = output.sort_values("finished_at")
    return output


def _candidate_market_type_counts(candidates: pd.DataFrame) -> pd.DataFrame:
    if candidates.empty:
        return pd.DataFrame(columns=["candidate_count", "polymarket_candidates", "kalshi_candidates"])
    output = candidates.copy()
    output["market_type"] = output.get("market_type", "unknown")
    output["market_type"] = output["market_type"].map(lambda value: _normalize_label(value, "unknown"))
    output["venue"] = output.get("venue", "unknown")
    output["venue"] = output["venue"].map(lambda value: _normalize_label(value, "unknown"))
    grouped = output.groupby("market_type", dropna=False)
    counts = grouped.size().rename("candidate_count").to_frame()
    venue_counts = output.pivot_table(index="market_type", columns="venue", aggfunc="size", fill_value=0)
    for venue in ("polymarket", "kalshi"):
        counts[f"{venue}_candidates"] = venue_counts[venue] if venue in venue_counts.columns else 0
    return counts


def _market_type_mapping_counts(frame: pd.DataFrame) -> pd.Series:
    if frame.empty:
        return pd.Series(dtype=int)
    output = frame.copy()
    output["market_type"] = output.get("market_type", "unknown")
    output["market_type"] = output["market_type"].map(lambda value: _normalize_label(value, "unknown"))
    if "mapping_id" in output.columns:
        output = output.dropna(subset=["mapping_id"]).drop_duplicates("mapping_id")
    return output["market_type"].value_counts()


def _approved_market_type_counts(mappings: pd.DataFrame, suggestions: pd.DataFrame) -> pd.Series:
    if mappings.empty:
        return pd.Series(dtype=int)
    approved = mappings.copy()
    if "is_approved" in approved.columns:
        approved = _coerce_bool_column(approved, "is_approved")
        approved = approved[approved["is_approved"] == True]  # noqa: E712
    elif "status" in approved.columns:
        approved = approved[approved["status"].fillna("").astype(str).str.casefold() == "approved"]
    if approved.empty:
        return pd.Series(dtype=int)
    approved = _attach_market_type_from_suggestions(approved, suggestions)
    return _market_type_mapping_counts(approved)


def _recommendation_market_type_counts(recommendations: pd.DataFrame | None, suggestions: pd.DataFrame) -> pd.DataFrame:
    if recommendations is None or recommendations.empty or "recommendation" not in recommendations.columns:
        return pd.DataFrame()
    output = _attach_market_type_from_suggestions(recommendations.copy(), suggestions)
    output["market_type"] = output["market_type"].map(lambda value: _normalize_label(value, "unknown"))
    output["recommendation"] = output["recommendation"].map(lambda value: _normalize_label(value, "missing"))
    if "mapping_id" in output.columns:
        output = output.dropna(subset=["mapping_id"]).drop_duplicates("mapping_id")
    return output.pivot_table(index="market_type", columns="recommendation", aggfunc="size", fill_value=0)


def _attach_market_type_from_suggestions(frame: pd.DataFrame, suggestions: pd.DataFrame) -> pd.DataFrame:
    output = frame.copy()
    if "market_type" in output.columns:
        output["market_type"] = output.apply(_market_type_or_infer, axis=1)
        return output
    if "mapping_id" not in output.columns or suggestions.empty or "mapping_id" not in suggestions.columns:
        output["market_type"] = output.apply(_infer_market_type_from_row, axis=1)
        return output
    if "market_type" not in suggestions.columns:
        output["market_type"] = output.apply(_infer_market_type_from_row, axis=1)
        return output
    market_types = (
        suggestions[["mapping_id", "market_type"]]
        .dropna(subset=["mapping_id"])
        .drop_duplicates("mapping_id")
        .assign(market_type=lambda frame_: frame_["market_type"].map(lambda value: _normalize_label(value, "unknown")))
    )
    merged = output.merge(market_types, on="mapping_id", how="left")
    merged["market_type"] = merged.apply(_market_type_or_infer, axis=1)
    return merged


def _market_type_or_infer(row: pd.Series) -> str:
    market_type = _normalize_label(row.get("market_type"), "unknown")
    return _infer_market_type_from_row(row) if market_type == "unknown" else market_type


def _infer_market_type_from_row(row: pd.Series) -> str:
    text = " ".join(
        str(row.get(column) or "")
        for column in ("mapping_id", "event_name", "proposition", "polymarket_slug", "kalshi_ticker", "kalshi_title")
    ).casefold()
    if any(token in text for token in (" to win", " win in ", "-tie", " end in a draw", " match winner")):
        return "match_winner"
    if any(token in text for token in (" over ", " under ", " total")):
        return "total"
    if any(token in text for token in (" spread", " handicap")):
        return "spread"
    return "unknown"


def _recommend_pair_action(row: pd.Series) -> pd.Series:
    snapshots = int(row.get("snapshots") or 0)
    alert_rate = _safe_float(row.get("alert_rate")) or 0.0
    positive_edge_rate = _safe_float(row.get("positive_edge_rate")) or 0.0
    best_net_edge = _safe_float(row.get("best_net_edge"))
    worst_min_depth = _safe_float(row.get("worst_min_depth")) or 0.0
    latest_reason = str(row.get("latest_exclusion_reason") or "")

    if snapshots < 3:
        return pd.Series(
            {
                "recommendation": "needs_more_data",
                "reason": "fewer_than_3_snapshots",
                "confidence": "low",
            }
        )
    if alert_rate > 0:
        return pd.Series(
            {
                "recommendation": "monitor",
                "reason": "conservative_alert_observed",
                "confidence": _sample_confidence(snapshots),
            }
        )
    if positive_edge_rate > 0 and worst_min_depth >= 10:
        return pd.Series(
            {
                "recommendation": "watch",
                "reason": "positive_edges_without_full_alert",
                "confidence": _sample_confidence(snapshots),
            }
        )
    if best_net_edge is not None and best_net_edge >= -0.01 and worst_min_depth >= 10:
        return pd.Series(
            {
                "recommendation": "watch",
                "reason": "near_break_even_under_current_prices",
                "confidence": _sample_confidence(snapshots),
            }
        )
    if latest_reason == "insufficient_depth" or worst_min_depth < 10:
        return pd.Series(
            {
                "recommendation": "pause",
                "reason": "insufficient_depth",
                "confidence": _sample_confidence(snapshots),
            }
        )
    return pd.Series(
        {
            "recommendation": "pause",
            "reason": "consistently_below_edge_threshold",
            "confidence": _sample_confidence(snapshots),
        }
    )


def _sample_confidence(snapshots: int) -> str:
    if snapshots >= 20:
        return "high"
    if snapshots >= 3:
        return "medium"
    return "low"


def filter_signals(
    signals: pd.DataFrame,
    *,
    search: str = "",
    min_net_edge: float | None = None,
    alerts_only: bool = False,
) -> pd.DataFrame:
    output = signals.copy()
    if output.empty:
        return output
    if "net_edge" in output.columns:
        output["net_edge"] = pd.to_numeric(output["net_edge"], errors="coerce")
    if alerts_only and "is_alert" in output.columns:
        output = _coerce_bool_column(output, "is_alert")
        output = output[output["is_alert"] == True]  # noqa: E712
    if min_net_edge is not None and "net_edge" in output.columns:
        output = output[output["net_edge"] >= min_net_edge]
    if search:
        needle = search.casefold()
        searchable_columns = [column for column in ("event_name", "proposition", "mapping_id", "direction") if column in output.columns]
        if searchable_columns:
            mask = pd.Series(False, index=output.index)
            for column in searchable_columns:
                mask = mask | output[column].fillna("").astype(str).str.casefold().str.contains(needle, regex=False)
            output = output[mask]
    if "net_edge" in output.columns:
        output = output.sort_values("net_edge", ascending=False)
    return output


def pair_history(signals: pd.DataFrame, mapping_id: str) -> pd.DataFrame:
    if signals.empty or not mapping_id or "mapping_id" not in signals.columns:
        return pd.DataFrame()
    history = signals[signals["mapping_id"] == mapping_id].copy()
    if history.empty:
        return history
    if "detected_at" in history.columns:
        history["detected_at"] = pd.to_datetime(history["detected_at"], errors="coerce")
    if "net_edge" in history.columns:
        history["net_edge"] = pd.to_numeric(history["net_edge"], errors="coerce")
    sort_columns = [column for column in ("detected_at", "direction") if column in history.columns]
    return history.sort_values(sort_columns) if sort_columns else history


def _prepare_signal_frame(signals: pd.DataFrame) -> pd.DataFrame:
    output = signals.copy()
    if output.empty:
        return output
    output = _coerce_bool_column(output, "is_alert")
    for column in ("price_available", "liquidity_ok", "threshold_ok"):
        output = _coerce_bool_column(output, column)
    return output


def _read_table_path(path: str) -> pd.DataFrame:
    if is_gcs_uri(path):
        return _read_gcs_table(path)

    local_path = Path(path)
    if not local_path.exists():
        raise FileNotFoundError(path)
    if local_path.suffix == ".parquet":
        return pd.read_parquet(local_path)
    if local_path.suffix == ".csv":
        return pd.read_csv(local_path)
    raise ValueError(f"Unsupported table extension: {path}")


def _read_gcs_table(uri: str) -> pd.DataFrame:
    try:
        from google.cloud import storage
        from google.api_core.exceptions import NotFound
    except ImportError as exc:  # pragma: no cover - exercised only without optional gcp extra
        raise RuntimeError('Install the GCP extra first: pip install -e ".[gcp]"') from exc

    bucket_name, blob_name = split_gcs_uri(uri)
    client = storage.Client()
    blob = client.bucket(bucket_name).blob(blob_name)
    try:
        payload = blob.download_as_bytes()
    except NotFound as exc:
        raise FileNotFoundError(uri) from exc

    buffer = BytesIO(payload)
    if uri.endswith(".parquet"):
        return pd.read_parquet(buffer)
    if uri.endswith(".csv"):
        return pd.read_csv(buffer)
    raise ValueError(f"Unsupported table extension: {uri}")


def _frame(tables: dict[str, TableLoadResult], name: str) -> pd.DataFrame:
    result = tables.get(name)
    return result.frame if result is not None else pd.DataFrame()


def _coerce_bool_column(frame: pd.DataFrame, column: str) -> pd.DataFrame:
    output = frame.copy()
    if column not in output.columns:
        return output
    if output[column].dtype == bool:
        return output
    output[column] = output[column].astype(str).str.lower().isin(("1", "true", "yes", "y"))
    return output


def _approved_mapping_count(frame: pd.DataFrame) -> int:
    if frame.empty:
        return 0
    if "is_approved" in frame.columns:
        return int(_coerce_bool_column(frame, "is_approved")["is_approved"].sum())
    if "status" in frame.columns:
        return int((frame["status"].fillna("").astype(str).str.casefold() == "approved").sum())
    return len(frame)


def _value_counts(frame: pd.DataFrame, column: str) -> pd.DataFrame:
    if frame.empty or column not in frame.columns:
        return pd.DataFrame(columns=[column, "count"])
    counts = frame[column].fillna("missing").astype(str).value_counts().reset_index()
    counts.columns = [column, "count"]
    return counts


def _bool_rate(frame: pd.DataFrame, column: str) -> float | None:
    if column not in frame.columns or frame.empty:
        return None
    coerced = _coerce_bool_column(frame, column)
    return float(coerced[column].sum() / len(coerced))


def _nullable_float(value: Any) -> float | None:
    if pd.isna(value):
        return None
    return float(value)


def _safe_float(value: Any) -> float | None:
    try:
        if pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_label(value: Any, default: str) -> str:
    if value is None or pd.isna(value):
        return default
    text = str(value).strip().casefold()
    return text or default


def _split_keywords(value: Any) -> list[str]:
    if value is None or pd.isna(value):
        return []
    return [
        part.strip().casefold()
        for part in str(value).replace(";", ",").split(",")
        if part.strip()
    ]
