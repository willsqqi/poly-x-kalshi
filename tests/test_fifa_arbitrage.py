from __future__ import annotations

from pathlib import Path

import httpx
import pandas as pd

from prediction_market.fifa_arbitrage import (
    ALERT_COLUMNS,
    APPROVAL_CANDIDATE_COLUMNS,
    MAPPING_COLUMNS,
    SIGNAL_COLUMNS,
    SUGGESTED_MAPPING_COLUMNS,
    approved_mappings,
    build_strategy_signals,
    build_approval_candidates,
    classify_market_type,
    extract_outcome_label,
    fetch_kalshi_fifa_markets,
    fetch_kalshi_sports_markets,
    fetch_mapped_orderbooks,
    fetch_polymarket_fifa_markets,
    fetch_polymarket_sports_markets,
    normalize_fifa_candidates,
    normalize_sports_candidates,
    run_fifa_snapshot,
    run_sports_snapshot,
    score_cross_market_arbitrage,
    snapshot_cli_main,
    sports_snapshot_cli_main,
    suggest_manual_mappings,
    validate_manual_mappings,
    watch_fifa_arbitrage,
    _gcs_blob_name,
    _gcs_uri,
    _attach_semantic_vectors,
    _is_gcs_uri,
    _obvious_parserless_pair_rejection_reason,
    _parserless_event_key,
    _parserless_event_pair_score,
    _event_scope_key,
    _semantic_embedding_lookup,
    _split_gcs_uri,
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


def test_cross_sports_discovery_finds_non_fifa_pairs() -> None:
    with httpx.Client(transport=httpx.MockTransport(_sports_discovery_handler)) as client:
        polymarket = fetch_polymarket_sports_markets(client, max_markets=10, page_size=10)
        kalshi = fetch_kalshi_sports_markets(client, max_markets=10, page_size=10)

    assert [market["conditionId"] for market in polymarket] == ["0xnba"]
    assert [market["ticker"] for market in kalshi] == ["KXNBA-26JUN23LALBOS-LAL"]

    frame = normalize_sports_candidates(polymarket, kalshi, run_id="sports-run", retrieved_at="2026-06-23T00:00:00Z")
    approval = build_approval_candidates(frame)
    suggestions = suggest_manual_mappings(approval, min_score=70)

    assert set(frame["venue"]) == {"polymarket", "kalshi"}
    assert "nba" in frame.loc[frame["venue"] == "polymarket", "keyword_hits"].iloc[0]
    assert approval["market_type"].tolist() == ["match_winner", "match_winner"]
    assert not suggestions.empty
    assert suggestions.iloc[0]["event_name"] == "Los Angeles Lakers vs. Boston Celtics"
    assert suggestions.iloc[0]["kalshi_ticker"] == "KXNBA-26JUN23LALBOS-LAL"


def test_kalshi_event_discovery_expands_world_soccer_cup_markets() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/trade-api/v2/markets" and request.url.params.get("event_ticker") == "KXWCHOST-2038":
            return httpx.Response(
                200,
                json={
                    "markets": [
                        {
                            "ticker": "KXWCHOST-2038-USA",
                            "event_ticker": "KXWCHOST-2038",
                            "series_ticker": "KXWCHOST",
                            "title": "United States",
                            "status": "active",
                            "yes_sub_title": "United States",
                            "no_sub_title": "United States",
                        }
                    ],
                    "cursor": "",
                },
            )
        if request.url.path == "/trade-api/v2/markets":
            return httpx.Response(200, json={"markets": [], "cursor": ""})
        if request.url.path == "/trade-api/v2/events":
            return httpx.Response(
                200,
                json={
                    "events": [
                        {
                            "event_ticker": "KXWCHOST-2038",
                            "series_ticker": "KXWCHOST",
                            "title": "Who will host the 2038 World Soccer Cup?",
                            "category": "Sports",
                        }
                    ],
                    "cursor": "",
                },
            )
        raise AssertionError(f"Unexpected request: {request.url}")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        markets = fetch_kalshi_fifa_markets(client, max_markets=10, page_size=10)

    assert [market["ticker"] for market in markets] == ["KXWCHOST-2038-USA"]
    assert markets[0]["_event_context_title"] == "Who will host the 2038 World Soccer Cup?"


def test_world_cup_game_event_children_create_exact_match_suggestions() -> None:
    with httpx.Client(transport=httpx.MockTransport(_world_cup_game_handler)) as client:
        polymarket = fetch_polymarket_fifa_markets(client, max_markets=20, page_size=20)
        kalshi = fetch_kalshi_fifa_markets(client, max_markets=20, page_size=20)

    assert {market["slug"] for market in polymarket} == {
        "fifwc-esp-ksa-2026-06-21-esp",
        "fifwc-esp-ksa-2026-06-21-draw",
        "fifwc-esp-ksa-2026-06-21-ksa",
    }
    assert {market["ticker"] for market in kalshi} == {
        "KXWCGAME-26JUN21ESPKSA-ESP",
        "KXWCGAME-26JUN21ESPKSA-TIE",
        "KXWCGAME-26JUN21ESPKSA-KSA",
    }

    candidates = normalize_fifa_candidates(
        polymarket,
        kalshi,
        run_id="esp-ksa-run",
        retrieved_at="2026-06-21T00:00:00Z",
    )
    approval = build_approval_candidates(candidates)
    suggestions = suggest_manual_mappings(approval, min_score=72)

    assert set(approval["event_match_key"]) == {"2026-06-21|saudi arabia|spain"}
    assert set(approval["outcome_label"]) == {"Spain", "Saudi Arabia", "Tie"}
    assert len(suggestions) == 3

    pairs = {(row["polymarket_slug"], row["kalshi_ticker"], row["outcome_label"]) for _, row in suggestions.iterrows()}
    assert ("fifwc-esp-ksa-2026-06-21-esp", "KXWCGAME-26JUN21ESPKSA-ESP", "Spain") in pairs
    assert ("fifwc-esp-ksa-2026-06-21-draw", "KXWCGAME-26JUN21ESPKSA-TIE", "Tie") in pairs
    assert ("fifwc-esp-ksa-2026-06-21-esp", "KXWCGAME-26JUN21ESPKSA-TIE", "Spain") not in pairs


def test_approval_candidates_classify_market_types_and_suggest_pairs() -> None:
    candidates = normalize_fifa_candidates(
        [
            {
                "id": "pm-host",
                "conditionId": "0xhost",
                "slug": "will-germany-host-2038-world-cup",
                "question": "Will Germany host the 2038 FIFA World Cup?",
                "active": True,
                "closed": False,
                "outcomes": '["Yes", "No"]',
                "clobTokenIds": '["pm-host-yes", "pm-host-no"]',
                "description": "Resolves Yes if Germany is announced as a 2038 FIFA World Cup host.",
            },
            _polymarket_hit(),
        ],
        [
            {
                "ticker": "KXWCHOST-2038-GER",
                "title": "Will Germany be announced as a host for the 2038 Men's FIFA World Cup?",
                "status": "active",
                "yes_sub_title": "Germany",
                "no_sub_title": "Germany",
                "rules_primary": "If Germany is announced as a host for the 2038 Men's FIFA World Cup, this resolves Yes.",
                "_event_context_title": "Who will host the 2038 World Soccer Cup?",
            }
        ],
        run_id="approval-run",
        retrieved_at="2026-06-21T00:00:00Z",
    )

    approval = build_approval_candidates(candidates)
    suggestions = suggest_manual_mappings(approval, min_score=70)

    assert list(approval.columns) == APPROVAL_CANDIDATE_COLUMNS
    by_slug = {row["ticker_or_slug"]: row for _, row in approval.iterrows()}
    assert by_slug["will-germany-host-2038-world-cup"]["market_type"] == "host_country"
    assert by_slug["will-germany-host-2038-world-cup"]["event_year"] == "2038"
    assert by_slug["usa-france-world-cup"]["market_type"] == "match_winner"
    assert by_slug["KXWCHOST-2038-GER"]["event_year"] == "2038"
    assert not suggestions.empty
    assert list(suggestions.columns) == SUGGESTED_MAPPING_COLUMNS
    assert suggestions.iloc[0]["market_type"] == "host_country"
    assert suggestions.iloc[0]["kalshi_ticker"] == "KXWCHOST-2038-GER"


def test_player_prop_and_exact_score_are_not_simple_winners() -> None:
    assert (
        classify_market_type(
            {
                "title": "Daizen Maeda: 2+ goals",
                "event_title": "Brazil vs. Japan - Player Props",
                "outcomes": '["Yes", "No"]',
            }
        )
        == "other"
    )
    assert (
        classify_market_type(
            {
                "title": "Exact Score: Brazil 3 - 2 Japan?",
                "event_title": "Brazil vs. Japan - Exact Score",
                "outcomes": '["Yes", "No"]',
            }
        )
        == "other"
    )


def test_non_winner_props_and_stat_leaders_are_not_match_winners() -> None:
    assert (
        classify_market_type(
            {
                "title": 'Will the announcers say "Nutmeg" during the South Africa vs Canada FIFA World Cup Match?',
                "event_title": 'Will the announcers say "Nutmeg" during the South Africa vs Canada FIFA World Cup Match?',
                "outcomes": '["Yes", "No"]',
            }
        )
        == "other"
    )
    assert (
        classify_market_type(
            {
                "title": "Game 1: Both Teams Slay Baron Nashor?",
                "event_title": "LoL: T1 vs Karmine Corp (BO5) - Mid-Season Invitational Play-In",
                "outcomes": '["Yes", "No"]',
            }
        )
        == "other"
    )
    assert (
        classify_market_type(
            {
                "title": "Mike Maignan: 3+ saves",
                "event_title": "France vs. Germany - Player Props",
                "outcomes": '["Yes", "No"]',
            }
        )
        == "other"
    )
    assert (
        classify_market_type(
            {
                "title": "Game 1: Ends in Daytime?",
                "event_title": "LoL: T1 vs Karmine Corp (BO5) - Mid-Season Invitational Play-In",
                "outcomes": '["Yes", "No"]',
            }
        )
        == "other"
    )
    assert (
        classify_market_type(
            {
                "title": "Will Tarik Skubal lead the MLB in ERA for the 2026 regular season?",
                "event_title": "MLB: ERA Leader",
                "outcomes": '["Yes", "No"]',
            }
        )
        == "player_award"
    )
    assert (
        classify_market_type(
            {
                "title": "Will Aaron Judge have the highest batting average in the 2026 MLB regular season?",
                "event_title": "MLB: Batting Average Leader",
                "outcomes": '["Yes", "No"]',
            }
        )
        == "player_award"
    )
    assert (
        classify_market_type(
            {
                "title": "Will Eddie Segura win 2026 MLS Defender of the Year?",
                "event_title": "2026 MLS Defender of the Year",
                "outcomes": '["Yes", "No"]',
            }
        )
        == "player_award"
    )
    assert (
        classify_market_type(
            {
                "title": "Will Lionel Messi win the 2026 MLS Golden Boot?",
                "event_title": "2026 MLS Golden Boot",
                "outcomes": '["Yes", "No"]',
            }
        )
        == "player_award"
    )
    assert (
        classify_market_type(
            {
                "title": "Will Angel Reese have the highest rebounds per game in the WNBA 2026 regular season?",
                "event_title": "WNBA: Rebounds Per Game Leader",
                "outcomes": '["Yes", "No"]',
            }
        )
        == "player_award"
    )
    assert (
        classify_market_type(
            {
                "title": "Will Jesús Luzardo strike out the most batters during the 2026 MLB regular season?",
                "event_title": "MLB: Strikeout Leader",
                "outcomes": '["Yes", "No"]',
            }
        )
        == "player_award"
    )
    assert (
        classify_market_type(
            {
                "title": "Will Xavi Simons record the most assists at the 2026 FIFA World Cup?",
                "event_title": "World Cup Most Assists",
                "outcomes": '["Yes", "No"]',
            }
        )
        == "player_award"
    )
    assert (
        classify_market_type(
            {
                "title": "Will Corbin Carroll hit the most triples in the 2026 MLB regular season?",
                "event_title": "MLB: Triples Leader",
                "outcomes": '["Yes", "No"]',
            }
        )
        == "player_award"
    )
    assert (
        classify_market_type(
            {
                "title": "Will Aaron Judge win the 2026 American League Hank Aaron Award?",
                "event_title": "2026 American League Hank Aaron Award",
                "outcomes": '["Yes", "No"]',
            }
        )
        == "player_award"
    )
    assert (
        classify_market_type(
            {
                "title": "Will Shohei Ohtani win the 2026 Edgar Martinez Outstanding Designated Hitter Award?",
                "event_title": "2026 Edgar Martinez Outstanding Designated Hitter Award",
                "outcomes": '["Yes", "No"]',
            }
        )
        == "player_award"
    )
    assert (
        classify_market_type(
            {
                "title": "Will Ivar Stenberg win the 2026-27 Calder Trophy?",
                "event_title": "NHL: 2026-27 Calder Trophy",
                "outcomes": '["Yes", "No"]',
            }
        )
        == "player_award"
    )
    assert (
        classify_market_type(
            {
                "title": "Will Aaron Judge record the most intentional walks during the 2026 MLB regular season?",
                "event_title": "MLB: Player to Record Most Intentional Walks?",
                "outcomes": '["Yes", "No"]',
            }
        )
        == "player_award"
    )
    assert (
        classify_market_type(
            {
                "title": "Will Alpine have the highest constructor score at the 2026 F1 Belgian Grand Prix?",
                "event_title": "Belgian Grand Prix: Which Constructor Scores 1st?",
                "outcomes": '["Yes", "No"]',
            }
        )
        == "team_stat"
    )
    assert (
        classify_market_type(
            {
                "title": "Will LCK (South Korea) Region Win the Most Series in the MSI 2026 Knockout Stage",
                "event_title": "Which Region will get the Most Series Wins During the MSI 2026 Knockout Stage",
                "outcomes": '["Yes", "No"]',
            }
        )
        == "team_stat"
    )
    assert (
        classify_market_type(
            {
                "title": "Will Sarah Ashlee Barker have the highest three point percentage in the WNBA 2026 regular season?",
                "event_title": "WNBA: Three Point Percentage Leader",
                "outcomes": '["Yes", "No"]',
            }
        )
        == "player_award"
    )
    assert (
        classify_market_type(
            {
                "title": "Game 3: Both Teams Beat Roshan?",
                "event_title": "Dota 2: Team A vs Team B",
                "outcomes": '["Yes", "No"]',
            }
        )
        == "other"
    )
    assert (
        classify_market_type(
            {
                "title": "Will Max Holloway win by KO or TKO?",
                "event_title": "Max Holloway vs. Conor McGregor (Welterweight, Main Card)",
                "outcomes": '["Yes", "No"]',
            }
        )
        == "other"
    )
    assert (
        classify_market_type(
            {
                "title": "Wimbledon ATP: Completed Match: Adam Walton vs Dino Prizmic",
                "event_title": "Adam Walton vs Dino Prizmic",
                "outcomes": '["Yes", "No"]',
            }
        )
        == "other"
    )
    assert (
        classify_market_type(
            {
                "title": "Will Kyler Murray be the Vikings' Week 1 starting QB?",
                "event_title": "Vikings Week 1 Starting QB",
                "outcomes": '["Yes", "No"]',
            }
        )
        == "other"
    )


def test_tennis_tournament_winner_is_championship_winner() -> None:
    row = {
        "title": "Will Alexander Zverev be the 2026 Men’s Wimbledon winner?",
        "event_title": "2026 Men’s Wimbledon Winner",
        "outcomes": '["Yes", "No"]',
    }

    assert classify_market_type(row) == "championship_winner"
    assert extract_outcome_label(row) == "Alexander Zverev"


def test_esports_tournament_winner_extracts_outcome() -> None:
    msi = {
        "title": "Will Hanwha Life Esports win MSI 2026?",
        "event_title": "",
        "outcomes": '["Yes", "No"]',
    }
    challengers = {
        "title": "Will M80 Win North America ACE Stage 3",
        "event_title": "Challengers 2026: North America ACE Stage 3 Winner",
        "outcomes": '["Yes", "No"]',
    }

    assert classify_market_type(msi) == "championship_winner"
    assert extract_outcome_label(msi) == "Hanwha Life Esports"
    assert classify_market_type(challengers) == "championship_winner"
    assert extract_outcome_label(challengers) == "M80"


def test_division_winner_extracts_team_outcome() -> None:
    row = {
        "title": "Will Cleveland Browns win the 2026 AFC North?",
        "event_title": "Pro Football: AFC North Champion",
        "outcomes": '["Yes", "No"]',
    }

    assert classify_market_type(row) == "championship_winner"
    assert extract_outcome_label(row) == "Cleveland Browns"


def test_semantic_vectors_do_not_attach_when_text_hash_changes() -> None:
    identity = "polymarket|market-1|slug-1|yes-1|team-a"
    lookup = _semantic_embedding_lookup(
        pd.DataFrame(
            [
                {
                    "embedding_key": f"{identity}|oldhash",
                    "embedding_vector": "[1.0, 0.0]",
                    "semantic_provider": "vertex-gemini",
                    "embedding_model": "gemini-embedding-2",
                    "embedding_dim": "3072",
                    "embedding_text_hash": "oldhash",
                }
            ]
        )
    )
    frame = pd.DataFrame([{"_semantic_embedding_key": f"{identity}|newhash"}])

    _attach_semantic_vectors(frame, lookup)

    assert frame.loc[0, "_semantic_vector"] == []
    assert frame.loc[0, "_semantic_provider"] == ""


def test_tennis_parserless_event_key_groups_market_types_by_match() -> None:
    total = {
        "venue": "polymarket",
        "category": "tennis",
        "market_type": "total",
        "title": "Blockx vs. Zverev: Match O/U 36.5",
        "event_title": "Blockx vs. Zverev",
        "event_date": "2026-06-30",
        "ticker_or_slug": "atp-blockx-zverev-2026-06-29-match-total-36pt5",
    }
    winner = {
        **total,
        "market_type": "match_winner",
        "title": "Wimbledon ATP: Alexander Blockx vs Alexander Zverev",
        "event_title": "Alexander Blockx vs Alexander Zverev",
        "outcome_label": "Alexander Zverev",
    }

    assert _parserless_event_key(total) == _parserless_event_key(winner)


def test_event_score_penalizes_incompatible_event_market_type() -> None:
    pm_stage = {
        "event_title": "World Cup: Netherlands Stage of Elimination",
        "event_text": "World Cup Netherlands Stage of Elimination Netherlands wins tournament champion",
        "event_year": "2026",
        "market_type_set": {"championship_winner"},
        "sport_context_set": {"soccer"},
        "scope_key_set": {"soccer_world_cup"},
    }
    kalshi_winner = {
        "event_title": "2026 FIFA World Cup Winner",
        "event_text": "2026 FIFA World Cup Winner Netherlands champion",
        "event_year": "2026",
        "market_type_set": {"championship_winner"},
        "sport_context_set": {"soccer"},
        "scope_key_set": {"soccer_world_cup"},
    }
    kalshi_match = {
        "event_title": "Netherlands vs Morocco",
        "event_text": "Netherlands vs Morocco match winner Netherlands Morocco",
        "event_year": "2026",
        "market_type_set": {"match_winner"},
        "sport_context_set": {"soccer"},
        "scope_key_set": set(),
    }

    winner_score, _ = _parserless_event_pair_score(pm_stage, kalshi_winner, semantic_enabled=False)
    match_score, parts = _parserless_event_pair_score(pm_stage, kalshi_match, semantic_enabled=False)

    assert parts["compatibility_cap"] == 68.0
    assert match_score < 72.0
    assert winner_score > match_score


def test_f1_q3_qualifying_scope_matches_regular_pole_but_not_sprint() -> None:
    regular_pole = {
        "category": "f1",
        "market_type": "pole_position_winner",
        "event_title": "British Grand Prix: Driver Pole Position",
        "title": "Will Lando Norris get pole position at the 2026 F1 British Grand Prix?",
    }
    kalshi_q3 = {
        "category": "f1",
        "market_type": "pole_position_winner",
        "event_title": "British Grand Prix Qualifying Session (Q3): Pole Position",
        "title": "Will Lando Norris set the fastest valid qualifying lap time in the Qualifying session (Q3) for the 2026 British Grand Prix?",
    }
    sprint_pole = {
        "category": "f1",
        "market_type": "pole_position_winner",
        "event_title": "British Grand Prix: Sprint Qualifying Pole Winner",
        "title": "Will Lando Norris achieve pole position in Sprint Qualifying at the 2026 F1 British Grand Prix?",
    }

    assert _event_scope_key(regular_pole) == "f1_british-grand-prix_pole"
    assert _event_scope_key(kalshi_q3) == "f1_british-grand-prix_pole"
    assert _event_scope_key(sprint_pole) == "f1_british-grand-prix_sprint_qualifying_pole"


def test_parserless_gate_rejects_missing_side_and_wrong_local_mlb_date() -> None:
    base = {
        "run_id": "gate-test",
        "market_type": "match_winner",
        "category": "mlb",
        "event_year": "2026",
    }
    missing_side = {
        **base,
        "venue": "polymarket",
        "category": "tennis",
        "title": "Wimbledon ATP: Completed Match: Felix Auger-Aliassime vs Alexander Shevchenko",
        "event_title": "Felix Auger-Aliassime vs Alexander Shevchenko",
        "event_date": "2026-06-29",
        "ticker_or_slug": "atp-augeral-shevche-2026-06-29-completed-match",
    }
    tennis_side = {
        **base,
        "venue": "kalshi",
        "category": "tennis",
        "title": "Will Alexander Zverev win the Blockx vs Zverev: Round Of 128 match?",
        "event_title": "Blockx vs Zverev",
        "event_date": "2026-06-29",
        "outcome_label": "Alexander Zverev",
        "ticker_or_slug": "KXATPMATCH-26JUN29BLOZVE-ZVE",
    }
    pm_mlb = {
        **base,
        "venue": "polymarket",
        "title": "Detroit Tigers vs. New York Yankees",
        "event_title": "Detroit Tigers vs. New York Yankees",
        "event_date": "2026-06-30",
        "outcome_label": "Detroit Tigers",
        "ticker_or_slug": "mlb-det-nyy-2026-06-30",
    }
    ks_mlb = {
        **base,
        "venue": "kalshi",
        "title": "Detroit vs New York Y Winner?",
        "event_title": "Detroit vs New York Y",
        "event_date": "2026-06-30",
        "outcome_label": "Detroit",
        "ticker_or_slug": "KXMLBGAME-26JUN291905DETNYY-DET",
    }

    assert _obvious_parserless_pair_rejection_reason(missing_side, tennis_side, semantic_enabled=True) == "excluded_missing_outcome"
    assert _obvious_parserless_pair_rejection_reason(pm_mlb, ks_mlb, semantic_enabled=True) == "excluded_wrong_date"


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

    signals = build_strategy_signals(scored)
    signals_by_direction = {row["direction"]: row for _, row in signals.iterrows()}
    assert list(signals.columns) == SIGNAL_COLUMNS
    assert signals_by_direction["buy_polymarket_yes_buy_kalshi_no"]["signal"] == "alert"
    assert bool(signals_by_direction["buy_polymarket_yes_buy_kalshi_no"]["price_available"]) is True
    assert bool(signals_by_direction["buy_polymarket_yes_buy_kalshi_no"]["threshold_ok"]) is True
    assert signals_by_direction["buy_kalshi_yes_buy_polymarket_no"]["signal"] == "watch_edge_below_threshold"

    empty_signals = build_strategy_signals(empty)
    assert list(empty_signals.columns) == SIGNAL_COLUMNS


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
    signals = pd.read_parquet(tmp_path / "out" / "processed" / "strategy_signals.parquet")
    candidates = pd.read_parquet(tmp_path / "out" / "processed" / "venue_market_candidates.parquet")
    latest_approval = pd.read_parquet(tmp_path / "out" / "processed" / "latest" / "approval_candidates.parquet")
    assert runs.iloc[0]["status"] == "succeeded"
    assert runs.iloc[0]["alert_count"] == 0
    assert alerts.empty
    assert signals.empty
    assert len(candidates) == 2
    assert len(latest_approval) == 2


def test_sports_snapshot_cli_writes_review_tables_with_empty_mappings(tmp_path: Path) -> None:
    mapping_path = tmp_path / "sports_mappings.csv"
    mapping_path.write_text(",".join(MAPPING_COLUMNS) + "\n", encoding="utf-8")

    with httpx.Client(transport=httpx.MockTransport(_sports_discovery_handler)) as client:
        exit_code = sports_snapshot_cli_main(
            [
                "--output-dir",
                str(tmp_path / "sports-out"),
                "--mapping-path",
                str(mapping_path),
                "--run-id",
                "sports-empty",
                "--market-limit",
                "10",
                "--page-size",
                "10",
            ],
            client=client,
        )

    assert exit_code == 0
    runs = pd.read_parquet(tmp_path / "sports-out" / "processed" / "scanner_runs.parquet")
    suggestions = pd.read_parquet(tmp_path / "sports-out" / "processed" / "suggested_mappings.parquet")
    signals = pd.read_parquet(tmp_path / "sports-out" / "processed" / "strategy_signals.parquet")
    assert runs.iloc[0]["run_id"] == "sports-empty"
    assert runs.iloc[0]["candidate_count"] == 2
    assert runs.iloc[0]["approved_mapping_count"] == 0
    assert not suggestions.empty
    assert signals.empty


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
    assert len(result["tables"]["strategy_signals"]) == 2
    assert (tmp_path / "out" / "processed" / "strategy_signals.parquet").exists()
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
    signals = pd.read_parquet(tmp_path / "watch" / "processed" / "strategy_signals.parquet")
    assert len(summaries) == 2
    assert len(runs) == 2
    assert len(signals) == 4
    assert sleeps == [0.1]
    assert runs["alert_count"].tolist() == [1, 1]

    def orderbook_only_factory() -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(_orderbook_handler))

    orderbook_only_summaries = watch_fifa_arbitrage(
        output_dir=tmp_path / "watch-no-discovery",
        mapping_path=mapping_path,
        interval_seconds=0.1,
        max_ticks=1,
        discover=False,
        sleeper=sleeps.append,
        client_factory=orderbook_only_factory,
    )

    orderbook_only_runs = pd.read_parquet(tmp_path / "watch-no-discovery" / "processed" / "scanner_runs.parquet")
    assert len(orderbook_only_summaries) == 1
    assert orderbook_only_runs.iloc[0]["candidate_count"] == 0
    assert orderbook_only_runs.iloc[0]["approved_mapping_count"] == 1
    assert orderbook_only_runs.iloc[0]["orderbook_count"] == 2


def test_gcs_output_uri_helpers() -> None:
    assert _is_gcs_uri("gs://poly-x-kalshi-dev/fifa_arbitrage")
    assert not _is_gcs_uri("data/fifa_arbitrage")
    assert _split_gcs_uri("gs://poly-x-kalshi-dev/fifa_arbitrage") == ("poly-x-kalshi-dev", "fifa_arbitrage")
    assert _split_gcs_uri("gs://poly-x-kalshi-dev") == ("poly-x-kalshi-dev", "")
    assert _gcs_blob_name("fifa_arbitrage", "processed/latest/strategy_signals.csv") == (
        "fifa_arbitrage/processed/latest/strategy_signals.csv"
    )
    assert _gcs_uri("poly-x-kalshi-dev", "fifa_arbitrage", "processed/latest/strategy_signals.csv") == (
        "gs://poly-x-kalshi-dev/fifa_arbitrage/processed/latest/strategy_signals.csv"
    )


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
    if request.url.host == "external-api.kalshi.com" and request.url.path == "/trade-api/v2/markets":
        return httpx.Response(200, json={"markets": [_kalshi_hit(), _kalshi_miss()], "cursor": ""})
    if request.url.host == "external-api.kalshi.com" and request.url.path == "/trade-api/v2/events":
        return httpx.Response(200, json={"events": [], "cursor": ""})
    raise AssertionError(f"Unexpected request: {request.url}")


def _sports_discovery_handler(request: httpx.Request) -> httpx.Response:
    if request.url.host == "gamma-api.polymarket.com" and request.url.path == "/markets":
        return httpx.Response(200, json=[_polymarket_nba_hit(), _polymarket_miss()])
    if request.url.host == "gamma-api.polymarket.com" and request.url.path == "/events":
        return httpx.Response(200, json=[])
    if request.url.host == "external-api.kalshi.com" and request.url.path == "/trade-api/v2/markets":
        return httpx.Response(200, json={"markets": [_kalshi_nba_hit(), _kalshi_miss()], "cursor": ""})
    if request.url.host == "external-api.kalshi.com" and request.url.path == "/trade-api/v2/events":
        return httpx.Response(200, json={"events": [], "cursor": ""})
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


def _world_cup_game_handler(request: httpx.Request) -> httpx.Response:
    if request.url.host == "gamma-api.polymarket.com" and request.url.path == "/markets":
        return httpx.Response(200, json=[])
    if request.url.host == "gamma-api.polymarket.com" and request.url.path == "/events":
        return httpx.Response(200, json=[_polymarket_esp_ksa_event()])
    if request.url.host == "external-api.kalshi.com" and request.url.path == "/trade-api/v2/events":
        if request.url.params.get("series_ticker") == "KXWCGAME":
            return httpx.Response(200, json={"events": [_kalshi_esp_ksa_event()], "cursor": ""})
        return httpx.Response(200, json={"events": [], "cursor": ""})
    if request.url.host == "external-api.kalshi.com" and request.url.path == "/trade-api/v2/markets":
        if request.url.params.get("event_ticker") == "KXWCGAME-26JUN21ESPKSA":
            return httpx.Response(200, json={"markets": _kalshi_esp_ksa_markets(), "cursor": ""})
        return httpx.Response(200, json={"markets": [], "cursor": ""})
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


def _polymarket_nba_hit() -> dict:
    return {
        "id": "pm-nba-market",
        "conditionId": "0xnba",
        "slug": "nba-lal-bos-2026-06-23-lal",
        "question": "Will Los Angeles Lakers win on 2026-06-23?",
        "active": True,
        "closed": False,
        "outcomes": '["Yes", "No"]',
        "clobTokenIds": '["pm-lal-yes", "pm-lal-no"]',
        "description": "NBA game Los Angeles Lakers vs. Boston Celtics. Resolves Yes if the Lakers win.",
        "_event_context_title": "Los Angeles Lakers vs. Boston Celtics",
        "_event_context_start_time": "2026-06-23T23:00:00Z",
        "_event_context_sport": "basketball",
    }


def _polymarket_esp_ksa_event() -> dict:
    return {
        "id": "351751",
        "ticker": "fifwc-esp-ksa-2026-06-21",
        "slug": "fifwc-esp-ksa-2026-06-21",
        "title": "Spain vs. Saudi Arabia",
        "description": "This event is for the upcoming FIFA World Cup game, scheduled for Sunday, June 21, 2026 between Spain and Saudi Arabia.",
        "resolutionSource": "https://www.fifa.com/fifaplus/en/tournaments/mens/worldcup",
        "active": True,
        "closed": False,
        "endDate": "2026-06-21T16:00:00Z",
        "sport": "soccer",
        "markets": [
            {
                "id": "1897161",
                "question": "Will Spain win on 2026-06-21?",
                "conditionId": "0xesp",
                "slug": "fifwc-esp-ksa-2026-06-21-esp",
                "description": "If Spain wins, this market will resolve to Yes. Otherwise, this market will resolve to No.",
                "outcomes": '["Yes", "No"]',
                "clobTokenIds": '["pm-esp-yes", "pm-esp-no"]',
                "active": True,
                "closed": False,
                "endDate": "2026-06-21T16:00:00Z",
            },
            {
                "id": "1897162",
                "question": "Will Spain vs. Saudi Arabia end in a draw?",
                "conditionId": "0xdraw",
                "slug": "fifwc-esp-ksa-2026-06-21-draw",
                "description": "If the game ends in a draw, this market will resolve to Yes. Otherwise, this market will resolve to No.",
                "outcomes": '["Yes", "No"]',
                "clobTokenIds": '["pm-draw-yes", "pm-draw-no"]',
                "active": True,
                "closed": False,
                "endDate": "2026-06-21T16:00:00Z",
            },
            {
                "id": "1897163",
                "question": "Will Saudi Arabia win on 2026-06-21?",
                "conditionId": "0xksa",
                "slug": "fifwc-esp-ksa-2026-06-21-ksa",
                "description": "If Saudi Arabia wins, this market will resolve to Yes. Otherwise, this market will resolve to No.",
                "outcomes": '["Yes", "No"]',
                "clobTokenIds": '["pm-ksa-yes", "pm-ksa-no"]',
                "active": True,
                "closed": False,
                "endDate": "2026-06-21T16:00:00Z",
            },
        ],
    }


def _kalshi_esp_ksa_event() -> dict:
    return {
        "category": "Sports",
        "event_ticker": "KXWCGAME-26JUN21ESPKSA",
        "mutually_exclusive": True,
        "product_metadata": {"competition": "World Soccer Cup", "competition_scope": "Game"},
        "series_ticker": "KXWCGAME",
        "sub_title": "ESP vs KSA (Jun 21)",
        "title": "Spain vs Saudi Arabia",
    }


def _kalshi_esp_ksa_markets() -> list[dict]:
    base = {
        "event_ticker": "KXWCGAME-26JUN21ESPKSA",
        "title": "Spain vs Saudi Arabia Winner?",
        "status": "active",
        "expected_expiration_time": "2026-06-21T19:00:00Z",
        "close_time": "2026-07-05T16:00:00Z",
        "rules_secondary": "The market refers to the Spain vs Saudi Arabia professional FIFA World Cup soccer game after 90 minutes plus stoppage time. Extra time and penalties are excluded.",
    }
    return [
        {
            **base,
            "ticker": "KXWCGAME-26JUN21ESPKSA-ESP",
            "yes_sub_title": "Spain",
            "no_sub_title": "Spain",
            "rules_primary": "If Spain wins the Spain vs Saudi Arabia professional FIFA World Cup soccer game originally scheduled for Jun 21, 2026 after 90 minutes plus stoppage time, then the market resolves to Yes.",
        },
        {
            **base,
            "ticker": "KXWCGAME-26JUN21ESPKSA-TIE",
            "yes_sub_title": "Tie",
            "no_sub_title": "Tie",
            "rules_primary": "If Tie wins the Spain vs Saudi Arabia professional FIFA World Cup soccer game originally scheduled for Jun 21, 2026 after 90 minutes plus stoppage time, then the market resolves to Yes.",
        },
        {
            **base,
            "ticker": "KXWCGAME-26JUN21ESPKSA-KSA",
            "yes_sub_title": "Saudi Arabia",
            "no_sub_title": "Saudi Arabia",
            "rules_primary": "If Saudi Arabia wins the Spain vs Saudi Arabia professional FIFA World Cup soccer game originally scheduled for Jun 21, 2026 after 90 minutes plus stoppage time, then the market resolves to Yes.",
        },
    ]


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


def _kalshi_nba_hit() -> dict:
    return {
        "ticker": "KXNBA-26JUN23LALBOS-LAL",
        "event_ticker": "KXNBA-26JUN23LALBOS",
        "series_ticker": "KXNBA",
        "title": "Los Angeles Lakers vs Boston Celtics Winner?",
        "subtitle": "Lakers vs Celtics",
        "category": "Sports",
        "status": "active",
        "close_time": "2026-06-23T23:00:00Z",
        "yes_sub_title": "Los Angeles Lakers",
        "no_sub_title": "Los Angeles Lakers",
        "rules_primary": "If Los Angeles Lakers wins the Los Angeles Lakers vs Boston Celtics professional NBA basketball game, then the market resolves to Yes.",
        "rules_secondary": "The market refers to the listed NBA game winner.",
    }


def _kalshi_miss() -> dict:
    return {
        "ticker": "KXWEATHER",
        "title": "Will NYC temperature exceed 90F?",
        "category": "Weather",
        "status": "active",
    }
