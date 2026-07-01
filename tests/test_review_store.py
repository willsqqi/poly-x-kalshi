from __future__ import annotations

import pandas as pd

from prediction_market.fifa_arbitrage import MAPPING_COLUMNS
from prediction_market.review_store import (
    build_manual_mapping_row,
    build_event_pair_review_row,
    candidate_rows_for_event_top_match,
    candidate_rows_for_review_event,
    default_event_pair_review_path,
    default_review_mapping_path,
    event_pair_review_errors,
    filter_review_candidates,
    filter_review_events,
    load_event_pair_reviews,
    load_review_mappings,
    review_row_errors,
    save_review_mapping,
    save_event_pair_review,
    suggested_event_pairs_for_event,
    suggested_pairs_for_candidate,
    suggestions_for_event_pair,
    top_event_matches_for_review_event,
)


def test_default_review_mapping_path_uses_source_root() -> None:
    assert default_review_mapping_path("data/cross_sports_arbitrage").endswith(
        "data/cross_sports_arbitrage/manual_review/approved_mappings/current.csv"
    )
    assert default_review_mapping_path("gs://bucket/cross_sports_arbitrage") == (
        "gs://bucket/cross_sports_arbitrage/manual_review/approved_mappings/current.csv"
    )
    assert default_event_pair_review_path("data/cross_sports_arbitrage").endswith(
        "data/cross_sports_arbitrage/manual_review/approved_event_pairs/current.csv"
    )


def test_filter_review_candidates_searches_separate_active_venue_lists() -> None:
    candidates = _candidate_frame()

    polymarket = filter_review_candidates(
        candidates,
        "polymarket",
        search="athletics",
        now="2026-06-26T00:00:00Z",
    )
    kalshi = filter_review_candidates(
        candidates,
        "kalshi",
        search="sf",
        now="2026-06-26T00:00:00Z",
    )

    assert polymarket["ticker_or_slug"].tolist() == ["mlb-athletics-giants"]
    assert kalshi["market_id"].tolist() == ["KXMLBGAME-26JUN242145ATHSF-SF"]


def test_filter_review_candidates_hides_expired_closed_and_approved_rows() -> None:
    candidates = _candidate_frame()
    mappings = pd.DataFrame(
        [
            {
                **_mapping_row(),
                "polymarket_market_id": "0xmlb",
                "polymarket_yes_token_id": "pm-ath",
                "kalshi_ticker": "KXMLBGAME-26JUN242145ATHSF-SF",
            }
        ],
        columns=MAPPING_COLUMNS,
    )

    polymarket = filter_review_candidates(
        candidates,
        "polymarket",
        mappings=mappings,
        now="2026-06-26T00:00:00Z",
    )
    kalshi = filter_review_candidates(
        candidates,
        "kalshi",
        mappings=mappings,
        now="2026-06-26T00:00:00Z",
    )

    assert polymarket.empty
    assert kalshi.empty


def test_build_and_save_manual_mapping_row_upserts_current_and_history(tmp_path) -> None:
    candidates = _candidate_frame()
    pm = candidates[candidates["venue"] == "polymarket"].iloc[0]
    ks = candidates[candidates["venue"] == "kalshi"].iloc[0]
    row = build_manual_mapping_row(
        pm,
        ks,
        status="approved",
        draw_handling="no standard draw",
        extra_time_handling="extra innings included",
        penalties_handling="not applicable",
        settlement_notes="Both sides refer to the same official game winner.",
        reviewer="tester",
        reviewed_at="2026-06-26T00:00:00Z",
    )
    assert review_row_errors(row) == []

    path = tmp_path / "manual_review" / "approved_mappings" / "current.csv"
    written = save_review_mapping(path, row)
    saved = load_review_mappings(path)

    assert saved["mapping_id"].tolist() == ["mlb-athletics-giants__kxmlbgame-26jun242145athsf-sf"]
    assert saved.iloc[0]["lifecycle_status"] == "active"
    assert saved.iloc[0]["kalshi_ticker"] == "KXMLBGAME-26JUN242145ATHSF-SF"
    assert "history" in written
    assert (tmp_path / "manual_review" / "approved_mappings" / "history").exists()


def test_suggested_pairs_for_candidate_ranks_other_side_matches() -> None:
    candidates = _candidate_frame()
    pm = candidates[candidates["venue"] == "polymarket"].iloc[0]
    suggestions = _suggestion_frame()

    matches = suggested_pairs_for_candidate(suggestions, pm, max_rows=2)

    assert matches["kalshi_ticker"].tolist() == [
        "KXMLBGAME-26JUN242145ATHSF-SF",
        "KXMLBGAME-26JUN242145ATHSF-ATH",
    ]
    assert matches["match_score"].tolist() == [98, 91]


def test_suggested_pairs_for_candidate_hides_reviewed_mappings() -> None:
    candidates = _candidate_frame()
    pm = candidates[candidates["venue"] == "polymarket"].iloc[0]
    suggestions = _suggestion_frame()
    mappings = pd.DataFrame(
        [
            {
                **_mapping_row(),
                "mapping_id": "pm-sf",
                "polymarket_market_id": "0xmlb",
                "polymarket_yes_token_id": "pm-ath",
                "kalshi_ticker": "KXMLBGAME-26JUN242145ATHSF-SF",
            }
        ],
        columns=MAPPING_COLUMNS,
    )

    matches = suggested_pairs_for_candidate(suggestions, pm, mappings=mappings)

    assert matches["mapping_id"].tolist() == ["pm-ath"]


def test_filter_review_events_groups_duplicate_market_rows_by_event() -> None:
    candidates = _event_candidate_frame()

    events = filter_review_events(
        candidates,
        "polymarket",
        search="athletics",
        now="2026-06-26T00:00:00Z",
    )

    assert len(events) == 1
    event = events.iloc[0]
    assert event["event_title"] == "Athletics vs. San Francisco Giants"
    assert event["market_count"] == 2
    assert event["outcome_count"] == 2
    assert event["outcomes_sample"] == "Athletics | San Francisco Giants"


def test_candidate_rows_for_review_event_returns_underlying_market_rows() -> None:
    candidates = _event_candidate_frame()
    event = filter_review_events(
        candidates,
        "polymarket",
        now="2026-06-26T00:00:00Z",
    ).iloc[0]

    rows = candidate_rows_for_review_event(
        candidates,
        event,
        now="2026-06-26T00:00:00Z",
    )

    assert rows["yes_token_id"].tolist() == ["pm-ath", "pm-sf"]
    assert rows["outcome_label"].tolist() == ["Athletics", "San Francisco Giants"]


def test_suggested_event_pairs_expand_back_to_market_level_suggestions() -> None:
    candidates = _event_candidate_frame()
    event = filter_review_events(
        candidates,
        "polymarket",
        now="2026-06-26T00:00:00Z",
    ).iloc[0]
    suggestions = _suggestion_frame()

    event_pairs = suggested_event_pairs_for_event(suggestions, event)

    assert len(event_pairs) == 1
    assert event_pairs.iloc[0]["other_event_title"] == "Athletics vs SF Winner?"
    assert event_pairs.iloc[0]["suggestion_count"] == 2
    market_matches = suggestions_for_event_pair(suggestions, event_pairs.iloc[0])
    assert market_matches["mapping_id"].tolist() == ["pm-sf", "pm-ath"]


def test_top_event_matches_for_review_event_returns_top_five_above_threshold() -> None:
    candidates = _event_candidate_frame()
    event = filter_review_events(
        candidates,
        "polymarket",
        now="2026-06-26T00:00:00Z",
    ).iloc[0]
    matches = top_event_matches_for_review_event(
        _event_top_match_frame(),
        event,
        min_score=68,
        max_rows=5,
    )

    assert matches["kalshi_event_title"].tolist() == [
        "Athletics vs SF Winner?",
        "Other candidate 1",
        "Other candidate 2",
        "Other candidate 3",
        "Other candidate 4",
    ]
    assert matches["event_score"].tolist() == [88.0, 79.0, 78.0, 77.0, 76.0]


def test_candidate_rows_for_event_top_match_resolves_ranked_kalshi_event() -> None:
    candidates = _event_candidate_frame()
    event = filter_review_events(
        candidates,
        "polymarket",
        now="2026-06-26T00:00:00Z",
    ).iloc[0]
    matches = top_event_matches_for_review_event(
        _event_top_match_frame(),
        event,
        min_score=68,
        max_rows=5,
    )

    rows = candidate_rows_for_event_top_match(
        candidates,
        matches.iloc[0],
        now="2026-06-26T00:00:00Z",
    )

    assert rows["market_id"].tolist() == ["KXMLBGAME-26JUN242145ATHSF-SF"]
    assert rows["outcome_label"].tolist() == ["San Francisco Giants"]


def test_build_and_save_event_pair_review_row_supports_no_match(tmp_path) -> None:
    candidates = _event_candidate_frame()
    pm_event = filter_review_events(
        candidates,
        "polymarket",
        now="2026-06-26T00:00:00Z",
    ).iloc[0]
    ks_event = filter_review_events(
        candidates,
        "kalshi",
        now="2026-06-26T00:00:00Z",
    ).iloc[0]

    approved = build_event_pair_review_row(
        pm_event,
        ks_event,
        status="approved",
        reviewer="tester",
        event_match=_event_top_match_frame().iloc[0],
        reviewed_at="2026-06-26T00:00:00Z",
    )
    assert event_pair_review_errors(approved) == []

    no_match = build_event_pair_review_row(
        pm_event,
        None,
        status="no_match",
        reviewer="tester",
        reviewed_at="2026-06-26T00:00:00Z",
    )
    assert event_pair_review_errors(no_match) == []
    assert no_match["other_event_key"] == ""

    path = tmp_path / "manual_review" / "approved_event_pairs" / "current.csv"
    save_event_pair_review(path, approved)
    save_event_pair_review(path, no_match)
    saved = load_event_pair_reviews(path)

    assert saved["status"].tolist() == ["approved", "no_match"]
    assert (tmp_path / "manual_review" / "approved_event_pairs" / "history").exists()


def test_review_row_errors_require_settlement_fields_for_approved_only() -> None:
    row = _mapping_row()
    row["status"] = "approved"
    row["settlement_notes"] = ""

    assert "settlement_notes is required for approved mappings" in review_row_errors(row)

    row["status"] = "needs_review"
    assert "settlement_notes is required for approved mappings" not in review_row_errors(row)


def _candidate_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "venue": "polymarket",
                "market_id": "0xmlb",
                "ticker_or_slug": "mlb-athletics-giants",
                "title": "Athletics vs. San Francisco Giants",
                "event_title": "Athletics vs. San Francisco Giants",
                "outcome_label": "Athletics",
                "market_type": "match_winner",
                "status": "active",
                "close_time": "2026-06-27T00:00:00Z",
                "yes_token_id": "pm-ath",
                "no_token_id": "pm-sf",
                "outcomes": '["Athletics","San Francisco Giants"]',
                "rules_text": "Official MLB game winner.",
                "settlement_summary": "Official MLB game winner.",
            },
            {
                "venue": "polymarket",
                "market_id": "0xold",
                "ticker_or_slug": "old-event",
                "title": "Old event",
                "event_title": "Old event",
                "outcome_label": "Old",
                "market_type": "match_winner",
                "status": "active",
                "close_time": "2026-06-25T00:00:00Z",
                "yes_token_id": "pm-old",
                "no_token_id": "pm-old-no",
                "outcomes": '["Old","No"]',
                "rules_text": "",
                "settlement_summary": "",
            },
            {
                "venue": "kalshi",
                "market_id": "KXMLBGAME-26JUN242145ATHSF-SF",
                "ticker_or_slug": "KXMLBGAME-26JUN242145ATHSF-SF",
                "title": "Athletics vs SF Winner?",
                "event_title": "Athletics vs SF Winner?",
                "outcome_label": "San Francisco Giants",
                "market_type": "match_winner",
                "status": "active",
                "close_time": "2026-06-27T00:00:00Z",
                "yes_token_id": "",
                "no_token_id": "",
                "outcomes": '["Yes","No"]',
                "rules_text": "Official baseball game winner.",
                "settlement_summary": "Official baseball game winner.",
            },
            {
                "venue": "kalshi",
                "market_id": "KXCLOSED",
                "ticker_or_slug": "KXCLOSED",
                "title": "Closed market",
                "event_title": "Closed market",
                "outcome_label": "Closed",
                "market_type": "match_winner",
                "status": "closed",
                "close_time": "2026-06-27T00:00:00Z",
                "yes_token_id": "",
                "no_token_id": "",
                "outcomes": '["Yes","No"]',
                "rules_text": "",
                "settlement_summary": "",
            },
        ]
    )


def _event_candidate_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "venue": "polymarket",
                "market_id": "0xmlb",
                "ticker_or_slug": "mlb-athletics-giants",
                "title": "Athletics vs. San Francisco Giants",
                "event_title": "Athletics vs. San Francisco Giants",
                "event_date": "2026-06-27",
                "event_match_key": "2026-06-27|athletics|san-francisco-giants",
                "outcome_label": "Athletics",
                "market_type": "match_winner",
                "status": "active",
                "close_time": "2026-06-27T00:00:00Z",
                "yes_token_id": "pm-ath",
                "no_token_id": "pm-sf",
                "outcomes": '["Athletics","San Francisco Giants"]',
                "rules_text": "Official MLB game winner.",
                "settlement_summary": "Official MLB game winner.",
            },
            {
                "venue": "polymarket",
                "market_id": "0xmlb",
                "ticker_or_slug": "mlb-athletics-giants",
                "title": "Athletics vs. San Francisco Giants",
                "event_title": "Athletics vs. San Francisco Giants",
                "event_date": "2026-06-27",
                "event_match_key": "2026-06-27|athletics|san-francisco-giants",
                "outcome_label": "San Francisco Giants",
                "market_type": "match_winner",
                "status": "active",
                "close_time": "2026-06-27T00:00:00Z",
                "yes_token_id": "pm-sf",
                "no_token_id": "pm-ath",
                "outcomes": '["San Francisco Giants","Athletics"]',
                "rules_text": "Official MLB game winner.",
                "settlement_summary": "Official MLB game winner.",
            },
            {
                "venue": "kalshi",
                "market_id": "KXMLBGAME-26JUN242145ATHSF-SF",
                "ticker_or_slug": "KXMLBGAME-26JUN242145ATHSF-SF",
                "title": "Athletics vs SF Winner?",
                "event_title": "Athletics vs SF Winner?",
                "event_date": "2026-06-27",
                "event_match_key": "2026-06-27|athletics|san-francisco-giants",
                "outcome_label": "San Francisco Giants",
                "market_type": "match_winner",
                "status": "active",
                "close_time": "2026-06-27T00:00:00Z",
                "yes_token_id": "",
                "no_token_id": "",
                "outcomes": '["Yes","No"]',
                "rules_text": "Official baseball game winner.",
                "settlement_summary": "Official baseball game winner.",
            },
        ]
    )


def _mapping_row() -> dict[str, str]:
    return {
        "mapping_id": "map-1",
        "status": "approved",
        "lifecycle_status": "active",
        "event_name": "Athletics vs. San Francisco Giants",
        "proposition": "Athletics to win",
        "polymarket_market_id": "0xmlb",
        "polymarket_slug": "mlb-athletics-giants",
        "polymarket_yes_token_id": "pm-ath",
        "polymarket_no_token_id": "pm-sf",
        "polymarket_yes_outcome": "Athletics",
        "polymarket_no_outcome": "San Francisco Giants",
        "kalshi_ticker": "KXMLBGAME-26JUN242145ATHSF-SF",
        "draw_handling": "no standard draw",
        "extra_time_handling": "extra innings included",
        "penalties_handling": "not applicable",
        "settlement_notes": "Both sides refer to the same official game winner.",
        "reviewer": "tester",
        "reviewed_at": "2026-06-26T00:00:00Z",
        "notes": "",
    }


def _suggestion_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "mapping_id": "pm-sf",
                "match_score": 98,
                "semantic_combined_score": 97,
                "suggestion_method": "semantic",
                "market_type": "match_winner",
                "event_name": "Athletics vs. San Francisco Giants",
                "event_match_key": "2026-06-27|athletics|san-francisco-giants",
                "outcome_label": "San Francisco Giants",
                "proposition": "San Francisco Giants to win",
                "polymarket_market_id": "0xmlb",
                "polymarket_slug": "mlb-athletics-giants",
                "polymarket_yes_token_id": "pm-sf",
                "polymarket_no_token_id": "pm-ath",
                "polymarket_yes_outcome": "San Francisco Giants",
                "polymarket_no_outcome": "Athletics",
                "polymarket_event_title": "Athletics vs. San Francisco Giants",
                "polymarket_title": "Athletics vs. San Francisco Giants",
                "kalshi_ticker": "KXMLBGAME-26JUN242145ATHSF-SF",
                "kalshi_event_title": "Athletics vs SF Winner?",
                "kalshi_title": "Athletics vs SF Winner?",
            },
            {
                "mapping_id": "pm-ath",
                "match_score": 91,
                "semantic_combined_score": 90,
                "suggestion_method": "semantic",
                "market_type": "match_winner",
                "event_name": "Athletics vs. San Francisco Giants",
                "event_match_key": "2026-06-27|athletics|san-francisco-giants",
                "outcome_label": "Athletics",
                "proposition": "Athletics to win",
                "polymarket_market_id": "0xmlb",
                "polymarket_slug": "mlb-athletics-giants",
                "polymarket_yes_token_id": "pm-ath",
                "polymarket_no_token_id": "pm-sf",
                "polymarket_yes_outcome": "Athletics",
                "polymarket_no_outcome": "San Francisco Giants",
                "polymarket_event_title": "Athletics vs. San Francisco Giants",
                "polymarket_title": "Athletics vs. San Francisco Giants",
                "kalshi_ticker": "KXMLBGAME-26JUN242145ATHSF-ATH",
                "kalshi_event_title": "Athletics vs SF Winner?",
                "kalshi_title": "Athletics vs SF Winner?",
            },
            {
                "mapping_id": "other",
                "match_score": 100,
                "semantic_combined_score": 100,
                "suggestion_method": "semantic",
                "market_type": "match_winner",
                "event_name": "Other Game",
                "event_match_key": "2026-06-28|other",
                "outcome_label": "Other",
                "polymarket_market_id": "0xother",
                "polymarket_slug": "other-game",
                "polymarket_event_title": "Other Game",
                "kalshi_ticker": "KXOTHER",
                "kalshi_event_title": "Other Game",
            },
        ]
    )


def _event_top_match_frame() -> pd.DataFrame:
    base = {
        "pm_event_key": "polymarket|2026-06-27|athletics vs san francisco giants",
        "pm_event_title": "Athletics vs. San Francisco Giants",
        "pm_row_count": "2",
        "pm_market_types": "match_winner",
        "pm_sample_outcomes": "Athletics | San Francisco Giants",
    }
    rows = [
        {
            **base,
            "rank": "1",
            "kalshi_event_key": "kalshi|2026-06-27|athletics vs sf winner",
            "kalshi_event_title": "Athletics vs SF Winner?",
            "kalshi_row_count": "1",
            "kalshi_market_types": "match_winner",
            "kalshi_sample_outcomes": "San Francisco Giants",
            "event_embedding_score": "88",
        },
        *[
            {
                **base,
                "rank": str(index),
                "kalshi_event_key": f"kalshi|2026-06-27|other-{index}",
                "kalshi_event_title": f"Other candidate {index - 1}",
                "kalshi_row_count": "1",
                "kalshi_market_types": "match_winner",
                "kalshi_sample_outcomes": "Other",
                "event_embedding_score": str(81 - index),
            }
            for index in range(2, 7)
        ],
        {
            **base,
            "rank": "7",
            "kalshi_event_key": "kalshi|2026-06-27|below-threshold",
            "kalshi_event_title": "Below threshold",
            "kalshi_row_count": "1",
            "kalshi_market_types": "match_winner",
            "kalshi_sample_outcomes": "Other",
            "event_embedding_score": "67.9",
        },
    ]
    return pd.DataFrame(rows)
