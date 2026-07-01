from __future__ import annotations

import pandas as pd

from prediction_market.dashboard_data import (
    build_coverage_funnel,
    build_keyword_coverage,
    build_pair_viability,
    build_pair_recommendations,
    build_sensitivity_grid,
    build_viability_summary,
    filter_signals,
    load_dashboard_tables,
    pair_history,
    summarize_dashboard,
)


def test_dashboard_loader_reads_latest_csv_tables(tmp_path) -> None:
    latest = tmp_path / "processed" / "latest"
    latest.mkdir(parents=True)
    pd.DataFrame(
        [
            {
                "run_id": "run-1",
                "finished_at": "2026-06-24T00:00:00Z",
                "status": "succeeded",
                "approved_mapping_count": 1,
                "alert_count": 0,
            }
        ]
    ).to_csv(latest / "scanner_runs.csv", index=False)
    pd.DataFrame([{"mapping_id": "pair-1", "status": "approved"}]).to_csv(
        latest / "manual_mappings_snapshot.csv", index=False
    )
    pd.DataFrame([{"mapping_id": "pair-1", "venue": "kalshi"}]).to_csv(latest / "orderbook_snapshots.csv", index=False)
    pd.DataFrame(
        [
            {
                "mapping_id": "pair-1",
                "event_name": "Team A vs Team B",
                "direction": "buy_polymarket_yes_buy_kalshi_no",
                "is_alert": False,
                "net_edge": -0.02,
                "exclusion_reason": "edge_below_threshold",
            },
            {
                "mapping_id": "pair-1",
                "event_name": "Team A vs Team B",
                "direction": "buy_kalshi_yes_buy_polymarket_no",
                "is_alert": True,
                "net_edge": 0.04,
                "exclusion_reason": "",
            },
        ]
    ).to_csv(latest / "strategy_signals.csv", index=False)
    pd.DataFrame([{"mapping_id": "pair-1", "is_alert": True}]).to_csv(latest / "arbitrage_alerts.csv", index=False)

    tables = load_dashboard_tables(tmp_path)
    summary = summarize_dashboard(tables)

    assert summary["run_count"] == 1
    assert summary["approved_mapping_count"] == 1
    assert summary["orderbook_count"] == 1
    assert summary["alert_count"] == 1
    assert summary["near_misses"].iloc[0]["net_edge"] == 0.04
    assert tables["scanner_runs"].source_path.endswith("scanner_runs.csv")


def test_dashboard_loader_prefers_history_for_appendable_tables(tmp_path) -> None:
    latest = tmp_path / "processed" / "latest"
    latest.mkdir(parents=True)
    processed = tmp_path / "processed"
    pd.DataFrame([{"run_id": "latest"}]).to_csv(latest / "scanner_runs.csv", index=False)
    pd.DataFrame([{"run_id": "history-1"}, {"run_id": "history-2"}]).to_csv(processed / "scanner_runs.csv", index=False)

    latest_tables = load_dashboard_tables(tmp_path, table_names=("scanner_runs",))
    historical_tables = load_dashboard_tables(tmp_path, table_names=("scanner_runs",), prefer_history=True)

    assert latest_tables["scanner_runs"].frame["run_id"].tolist() == ["latest"]
    assert historical_tables["scanner_runs"].frame["run_id"].tolist() == ["history-1", "history-2"]


def test_filter_signals_search_edge_and_alerts_only() -> None:
    frame = pd.DataFrame(
        [
            {"mapping_id": "a", "event_name": "France vs Brazil", "is_alert": "true", "net_edge": "0.05"},
            {"mapping_id": "b", "event_name": "Spain vs Japan", "is_alert": "false", "net_edge": "0.01"},
        ]
    )

    filtered = filter_signals(frame, search="france", min_net_edge=0.02, alerts_only=True)

    assert len(filtered) == 1
    assert filtered.iloc[0]["mapping_id"] == "a"


def test_pair_history_sorts_by_detected_at() -> None:
    frame = pd.DataFrame(
        [
            {"mapping_id": "pair-1", "detected_at": "2026-06-24T00:01:00Z", "net_edge": 0.01, "direction": "b"},
            {"mapping_id": "pair-1", "detected_at": "2026-06-24T00:00:00Z", "net_edge": 0.02, "direction": "a"},
            {"mapping_id": "pair-2", "detected_at": "2026-06-24T00:00:00Z", "net_edge": 0.03, "direction": "a"},
        ]
    )

    history = pair_history(frame, "pair-1")

    assert history["net_edge"].tolist() == [0.02, 0.01]


def test_viability_summary_and_pair_ranking() -> None:
    frame = pd.DataFrame(
        [
            {
                "run_id": "run-1",
                "mapping_id": "pair-1",
                "event_name": "A vs B",
                "proposition": "A wins",
                "is_alert": True,
                "net_edge": 0.04,
                "min_depth": 25,
                "price_available": True,
                "liquidity_ok": True,
                "exclusion_reason": "",
            },
            {
                "run_id": "run-2",
                "mapping_id": "pair-1",
                "event_name": "A vs B",
                "proposition": "A wins",
                "is_alert": False,
                "net_edge": -0.01,
                "min_depth": 15,
                "price_available": True,
                "liquidity_ok": True,
                "exclusion_reason": "edge_below_threshold",
            },
            {
                "run_id": "run-1",
                "mapping_id": "pair-2",
                "event_name": "C vs D",
                "proposition": "C wins",
                "is_alert": False,
                "net_edge": -0.10,
                "min_depth": 0,
                "price_available": True,
                "liquidity_ok": False,
                "exclusion_reason": "insufficient_depth",
            },
        ]
    )

    summary = build_viability_summary(frame)
    ranking = build_pair_viability(frame)

    assert summary["snapshot_count"] == 2
    assert summary["pair_count"] == 2
    assert summary["alert_rate"] == 1 / 3
    assert summary["positive_edge_rate"] == 1 / 3
    assert summary["best_net_edge"] == 0.04
    assert ranking.iloc[0]["mapping_id"] == "pair-1"
    assert ranking.iloc[0]["positive_edge_count"] == 1
    assert ranking.iloc[0]["worst_min_depth"] == 15


def test_sensitivity_grid_reprices_gross_costs() -> None:
    frame = pd.DataFrame(
        [
            {
                "mapping_id": "pair-1",
                "gross_cost": 0.98,
                "min_depth": 50,
                "is_alert": False,
            },
            {
                "mapping_id": "pair-2",
                "gross_cost": 1.01,
                "min_depth": 500,
                "is_alert": False,
            },
            {
                "mapping_id": "pair-3",
                "gross_cost": None,
                "min_depth": 1000,
                "is_alert": False,
            },
        ]
    )

    grid = build_sensitivity_grid(
        frame,
        total_buffers=(0.0, 0.02),
        min_net_edges=(0.0, 0.02),
        min_depths=(0.0, 100.0),
    )

    relaxed = grid[
        (grid["total_buffer"] == 0.0)
        & (grid["min_net_edge"] == 0.0)
        & (grid["min_depth"] == 0.0)
    ].iloc[0]
    strict_depth = grid[
        (grid["total_buffer"] == 0.0)
        & (grid["min_net_edge"] == 0.0)
        & (grid["min_depth"] == 100.0)
    ].iloc[0]
    buffered = grid[
        (grid["total_buffer"] == 0.02)
        & (grid["min_net_edge"] == 0.02)
        & (grid["min_depth"] == 0.0)
    ].iloc[0]

    assert relaxed["eligible_signals"] == 1
    assert relaxed["eligible_pairs"] == 1
    assert relaxed["price_rows"] == 2
    assert strict_depth["eligible_signals"] == 0
    assert buffered["eligible_signals"] == 0


def test_pair_recommendations_classify_actions() -> None:
    frame = pd.DataFrame(
        [
            {
                "run_id": "run-1",
                "mapping_id": "monitor-pair",
                "event_name": "A vs B",
                "proposition": "A wins",
                "is_alert": True,
                "net_edge": 0.04,
                "min_depth": 100,
                "exclusion_reason": "",
            },
            {
                "run_id": "run-2",
                "mapping_id": "monitor-pair",
                "event_name": "A vs B",
                "proposition": "A wins",
                "is_alert": False,
                "net_edge": -0.01,
                "min_depth": 100,
                "exclusion_reason": "edge_below_threshold",
            },
            {
                "run_id": "run-3",
                "mapping_id": "monitor-pair",
                "event_name": "A vs B",
                "proposition": "A wins",
                "is_alert": False,
                "net_edge": -0.02,
                "min_depth": 100,
                "exclusion_reason": "edge_below_threshold",
            },
            {
                "run_id": "run-1",
                "mapping_id": "watch-pair",
                "event_name": "C vs D",
                "proposition": "C wins",
                "is_alert": False,
                "net_edge": 0.01,
                "min_depth": 100,
                "exclusion_reason": "edge_below_threshold",
            },
            {
                "run_id": "run-2",
                "mapping_id": "watch-pair",
                "event_name": "C vs D",
                "proposition": "C wins",
                "is_alert": False,
                "net_edge": -0.01,
                "min_depth": 100,
                "exclusion_reason": "edge_below_threshold",
            },
            {
                "run_id": "run-3",
                "mapping_id": "watch-pair",
                "event_name": "C vs D",
                "proposition": "C wins",
                "is_alert": False,
                "net_edge": -0.02,
                "min_depth": 100,
                "exclusion_reason": "edge_below_threshold",
            },
            {
                "run_id": "run-1",
                "mapping_id": "pause-pair",
                "event_name": "E vs F",
                "proposition": "E wins",
                "is_alert": False,
                "net_edge": -0.04,
                "min_depth": 100,
                "exclusion_reason": "edge_below_threshold",
            },
            {
                "run_id": "run-2",
                "mapping_id": "pause-pair",
                "event_name": "E vs F",
                "proposition": "E wins",
                "is_alert": False,
                "net_edge": -0.03,
                "min_depth": 100,
                "exclusion_reason": "edge_below_threshold",
            },
            {
                "run_id": "run-3",
                "mapping_id": "pause-pair",
                "event_name": "E vs F",
                "proposition": "E wins",
                "is_alert": False,
                "net_edge": -0.05,
                "min_depth": 100,
                "exclusion_reason": "edge_below_threshold",
            },
            {
                "run_id": "run-1",
                "mapping_id": "new-pair",
                "event_name": "G vs H",
                "proposition": "G wins",
                "is_alert": False,
                "net_edge": 0.03,
                "min_depth": 100,
                "exclusion_reason": "edge_below_threshold",
            },
        ]
    )

    recommendations = build_pair_recommendations(frame).set_index("mapping_id")

    assert recommendations.loc["monitor-pair", "recommendation"] == "monitor"
    assert recommendations.loc["watch-pair", "recommendation"] == "watch"
    assert recommendations.loc["pause-pair", "recommendation"] == "pause"
    assert recommendations.loc["new-pair", "recommendation"] == "needs_more_data"


def test_coverage_funnel_infers_approved_market_type_from_suggestions() -> None:
    candidates = pd.DataFrame(
        [
            {"venue": "polymarket", "market_type": "match_winner"},
            {"venue": "kalshi", "market_type": "match_winner"},
            {"venue": "polymarket", "market_type": "total"},
        ]
    )
    suggestions = pd.DataFrame(
        [
            {"mapping_id": "pair-1", "market_type": "match_winner"},
            {"mapping_id": "pair-2", "market_type": "total"},
        ]
    )
    mappings = pd.DataFrame(
        [
            {"mapping_id": "pair-1", "status": "approved"},
            {"mapping_id": "pair-2", "status": "review_required"},
        ]
    )
    recommendations = pd.DataFrame(
        [
            {"mapping_id": "pair-1", "recommendation": "monitor"},
            {"mapping_id": "pair-2", "recommendation": "pause"},
        ]
    )

    funnel = build_coverage_funnel(candidates, suggestions, mappings, recommendations).set_index("market_type")

    assert funnel.loc["match_winner", "candidate_count"] == 2
    assert funnel.loc["match_winner", "polymarket_candidates"] == 1
    assert funnel.loc["match_winner", "kalshi_candidates"] == 1
    assert funnel.loc["match_winner", "suggested_mapping_count"] == 1
    assert funnel.loc["match_winner", "approved_mapping_count"] == 1
    assert funnel.loc["match_winner", "monitor_count"] == 1
    assert funnel.loc["match_winner", "suggestion_rate"] == 0.5
    assert funnel.loc["match_winner", "approval_rate"] == 1.0
    assert funnel.loc["total", "approved_mapping_count"] == 0
    assert funnel.loc["total", "pause_count"] == 1


def test_coverage_funnel_infers_market_type_for_mapping_not_in_suggestions() -> None:
    mappings = pd.DataFrame(
        [
            {
                "mapping_id": "fifwc-team-a-team-b-draw__kalshi-tie",
                "status": "approved",
                "event_name": "Team A vs Team B",
                "proposition": "Team A vs Team B to end in a draw in regular time",
            }
        ]
    )
    recommendations = pd.DataFrame(
        [
            {
                "mapping_id": "fifwc-team-a-team-b-draw__kalshi-tie",
                "recommendation": "pause",
                "proposition": "Team A vs Team B to end in a draw in regular time",
            }
        ]
    )

    funnel = build_coverage_funnel(pd.DataFrame(), pd.DataFrame(), mappings, recommendations).set_index("market_type")

    assert funnel.loc["match_winner", "approved_mapping_count"] == 1
    assert funnel.loc["match_winner", "pause_count"] == 1


def test_keyword_coverage_explodes_keyword_hits() -> None:
    candidates = pd.DataFrame(
        [
            {"venue": "polymarket", "market_type": "match_winner", "keyword_hits": "world cup,soccer"},
            {"venue": "kalshi", "market_type": "total", "keyword_hits": "soccer"},
            {"venue": "polymarket", "market_type": "spread", "keyword_hits": ""},
        ]
    )

    coverage = build_keyword_coverage(candidates).set_index("keyword")

    assert coverage.loc["soccer", "candidate_count"] == 2
    assert coverage.loc["soccer", "polymarket_candidates"] == 1
    assert coverage.loc["soccer", "kalshi_candidates"] == 1
    assert coverage.loc["world cup", "match_winner_candidates"] == 1
    assert coverage.loc["missing", "spread_candidates"] == 1
