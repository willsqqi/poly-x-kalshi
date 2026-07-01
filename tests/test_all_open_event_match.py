from __future__ import annotations

import pandas as pd

from prediction_market import all_open_event_match as events


def test_sports_event_predicates_use_venue_native_fields() -> None:
    assert events.is_polymarket_sports_event({"tags": [{"label": "Sports"}, {"label": "Soccer"}]})
    assert not events.is_polymarket_sports_event({"tags": [{"label": "Crypto"}]})

    assert events.is_kalshi_sports_event({"category": "Sports"})
    assert not events.is_kalshi_sports_event({"category": "Crypto"})


def test_filter_sports_events_keeps_polymarket_tags_and_kalshi_category() -> None:
    frame = pd.DataFrame(
        [
            {
                "venue": "polymarket",
                "event_id": "pm-sports",
                "category": "",
                "raw_event_payload": '{"tags":[{"label":"Sports"}]}',
            },
            {
                "venue": "polymarket",
                "event_id": "pm-crypto",
                "category": "Crypto",
                "raw_event_payload": '{"tags":[{"label":"Crypto"}]}',
            },
            {
                "venue": "kalshi",
                "event_id": "ks-sports",
                "category": "Sports",
                "raw_event_payload": '{"category":"Sports"}',
            },
            {
                "venue": "kalshi",
                "event_id": "ks-economics",
                "category": "Economics",
                "raw_event_payload": '{"category":"Economics"}',
            },
        ]
    )

    filtered = events.filter_sports_events(frame)

    assert filtered["event_id"].tolist() == ["pm-sports", "ks-sports"]
