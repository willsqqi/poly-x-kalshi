from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd

from prediction_market.dashboard_data import load_dashboard_tables, summarize_dashboard


DEFAULT_SOURCE = "data/cross_sports_arbitrage"
RECOMMENDATION_EXPORT_COLUMNS = [
    "mapping_id",
    "recommendation",
    "confidence",
    "reason",
    "event_name",
    "proposition",
    "snapshots",
    "observations",
    "alert_count",
    "alert_rate",
    "positive_edge_count",
    "positive_edge_rate",
    "best_net_edge",
    "median_net_edge",
    "latest_net_edge",
    "latest_exclusion_reason",
    "worst_min_depth",
]


def generate_viability_report(source: str | Path = DEFAULT_SOURCE, *, top_n: int = 10, prefer_history: bool = True) -> str:
    summary = load_viability_summary(source, prefer_history=prefer_history)
    return _render_viability_report(source, summary=summary, top_n=top_n, prefer_history=prefer_history)


def load_viability_summary(source: str | Path = DEFAULT_SOURCE, *, prefer_history: bool = True) -> dict[str, Any]:
    tables = load_dashboard_tables(source, prefer_history=prefer_history)
    return summarize_dashboard(tables)


def recommendation_export_frame(summary: dict[str, Any], actions: set[str] | None = None) -> pd.DataFrame:
    frame = summary.get("pair_recommendations", pd.DataFrame()).copy()
    if frame.empty:
        return pd.DataFrame(columns=RECOMMENDATION_EXPORT_COLUMNS)
    if actions:
        normalized_actions = {action.strip().casefold() for action in actions if action.strip()}
        frame = frame[frame["recommendation"].fillna("").astype(str).str.casefold().isin(normalized_actions)]
    columns = [column for column in RECOMMENDATION_EXPORT_COLUMNS if column in frame.columns]
    return frame[columns].reset_index(drop=True)


def write_recommendation_exports(
    summary: dict[str, Any],
    *,
    csv_path: str | Path | None = None,
    json_path: str | Path | None = None,
    actions: set[str] | None = None,
) -> dict[str, str]:
    frame = recommendation_export_frame(summary, actions=actions)
    written: dict[str, str] = {}
    if csv_path:
        path = Path(csv_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        frame.to_csv(path, index=False)
        written["csv"] = str(path)
    if json_path:
        path = Path(json_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        records = _json_records(frame)
        path.write_text(json.dumps(records, indent=2, sort_keys=True), encoding="utf-8")
        written["json"] = str(path)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a viability report from Poly x Kalshi scanner outputs.")
    parser.add_argument("--source", default=DEFAULT_SOURCE, help="Local output directory or gs:// output prefix.")
    parser.add_argument("--output", help="Optional Markdown report path to write.")
    parser.add_argument("--recommendations-csv", help="Optional CSV path for machine-readable pair recommendations.")
    parser.add_argument("--recommendations-json", help="Optional JSON path for machine-readable pair recommendations.")
    parser.add_argument(
        "--recommendation-action",
        action="append",
        default=[],
        help="Filter recommendation exports to one action. Repeat for multiple actions, e.g. monitor and watch.",
    )
    parser.add_argument("--top-n", type=int, default=10, help="Rows to include in ranking sections.")
    parser.add_argument("--latest", action="store_true", help="Use processed/latest tables instead of appendable history.")
    args = parser.parse_args(argv)

    summary = load_viability_summary(args.source, prefer_history=not args.latest)
    report = _render_viability_report(args.source, summary=summary, top_n=args.top_n, prefer_history=not args.latest)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(report, encoding="utf-8")
    write_recommendation_exports(
        summary,
        csv_path=args.recommendations_csv,
        json_path=args.recommendations_json,
        actions=set(args.recommendation_action),
    )
    print(report)
    return 0


def _render_viability_report(
    source: str | Path,
    *,
    summary: dict[str, Any],
    top_n: int,
    prefer_history: bool,
) -> str:
    viability = summary["viability_summary"]
    pair_viability = summary["pair_viability"]
    run_trend = summary["run_trend"]
    latest_run = summary["latest_run"]
    exclusions = summary["exclusion_counts"]
    sensitivity = summary["sensitivity_grid"]
    recommendations = summary["pair_recommendations"]
    coverage = summary["coverage_funnel"]
    keyword_coverage = summary["keyword_coverage"]

    lines = [
        "# Poly x Kalshi Viability Report",
        "",
        f"- Source: `{source}`",
        f"- Mode: `{'history' if prefer_history else 'latest'}`",
    ]
    if latest_run:
        lines.extend(
            [
                f"- Latest run: `{latest_run.get('run_id', 'unknown')}`",
                f"- Latest status: `{latest_run.get('status', 'unknown')}`",
            ]
        )
    lines.extend(
        [
            "",
            "## Verdict",
            "",
            _verdict_text(viability),
            "",
            "## Core Metrics",
            "",
            "| Metric | Value |",
            "| --- | ---: |",
            f"| Snapshots | {viability['snapshot_count']} |",
            f"| Pairs observed | {viability['pair_count']} |",
            f"| Directional signals | {viability['direction_count']} |",
            f"| Executable alerts | {viability['alert_count']} |",
            f"| Alert rate | {_percent(viability['alert_rate'])} |",
            f"| Positive-edge count | {viability['positive_edge_count']} |",
            f"| Positive-edge rate | {_percent(viability['positive_edge_rate'])} |",
            f"| Best net edge | {_decimal(viability['best_net_edge'])} |",
            f"| Median net edge | {_decimal(viability['median_net_edge'])} |",
            f"| P95 net edge | {_decimal(viability['p95_net_edge'])} |",
            f"| Price available rate | {_percent(viability['price_available_rate'])} |",
            f"| Liquidity-ok rate | {_percent(viability['liquidity_ok_rate'])} |",
            "",
            "## Coverage Funnel",
            "",
            _markdown_table(
                coverage,
                [
                    "market_type",
                    "candidate_count",
                    "polymarket_candidates",
                    "kalshi_candidates",
                    "suggested_mapping_count",
                    "approved_mapping_count",
                    "monitor_count",
                    "watch_count",
                    "pause_count",
                    "suggestion_rate",
                    "approval_rate",
                ],
                top_n,
            ),
            "",
            "## Keyword Coverage",
            "",
            _markdown_table(
                keyword_coverage,
                [
                    "keyword",
                    "candidate_count",
                    "polymarket_candidates",
                    "kalshi_candidates",
                    "match_winner_candidates",
                    "total_candidates",
                    "spread_candidates",
                    "other_candidates",
                ],
                top_n,
            ),
            "",
            "## Pair Recommendations",
            "",
            _markdown_table(
                recommendations,
                [
                    "recommendation",
                    "confidence",
                    "reason",
                    "event_name",
                    "proposition",
                    "snapshots",
                    "alert_rate",
                    "positive_edge_rate",
                    "best_net_edge",
                    "latest_net_edge",
                    "worst_min_depth",
                ],
                top_n,
            ),
            "",
            "## Top Pairs",
            "",
            _markdown_table(
                pair_viability,
                [
                    "event_name",
                    "proposition",
                    "snapshots",
                    "alert_count",
                    "positive_edge_count",
                    "best_net_edge",
                    "latest_net_edge",
                    "latest_exclusion_reason",
                    "worst_min_depth",
                ],
                top_n,
            ),
            "",
            "## Exclusion Reasons",
            "",
            _markdown_table(exclusions, list(exclusions.columns), top_n),
            "",
            "## Buffer Sensitivity",
            "",
            _markdown_table(
                sensitivity,
                [
                    "total_buffer",
                    "min_net_edge",
                    "min_depth",
                    "eligible_signals",
                    "eligible_rate",
                    "eligible_pairs",
                    "price_rows",
                    "best_repriced_net_edge",
                ],
                top_n,
            ),
            "",
            "## Run Trend",
            "",
            _markdown_table(
                run_trend,
                [
                    "run_id",
                    "finished_at",
                    "status",
                    "candidate_count",
                    "approved_mapping_count",
                    "orderbook_count",
                    "alert_count",
                ],
                top_n,
                tail=True,
            ),
            "",
            "## Interpretation",
            "",
            "- `alert_rate` is the strictest measure: it only counts signals that survived fees, slippage, threshold, and depth filters.",
            "- `positive_edge_rate` is a weaker early signal: it shows whether raw gaps appear before all execution filters pass.",
            "- Repeated `edge_below_threshold` means venues are efficiently aligned under current buffers.",
            "- Repeated `insufficient_depth` means the market may show prices but not enough executable size.",
            "- `Buffer Sensitivity` recomputes historical eligibility from gross two-leg costs. It is not a promise of executable profit; it only shows how close the observed prices were.",
            "- `Pair Recommendations` are research operations labels: `monitor` for stronger evidence, `watch` for weak/near evidence, `pause` for low-value pairs, and `needs_more_data` for short histories.",
            "- A pair needs repeated positive edges and reliable depth before it deserves more engineering investment.",
        ]
    )
    return "\n".join(lines).strip() + "\n"


def _verdict_text(viability: dict[str, Any]) -> str:
    snapshots = int(viability.get("snapshot_count") or 0)
    alert_count = int(viability.get("alert_count") or 0)
    positive_edge_count = int(viability.get("positive_edge_count") or 0)
    best_net_edge = viability.get("best_net_edge")

    if snapshots == 0:
        return "No scanner history is available yet. Run local or cloud snapshots before judging viability."
    if alert_count > 0:
        return "Viable enough for deeper review: at least one conservative executable alert was observed."
    if positive_edge_count > 0:
        return "Weak but worth monitoring: positive raw net edges appeared, but no signal passed all execution filters."
    if best_net_edge is not None and best_net_edge >= -0.02:
        return "No executable edge yet, but markets are close. Continue burn-in around high-volatility windows."
    return "Not viable in this sample: observed gaps did not overcome conservative buffers."


def _markdown_table(frame: pd.DataFrame, columns: list[str], limit: int, *, tail: bool = False) -> str:
    if frame.empty:
        return "_No rows._"
    present = [column for column in columns if column in frame.columns]
    if not present:
        return "_No displayable columns._"
    output = frame[present].copy()
    output = output.tail(limit) if tail else output.head(limit)
    output = output.fillna("")
    header = "| " + " | ".join(_escape_markdown_cell(column) for column in output.columns) + " |"
    separator = "| " + " | ".join("---" for _ in output.columns) + " |"
    rows = [
        "| " + " | ".join(_escape_markdown_cell(_format_cell(value)) for value in row) + " |"
        for row in output.itertuples(index=False, name=None)
    ]
    return "\n".join([header, separator, *rows])


def _percent(value: Any) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"{float(value):.2%}"


def _decimal(value: Any) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"{float(value):.4f}"


def _escape_markdown_cell(value: Any) -> str:
    text = str(value)
    text = text.replace("\n", " ").replace("|", "\\|")
    return text


def _format_cell(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def _json_records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    output = frame.copy()
    output = output.astype(object).where(pd.notna(output), None)
    records: list[dict[str, Any]] = []
    for record in output.to_dict(orient="records"):
        records.append({key: _json_value(value) for key, value in record.items()})
    return records


def _json_value(value: Any) -> Any:
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value


if __name__ == "__main__":
    raise SystemExit(main())
