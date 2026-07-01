from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any

import pandas as pd

from .fifa_arbitrage import MAPPING_COLUMNS


ACTIVE_CANDIDATE_STATUSES = {"active", "open", "trading"}
ACTIVE_MAPPING_LIFECYCLES = {"", "active", "open", "trading"}
SEARCH_COLUMNS = (
    "title",
    "subtitle",
    "event_title",
    "outcome_label",
    "subject",
    "market_type",
    "category",
    "market_id",
    "ticker_or_slug",
    "outcomes",
    "rules_text",
    "settlement_summary",
    "keyword_hits",
)
EVENT_SEARCH_COLUMNS = (
    "event_title",
    "event_date",
    "market_types",
    "outcomes_sample",
    "titles_sample",
    "settlement_sample",
    "rules_sample",
    "market_ids",
    "tickers_or_slugs",
)
EVENT_TOP_MATCH_COLUMNS = (
    "pm_event_key",
    "pm_event_title",
    "pm_row_count",
    "pm_market_types",
    "pm_sample_outcomes",
    "rank",
    "kalshi_event_key",
    "kalshi_event_title",
    "kalshi_row_count",
    "kalshi_market_types",
    "kalshi_sample_outcomes",
    "event_embedding_score",
)
REQUIRED_APPROVAL_FIELDS = (
    "draw_handling",
    "extra_time_handling",
    "penalties_handling",
    "settlement_notes",
    "reviewer",
)
EVENT_PAIR_REVIEW_COLUMNS = (
    "event_pair_id",
    "status",
    "lifecycle_status",
    "source_venue",
    "source_event_key",
    "source_event_title",
    "source_event_date",
    "source_market_types",
    "source_sample_outcomes",
    "source_settlement_sample",
    "source_rules_sample",
    "other_venue",
    "other_event_key",
    "other_event_title",
    "other_event_date",
    "other_market_types",
    "other_sample_outcomes",
    "other_settlement_sample",
    "other_rules_sample",
    "event_score",
    "rank",
    "reviewer",
    "reviewed_at",
    "notes",
)


def default_review_mapping_path(source: str | Path) -> str:
    source_text = str(source).rstrip("/")
    if _is_gcs_uri(source_text):
        return f"{source_text}/manual_review/approved_mappings/current.csv"
    return str(Path(source_text) / "manual_review" / "approved_mappings" / "current.csv")


def default_event_pair_review_path(source: str | Path) -> str:
    source_text = str(source).rstrip("/")
    if _is_gcs_uri(source_text):
        return f"{source_text}/manual_review/approved_event_pairs/current.csv"
    return str(Path(source_text) / "manual_review" / "approved_event_pairs" / "current.csv")


def load_review_mappings(path: str | Path) -> pd.DataFrame:
    try:
        frame = _read_csv(path)
    except FileNotFoundError:
        return pd.DataFrame(columns=MAPPING_COLUMNS)
    return _ensure_mapping_columns(frame)


def save_review_mapping(path: str | Path, row: dict[str, Any]) -> dict[str, str]:
    current = load_review_mappings(path)
    mapping_id = str(row.get("mapping_id") or "").strip()
    if not mapping_id:
        raise ValueError("mapping_id is required")

    row_frame = _ensure_mapping_columns(pd.DataFrame([row]))
    if current.empty:
        updated = row_frame
    else:
        current = current[current["mapping_id"].astype(str) != mapping_id]
        updated = pd.concat([current, row_frame], ignore_index=True)
    updated = updated[MAPPING_COLUMNS].fillna("")

    _write_csv(path, updated)
    history_path = _history_path(path, str(row.get("reviewed_at") or utc_now_compact()))
    _write_csv(history_path, updated)
    return {"current": str(path), "history": history_path}


def load_event_pair_reviews(path: str | Path) -> pd.DataFrame:
    try:
        frame = _read_csv(path)
    except FileNotFoundError:
        return pd.DataFrame(columns=EVENT_PAIR_REVIEW_COLUMNS)
    return _ensure_event_pair_review_columns(frame)


def save_event_pair_review(path: str | Path, row: dict[str, Any]) -> dict[str, str]:
    current = load_event_pair_reviews(path)
    event_pair_id = str(row.get("event_pair_id") or "").strip()
    if not event_pair_id:
        raise ValueError("event_pair_id is required")

    row_frame = _ensure_event_pair_review_columns(pd.DataFrame([row]))
    status = str(row.get("status") or "").strip().casefold()
    if current.empty:
        updated = row_frame
    else:
        current = current[current["event_pair_id"].astype(str) != event_pair_id]
        if status == "approved":
            source_key = str(row.get("source_event_key") or "").strip()
            other_key = str(row.get("other_event_key") or "").strip()
            lifecycle = current["lifecycle_status"].fillna("").astype(str).str.strip().str.casefold()
            conflict = pd.Series(False, index=current.index)
            if source_key:
                conflict |= current["source_event_key"].fillna("").astype(str).eq(source_key)
            if other_key:
                conflict |= current["other_event_key"].fillna("").astype(str).eq(other_key)
            current = current[~(conflict & lifecycle.isin(ACTIVE_MAPPING_LIFECYCLES))]
        updated = pd.concat([current, row_frame], ignore_index=True)
    updated = updated[list(EVENT_PAIR_REVIEW_COLUMNS)].fillna("")

    _write_csv(path, updated)
    history_path = _history_path(path, str(row.get("reviewed_at") or utc_now_compact()))
    _write_csv(history_path, updated)
    return {"current": str(path), "history": history_path}


def event_pair_review_errors(row: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    status = str(row.get("status") or "").strip().casefold()
    if status not in {"approved", "rejected", "needs_review", "no_match"}:
        errors.append("status must be approved, rejected, needs_review, or no_match")
    required = ["event_pair_id", "source_event_key", "source_event_title"]
    if status != "no_match":
        required.extend(["other_event_key", "other_event_title"])
    for column in required:
        if not str(row.get(column) or "").strip():
            errors.append(f"{column} is required")
    if not str(row.get("reviewer") or "").strip():
        errors.append("reviewer is required")
    return errors


def build_event_pair_review_row(
    source_event: pd.Series | dict[str, Any],
    other_event: pd.Series | dict[str, Any] | None,
    *,
    status: str,
    reviewer: str,
    notes: str = "",
    event_match: pd.Series | dict[str, Any] | None = None,
    reviewed_at: str | None = None,
) -> dict[str, Any]:
    source_key = _review_event_identifier(source_event)
    other_key = _review_event_identifier(other_event) if other_event is not None else ""
    reviewed_at = reviewed_at or utc_now_iso()
    event_score = ""
    rank = ""
    if event_match is not None:
        event_score = str(_row_get(event_match, "event_score") or _row_get(event_match, "event_embedding_score") or "")
        rank = str(_row_get(event_match, "rank") or "")
    event_pair_id = "__".join([_slugify(source_key), _slugify(other_key or "no-match")]).strip("_")
    return {
        "event_pair_id": event_pair_id,
        "status": status.strip().casefold(),
        "lifecycle_status": "active",
        "source_venue": str(_row_get(source_event, "venue") or ""),
        "source_event_key": source_key,
        "source_event_title": str(_row_get(source_event, "event_title") or ""),
        "source_event_date": str(_row_get(source_event, "event_date") or ""),
        "source_market_types": str(_row_get(source_event, "market_types") or ""),
        "source_sample_outcomes": str(_row_get(source_event, "outcomes_sample") or ""),
        "source_settlement_sample": str(_row_get(source_event, "settlement_sample") or ""),
        "source_rules_sample": str(_row_get(source_event, "rules_sample") or ""),
        "other_venue": str(_row_get(other_event, "venue") or "") if other_event is not None else "",
        "other_event_key": other_key,
        "other_event_title": str(_row_get(other_event, "event_title") or "") if other_event is not None else "",
        "other_event_date": str(_row_get(other_event, "event_date") or "") if other_event is not None else "",
        "other_market_types": str(_row_get(other_event, "market_types") or "") if other_event is not None else "",
        "other_sample_outcomes": str(_row_get(other_event, "outcomes_sample") or "") if other_event is not None else "",
        "other_settlement_sample": str(_row_get(other_event, "settlement_sample") or "") if other_event is not None else "",
        "other_rules_sample": str(_row_get(other_event, "rules_sample") or "") if other_event is not None else "",
        "event_score": event_score,
        "rank": rank,
        "reviewer": reviewer.strip(),
        "reviewed_at": reviewed_at,
        "notes": notes.strip(),
    }


def event_pair_review_status_by_source_event(reviews: pd.DataFrame) -> dict[str, str]:
    if reviews.empty:
        return {}
    frame = _ensure_event_pair_review_columns(reviews)
    output: dict[str, str] = {}
    for _, row in frame.iterrows():
        source_key = str(row.get("source_event_key") or "").strip()
        status = str(row.get("status") or "").strip()
        lifecycle = str(row.get("lifecycle_status") or "").strip().casefold()
        if source_key and lifecycle in ACTIVE_MAPPING_LIFECYCLES:
            output[source_key] = status
    return output


def filter_review_candidates(
    candidates: pd.DataFrame,
    venue: str,
    search: str = "",
    mappings: pd.DataFrame | None = None,
    *,
    active_only: bool = True,
    hide_approved: bool = True,
    now: datetime | str | None = None,
) -> pd.DataFrame:
    if candidates.empty:
        return pd.DataFrame(columns=candidates.columns)
    output = candidates.copy()
    if "venue" in output.columns:
        output = output[output["venue"].fillna("").astype(str).str.casefold() == venue.casefold()]
    if active_only:
        output = output[output.apply(lambda row: is_active_candidate(row, now=now), axis=1)]
    if hide_approved and mappings is not None and not mappings.empty:
        approved_keys = approved_candidate_keys(mappings)
        output = output[~output.apply(lambda row: candidate_key(row) in approved_keys, axis=1)]
    if search.strip():
        terms = [term for term in re.split(r"\s+", search.casefold().strip()) if term]
        output = output[output.apply(lambda row: _matches_terms(row, terms), axis=1)]
    sort_columns = [column for column in ("event_date", "close_time", "event_title", "title", "outcome_label") if column in output.columns]
    if sort_columns:
        output = output.sort_values(sort_columns, na_position="last")
    return output.reset_index(drop=True)


def filter_review_events(
    candidates: pd.DataFrame,
    venue: str,
    search: str = "",
    mappings: pd.DataFrame | None = None,
    *,
    active_only: bool = True,
    hide_approved: bool = True,
    now: datetime | str | None = None,
) -> pd.DataFrame:
    """Group active candidate rows into venue-level events for review."""
    rows = filter_review_candidates(
        candidates,
        venue,
        "",
        mappings=mappings,
        active_only=active_only,
        hide_approved=hide_approved,
        now=now,
    )
    columns = [
        "venue",
        "event_key",
        "event_title",
        "event_date",
        "close_time",
        "market_count",
        "outcome_count",
        "market_types",
        "outcomes_sample",
        "titles_sample",
        "market_ids",
        "tickers_or_slugs",
        "settlement_sample",
        "rules_sample",
        "status",
    ]
    if rows.empty:
        return pd.DataFrame(columns=columns)

    rows = rows.copy()
    rows["_review_event_key"] = rows.apply(review_event_key, axis=1)
    grouped_rows: list[dict[str, Any]] = []
    for event_key, group in rows.groupby("_review_event_key", dropna=False, sort=False):
        grouped_rows.append(
            {
                "venue": venue,
                "event_key": event_key,
                "event_title": _first_non_blank(group, "event_title") or _first_non_blank(group, "title"),
                "event_date": _first_non_blank(group, "event_date"),
                "close_time": _first_non_blank(group.sort_values("close_time", na_position="last"), "close_time"),
                "market_count": int(len(group)),
                "outcome_count": int(group.get("outcome_label", pd.Series(dtype=str)).fillna("").astype(str).replace("", pd.NA).dropna().nunique()),
                "market_types": _joined_unique(group, "market_type"),
                "outcomes_sample": _joined_unique(group, "outcome_label", limit=8),
                "titles_sample": _joined_unique(group, "title", limit=5),
                "market_ids": _joined_unique(group, "market_id", limit=40),
                "tickers_or_slugs": _joined_unique(group, "ticker_or_slug", limit=40),
                "settlement_sample": _joined_unique(group, "settlement_summary", limit=3),
                "rules_sample": _joined_unique(group, "rules_text", limit=2),
                "status": _joined_unique(group, "status", limit=4),
            }
        )
    output = pd.DataFrame(grouped_rows, columns=columns)
    if search.strip():
        terms = [term for term in re.split(r"\s+", search.casefold().strip()) if term]
        output = output[output.apply(lambda row: _matches_terms(row, terms, columns=EVENT_SEARCH_COLUMNS), axis=1)]
    sort_columns = [column for column in ("event_date", "close_time", "event_title") if column in output.columns]
    if sort_columns:
        output = output.sort_values(sort_columns, na_position="last")
    return output.reset_index(drop=True)


def review_event_key(row: pd.Series | dict[str, Any]) -> str:
    event_match_key = str(_row_get(row, "event_match_key") or "").strip()
    if event_match_key:
        return f"match|{event_match_key}"
    event_title = _normalize_review_text(_row_get(row, "event_title") or _row_get(row, "title"))
    event_date = str(_row_get(row, "event_date") or "").strip()
    if event_title:
        return f"title|{event_date}|{event_title}"
    market_id = str(_row_get(row, "market_id") or _row_get(row, "ticker_or_slug") or "").strip()
    return f"market|{market_id}"


def candidate_rows_for_review_event(
    candidates: pd.DataFrame,
    event: pd.Series | dict[str, Any],
    mappings: pd.DataFrame | None = None,
    *,
    active_only: bool = True,
    hide_approved: bool = True,
    now: datetime | str | None = None,
) -> pd.DataFrame:
    """Return the underlying active market/outcome rows for a grouped review event."""
    venue = str(_row_get(event, "venue") or "").strip()
    event_key = str(_row_get(event, "event_key") or "").strip()
    if candidates.empty or not venue or not event_key:
        return pd.DataFrame(columns=candidates.columns)
    rows = filter_review_candidates(
        candidates,
        venue,
        "",
        mappings=mappings,
        active_only=active_only,
        hide_approved=hide_approved,
        now=now,
    )
    if rows.empty:
        return rows
    output = rows[rows.apply(review_event_key, axis=1).eq(event_key)]
    sort_columns = [column for column in ("market_type", "outcome_label", "title", "ticker_or_slug", "market_id") if column in output.columns]
    if sort_columns:
        output = output.sort_values(sort_columns, na_position="last")
    return output.reset_index(drop=True)


def suggested_event_pairs_for_event(
    suggestions: pd.DataFrame,
    event: pd.Series | dict[str, Any],
    mappings: pd.DataFrame | None = None,
    *,
    hide_reviewed: bool = True,
    max_rows: int | None = None,
) -> pd.DataFrame:
    """Aggregate market-level suggestions into event-level pair candidates."""
    matched = suggestions_for_review_event(
        suggestions,
        event,
        mappings=mappings,
        hide_reviewed=hide_reviewed,
    )
    columns = [
        "source_venue",
        "source_event_title",
        "other_venue",
        "other_event_title",
        "event_match_key",
        "suggestion_count",
        "max_match_score",
        "max_semantic_score",
        "methods",
        "market_types",
        "outcomes_sample",
        "other_market_ids",
        "mapping_ids",
        "review_notes",
    ]
    if matched.empty:
        return pd.DataFrame(columns=columns)

    source_venue = str(_row_get(event, "venue") or "").strip().casefold()
    if source_venue == "polymarket":
        other_venue = "kalshi"
        other_event_col = "kalshi_event_title"
        other_market_col = "kalshi_ticker"
    else:
        other_venue = "polymarket"
        other_event_col = "polymarket_event_title"
        other_market_col = "polymarket_slug"

    grouped_rows: list[dict[str, Any]] = []
    matched = matched.copy()
    matched["_other_event_key"] = matched.apply(lambda row: _suggestion_event_key(row, other_event_col), axis=1)
    for _, group in matched.groupby("_other_event_key", dropna=False, sort=False):
        grouped_rows.append(
            {
                "source_venue": source_venue,
                "source_event_title": str(_row_get(event, "event_title") or ""),
                "other_venue": other_venue,
                "other_event_title": _first_non_blank(group, other_event_col),
                "event_match_key": _first_non_blank(group, "event_match_key"),
                "suggestion_count": int(len(group)),
                "max_match_score": _max_numeric(group, "match_score"),
                "max_semantic_score": _max_numeric(group, "semantic_combined_score"),
                "methods": _joined_unique(group, "suggestion_method"),
                "market_types": _joined_unique(group, "market_type"),
                "outcomes_sample": _joined_unique(group, "outcome_label", limit=8),
                "other_market_ids": _joined_unique(group, other_market_col, limit=20),
                "mapping_ids": _joined_unique(group, "mapping_id", limit=100),
                "review_notes": _joined_unique(group, "review_notes", limit=3),
            }
        )
    output = pd.DataFrame(grouped_rows, columns=columns)
    sort_columns = [column for column in ("max_match_score", "max_semantic_score", "suggestion_count") if column in output.columns]
    if sort_columns:
        output = output.sort_values(sort_columns, ascending=[False] * len(sort_columns), na_position="last")
    if max_rows is not None and max_rows > 0:
        output = output.head(max_rows)
    return output.reset_index(drop=True)


def top_event_matches_for_review_event(
    event_matches: pd.DataFrame,
    event: pd.Series | dict[str, Any],
    *,
    min_score: float = 68.0,
    max_rows: int = 5,
) -> pd.DataFrame:
    """Return ranked Kalshi event candidates for one Polymarket review event."""
    columns = [
        *EVENT_TOP_MATCH_COLUMNS,
        "source_venue",
        "source_event_title",
        "other_venue",
        "other_event_title",
        "event_score",
    ]
    if event_matches.empty:
        return pd.DataFrame(columns=columns)
    venue = str(_row_get(event, "venue") or "").strip().casefold()
    if venue != "polymarket":
        return pd.DataFrame(columns=columns)

    output = _ensure_event_top_match_columns(event_matches)
    pm_title = _normalize_review_text(_row_get(event, "event_title"))
    pm_date = str(_row_get(event, "event_date") or "").strip()
    if not pm_title:
        return pd.DataFrame(columns=columns)

    title_matches = output["pm_event_title"].fillna("").astype(str).map(_normalize_review_text).eq(pm_title)
    if pm_date:
        date_matches = output["pm_event_key"].fillna("").astype(str).str.contains(pm_date, regex=False)
        mask = title_matches & date_matches
        if not mask.any():
            mask = title_matches
    else:
        mask = title_matches
    output = output[mask].copy()
    if output.empty:
        return pd.DataFrame(columns=columns)

    output["event_score"] = pd.to_numeric(output["event_embedding_score"], errors="coerce")
    output["rank"] = pd.to_numeric(output["rank"], errors="coerce")
    output = output[output["event_score"].fillna(0.0) >= float(min_score)]
    output = output.sort_values(["rank", "event_score"], ascending=[True, False], na_position="last")
    if max_rows > 0:
        output = output.head(int(max_rows))
    output["source_venue"] = "polymarket"
    output["source_event_title"] = str(_row_get(event, "event_title") or "")
    output["other_venue"] = "kalshi"
    output["other_event_title"] = output["kalshi_event_title"]
    return output.reindex(columns=columns).reset_index(drop=True)


def candidate_rows_for_event_top_match(
    candidates: pd.DataFrame,
    event_match: pd.Series | dict[str, Any],
    mappings: pd.DataFrame | None = None,
    *,
    active_only: bool = True,
    hide_approved: bool = True,
    now: datetime | str | None = None,
) -> pd.DataFrame:
    """Return active Kalshi market rows backing one event-top candidate."""
    if candidates.empty:
        return pd.DataFrame(columns=candidates.columns)
    event = review_event_for_event_top_match(
        candidates,
        event_match,
        mappings=mappings,
        active_only=active_only,
        hide_approved=hide_approved,
        now=now,
    )
    if event is None:
        return pd.DataFrame(columns=candidates.columns)
    return candidate_rows_for_review_event(
        candidates,
        event,
        mappings=mappings,
        active_only=active_only,
        hide_approved=hide_approved,
        now=now,
    )


def review_event_for_event_top_match(
    candidates: pd.DataFrame,
    event_match: pd.Series | dict[str, Any],
    mappings: pd.DataFrame | None = None,
    *,
    active_only: bool = True,
    hide_approved: bool = True,
    now: datetime | str | None = None,
) -> pd.Series | None:
    """Resolve a ranked event-top candidate to the grouped Kalshi review event."""
    if candidates.empty:
        return None
    title = str(_row_get(event_match, "kalshi_event_title") or _row_get(event_match, "other_event_title") or "").strip()
    if not title:
        return None
    date = _date_from_event_top_key(_row_get(event_match, "kalshi_event_key"))
    events = filter_review_events(
        candidates,
        "kalshi",
        "",
        mappings=mappings,
        active_only=active_only,
        hide_approved=hide_approved,
        now=now,
    )
    if events.empty:
        return None
    title_mask = events["event_title"].fillna("").astype(str).map(_normalize_review_text).eq(_normalize_review_text(title))
    matched = events[title_mask]
    if date and not matched.empty and "event_date" in matched.columns:
        dated = matched[matched["event_date"].fillna("").astype(str).eq(date)]
        if not dated.empty:
            matched = dated
    if matched.empty:
        return None
    return matched.iloc[0]


def suggestions_for_review_event(
    suggestions: pd.DataFrame,
    event: pd.Series | dict[str, Any],
    mappings: pd.DataFrame | None = None,
    *,
    hide_reviewed: bool = True,
) -> pd.DataFrame:
    if suggestions.empty:
        return pd.DataFrame(columns=suggestions.columns)
    source_venue = str(_row_get(event, "venue") or "").strip().casefold()
    if source_venue == "polymarket":
        event_title_col = "polymarket_event_title"
        market_id_col = "polymarket_market_id"
        slug_col = "polymarket_slug"
    elif source_venue == "kalshi":
        event_title_col = "kalshi_event_title"
        market_id_col = "kalshi_ticker"
        slug_col = "kalshi_ticker"
    else:
        return pd.DataFrame(columns=suggestions.columns)

    output = suggestions.copy()
    mask = pd.Series(False, index=output.index)
    event_match_key = _event_match_key_from_review_event(event)
    if event_match_key and "event_match_key" in output.columns:
        mask |= output["event_match_key"].fillna("").astype(str).eq(event_match_key)

    event_title = _normalize_review_text(_row_get(event, "event_title"))
    if event_title and event_title_col in output.columns:
        mask |= output[event_title_col].fillna("").astype(str).map(_normalize_review_text).eq(event_title)

    ids = _split_joined_values(_row_get(event, "market_ids"))
    slugs = _split_joined_values(_row_get(event, "tickers_or_slugs"))
    if ids and market_id_col in output.columns:
        mask |= output[market_id_col].fillna("").astype(str).isin(ids)
    if slugs and slug_col in output.columns:
        mask |= output[slug_col].fillna("").astype(str).isin(slugs)

    output = output[mask]
    return _sort_and_hide_reviewed_suggestions(output, mappings, hide_reviewed=hide_reviewed)


def suggestions_for_event_pair(
    suggestions: pd.DataFrame,
    event_pair: pd.Series | dict[str, Any],
    mappings: pd.DataFrame | None = None,
    *,
    hide_reviewed: bool = True,
) -> pd.DataFrame:
    if suggestions.empty:
        return pd.DataFrame(columns=suggestions.columns)
    mapping_ids = _split_joined_values(_row_get(event_pair, "mapping_ids"))
    if not mapping_ids or "mapping_id" not in suggestions.columns:
        return pd.DataFrame(columns=suggestions.columns)
    output = suggestions[suggestions["mapping_id"].fillna("").astype(str).isin(mapping_ids)].copy()
    return _sort_and_hide_reviewed_suggestions(output, mappings, hide_reviewed=hide_reviewed)


def suggested_pairs_for_candidate(
    suggestions: pd.DataFrame,
    candidate: pd.Series | dict[str, Any],
    mappings: pd.DataFrame | None = None,
    *,
    hide_reviewed: bool = True,
    max_rows: int | None = None,
) -> pd.DataFrame:
    """Return ranked suggested mappings connected to one selected review candidate."""
    if suggestions.empty:
        return pd.DataFrame(columns=suggestions.columns)

    venue = str(_row_get(candidate, "venue") or "").strip().casefold()
    output = suggestions.copy()
    if venue == "polymarket":
        market_id = str(_row_get(candidate, "market_id") or "").strip()
        slug = str(_row_get(candidate, "ticker_or_slug") or "").strip()
        token_id = str(_row_get(candidate, "yes_token_id") or "").strip()
        mask = pd.Series(False, index=output.index)
        if market_id and "polymarket_market_id" in output.columns:
            mask |= output["polymarket_market_id"].fillna("").astype(str).eq(market_id)
        if slug and "polymarket_slug" in output.columns:
            mask |= output["polymarket_slug"].fillna("").astype(str).eq(slug)
        if token_id and "polymarket_yes_token_id" in output.columns and not mask.any():
            mask |= output["polymarket_yes_token_id"].fillna("").astype(str).eq(token_id)
        output = output[mask]
    elif venue == "kalshi":
        ticker = str(_row_get(candidate, "market_id") or _row_get(candidate, "ticker_or_slug") or "").strip()
        if not ticker or "kalshi_ticker" not in output.columns:
            return pd.DataFrame(columns=suggestions.columns)
        output = output[output["kalshi_ticker"].fillna("").astype(str).eq(ticker)]
    else:
        return pd.DataFrame(columns=suggestions.columns)

    output = _sort_and_hide_reviewed_suggestions(output, mappings, hide_reviewed=hide_reviewed)
    if max_rows is not None and max_rows > 0:
        output = output.head(max_rows)
    return output.reset_index(drop=True)


def is_active_candidate(row: pd.Series | dict[str, Any], now: datetime | str | None = None) -> bool:
    status = str(_row_get(row, "status") or "").strip().casefold()
    if status not in ACTIVE_CANDIDATE_STATUSES:
        return False
    close_time = pd.to_datetime(_row_get(row, "close_time"), errors="coerce", utc=True)
    if pd.isna(close_time):
        return True
    now_ts = pd.Timestamp(now) if now is not None else pd.Timestamp.now(tz=UTC)
    if now_ts.tzinfo is None:
        now_ts = now_ts.tz_localize(UTC)
    else:
        now_ts = now_ts.tz_convert(UTC)
    return close_time > now_ts


def approved_candidate_keys(mappings: pd.DataFrame) -> set[str]:
    if mappings.empty:
        return set()
    frame = _ensure_mapping_columns(mappings)
    status = frame["status"].fillna("").astype(str).str.strip().str.casefold()
    lifecycle = frame["lifecycle_status"].fillna("").astype(str).str.strip().str.casefold()
    approved = frame[(status == "approved") & lifecycle.isin(ACTIVE_MAPPING_LIFECYCLES)]
    keys: set[str] = set()
    for _, row in approved.iterrows():
        pm_key = _candidate_key_parts("polymarket", row.get("polymarket_market_id"), row.get("polymarket_slug"), row.get("polymarket_yes_token_id"))
        ks_key = _candidate_key_parts("kalshi", row.get("kalshi_ticker"), row.get("kalshi_ticker"), "")
        if pm_key:
            keys.add(pm_key)
        if ks_key:
            keys.add(ks_key)
    return keys


def candidate_key(row: pd.Series | dict[str, Any]) -> str:
    venue = str(_row_get(row, "venue") or "")
    if venue == "polymarket":
        return _candidate_key_parts(venue, _row_get(row, "market_id"), _row_get(row, "ticker_or_slug"), _row_get(row, "yes_token_id"))
    return _candidate_key_parts(venue, _row_get(row, "market_id"), _row_get(row, "ticker_or_slug"), "")


def build_manual_mapping_row(
    polymarket_row: pd.Series | dict[str, Any],
    kalshi_row: pd.Series | dict[str, Any],
    *,
    status: str,
    draw_handling: str,
    extra_time_handling: str,
    penalties_handling: str,
    settlement_notes: str,
    reviewer: str,
    notes: str = "",
    proposition: str = "",
    reviewed_at: str | None = None,
) -> dict[str, Any]:
    reviewed_at = reviewed_at or utc_now_iso()
    outcome_label = str(_row_get(polymarket_row, "outcome_label") or _row_get(kalshi_row, "outcome_label") or "").strip()
    event_name = str(_row_get(polymarket_row, "event_title") or _row_get(kalshi_row, "event_title") or _row_get(polymarket_row, "title") or "").strip()
    pm_outcomes = _parse_json_list(_row_get(polymarket_row, "outcomes"))
    mapping_id = "__".join(
        [
            _slugify(str(_row_get(polymarket_row, "ticker_or_slug") or _row_get(polymarket_row, "market_id") or _row_get(polymarket_row, "yes_token_id"))),
            _slugify(str(_row_get(kalshi_row, "ticker_or_slug") or _row_get(kalshi_row, "market_id"))),
        ]
    ).strip("_")
    return {
        "mapping_id": mapping_id,
        "status": status.strip().casefold(),
        "lifecycle_status": "active",
        "event_name": event_name,
        "proposition": proposition.strip() or _default_proposition(event_name, outcome_label),
        "polymarket_market_id": str(_row_get(polymarket_row, "market_id") or ""),
        "polymarket_slug": str(_row_get(polymarket_row, "ticker_or_slug") or ""),
        "polymarket_yes_token_id": str(_row_get(polymarket_row, "yes_token_id") or ""),
        "polymarket_no_token_id": str(_row_get(polymarket_row, "no_token_id") or ""),
        "polymarket_yes_outcome": str(pm_outcomes[0]) if len(pm_outcomes) > 0 else outcome_label,
        "polymarket_no_outcome": str(pm_outcomes[1]) if len(pm_outcomes) > 1 else "",
        "kalshi_ticker": str(_row_get(kalshi_row, "market_id") or _row_get(kalshi_row, "ticker_or_slug") or ""),
        "draw_handling": draw_handling.strip(),
        "extra_time_handling": extra_time_handling.strip(),
        "penalties_handling": penalties_handling.strip(),
        "settlement_notes": settlement_notes.strip(),
        "reviewer": reviewer.strip(),
        "reviewed_at": reviewed_at,
        "notes": notes.strip(),
    }


def review_row_errors(row: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    status = str(row.get("status") or "").strip().casefold()
    if status not in {"approved", "rejected", "needs_review"}:
        errors.append("status must be approved, rejected, or needs_review")
    for column in ("mapping_id", "polymarket_yes_token_id", "polymarket_no_token_id", "kalshi_ticker"):
        if not str(row.get(column) or "").strip():
            errors.append(f"{column} is required")
    if status == "approved":
        for column in REQUIRED_APPROVAL_FIELDS:
            if not str(row.get(column) or "").strip():
                errors.append(f"{column} is required for approved mappings")
    return errors


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def utc_now_compact() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _ensure_mapping_columns(frame: pd.DataFrame) -> pd.DataFrame:
    output = frame.copy()
    for column in MAPPING_COLUMNS:
        if column not in output.columns:
            output[column] = ""
    output["lifecycle_status"] = output["lifecycle_status"].fillna("").replace("", "active")
    return output[MAPPING_COLUMNS].fillna("")


def _ensure_event_top_match_columns(frame: pd.DataFrame) -> pd.DataFrame:
    output = frame.copy()
    for column in EVENT_TOP_MATCH_COLUMNS:
        if column not in output.columns:
            output[column] = ""
    return output[list(EVENT_TOP_MATCH_COLUMNS)].fillna("")


def _ensure_event_pair_review_columns(frame: pd.DataFrame) -> pd.DataFrame:
    output = frame.copy()
    for column in EVENT_PAIR_REVIEW_COLUMNS:
        if column not in output.columns:
            output[column] = ""
    output["lifecycle_status"] = output["lifecycle_status"].fillna("").replace("", "active")
    return output[list(EVENT_PAIR_REVIEW_COLUMNS)].fillna("")


def _read_csv(path: str | Path) -> pd.DataFrame:
    if _is_gcs_uri(path):
        return pd.read_csv(BytesIO(_download_gcs_bytes(str(path))), dtype=str, keep_default_na=False)
    local_path = Path(path)
    if not local_path.exists():
        raise FileNotFoundError(str(path))
    return pd.read_csv(local_path, dtype=str, keep_default_na=False)


def _write_csv(path: str | Path, frame: pd.DataFrame) -> None:
    body = frame.to_csv(index=False)
    if _is_gcs_uri(path):
        _upload_gcs_text(str(path), body)
        return
    local_path = Path(path)
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_text(body, encoding="utf-8")


def _history_path(path: str | Path, reviewed_at: str) -> str:
    safe_time = _slugify(reviewed_at) or utc_now_compact()
    path_text = str(path).rstrip("/")
    if _is_gcs_uri(path_text):
        root = path_text.rsplit("/", 1)[0]
        return f"{root}/history/reviewed_at={safe_time}/approved_mappings.csv"
    local_path = Path(path_text)
    return str(local_path.parent / "history" / f"reviewed_at={safe_time}" / "approved_mappings.csv")


def _is_gcs_uri(value: str | Path) -> bool:
    return str(value).startswith("gs://")


def _split_gcs_uri(uri: str) -> tuple[str, str]:
    if not uri.startswith("gs://"):
        raise ValueError(f"Not a GCS URI: {uri}")
    bucket, _, blob = uri.removeprefix("gs://").partition("/")
    if not bucket:
        raise ValueError(f"GCS URI is missing a bucket: {uri}")
    return bucket, blob.strip("/")


def _download_gcs_bytes(uri: str) -> bytes:
    try:
        from google.api_core.exceptions import NotFound
        from google.cloud import storage
    except ImportError as exc:  # pragma: no cover - optional dependency path
        raise RuntimeError('Install the GCP extra first: pip install -e ".[gcp]"') from exc

    bucket_name, blob_name = _split_gcs_uri(uri)
    try:
        return storage.Client().bucket(bucket_name).blob(blob_name).download_as_bytes()
    except NotFound as exc:
        raise FileNotFoundError(uri) from exc


def _upload_gcs_text(uri: str, body: str) -> None:
    try:
        from google.cloud import storage
    except ImportError as exc:  # pragma: no cover - optional dependency path
        raise RuntimeError('Install the GCP extra first: pip install -e ".[gcp]"') from exc

    bucket_name, blob_name = _split_gcs_uri(uri)
    storage.Client().bucket(bucket_name).blob(blob_name).upload_from_string(body, content_type="text/csv")


def _candidate_key_parts(venue: Any, market_id: Any, ticker_or_slug: Any, token_id: Any) -> str:
    venue_text = str(venue or "").strip().casefold()
    primary = str(market_id or "").strip()
    fallback = str(ticker_or_slug or "").strip()
    token = str(token_id or "").strip()
    if venue_text == "polymarket":
        if primary and token:
            return f"polymarket|{primary}|{token}"
        if fallback and token:
            return f"polymarket|{fallback}|{token}"
    if venue_text == "kalshi":
        key = primary or fallback
        return f"kalshi|{key}" if key else ""
    return ""


def _first_non_blank(frame: pd.DataFrame, column: str) -> str:
    if column not in frame.columns:
        return ""
    for value in frame[column].tolist():
        text = str(value or "").strip()
        if text and text.casefold() != "nan":
            return text
    return ""


def _joined_unique(frame: pd.DataFrame, column: str, limit: int = 12) -> str:
    if column not in frame.columns:
        return ""
    values: list[str] = []
    seen: set[str] = set()
    for value in frame[column].tolist():
        text = str(value or "").strip()
        if not text or text.casefold() == "nan":
            continue
        key = text.casefold()
        if key in seen:
            continue
        values.append(text)
        seen.add(key)
        if len(values) >= limit:
            break
    return " | ".join(values)


def _split_joined_values(value: Any) -> set[str]:
    text = str(value or "").strip()
    if not text:
        return set()
    return {part.strip() for part in text.split("|") if part.strip()}


def _date_from_event_top_key(value: Any) -> str:
    match = re.search(r"\b20\d{2}-\d{2}-\d{2}\b", str(value or ""))
    return match.group(0) if match else ""


def _max_numeric(frame: pd.DataFrame, column: str) -> float:
    if column not in frame.columns:
        return 0.0
    values = pd.to_numeric(frame[column], errors="coerce").dropna()
    if values.empty:
        return 0.0
    return float(values.max())


def _normalize_review_text(value: Any) -> str:
    text = str(value or "").casefold()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _event_match_key_from_review_event(event: pd.Series | dict[str, Any]) -> str:
    direct = str(_row_get(event, "event_match_key") or "").strip()
    if direct:
        return direct
    event_key = str(_row_get(event, "event_key") or "").strip()
    if event_key.startswith("match|"):
        return event_key.removeprefix("match|")
    return ""


def _review_event_identifier(event: pd.Series | dict[str, Any]) -> str:
    event_key = str(_row_get(event, "event_key") or "").strip()
    return event_key or review_event_key(event)


def _suggestion_event_key(row: pd.Series | dict[str, Any], event_title_column: str) -> str:
    event_match_key = str(_row_get(row, "event_match_key") or "").strip()
    if event_match_key:
        return f"match|{event_match_key}"
    event_title = _normalize_review_text(_row_get(row, event_title_column))
    return f"title||{event_title}" if event_title else "missing"


def _sort_and_hide_reviewed_suggestions(
    output: pd.DataFrame,
    mappings: pd.DataFrame | None,
    *,
    hide_reviewed: bool,
) -> pd.DataFrame:
    if output.empty:
        return output.reset_index(drop=True)
    output = output.copy()
    if hide_reviewed and mappings is not None and not mappings.empty and "mapping_id" in output.columns and "mapping_id" in mappings.columns:
        reviewed = set(mappings["mapping_id"].fillna("").astype(str))
        output = output[~output["mapping_id"].fillna("").astype(str).isin(reviewed)]
    sort_columns = [
        column
        for column in (
            "match_score",
            "semantic_combined_score",
            "gemini_embedding_score",
            "embedding_score",
            "lexical_score",
        )
        if column in output.columns
    ]
    for column in sort_columns:
        output[column] = pd.to_numeric(output[column], errors="coerce")
    if sort_columns:
        output = output.sort_values(sort_columns, ascending=[False] * len(sort_columns), na_position="last")
    return output.reset_index(drop=True)


def _matches_terms(row: pd.Series | dict[str, Any], terms: list[str], *, columns: tuple[str, ...] = SEARCH_COLUMNS) -> bool:
    if isinstance(row, pd.Series):
        haystack = " ".join(str(row.get(column) or "") for column in columns if column in row.index).casefold()
    else:
        haystack = " ".join(str(row.get(column) or "") for column in columns).casefold()
    return all(term in haystack for term in terms)


def _parse_json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def _default_proposition(event_name: str, outcome_label: str) -> str:
    if event_name and outcome_label:
        return f"{outcome_label} to win / resolve Yes in {event_name}"
    return outcome_label or event_name or "Manual cross-market mapping"


def _slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")[:180]


def _row_get(row: pd.Series | dict[str, Any], key: str) -> Any:
    if isinstance(row, pd.Series):
        return row.get(key)
    return row.get(key)
