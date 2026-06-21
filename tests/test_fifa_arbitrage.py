from __future__ import annotations

from pathlib import Path

import httpx
import pandas as pd

from prediction_market.fifa_arbitrage import (
    ALERT_COLUMNS,
    MAPPING_COLUMNS,
    approved_mappings,
    fetch_kalshi_fifa_markets,
    fetch_mapped_orderbooks,
    fetch_polymarket_fifa_markets,
    normalize_fifa_candidates,
    run_fifa_snapshot,
    score_cross_market_arbitrage,
    snapshot_cli_main,
    validate_manual_mappings,
    watch_fifa_arbitrage,
)


def test_fifa_market_discovery_filters_both_venues() -> None:
    with httpx.Client(transport=httpx.MockTransport(_discovery_handler)) as client:
        polymarket = fetch_polymarket_fifa_markets(client, max_markets=10, page_size=10)
        kalshi = fetch_kalshi_fifa_markets(client, max_markets=10, page_size=10)

    assert [market["conditionId"] for market in polymarket] == ["0xfifa"]
    assert [market["ticker"] for market in kalshi] == ["KXWCFINAL-26USA"]

    frame = normalize_fifa_candidates(polymarket, kalshi, run_id="test-run", retrieved_at="2026-06-21T00:00:00Z")

    assert set(frame["venue"]) == {"polymarket", "kalshi"}
    assert frame.loc[frame["venue"] == "polymarket", "yes_token_id"].iloc[0] == "pm-yes"
    assert "world cup" in frame.loc[frame["venue"] == "kalshi", "keyword_hits"].iloc[0]


def test_manual_mapping_validation_requires_approval_and_settlement_notes() -> None:
    mappings = pd.DataFrame(
        [
            _mapping_row(mapping_id="approved-good"),
            _mapping_row(mapping_id="approved-bad", draw_handling="unclear"),
            _mapping_row(mapping_id="draft-row", status="draft"),
        ],
        columns=MAPPING_COLUMNS,
    )

    validated = validate_manual_mappings(mappings)
    approved = approved_mappings(mappings)

    assert bool(validated.loc[validated["mapping_id"] == "approved-good", "is_approved"].iloc[0]) is True
    assert "unclear_draw_handling" in validated.loc[validated["mapping_id"] == "approved-bad", "validation_errors"].iloc[0]
    assert bool(validated.loc[validated["mapping_id"] == "draft-row", "is_approved"].iloc[0]) is False
    assert approved["mapping_id"].tolist() == ["approved-good"]


def test_orderbook_normalization_converts_yes_no_asks_for_both_venues() -> None:
    mappings = pd.DataFrame([_mapping_row()], columns=MAPPING_COLUMNS)
    eligible = approved_mappings(mappings)

    with httpx.Client(transport=httpx.MockTransport(_orderbook_handler)) as client:
        frame = fetch_mapped_orderbooks(client, eligible, run_id="run-1", retrieved_at="2026-06-21T00:00:00Z")

    pm = frame[frame["venue"] == "polymarket"].iloc[0]
    ks = frame[frame["venue"] == "kalshi"].iloc[0]

    assert pm["yes_bid"] == 0.37
    assert pm["yes_ask"] == 0.40
    assert pm["no_bid"] == 0.56
    assert pm["no_ask"] == 0.59
    assert pm["yes_ask_depth"] == 25.0
    assert ks["yes_bid"] == 0.48
    assert ks["yes_ask"] == 0.47
    assert ks["no_bid"] == 0.53
    assert ks["no_ask"] == 0.52
    assert ks["yes_ask_depth"] == 30.0
    assert ks["no_ask_depth"] == 40.0


def test_arbitrage_scoring_flags_both_directions_and_exclusions() -> None:
    mappings = approved_mappings(pd.DataFrame([_mapping_row()], columns=MAPPING_COLUMNS))
    orderbooks = pd.DataFrame(
        [
            {
                "run_id": "run-1",
                "retrieved_at": "2026-06-21T00:00:00Z",
                "mapping_id": "map-1",
                "venue": "polymarket",
                "market_id": "0xfifa",
                "yes_bid": 0.37,
                "yes_ask": 0.40,
                "no_bid": 0.56,
                "no_ask": 0.70,
                "yes_bid_depth": 20.0,
                "yes_ask_depth": 25.0,
                "no_bid_depth": 20.0,
                "no_ask_depth": 25.0,
                "raw_orderbook": "{}",
                "error": "",
            },
            {
                "run_id": "run-1",
                "retrieved_at": "2026-06-21T00:00:00Z",
                "mapping_id": "map-1",
                "venue": "kalshi",
                "market_id": "KXWCFINAL-26USA",
                "yes_bid": 0.48,
                "yes_ask": 0.44,
                "no_bid": 0.56,
                "no_ask": 0.52,
                "yes_bid_depth": 40.0,
                "yes_ask_depth": 30.0,
                "no_bid_depth": 30.0,
                "no_ask_depth": 40.0,
                "raw_orderbook": "{}",
                "error": "",
            },
        ]
    )

    scored = score_cross_market_arbitrage(
        mappings,
        orderbooks,
        run_id="run-1",
        min_net_edge=0.02,
        slippage_buffer_per_leg=0.005,
        fee_buffer_total=0.01,
        min_depth_per_leg=10,
        detected_at="2026-06-21T00:00:00Z",
    )

    by_direction = {row["direction"]: row for _, row in scored.iterrows()}
    assert bool(by_direction["buy_polymarket_yes_buy_kalshi_no"]["is_alert"]) is True
    assert round(by_direction["buy_polymarket_yes_buy_kalshi_no"]["net_edge"], 6) == 0.06
    assert bool(by_direction["buy_kalshi_yes_buy_polymarket_no"]["is_alert"]) is False
    assert by_direction["buy_kalshi_yes_buy_polymarket_no"]["exclusion_reason"] == "edge_below_threshold"

    empty = score_cross_market_arbitrage(pd.DataFrame(columns=MAPPING_COLUMNS), orderbooks, run_id="run-1")
    assert list(empty.columns) == ALERT_COLUMNS


def test_snapshot_cli_writes_valid_no_alert_run_with_empty_mappings(tmp_path: Path) -> None:
    mapping_path = tmp_path / "mappings.csv"
    mapping_path.write_text(",".join(MAPPING_COLUMNS) + "\n", encoding="utf-8")

    with httpx.Client(transport=httpx.MockTransport(_discovery_handler)) as client:
        exit_code = snapshot_cli_main(
            [
                "--output-dir",
                str(tmp_path / "out"),
                "--mapping-path",
                str(mapping_path),
                "--run-id",
                "run-empty",
                "--market-limit",
                "10",
                "--page-size",
                "10",
            ],
            client=client,
        )

    assert exit_code == 0
    runs = pd.read_parquet(tmp_path / "out" / "processed" / "scanner_runs.parquet")
    alerts = pd.read_parquet(tmp_path / "out" / "processed" / "arbitrage_alerts.parquet")
    candidates = pd.read_parquet(tmp_path / "out" / "processed" / "venue_market_candidates.parquet")
    assert runs.iloc[0]["status"] == "succeeded"
    assert runs.iloc[0]["alert_count"] == 0
    assert alerts.empty
    assert len(candidates) == 2


def test_snapshot_and_watch_loop_with_mocked_orderbooks(tmp_path: Path) -> None:
    mapping_path = tmp_path / "mappings.csv"
    pd.DataFrame([_mapping_row()], columns=MAPPING_COLUMNS).to_csv(mapping_path, index=False)

    with httpx.Client(transport=httpx.MockTransport(_combined_handler)) as client:
        result = run_fifa_snapshot(
            output_dir=tmp_path / "out",
            mapping_path=mapping_path,
            run_id="snapshot-1",
            market_limit=10,
            page_size=10,
            client=client,
        )

    assert result["tables"]["scanner_runs"].iloc[0]["alert_count"] == 1
    assert (tmp_path / "out" / "alerts" / "arbitrage_alerts.jsonl").exists()

    sleeps: list[float] = []

    def factory() -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(_combined_handler))

    summaries = watch_fifa_arbitrage(
        output_dir=tmp_path / "watch",
        mapping_path=mapping_path,
        interval_seconds=0.1,
        max_ticks=2,
        market_limit=10,
        page_size=10,
        sleeper=sleeps.append,
        client_factory=factory,
    )

    runs = pd.read_parquet(tmp_path / "watch" / "processed" / "scanner_runs.parquet")
    assert len(summaries) == 2
    assert len(runs) == 2
    assert sleeps == [0.1]
    assert runs["alert_count"].tolist() == [1, 1]


def _mapping_row(mapping_id: str = "map-1", status: str = "approved", draw_handling: str = "draw means NO") -> dict[str, str]:
    return {
        "mapping_id": mapping_id,
        "status": status,
        "event_name": "USA vs France",
        "proposition": "USA to win in regular time",
        "polymarket_market_id": "0xfifa",
        "polymarket_slug": "usa-france-world-cup",
        "polymarket_yes_token_id": "pm-yes",
        "polymarket_no_token_id": "pm-no",
        "polymarket_yes_outcome": "USA",
        "polymarket_no_outcome": "France or draw",
        "kalshi_ticker": "KXWCFINAL-26USA",
        "draw_handling": draw_handling,
        "extra_time_handling": "extra time excluded",
        "penalties_handling": "penalties excluded",
        "settlement_notes": "Both markets settle on regular-time USA win.",
        "reviewer": "tester",
        "reviewed_at": "2026-06-21T00:00:00Z",
        "notes": "",
    }


def _discovery_handler(request: httpx.Request) -> httpx.Response:
    if request.url.host == "gamma-api.polymarket.com":
        return httpx.Response(200, json=[_polymarket_hit(), _polymarket_miss()])
    if request.url.host == "external-api.kalshi.com":
        return httpx.Response(200, json={"markets": [_kalshi_hit(), _kalshi_miss()], "cursor": ""})
    raise AssertionError(f"Unexpected request: {request.url}")


def _orderbook_handler(request: httpx.Request) -> httpx.Response:
    if request.url.host == "clob.polymarket.com":
        token_id = request.url.params.get("token_id")
        if token_id == "pm-yes":
            return httpx.Response(200, json={"bids": [{"price": "0.37", "size": "20"}], "asks": [{"price": "0.40", "size": "25"}]})
        if token_id == "pm-no":
            return httpx.Response(200, json={"bids": [{"price": "0.56", "size": "20"}], "asks": [{"price": "0.59", "size": "25"}]})
    if request.url.host == "external-api.kalshi.com" and request.url.path.endswith("/orderbook"):
        return httpx.Response(
            200,
            json={
                "orderbook_fp": {
                    "yes_dollars": [["0.48", "40"]],
                    "no_dollars": [["0.53", "30"]],
                }
            },
        )
    raise AssertionError(f"Unexpected request: {request.url}")


def _combined_handler(request: httpx.Request) -> httpx.Response:
    if request.url.host in {"gamma-api.polymarket.com", "external-api.kalshi.com"} and not request.url.path.endswith("/orderbook"):
        return _discovery_handler(request)
    if request.url.host in {"clob.polymarket.com", "external-api.kalshi.com"}:
        return _orderbook_handler(request)
    raise AssertionError(f"Unexpected request: {request.url}")


def _polymarket_hit() -> dict:
    return {
        "id": "pm-market",
        "conditionId": "0xfifa",
        "slug": "usa-france-world-cup",
        "question": "USA vs France World Cup: USA to win?",
        "active": True,
        "closed": False,
        "outcomes": '["USA", "France or draw"]',
        "clobTokenIds": '["pm-yes", "pm-no"]',
        "description": "2026 FIFA World Cup match winner in regular time.",
    }


def _polymarket_miss() -> dict:
    return {
        "conditionId": "0xcrypto",
        "slug": "bitcoin-up",
        "question": "Bitcoin up tomorrow?",
        "active": True,
        "closed": False,
        "outcomes": '["Yes", "No"]',
        "clobTokenIds": '["btc-yes", "btc-no"]',
    }


def _kalshi_hit() -> dict:
    return {
        "ticker": "KXWCFINAL-26USA",
        "title": "World Cup: USA to win?",
        "subtitle": "USA vs France",
        "category": "Sports",
        "status": "active",
        "close_time": "2026-07-01T00:00:00Z",
        "yes_sub_title": "USA wins",
        "no_sub_title": "USA does not win",
        "rules_primary": "Market resolves Yes if USA wins this World Cup match in regulation.",
    }


def _kalshi_miss() -> dict:
    return {
        "ticker": "KXWEATHER",
        "title": "Will NYC temperature exceed 90F?",
        "category": "Weather",
        "status": "active",
    }
