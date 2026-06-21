from __future__ import annotations

from pathlib import Path

import pandas as pd

from prediction_market.arbitrage import (
    classify_sportsbook_market,
    decimal_to_implied_probability,
    match_odds_to_polymarket,
    normalize_team_name,
    parse_oddsportal_html,
    polymarket_market_exclusion_reason,
    remove_two_way_overround,
    score_opportunities,
    team_match_score,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures"


def test_decimal_odds_and_two_way_overround_are_normalized() -> None:
    assert round(decimal_to_implied_probability(2.0), 6) == 0.5

    first, second = remove_two_way_overround(1.80, 2.10)

    assert round(first + second, 6) == 1.0
    assert round(first, 6) == 0.538462
    assert round(second, 6) == 0.461538


def test_team_normalization_and_fuzzy_score() -> None:
    assert normalize_team_name("Team Liquid Esports") == "liquid"
    assert team_match_score("Team Liquid Esports", "Liquid") >= 90
    assert team_match_score("Team Liquid", "Fnatic") < 50


def test_parse_oddsportal_fixture_extracts_binary_and_excludes_three_way() -> None:
    html = (FIXTURE_DIR / "oddsportal_sample.html").read_text(encoding="utf-8")

    frame = parse_oddsportal_html(html, source_url="https://www.oddsportal.com/esports/")

    binary = frame[frame["event_id"] == "lol-001"]
    three_way = frame[frame["event_id"] == "soccer-001"]
    assert len(binary) == 2
    assert binary["market_type"].unique().tolist() == ["binary_winner"]
    assert binary["exclusion_reason"].fillna("").unique().tolist() == [""]
    assert round(binary["fair_probability"].sum(), 6) == 1.0
    assert set(binary["team"]) == {"Team Liquid", "Fnatic"}
    assert len(three_way) == 3
    assert three_way["market_type"].unique().tolist() == ["excluded"]
    assert three_way["exclusion_reason"].unique().tolist() == ["non_binary_market"]


def test_market_filter_rejects_unclear_or_non_binary_markets() -> None:
    assert classify_sportsbook_market(["Team A", "Team B"], "Team A vs Team B") == ("binary_winner", "")
    assert classify_sportsbook_market(["Team A", "Draw", "Team B"], "Team A vs Team B")[1] == "non_binary_market"
    assert (
        polymarket_market_exclusion_reason(
            {
                "question": "Will Team A beat the spread?",
                "outcomes": '["Yes", "No"]',
                "clobTokenIds": '["1", "2"]',
            }
        )
        == "non_winner_market"
    )


def test_matching_links_sportsbook_team_to_polymarket_outcome() -> None:
    odds = pd.DataFrame(
        [
            {
                "event_id": "lol-001",
                "event_name": "Team Liquid vs Fnatic",
                "team": "Team Liquid",
                "decimal_odds": 1.80,
                "fair_probability": 0.538462,
                "bookmaker": "Pinnacle",
                "exclusion_reason": "",
            }
        ]
    )
    books = pd.DataFrame(
        [
            _book_row("101", "Will Team Liquid beat Fnatic?", "Team Liquid", best_ask=0.49),
            _book_row("101", "Will Team Liquid beat Fnatic?", "Fnatic", best_ask=0.51),
        ]
    )

    matched = match_odds_to_polymarket(odds, books, min_score=70)

    assert len(matched) == 1
    assert matched.iloc[0]["polymarket_outcome"] == "Team Liquid"
    assert matched.iloc[0]["match_score"] >= 80


def test_score_opportunities_marks_taker_and_maker_candidates() -> None:
    matched = pd.DataFrame(
        [
            _match_row(best_bid=0.47, best_ask=0.49, fair_probability=0.55),
            _match_row(best_bid=0.45, best_ask=0.60, fair_probability=0.51),
            _match_row(best_bid=0.49, best_ask=0.50, fair_probability=0.515),
        ]
    )

    opportunities = score_opportunities(matched, min_net_edge=0.03, slippage_buffer=0.01)

    by_ask = {round(row["polymarket_best_ask"], 2): row for _, row in opportunities.iterrows()}
    assert by_ask[0.49]["opportunity_type"] == "taker_candidate"
    assert by_ask[0.60]["opportunity_type"] == "maker_candidate"
    assert by_ask[0.50]["opportunity_type"] == "excluded"
    assert by_ask[0.50]["exclusion_reason"] == "edge_below_threshold"


def _book_row(market_id: str, question: str, outcome: str, best_ask: float) -> dict:
    return {
        "market_id": market_id,
        "condition_id": f"0x{market_id}",
        "slug": "team-liquid-fnatic",
        "question": question,
        "event_title": "Team Liquid vs Fnatic",
        "sport": "esports",
        "league": "League of Legends",
        "outcome": outcome,
        "outcome_index": 0,
        "token_id": f"token-{outcome}",
        "best_bid": best_ask - 0.03,
        "best_ask": best_ask,
        "bid_depth": 100.0,
        "ask_depth": 120.0,
        "spread": 0.03,
        "retrieved_at": "2026-06-21T00:00:00Z",
        "exclusion_reason": "",
        "raw_market": "{}",
        "raw_orderbook": "{}",
    }


def _match_row(best_bid: float, best_ask: float, fair_probability: float) -> dict:
    return {
        "source_event_id": "lol-001",
        "source_event_name": "Team Liquid vs Fnatic",
        "source_team": "Team Liquid",
        "sportsbook": "Pinnacle",
        "decimal_odds": 1.80,
        "fair_probability": fair_probability,
        "polymarket_market_id": "101",
        "polymarket_slug": "team-liquid-fnatic",
        "polymarket_question": "Will Team Liquid beat Fnatic?",
        "polymarket_outcome": "Team Liquid",
        "polymarket_token_id": "token-1",
        "polymarket_best_bid": best_bid,
        "polymarket_best_ask": best_ask,
        "polymarket_bid_depth": 100.0,
        "polymarket_ask_depth": 100.0,
        "polymarket_spread": best_ask - best_bid,
        "match_score": 95.0,
        "event_score": 95.0,
        "team_score": 100.0,
        "league_score": 80.0,
        "matched_at": "2026-06-21T00:00:00Z",
    }
