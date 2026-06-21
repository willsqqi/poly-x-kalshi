from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

import httpx
import pandas as pd
from bs4 import BeautifulSoup
from rapidfuzz import fuzz

from .collectors import POLYMARKET_CLOB_BASE, POLYMARKET_GAMMA_BASE
from .utils import best_ask, best_bid, compact_json, parse_json_array, parse_timestamp, to_float, total_size, utc_now_iso

ODDS_QUOTE_COLUMNS = [
    "source",
    "sport",
    "league",
    "event_id",
    "event_name",
    "start_time",
    "team",
    "opponent",
    "side_index",
    "decimal_odds",
    "fair_probability",
    "bookmaker",
    "retrieved_at",
    "source_url",
    "market_type",
    "exclusion_reason",
    "raw_payload",
]

POLYMARKET_BOOK_COLUMNS = [
    "market_id",
    "condition_id",
    "slug",
    "question",
    "event_title",
    "sport",
    "league",
    "outcome",
    "outcome_index",
    "token_id",
    "best_bid",
    "best_ask",
    "bid_depth",
    "ask_depth",
    "spread",
    "retrieved_at",
    "exclusion_reason",
    "raw_market",
    "raw_orderbook",
]

MATCHED_EVENT_COLUMNS = [
    "source_event_id",
    "source_event_name",
    "source_team",
    "sportsbook",
    "decimal_odds",
    "fair_probability",
    "polymarket_market_id",
    "polymarket_slug",
    "polymarket_question",
    "polymarket_outcome",
    "polymarket_token_id",
    "polymarket_best_bid",
    "polymarket_best_ask",
    "polymarket_bid_depth",
    "polymarket_ask_depth",
    "polymarket_spread",
    "match_score",
    "event_score",
    "team_score",
    "league_score",
    "matched_at",
]

OPPORTUNITY_COLUMNS = [
    *MATCHED_EVENT_COLUMNS,
    "gross_edge",
    "net_edge",
    "maker_quote",
    "maker_net_edge",
    "opportunity_type",
    "exclusion_reason",
]

DRAW_TERMS = {"draw", "tie", "x"}
NON_WINNER_TERMS = {
    "spread",
    "handicap",
    "total",
    "over",
    "under",
    "points",
    "goals",
    "round",
    "map",
    "series correct score",
    "parlay",
    "multi",
}
TEAM_SUFFIXES = {
    "fc",
    "cf",
    "sc",
    "esports",
    "e-sports",
    "team",
    "club",
}


def decimal_to_implied_probability(decimal_odds: float | int | str | None) -> float | None:
    odds = to_float(decimal_odds)
    if odds is None or odds <= 1:
        return None
    return 1.0 / odds


def remove_two_way_overround(first_odds: float | int | str, second_odds: float | int | str) -> tuple[float | None, float | None]:
    first = decimal_to_implied_probability(first_odds)
    second = decimal_to_implied_probability(second_odds)
    if first is None or second is None:
        return None, None
    total = first + second
    if total <= 0:
        return None, None
    return first / total, second / total


def normalize_team_name(value: Any) -> str:
    text = str(value or "").lower()
    text = re.sub(r"\([^)]*\)", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    tokens = [token for token in text.split() if token not in TEAM_SUFFIXES]
    return " ".join(tokens)


def team_match_score(left: Any, right: Any) -> float:
    left_norm = normalize_team_name(left)
    right_norm = normalize_team_name(right)
    if not left_norm or not right_norm:
        return 0.0
    return float(max(fuzz.token_set_ratio(left_norm, right_norm), fuzz.WRatio(left_norm, right_norm)))


def event_match_score(left: Any, right: Any) -> float:
    left_norm = normalize_team_name(left)
    right_norm = normalize_team_name(right)
    if not left_norm or not right_norm:
        return 0.0
    return float(fuzz.token_set_ratio(left_norm, right_norm))


def parse_oddsportal_html(html: str, source_url: str = "", retrieved_at: str | None = None) -> pd.DataFrame:
    retrieved_at = retrieved_at or utc_now_iso()
    soup = BeautifulSoup(html, "html.parser")
    rows: list[dict[str, Any]] = []

    for index, node in enumerate(_event_nodes(soup), start=1):
        event_name = _text_or_attr(node, "data-event-name") or _event_name_from_node(node)
        home_team = _text_or_attr(node, "data-home-team") or _first_text(node, ".participant-home", "[data-home-team]")
        away_team = _text_or_attr(node, "data-away-team") or _first_text(node, ".participant-away", "[data-away-team]")
        draw_label = _text_or_attr(node, "data-draw-team") or _first_text(node, ".participant-draw", "[data-draw-team]")
        home_odds = _text_or_attr(node, "data-home-odds") or _first_text(node, "[data-odds-home]", ".odds-home")
        away_odds = _text_or_attr(node, "data-away-odds") or _first_text(node, "[data-odds-away]", ".odds-away")
        draw_odds = _text_or_attr(node, "data-draw-odds") or _first_text(node, "[data-odds-draw]", ".odds-draw")
        bookmaker = _text_or_attr(node, "data-bookmaker") or _first_text(node, "[data-bookmaker-name]", ".bookmaker")
        sport = _closest_attr(node, "data-sport") or _sport_from_url(source_url)
        league = _closest_attr(node, "data-league") or _first_text(node, "[data-league]", ".league")
        start_time = parse_timestamp(_text_or_attr(node, "data-start-time") or _first_text(node, "time", "[datetime]"))
        event_id = _text_or_attr(node, "data-event-id") or f"oddsportal-{index}"

        sides = [
            (home_team, away_team, home_odds),
            (away_team, home_team, away_odds),
        ]
        if to_float(draw_odds) is not None:
            sides.append((draw_label or "Draw", "", draw_odds))

        market_type, exclusion_reason = classify_sportsbook_market([side[0] for side in sides], event_name)
        fair_probs = (None, None)
        if market_type == "binary_winner":
            fair_probs = remove_two_way_overround(home_odds, away_odds)

        for side_index, (team, opponent, odds) in enumerate(sides):
            if not team or to_float(odds) is None:
                continue
            fair_probability = fair_probs[side_index] if side_index < 2 else None
            rows.append(
                {
                    "source": "oddsportal",
                    "sport": sport,
                    "league": league,
                    "event_id": event_id,
                    "event_name": event_name,
                    "start_time": start_time,
                    "team": team,
                    "opponent": opponent,
                    "side_index": side_index,
                    "decimal_odds": to_float(odds),
                    "fair_probability": fair_probability,
                    "bookmaker": bookmaker or "unknown",
                    "retrieved_at": retrieved_at,
                    "source_url": source_url,
                    "market_type": market_type,
                    "exclusion_reason": exclusion_reason,
                    "raw_payload": compact_json(_node_payload(node)),
                }
            )

    return pd.DataFrame(rows, columns=ODDS_QUOTE_COLUMNS)


def capture_oddsportal_snapshots(
    urls: Iterable[str],
    output_dir: str | Path = "data/arbitrage/raw/oddsportal",
    headless: bool = True,
    timeout_ms: int = 30_000,
) -> list[Path]:
    from playwright.sync_api import sync_playwright

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        try:
            page = browser.new_page()
            for url in urls:
                page.goto(url, wait_until="networkidle", timeout=timeout_ms)
                html = page.content()
                target = output_path / f"{_safe_name(url)}.html"
                target.write_text(html, encoding="utf-8")
                written.append(target)
        finally:
            browser.close()
    return written


def classify_sportsbook_market(teams: Iterable[Any], event_name: Any = "") -> tuple[str, str]:
    cleaned = [normalize_team_name(team) for team in teams if normalize_team_name(team)]
    if len(cleaned) != 2:
        return "excluded", "non_binary_market"
    if any(team in DRAW_TERMS for team in cleaned):
        return "excluded", "draw_or_three_way_market"
    name = normalize_team_name(event_name)
    if any(term in name for term in NON_WINNER_TERMS):
        return "excluded", "non_winner_market"
    return "binary_winner", ""


def polymarket_market_exclusion_reason(market: dict[str, Any]) -> str:
    outcomes = [str(outcome) for outcome in parse_json_array(market.get("outcomes"))]
    question = str(market.get("question") or market.get("title") or "")
    market_type, reason = classify_sportsbook_market(outcomes, question)
    if market_type != "binary_winner":
        return reason
    token_ids = parse_json_array(market.get("clobTokenIds"))
    if len(token_ids) < 2:
        return "missing_clob_tokens"
    return ""


def is_binary_winner_polymarket(market: dict[str, Any]) -> bool:
    return polymarket_market_exclusion_reason(market) == ""


def fetch_polymarket_candidate_markets(
    client: httpx.Client,
    limit: int = 200,
    include_excluded: bool = True,
) -> list[dict[str, Any]]:
    response = client.get(
        f"{POLYMARKET_GAMMA_BASE}/markets",
        params={"active": "true", "closed": "false", "limit": limit, "order": "volume", "ascending": "false"},
    )
    response.raise_for_status()
    payload = response.json()
    markets = payload if isinstance(payload, list) else payload.get("markets", [])
    if include_excluded:
        return markets
    return [market for market in markets if is_binary_winner_polymarket(market)]


def fetch_polymarket_orderbook_for_token(client: httpx.Client, token_id: str) -> dict[str, Any]:
    response = client.get(f"{POLYMARKET_CLOB_BASE}/book", params={"token_id": token_id})
    response.raise_for_status()
    return response.json()


def fetch_polymarket_books_for_markets(client: httpx.Client, markets: list[dict[str, Any]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    retrieved_at = utc_now_iso()
    for market in markets:
        outcomes = parse_json_array(market.get("outcomes"))
        token_ids = parse_json_array(market.get("clobTokenIds"))
        exclusion_reason = polymarket_market_exclusion_reason(market)
        for outcome_index, token_id in enumerate(token_ids[:2]):
            raw_orderbook: dict[str, Any] = {}
            error = ""
            try:
                raw_orderbook = fetch_polymarket_orderbook_for_token(client, str(token_id))
            except (httpx.HTTPError, ValueError) as exc:
                error = str(exc)
            bids = raw_orderbook.get("bids") if isinstance(raw_orderbook.get("bids"), list) else []
            asks = raw_orderbook.get("asks") if isinstance(raw_orderbook.get("asks"), list) else []
            bid = best_bid(bids)
            ask = best_ask(asks)
            rows.append(
                {
                    "market_id": str(market.get("id") or market.get("conditionId") or ""),
                    "condition_id": str(market.get("conditionId") or ""),
                    "slug": market.get("slug", ""),
                    "question": market.get("question") or market.get("title", ""),
                    "event_title": _event_title(market),
                    "sport": _market_sport(market),
                    "league": _market_league(market),
                    "outcome": outcomes[outcome_index] if outcome_index < len(outcomes) else str(outcome_index),
                    "outcome_index": outcome_index,
                    "token_id": str(token_id),
                    "best_bid": bid,
                    "best_ask": ask,
                    "bid_depth": total_size(bids),
                    "ask_depth": total_size(asks),
                    "spread": ask - bid if ask is not None and bid is not None else None,
                    "retrieved_at": retrieved_at,
                    "exclusion_reason": exclusion_reason or error,
                    "raw_market": compact_json(market),
                    "raw_orderbook": compact_json(raw_orderbook),
                }
            )
    return pd.DataFrame(rows, columns=POLYMARKET_BOOK_COLUMNS)


def match_odds_to_polymarket(
    odds_quotes: pd.DataFrame,
    polymarket_books: pd.DataFrame,
    min_score: float = 72.0,
) -> pd.DataFrame:
    if odds_quotes.empty or polymarket_books.empty:
        return pd.DataFrame(columns=MATCHED_EVENT_COLUMNS)

    book_candidates = polymarket_books[polymarket_books["exclusion_reason"].fillna("") == ""].copy()
    odds_candidates = odds_quotes[odds_quotes["exclusion_reason"].fillna("") == ""].copy()
    rows: list[dict[str, Any]] = []
    matched_at = utc_now_iso()

    for _, quote in odds_candidates.iterrows():
        best_match: dict[str, Any] | None = None
        for _, book in book_candidates.iterrows():
            team_score = team_match_score(quote["team"], book["outcome"])
            event_score = event_match_score(quote["event_name"], book["question"]) or event_match_score(
                quote["event_name"], book.get("event_title", "")
            )
            league_score = event_match_score(quote.get("league", ""), book.get("league", ""))
            score = 0.65 * team_score + 0.25 * event_score + 0.10 * league_score
            if best_match is None or score > best_match["match_score"]:
                best_match = {
                    "source_event_id": quote["event_id"],
                    "source_event_name": quote["event_name"],
                    "source_team": quote["team"],
                    "sportsbook": quote["bookmaker"],
                    "decimal_odds": quote["decimal_odds"],
                    "fair_probability": quote["fair_probability"],
                    "polymarket_market_id": book["market_id"],
                    "polymarket_slug": book["slug"],
                    "polymarket_question": book["question"],
                    "polymarket_outcome": book["outcome"],
                    "polymarket_token_id": book["token_id"],
                    "polymarket_best_bid": book["best_bid"],
                    "polymarket_best_ask": book["best_ask"],
                    "polymarket_bid_depth": book["bid_depth"],
                    "polymarket_ask_depth": book["ask_depth"],
                    "polymarket_spread": book["spread"],
                    "match_score": score,
                    "event_score": event_score,
                    "team_score": team_score,
                    "league_score": league_score,
                    "matched_at": matched_at,
                }
        if best_match and best_match["match_score"] >= min_score:
            rows.append(best_match)

    return pd.DataFrame(rows, columns=MATCHED_EVENT_COLUMNS)


def score_opportunities(
    matched_events: pd.DataFrame,
    slippage_buffer: float = 0.01,
    fee_buffer: float = 0.0,
    min_net_edge: float = 0.03,
    min_match_score: float = 72.0,
    tick_size: float = 0.01,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for _, match in matched_events.iterrows():
        fair_probability = to_float(match.get("fair_probability"))
        best_ask_value = to_float(match.get("polymarket_best_ask"))
        best_bid_value = to_float(match.get("polymarket_best_bid"))
        spread = to_float(match.get("polymarket_spread"))
        match_score = to_float(match.get("match_score")) or 0.0

        gross_edge = None
        net_edge = None
        maker_quote = None
        maker_net_edge = None
        opportunity_type = "excluded"
        exclusion_reason = ""

        if match_score < min_match_score:
            exclusion_reason = "low_match_confidence"
        elif fair_probability is None:
            exclusion_reason = "missing_fair_probability"
        elif best_ask_value is None:
            exclusion_reason = "missing_polymarket_ask"
        else:
            gross_edge = fair_probability - best_ask_value
            net_edge = gross_edge - slippage_buffer - fee_buffer
            if net_edge >= min_net_edge:
                opportunity_type = "taker_candidate"
            elif best_bid_value is not None and spread is not None and spread >= 2 * tick_size:
                maker_quote = min(best_bid_value + tick_size, best_ask_value - tick_size)
                maker_net_edge = fair_probability - maker_quote - fee_buffer
                if maker_net_edge >= min_net_edge:
                    opportunity_type = "maker_candidate"
                else:
                    exclusion_reason = "edge_below_threshold"
            else:
                exclusion_reason = "edge_below_threshold"

        rows.append(
            {
                **{column: match.get(column) for column in MATCHED_EVENT_COLUMNS},
                "gross_edge": gross_edge,
                "net_edge": net_edge,
                "maker_quote": maker_quote,
                "maker_net_edge": maker_net_edge,
                "opportunity_type": opportunity_type,
                "exclusion_reason": exclusion_reason,
            }
        )

    frame = pd.DataFrame(rows, columns=OPPORTUNITY_COLUMNS)
    if frame.empty:
        return frame
    frame["_rank"] = frame["opportunity_type"].map({"taker_candidate": 0, "maker_candidate": 1}).fillna(2)
    return frame.sort_values(
        ["_rank", "net_edge", "maker_net_edge", "match_score"],
        ascending=[True, False, False, False],
        na_position="last",
    ).drop(columns=["_rank"]).reset_index(drop=True)


def write_arbitrage_tables(
    tables: dict[str, pd.DataFrame],
    output_dir: str | Path = "data/arbitrage/processed",
) -> dict[str, dict[str, Path]]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    written: dict[str, dict[str, Path]] = {}
    for name, frame in tables.items():
        parquet_path = root / f"{name}.parquet"
        csv_path = root / f"{name}.csv"
        frame.to_parquet(parquet_path, index=False)
        frame.to_csv(csv_path, index=False)
        written[name] = {"parquet": parquet_path, "csv": csv_path}
    return written


def load_oddsportal_html_files(paths: Iterable[str | Path]) -> pd.DataFrame:
    frames = []
    for path in paths:
        candidate = Path(path)
        frames.append(parse_oddsportal_html(candidate.read_text(encoding="utf-8"), source_url=candidate.resolve().as_uri()))
    if not frames:
        return pd.DataFrame(columns=ODDS_QUOTE_COLUMNS)
    return pd.concat(frames, ignore_index=True)


def _event_nodes(soup: BeautifulSoup) -> list[Any]:
    nodes = soup.select("[data-event-name], .event-row, tr[data-home-team][data-away-team]")
    if nodes:
        return nodes
    return [row for row in soup.select("tr") if row.select_one(".participant-home") and row.select_one(".participant-away")]


def _text_or_attr(node: Any, attr: str) -> str:
    value = node.get(attr)
    if value is not None:
        return str(value).strip()
    selected = node.select_one(f"[{attr}]")
    if selected:
        nested = selected.get(attr)
        if nested is not None:
            return str(nested).strip()
    return ""


def _closest_attr(node: Any, attr: str) -> str:
    current = node
    while current is not None and getattr(current, "name", None) is not None:
        value = _text_or_attr(current, attr)
        if value:
            return value
        current = current.parent
    return ""


def _first_text(node: Any, *selectors: str) -> str:
    for selector in selectors:
        selected = node.select_one(selector)
        if selected:
            if selected.get("datetime"):
                return str(selected["datetime"]).strip()
            text = selected.get_text(" ", strip=True)
            if text:
                return text
    return ""


def _event_name_from_node(node: Any) -> str:
    value = _first_text(node, ".event-name", "[data-event-title]", ".name")
    if value:
        return value
    home = _text_or_attr(node, "data-home-team") or _first_text(node, ".participant-home")
    away = _text_or_attr(node, "data-away-team") or _first_text(node, ".participant-away")
    return f"{home} vs {away}".strip()


def _node_payload(node: Any) -> dict[str, Any]:
    return {
        "attrs": dict(node.attrs),
        "text": node.get_text(" ", strip=True)[:500],
    }


def _safe_name(url: str) -> str:
    parsed = urlparse(url)
    candidate = f"{parsed.netloc}{parsed.path}".strip("/") or "oddsportal"
    return re.sub(r"[^A-Za-z0-9_.=-]+", "-", candidate).strip("-")[:160] or "oddsportal"


def _sport_from_url(source_url: str) -> str:
    parts = [part for part in urlparse(source_url).path.split("/") if part]
    return parts[0] if parts else ""


def _event_title(market: dict[str, Any]) -> str:
    events = parse_json_array(market.get("events"))
    if events and isinstance(events[0], dict):
        return str(events[0].get("title") or "")
    return ""


def _market_sport(market: dict[str, Any]) -> str:
    events = parse_json_array(market.get("events"))
    for event in events:
        if isinstance(event, dict):
            value = event.get("sport") or event.get("category")
            if value:
                return str(value)
    return str(market.get("category") or "")


def _market_league(market: dict[str, Any]) -> str:
    events = parse_json_array(market.get("events"))
    for event in events:
        if isinstance(event, dict):
            value = event.get("league") or event.get("series") or event.get("title")
            if value:
                return str(value)
    return ""
