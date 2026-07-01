from __future__ import annotations

import pandas as pd

from prediction_market.cloud_daily_pipeline import (
    approved_market_pairs_to_event_pairs,
    filter_approved_market_pairs_to_active_event_pairs,
    hydrate_latest_tables,
)
from prediction_market.cloud_db import read_table
from prediction_market.fifa_arbitrage import write_latest_processed_table


def test_approved_market_pairs_to_event_pairs_filters_and_dedupes() -> None:
    frame = pd.DataFrame(
        [
            {
                "manual_decision": "approved",
                "lifecycle_status": "active",
                "event_match_key": "PM-1__KS-1",
                "event_name": "PM fallback title",
                "polymarket_event_title": "PM One",
                "kalshi_event_title": "KS One",
            },
            {
                "manual_decision": "approved",
                "lifecycle_status": "active",
                "event_match_key": "PM-1__KS-1",
                "polymarket_event_title": "PM One duplicate",
                "kalshi_event_title": "KS One duplicate",
            },
            {
                "manual_decision": "rejected",
                "lifecycle_status": "active",
                "event_match_key": "PM-2__KS-2",
            },
            {
                "manual_decision": "approved",
                "lifecycle_status": "expired",
                "event_match_key": "PM-3__KS-3",
            },
            {
                "manual_decision": "approved",
                "lifecycle_status": "active",
                "event_match_key": "malformed",
            },
        ]
    )

    event_pairs = approved_market_pairs_to_event_pairs(frame)

    assert event_pairs.to_dict("records") == [
        {
            "verdict": "valid",
            "pm_event_id": "PM-1",
            "ks_event_id": "KS-1",
            "pm_title": "PM One",
            "ks_title": "KS One",
            "review_reason": "Derived from approved market-pair seed.",
        }
    ]


def test_hydrate_latest_tables_copies_existing_tables_and_reports_missing(tmp_path) -> None:
    source = tmp_path / "source"
    work_dir = tmp_path / "work"
    write_latest_processed_table("cache_table", pd.DataFrame([{"key": "one"}]), source)

    result = hydrate_latest_tables(source, work_dir, ["cache_table", "missing_table"])

    assert result == {"cache_table": "hydrated 1 rows", "missing_table": "missing"}
    hydrated = read_table(work_dir, "cache_table")
    assert hydrated.to_dict("records") == [{"key": "one"}]


def test_filter_approved_market_pairs_to_active_event_pairs_requires_both_venues_active() -> None:
    approved = pd.DataFrame(
        [
            {"manual_decision": "approved", "event_match_key": "PM-SPORT__KS-SPORT", "polymarket_market_id": "pm-1"},
            {"manual_decision": "approved", "event_match_key": "PM-SPORT__KS-SPORT", "polymarket_market_id": "pm-2"},
            {"manual_decision": "approved", "event_match_key": "PM-SPORT__KS-MISSING", "polymarket_market_id": "pm-3"},
            {"manual_decision": "approved", "event_match_key": "PM-MISSING__KS-SPORT", "polymarket_market_id": "pm-4"},
            {"manual_decision": "approved", "event_match_key": "malformed", "polymarket_market_id": "pm-5"},
        ]
    )
    active = pd.DataFrame(
        [
            {
                "venue": "polymarket",
                "raw_payload": '{"_event_context_id":"PM-SPORT"}',
            },
            {
                "venue": "kalshi",
                "raw_payload": '{"_event_context_ticker":"KS-SPORT"}',
            },
        ]
    )

    filtered, summary = filter_approved_market_pairs_to_active_event_pairs(approved, active)

    assert filtered["polymarket_market_id"].tolist() == ["pm-1", "pm-2"]
    assert summary == {
        "approved_market_pair_rows_before": 5,
        "approved_market_pair_rows_after": 2,
        "derived_event_pairs_before": 3,
        "derived_event_pairs_after": 1,
        "active_polymarket_event_keys": 1,
        "active_kalshi_event_keys": 1,
        "filtered_out_rows": 3,
    }
