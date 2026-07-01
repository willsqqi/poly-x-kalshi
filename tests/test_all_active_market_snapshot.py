from __future__ import annotations

from typing import Any

from prediction_market import all_active_market_snapshot as snapshot


def test_parallel_kalshi_event_market_fetch_dedupes_and_keeps_context(monkeypatch: Any, capsys: Any) -> None:
    events = [
        {"event_ticker": "KX-1", "title": "Event One"},
        {"event_ticker": "KX-2", "title": "Event Two"},
        {"event_ticker": "KX-3", "title": "Event Three"},
        {"event_ticker": "KX-4", "title": "Event Four"},
    ]
    event_markets = {
        "KX-1": [{"ticker": "KX-1-A", "title": "One A"}],
        "KX-2": [{"ticker": "KX-2-A", "title": "Two A"}],
        "KX-3": [
            {"ticker": "KX-2-A", "title": "Duplicate"},
            {"ticker": "KX-3-COMBO", "mve_collection_ticker": "combo"},
        ],
        "KX-4": [{"ticker": "KX-4-A", "title": "Four A"}],
    }

    monkeypatch.setattr(snapshot, "fetch_all_open_kalshi_events", lambda *args, **kwargs: events)
    monkeypatch.setattr(
        snapshot,
        "fetch_open_kalshi_event_markets",
        lambda _client, event_ticker, **_kwargs: event_markets[event_ticker],
    )

    markets = snapshot.fetch_all_open_kalshi_markets(object(), event_market_workers=2)

    assert {market["ticker"] for market in markets} == {"KX-1-A", "KX-2-A", "KX-4-A"}
    assert {market["_event_context_ticker"] for market in markets} == {"KX-1", "KX-2", "KX-4"}
    assert "events=4/4" in capsys.readouterr().out


def test_kalshi_event_market_fetch_stays_sequential_when_market_limited(monkeypatch: Any) -> None:
    events = [
        {"event_ticker": "KX-1", "title": "Event One"},
        {"event_ticker": "KX-2", "title": "Event Two"},
        {"event_ticker": "KX-3", "title": "Event Three"},
    ]
    calls: list[str] = []

    def fake_event_markets(_client: object, event_ticker: str, **_kwargs: Any) -> list[dict[str, str]]:
        calls.append(event_ticker)
        return [{"ticker": f"{event_ticker}-A", "title": event_ticker}]

    monkeypatch.setattr(snapshot, "fetch_all_open_kalshi_events", lambda *args, **kwargs: events)
    monkeypatch.setattr(snapshot, "fetch_open_kalshi_event_markets", fake_event_markets)

    markets = snapshot.fetch_all_open_kalshi_markets(object(), max_markets=2, event_market_workers=8)

    assert [market["ticker"] for market in markets] == ["KX-1-A", "KX-2-A"]
    assert calls == ["KX-1", "KX-2"]


def test_polymarket_sports_market_uses_nested_event_tags() -> None:
    assert snapshot.is_polymarket_sports_market(
        {
            "question": "Will Spain win?",
            "_event_context_payload": {"tags": [{"label": "Sports"}, {"label": "Soccer"}]},
        }
    )
    assert not snapshot.is_polymarket_sports_market(
        {
            "question": "Bitcoin up?",
            "_event_context_payload": {"tags": [{"label": "Crypto"}]},
        }
    )


def test_polymarket_sports_market_can_use_sports_event_ids() -> None:
    assert snapshot.is_polymarket_sports_market(
        {
            "question": "Cincinnati Reds vs. Milwaukee Brewers",
            "_event_context_id": "123",
            "_event_context_payload": {"title": "Cincinnati Reds vs. Milwaukee Brewers"},
        },
        sports_event_ids={"123"},
    )
    assert not snapshot.is_polymarket_sports_market(
        {
            "question": "Ethereum above 1400?",
            "_event_context_id": "456",
            "_event_context_payload": {"title": "Ethereum above ___ on July 3?"},
        },
        sports_event_ids={"123"},
    )
