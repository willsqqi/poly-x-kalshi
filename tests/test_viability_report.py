from __future__ import annotations

import json

import pandas as pd

from prediction_market.viability_report import generate_viability_report, main, recommendation_export_frame, write_recommendation_exports


def test_generate_viability_report_from_history(tmp_path) -> None:
    processed = tmp_path / "processed"
    processed.mkdir()
    pd.DataFrame(
        [
            {
                "run_id": "run-1",
                "finished_at": "2026-06-24T00:00:00Z",
                "status": "succeeded",
                "candidate_count": 2,
                "approved_mapping_count": 1,
                "orderbook_count": 2,
                "alert_count": 1,
            }
        ]
    ).to_parquet(processed / "scanner_runs.parquet", index=False)
    pd.DataFrame(
        [
            {
                "run_id": "run-1",
                "detected_at": "2026-06-24T00:00:00Z",
                "mapping_id": "pair-1",
                "event_name": "A vs B",
                "proposition": "A wins",
                "is_alert": True,
                "price_available": True,
                "liquidity_ok": True,
                "net_edge": 0.04,
                "min_depth": 50,
                "exclusion_reason": "",
            }
        ]
    ).to_parquet(processed / "strategy_signals.parquet", index=False)
    pd.DataFrame([{"mapping_id": "pair-1", "is_alert": True}]).to_parquet(
        processed / "arbitrage_alerts.parquet", index=False
    )

    report = generate_viability_report(tmp_path)

    assert "Viable enough for deeper review" in report
    assert "| Executable alerts | 1 |" in report
    assert "A vs B" in report


def test_viability_report_cli_writes_output(tmp_path, capsys) -> None:
    processed = tmp_path / "processed"
    processed.mkdir()
    pd.DataFrame([{"run_id": "run-1", "finished_at": "2026-06-24T00:00:00Z", "status": "succeeded"}]).to_csv(
        processed / "scanner_runs.csv", index=False
    )
    pd.DataFrame(
        [
            {
                "run_id": "run-1",
                "mapping_id": "pair-1",
                "event_name": "A vs B",
                "proposition": "A wins",
                "is_alert": False,
                "net_edge": -0.01,
                "exclusion_reason": "edge_below_threshold",
            }
        ]
    ).to_csv(processed / "strategy_signals.csv", index=False)
    output_path = tmp_path / "reports" / "report.md"

    exit_code = main(["--source", str(tmp_path), "--output", str(output_path), "--top-n", "3"])

    assert exit_code == 0
    assert output_path.exists()
    assert "No executable edge yet" in output_path.read_text()
    assert "Poly x Kalshi Viability Report" in capsys.readouterr().out


def test_recommendation_exports_filter_actions(tmp_path) -> None:
    summary = {
        "pair_recommendations": pd.DataFrame(
            [
                {
                    "mapping_id": "pair-monitor",
                    "recommendation": "monitor",
                    "confidence": "high",
                    "reason": "conservative_alert_observed",
                    "event_name": "A vs B",
                    "proposition": "A wins",
                    "snapshots": 10,
                    "observations": 20,
                    "alert_count": 2,
                    "alert_rate": 0.1,
                    "positive_edge_count": 3,
                    "positive_edge_rate": 0.15,
                    "best_net_edge": 0.04,
                    "median_net_edge": -0.01,
                    "latest_net_edge": 0.02,
                    "latest_exclusion_reason": "",
                    "worst_min_depth": 50,
                },
                {
                    "mapping_id": "pair-pause",
                    "recommendation": "pause",
                    "confidence": "medium",
                    "reason": "consistently_below_edge_threshold",
                    "event_name": "C vs D",
                    "proposition": "C wins",
                    "snapshots": 10,
                    "observations": 20,
                    "alert_count": 0,
                    "alert_rate": 0.0,
                    "positive_edge_count": 0,
                    "positive_edge_rate": 0.0,
                    "best_net_edge": -0.02,
                    "median_net_edge": -0.03,
                    "latest_net_edge": -0.04,
                    "latest_exclusion_reason": "edge_below_threshold",
                    "worst_min_depth": 100,
                },
            ]
        )
    }
    csv_path = tmp_path / "recommendations.csv"
    json_path = tmp_path / "recommendations.json"

    written = write_recommendation_exports(
        summary,
        csv_path=csv_path,
        json_path=json_path,
        actions={"monitor"},
    )
    exported = recommendation_export_frame(summary, actions={"monitor"})

    assert written == {"csv": str(csv_path), "json": str(json_path)}
    assert exported["mapping_id"].tolist() == ["pair-monitor"]
    assert pd.read_csv(csv_path)["mapping_id"].tolist() == ["pair-monitor"]
    assert json.loads(json_path.read_text())[0]["mapping_id"] == "pair-monitor"


def test_viability_report_cli_writes_recommendation_exports(tmp_path) -> None:
    processed = tmp_path / "processed"
    processed.mkdir()
    pd.DataFrame([{"run_id": "run-1", "finished_at": "2026-06-24T00:00:00Z", "status": "succeeded"}]).to_csv(
        processed / "scanner_runs.csv", index=False
    )
    pd.DataFrame(
        [
            {
                "run_id": "run-1",
                "mapping_id": "pair-1",
                "event_name": "A vs B",
                "proposition": "A wins",
                "is_alert": False,
                "net_edge": -0.01,
                "min_depth": 10,
                "exclusion_reason": "edge_below_threshold",
            }
        ]
    ).to_csv(processed / "strategy_signals.csv", index=False)
    csv_path = tmp_path / "recommendations.csv"
    json_path = tmp_path / "recommendations.json"

    exit_code = main(
        [
            "--source",
            str(tmp_path),
            "--recommendations-csv",
            str(csv_path),
            "--recommendations-json",
            str(json_path),
        ]
    )

    assert exit_code == 0
    assert csv_path.exists()
    assert json_path.exists()
