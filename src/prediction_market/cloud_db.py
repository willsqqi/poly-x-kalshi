from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

import pandas as pd

from .utils import parse_timestamp, utc_now_iso


DEFAULT_SOURCE = "data/cross_sports_arbitrage"
DEFAULT_SCHEMA_PATH = "infra/gcp/sql/prediction_market_schema.sql"
ACTIVE_APPROVAL_TABLE = "all_active_approval_candidates"
APPROVED_MARKET_PAIRS_PATH = "manual_review/approved_market_pairs/current.csv"


@dataclass(frozen=True)
class DbConfig:
    dsn: str
    host: str
    dbname: str
    user: str
    password: str
    port: int


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cloud SQL utilities for the Poly x Kalshi matching database.")
    parser.add_argument("--dsn", default=os.getenv("POLY_X_KALSHI_DB_DSN", ""))
    subparsers = parser.add_subparsers(dest="command", required=True)

    migrate = subparsers.add_parser("migrate", help="Apply the PostgreSQL schema.")
    migrate.add_argument("--schema-path", default=DEFAULT_SCHEMA_PATH)

    ingest = subparsers.add_parser("ingest-active", help="Upsert active events, markets, and outcomes.")
    ingest.add_argument("--source", default=DEFAULT_SOURCE)
    ingest.add_argument("--table", default=ACTIVE_APPROVAL_TABLE)
    ingest.add_argument("--run-id", default="")

    seed = subparsers.add_parser("seed-approved", help="Load approved market pairs into Cloud SQL.")
    seed.add_argument("--source", default=DEFAULT_SOURCE)
    seed.add_argument("--path", default="")
    seed.add_argument("--reviewer", default="cloud-db-seed")

    args = parser.parse_args(argv)
    if args.command == "migrate":
        apply_schema(schema_path=args.schema_path, dsn=args.dsn)
    elif args.command == "ingest-active":
        frame = read_table(args.source, args.table)
        summary = ingest_active_candidates(frame, run_id=args.run_id or _latest_run_id(frame), dsn=args.dsn)
        print(json.dumps(summary, indent=2, sort_keys=True))
    elif args.command == "seed-approved":
        path = args.path or _join_source(args.source, APPROVED_MARKET_PAIRS_PATH)
        frame = read_csv_path(path)
        summary = seed_approved_market_pairs(frame, reviewer=args.reviewer, dsn=args.dsn)
        print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def apply_schema(*, schema_path: str | Path = DEFAULT_SCHEMA_PATH, dsn: str = "") -> None:
    sql = _read_text(schema_path)
    with connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()


def ingest_active_candidates(frame: pd.DataFrame, *, run_id: str = "", dsn: str = "") -> dict[str, Any]:
    if frame.empty:
        return {"run_id": run_id, "active_rows": 0, "events_upserted": 0, "markets_upserted": 0, "outcomes_upserted": 0}
    run_id = run_id or _latest_run_id(frame) or f"active-ingest-{utc_now_iso().replace(':', '').replace('-', '')}"
    data = frame.fillna("").copy()
    now = utc_now_iso()
    event_ids: dict[tuple[str, str], int] = {}
    market_ids: dict[tuple[str, str], int] = {}
    outcome_count = 0
    with connect(dsn) as conn:
        with conn.cursor() as cur:
            _upsert_run(cur, run_id, "active_universe_ingest", started_at=str(data.get("retrieved_at", pd.Series([now])).iloc[0] or now))
            for _, row in data.iterrows():
                venue = _clean(row.get("venue"))
                if venue not in {"polymarket", "kalshi"}:
                    continue
                event_key = _event_key(row)
                market_key = _market_id(row)
                if not event_key or not market_key:
                    continue
                event_tuple = (venue, event_key)
                if event_tuple not in event_ids:
                    event_ids[event_tuple] = _upsert_event(cur, row, event_key=event_key, run_id=run_id, seen_at=now)
                market_tuple = (venue, market_key)
                if market_tuple not in market_ids:
                    market_ids[market_tuple] = _upsert_market(
                        cur,
                        row,
                        market_key=market_key,
                        event_id=event_ids[event_tuple],
                        run_id=run_id,
                        seen_at=now,
                )
                _upsert_outcome(cur, row, market_id=market_ids[market_tuple], seen_at=now)
                outcome_count += 1
                if outcome_count <= 3 or outcome_count % 5000 == 0:
                    print(
                        "cloud db ingest: "
                        f"rows={outcome_count:,}/{len(data):,} "
                        f"events={len(event_ids):,} "
                        f"markets={len(market_ids):,}",
                        flush=True,
                    )
            expired = expire_missing_records(cur, run_id=run_id)
            cur.execute(
                """
                UPDATE prediction_market.etl_runs
                SET status = 'succeeded', finished_at = now(), notes = %s
                WHERE run_id = %s
                """,
                (json.dumps({"expired": expired}, sort_keys=True), run_id),
            )
        conn.commit()
    return {
        "run_id": run_id,
        "active_rows": int(len(data)),
        "events_upserted": len(event_ids),
        "markets_upserted": len(market_ids),
        "outcomes_upserted": outcome_count,
        "expired": expired,
    }


def seed_approved_market_pairs(frame: pd.DataFrame, *, reviewer: str = "cloud-db-seed", dsn: str = "") -> dict[str, Any]:
    if frame.empty:
        return {"approved_rows": 0, "seeded_market_pairs": 0, "skipped_rows": 0}
    data = frame.fillna("").copy()
    seeded = 0
    skipped = 0
    reviewed_at = utc_now_iso()
    with connect(dsn) as conn:
        with conn.cursor() as cur:
            for _, row in data.iterrows():
                pm_outcome_id = _find_outcome(
                    cur,
                    venue="polymarket",
                    venue_market_id=_clean(row.get("polymarket_market_id")),
                    token_id=_clean(row.get("polymarket_yes_token_id")),
                    outcome_label=_clean(row.get("polymarket_yes_outcome")),
                )
                ks_outcome_id = _find_outcome(
                    cur,
                    venue="kalshi",
                    venue_market_id=_clean(row.get("kalshi_ticker")),
                    token_id="",
                    outcome_label=_kalshi_yes_label(row),
                )
                if not pm_outcome_id or not ks_outcome_id:
                    skipped += 1
                    continue
                event_pair_id = _upsert_approved_event_pair(
                    cur,
                    pm_outcome_id=pm_outcome_id,
                    ks_outcome_id=ks_outcome_id,
                    reviewer=reviewer,
                    reviewed_at=reviewed_at,
                    notes="Seeded from approved_market_pairs/current.csv",
                )
                _upsert_approved_market_pair(
                    cur,
                    event_pair_id=event_pair_id,
                    pm_outcome_id=pm_outcome_id,
                    ks_outcome_id=ks_outcome_id,
                    reviewer=reviewer,
                    reviewed_at=reviewed_at,
                    settlement_notes=_clean(row.get("settlement_notes")),
                    notes=_clean(row.get("manual_notes")) or "Seeded from approved_market_pairs/current.csv",
                )
                seeded += 1
                if seeded <= 3 or seeded % 1000 == 0:
                    print(
                        "cloud db approved seed: "
                        f"seeded={seeded:,} skipped={skipped:,} total={len(data):,}",
                        flush=True,
                    )
            _insert_approval_action(
                cur,
                entity_type="market_pair",
                entity_id=0,
                action="approved",
                reviewer=reviewer,
                notes=f"bulk seed completed; seeded={seeded}; skipped={skipped}",
            )
        conn.commit()
    return {"approved_rows": int(len(data)), "seeded_market_pairs": seeded, "skipped_rows": skipped}


def expire_missing_records(cur: Any, *, run_id: str) -> dict[str, int]:
    cur.execute(
        """
        UPDATE prediction_market.venue_events
        SET lifecycle_status = 'expired', expired_at = COALESCE(expired_at, now()), updated_at = now()
        WHERE lifecycle_status = 'active'
          AND last_seen_run_id IS DISTINCT FROM %s
        """,
        (run_id,),
    )
    events = cur.rowcount
    cur.execute(
        """
        UPDATE prediction_market.venue_markets
        SET lifecycle_status = 'expired', expired_at = COALESCE(expired_at, now()), updated_at = now()
        WHERE lifecycle_status = 'active'
          AND last_seen_run_id IS DISTINCT FROM %s
        """,
        (run_id,),
    )
    markets = cur.rowcount
    cur.execute(
        """
        UPDATE prediction_market.market_outcomes o
        SET lifecycle_status = 'expired', expired_at = COALESCE(o.expired_at, now())
        FROM prediction_market.venue_markets m
        WHERE o.market_id = m.market_id
          AND o.lifecycle_status = 'active'
          AND m.lifecycle_status <> 'active'
        """
    )
    outcomes = cur.rowcount
    cur.execute(
        """
        UPDATE prediction_market.approved_event_pairs ep
        SET lifecycle_status = 'expired', expired_at = COALESCE(ep.expired_at, now()), updated_at = now()
        FROM prediction_market.venue_events pm, prediction_market.venue_events ks
        WHERE ep.pm_event_id = pm.event_id
          AND ep.kalshi_event_id = ks.event_id
          AND ep.lifecycle_status = 'active'
          AND (pm.lifecycle_status <> 'active' OR ks.lifecycle_status <> 'active')
        """
    )
    event_pairs = cur.rowcount
    cur.execute(
        """
        UPDATE prediction_market.approved_market_pairs amp
        SET lifecycle_status = 'expired', expired_at = COALESCE(amp.expired_at, now()), updated_at = now()
        FROM prediction_market.market_outcomes pm, prediction_market.market_outcomes ks
        WHERE amp.pm_outcome_id = pm.outcome_id
          AND amp.kalshi_outcome_id = ks.outcome_id
          AND amp.lifecycle_status = 'active'
          AND (pm.lifecycle_status <> 'active' OR ks.lifecycle_status <> 'active')
        """
    )
    market_pairs = cur.rowcount
    return {
        "events": int(events),
        "markets": int(markets),
        "outcomes": int(outcomes),
        "event_pairs": int(event_pairs),
        "market_pairs": int(market_pairs),
    }


def connect(dsn: str = "") -> Any:
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover - deployment dependency guard
        raise RuntimeError("Cloud DB support requires installing the gcp extra with psycopg.") from exc
    config = db_config(dsn)
    if config.dsn:
        return psycopg.connect(config.dsn)
    return psycopg.connect(
        host=config.host,
        port=config.port,
        dbname=config.dbname,
        user=config.user,
        password=config.password,
    )


def db_config(dsn: str = "") -> DbConfig:
    return DbConfig(
        dsn=dsn or os.getenv("POLY_X_KALSHI_DB_DSN", ""),
        host=os.getenv("POLY_X_KALSHI_DB_HOST", "127.0.0.1"),
        dbname=os.getenv("POLY_X_KALSHI_DB_NAME", "prediction_market"),
        user=os.getenv("POLY_X_KALSHI_DB_USER", "prediction_market_app"),
        password=os.getenv("POLY_X_KALSHI_DB_PASSWORD", ""),
        port=int(os.getenv("POLY_X_KALSHI_DB_PORT", "5432")),
    )


def read_table(source: str | Path, table_name: str) -> pd.DataFrame:
    base = _join_source(source, f"processed/latest/{table_name}")
    parquet = f"{base}.parquet"
    csv = f"{base}.csv"
    if str(source).startswith("gs://"):
        try:
            return pd.read_parquet(BytesIO(_download_gcs_bytes(parquet)))
        except FileNotFoundError:
            return pd.read_csv(BytesIO(_download_gcs_bytes(csv)), dtype=str, keep_default_na=False)
    parquet_path = Path(parquet)
    csv_path = Path(csv)
    if parquet_path.exists():
        return pd.read_parquet(parquet_path)
    if csv_path.exists():
        return pd.read_csv(csv_path, dtype=str, keep_default_na=False)
    raise FileNotFoundError(f"Expected {parquet_path} or {csv_path}")


def read_csv_path(path: str | Path) -> pd.DataFrame:
    text = str(path)
    if text.startswith("gs://"):
        return pd.read_csv(BytesIO(_download_gcs_bytes(text)), dtype=str, keep_default_na=False)
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def _upsert_run(cur: Any, run_id: str, run_type: str, *, started_at: str) -> None:
    cur.execute(
        """
        INSERT INTO prediction_market.etl_runs (run_id, run_type, status, started_at)
        VALUES (%s, %s, 'running', COALESCE(%s::timestamptz, now()))
        ON CONFLICT (run_id) DO UPDATE
        SET run_type = EXCLUDED.run_type,
            status = 'running',
            started_at = EXCLUDED.started_at
        """,
        (run_id, run_type, started_at or None),
    )


def _upsert_event(cur: Any, row: pd.Series, *, event_key: str, run_id: str, seen_at: str) -> int:
    payload = _json_payload(row.get("raw_payload"))
    context = _json_payload(payload.get("_event_context_payload"))
    title = _clean(row.get("event_title")) or _clean(context.get("title")) or _clean(row.get("title")) or event_key
    cur.execute(
        """
        INSERT INTO prediction_market.venue_events (
            venue, venue_event_id, event_ticker, slug, title, subtitle, category,
            series_ticker, product_metadata, event_status, lifecycle_status,
            close_time, expiration_time, first_seen_at, last_seen_at, last_seen_run_id,
            fact_text_hash, raw_payload, updated_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'active', %s, %s, %s, %s, %s, %s, %s, now())
        ON CONFLICT (venue, venue_event_id) DO UPDATE
        SET event_ticker = EXCLUDED.event_ticker,
            slug = EXCLUDED.slug,
            title = EXCLUDED.title,
            subtitle = EXCLUDED.subtitle,
            category = EXCLUDED.category,
            series_ticker = EXCLUDED.series_ticker,
            product_metadata = EXCLUDED.product_metadata,
            event_status = EXCLUDED.event_status,
            lifecycle_status = 'active',
            close_time = EXCLUDED.close_time,
            expiration_time = EXCLUDED.expiration_time,
            last_seen_at = EXCLUDED.last_seen_at,
            last_seen_run_id = EXCLUDED.last_seen_run_id,
            fact_text_hash = EXCLUDED.fact_text_hash,
            raw_payload = EXCLUDED.raw_payload,
            updated_at = now()
        RETURNING event_id
        """,
        (
            _clean(row.get("venue")),
            event_key,
            _clean(context.get("event_ticker") or context.get("ticker") or event_key),
            _clean(context.get("slug") or payload.get("slug")),
            title,
            _clean(row.get("subtitle") or context.get("sub_title") or context.get("subtitle")),
            _clean(row.get("category") or context.get("category")),
            _clean(context.get("series_ticker") or payload.get("series_ticker")),
            _clean(context.get("product_metadata") or payload.get("product_metadata")),
            _clean(row.get("status") or context.get("status")),
            _timestamp(row.get("close_time") or context.get("close_time") or context.get("expected_expiration_time")),
            _timestamp(context.get("expiration_time") or context.get("latest_expiration_time")),
            seen_at,
            seen_at,
            run_id,
            _clean(row.get("event_match_key")),
            _jsonb(payload),
        ),
    )
    return int(cur.fetchone()[0])


def _upsert_market(cur: Any, row: pd.Series, *, market_key: str, event_id: int, run_id: str, seen_at: str) -> int:
    payload = _json_payload(row.get("raw_payload"))
    cur.execute(
        """
        INSERT INTO prediction_market.venue_markets (
            venue, venue_market_id, venue_event_id, ticker_or_slug, title, subtitle,
            category, market_type, market_status, lifecycle_status, close_time,
            expiration_time, first_seen_at, last_seen_at, last_seen_run_id,
            rules_text, settlement_summary, liquidity_hint, fact_text_hash, raw_payload,
            updated_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'active', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
        ON CONFLICT (venue, venue_market_id) DO UPDATE
        SET venue_event_id = EXCLUDED.venue_event_id,
            ticker_or_slug = EXCLUDED.ticker_or_slug,
            title = EXCLUDED.title,
            subtitle = EXCLUDED.subtitle,
            category = EXCLUDED.category,
            market_type = EXCLUDED.market_type,
            market_status = EXCLUDED.market_status,
            lifecycle_status = 'active',
            close_time = EXCLUDED.close_time,
            expiration_time = EXCLUDED.expiration_time,
            last_seen_at = EXCLUDED.last_seen_at,
            last_seen_run_id = EXCLUDED.last_seen_run_id,
            rules_text = EXCLUDED.rules_text,
            settlement_summary = EXCLUDED.settlement_summary,
            liquidity_hint = EXCLUDED.liquidity_hint,
            fact_text_hash = EXCLUDED.fact_text_hash,
            raw_payload = EXCLUDED.raw_payload,
            updated_at = now()
        RETURNING market_id
        """,
        (
            _clean(row.get("venue")),
            market_key,
            event_id,
            _clean(row.get("ticker_or_slug") or market_key),
            _clean(row.get("title")) or market_key,
            _clean(row.get("subtitle")),
            _clean(row.get("category")),
            _clean(row.get("market_type")),
            _clean(row.get("status")),
            _timestamp(row.get("close_time")),
            _timestamp(payload.get("expiration_time") or payload.get("expected_expiration_time") or payload.get("latest_expiration_time")),
            seen_at,
            seen_at,
            run_id,
            _clean(row.get("rules_text")),
            _clean(row.get("settlement_summary")),
            _clean(row.get("liquidity_hint")),
            _clean(row.get("event_match_key")),
            _jsonb(payload),
        ),
    )
    return int(cur.fetchone()[0])


def _upsert_outcome(cur: Any, row: pd.Series, *, market_id: int, seen_at: str) -> int:
    outcome_label = _clean(row.get("outcome_label")) or _first_outcome_label(row.get("outcomes")) or "Yes"
    outcome_key = _clean(row.get("yes_token_id")) or outcome_label
    cur.execute(
        """
        INSERT INTO prediction_market.market_outcomes (
            market_id, outcome_key, outcome_label, side, token_id, no_token_id,
            lifecycle_status, first_seen_at, last_seen_at, raw_payload
        )
        VALUES (%s, %s, %s, %s, %s, %s, 'active', %s, %s, %s)
        ON CONFLICT (market_id, outcome_key) DO UPDATE
        SET outcome_label = EXCLUDED.outcome_label,
            side = EXCLUDED.side,
            token_id = EXCLUDED.token_id,
            no_token_id = EXCLUDED.no_token_id,
            lifecycle_status = 'active',
            last_seen_at = EXCLUDED.last_seen_at,
            raw_payload = EXCLUDED.raw_payload
        RETURNING outcome_id
        """,
        (
            market_id,
            outcome_key,
            outcome_label,
            "yes",
            _clean(row.get("yes_token_id")),
            _clean(row.get("no_token_id")),
            seen_at,
            seen_at,
            _jsonb({"outcomes": _parse_json_array(row.get("outcomes")), "source_row": row.to_dict()}),
        ),
    )
    return int(cur.fetchone()[0])


def _find_outcome(cur: Any, *, venue: str, venue_market_id: str, token_id: str, outcome_label: str) -> int | None:
    if not venue_market_id:
        return None
    if token_id:
        cur.execute(
            """
            SELECT o.outcome_id
            FROM prediction_market.market_outcomes o
            JOIN prediction_market.venue_markets m ON m.market_id = o.market_id
            JOIN prediction_market.venue_events e ON e.event_id = m.venue_event_id
            WHERE m.venue = %s AND m.venue_market_id = %s AND o.token_id = %s
              AND m.lifecycle_status = 'active'
              AND e.lifecycle_status = 'active'
              AND o.lifecycle_status = 'active'
            LIMIT 1
            """,
            (venue, venue_market_id, token_id),
        )
    else:
        cur.execute(
            """
            SELECT o.outcome_id
            FROM prediction_market.market_outcomes o
            JOIN prediction_market.venue_markets m ON m.market_id = o.market_id
            JOIN prediction_market.venue_events e ON e.event_id = m.venue_event_id
            WHERE m.venue = %s
              AND m.venue_market_id = %s
              AND lower(o.outcome_label) = lower(%s)
              AND m.lifecycle_status = 'active'
              AND e.lifecycle_status = 'active'
              AND o.lifecycle_status = 'active'
            LIMIT 1
            """,
            (venue, venue_market_id, outcome_label),
        )
    row = cur.fetchone()
    return int(row[0]) if row else None


def _upsert_approved_event_pair(
    cur: Any,
    *,
    pm_outcome_id: int,
    ks_outcome_id: int,
    reviewer: str,
    reviewed_at: str,
    notes: str,
) -> int:
    cur.execute(
        """
        SELECT pm_event.event_id, ks_event.event_id
        FROM prediction_market.market_outcomes pm_outcome
        JOIN prediction_market.venue_markets pm_market ON pm_market.market_id = pm_outcome.market_id
        JOIN prediction_market.venue_events pm_event ON pm_event.event_id = pm_market.venue_event_id
        CROSS JOIN prediction_market.market_outcomes ks_outcome
        JOIN prediction_market.venue_markets ks_market ON ks_market.market_id = ks_outcome.market_id
        JOIN prediction_market.venue_events ks_event ON ks_event.event_id = ks_market.venue_event_id
        WHERE pm_outcome.outcome_id = %s AND ks_outcome.outcome_id = %s
        """,
        (pm_outcome_id, ks_outcome_id),
    )
    pm_event_id, ks_event_id = cur.fetchone()
    cur.execute(
        """
        INSERT INTO prediction_market.approved_event_pairs (
            pm_event_id, kalshi_event_id, review_status, lifecycle_status, reviewer, reviewed_at, notes, updated_at
        )
        VALUES (%s, %s, 'approved', 'active', %s, %s, %s, now())
        ON CONFLICT (pm_event_id, kalshi_event_id) DO UPDATE
        SET review_status = 'approved',
            lifecycle_status = 'active',
            reviewer = EXCLUDED.reviewer,
            reviewed_at = EXCLUDED.reviewed_at,
            notes = EXCLUDED.notes,
            updated_at = now()
        RETURNING event_pair_id
        """,
        (pm_event_id, ks_event_id, reviewer, reviewed_at, notes),
    )
    return int(cur.fetchone()[0])


def _upsert_approved_market_pair(
    cur: Any,
    *,
    event_pair_id: int,
    pm_outcome_id: int,
    ks_outcome_id: int,
    reviewer: str,
    reviewed_at: str,
    settlement_notes: str,
    notes: str,
) -> int:
    cur.execute(
        """
        INSERT INTO prediction_market.approved_market_pairs (
            event_pair_id, pm_outcome_id, kalshi_outcome_id, review_status, lifecycle_status,
            reviewer, reviewed_at, settlement_notes, notes, updated_at
        )
        VALUES (%s, %s, %s, 'approved', 'active', %s, %s, %s, %s, now())
        ON CONFLICT (pm_outcome_id, kalshi_outcome_id) DO UPDATE
        SET event_pair_id = EXCLUDED.event_pair_id,
            review_status = 'approved',
            lifecycle_status = 'active',
            reviewer = EXCLUDED.reviewer,
            reviewed_at = EXCLUDED.reviewed_at,
            settlement_notes = EXCLUDED.settlement_notes,
            notes = EXCLUDED.notes,
            updated_at = now()
        RETURNING market_pair_id
        """,
        (event_pair_id, pm_outcome_id, ks_outcome_id, reviewer, reviewed_at, settlement_notes, notes),
    )
    return int(cur.fetchone()[0])


def _insert_approval_action(
    cur: Any,
    *,
    entity_type: str,
    entity_id: int,
    action: str,
    reviewer: str,
    notes: str,
) -> None:
    cur.execute(
        """
        INSERT INTO prediction_market.approval_actions (
            entity_type, entity_id, action, reviewer, notes, new_status
        )
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (entity_type, entity_id, action, reviewer, notes, action),
    )


def load_approved_mappings_from_db(*, dsn: str = "") -> pd.DataFrame:
    with connect(dsn) as conn:
        return pd.read_sql(
            """
            SELECT
                market_pair_id::text AS mapping_id,
                'approved' AS status,
                'active' AS lifecycle_status,
                polymarket_event_id AS event_name,
                polymarket_outcome AS proposition,
                polymarket_market_id,
                polymarket_slug,
                polymarket_yes_token_id,
                polymarket_no_token_id,
                polymarket_outcome AS polymarket_yes_outcome,
                'No' AS polymarket_no_outcome,
                kalshi_ticker,
                '' AS draw_handling,
                '' AS extra_time_handling,
                '' AS penalties_handling,
                settlement_notes,
                'cloud_sql' AS reviewer,
                '' AS reviewed_at,
                '' AS notes
            FROM prediction_market.active_approved_market_pairs
            """,
            conn,
        )


def _event_key(row: pd.Series) -> str:
    payload = _json_payload(row.get("raw_payload"))
    venue = _clean(row.get("venue")).casefold()
    if venue == "polymarket":
        return _clean(payload.get("_event_context_id") or row.get("event_match_key") or row.get("event_title"))
    if venue == "kalshi":
        return _clean(payload.get("_event_context_ticker") or payload.get("event_ticker") or row.get("event_match_key") or row.get("event_title"))
    return ""


def _market_id(row: pd.Series) -> str:
    return _clean(row.get("market_id") or row.get("ticker_or_slug"))


def _latest_run_id(frame: pd.DataFrame) -> str:
    if "run_id" in frame.columns and not frame.empty:
        values = frame["run_id"].fillna("").astype(str)
        if not values.empty:
            return str(values.iloc[0])
    return ""


def _kalshi_yes_label(row: pd.Series) -> str:
    outcomes = _parse_json_array(row.get("kalshi_outcomes"))
    for value in outcomes:
        text = str(value)
        if text.casefold().startswith("yes:"):
            return text.split(":", 1)[1].strip()
    return _clean(row.get("outcome_label")) or _clean(row.get("kalshi_title"))


def _first_outcome_label(value: Any) -> str:
    outcomes = _parse_json_array(value)
    if not outcomes:
        return ""
    first = str(outcomes[0])
    return first.split(":", 1)[1].strip() if first.casefold().startswith("yes:") else first


def _parse_json_array(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _json_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _jsonb(value: Any) -> Any:
    try:
        from psycopg.types.json import Jsonb
    except ImportError:  # pragma: no cover - connect() already guards this
        return json.dumps(value, sort_keys=True, default=str)
    return Jsonb(value)


def _timestamp(value: Any) -> str | None:
    return parse_timestamp(value)


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _join_source(source: str | Path, suffix: str) -> str:
    source_text = str(source).rstrip("/")
    if source_text.startswith("gs://"):
        return f"{source_text}/{suffix.lstrip('/')}"
    return str(Path(source_text) / suffix)


def _read_text(path: str | Path) -> str:
    text = str(path)
    if text.startswith("gs://"):
        return _download_gcs_bytes(text).decode("utf-8")
    return Path(path).read_text(encoding="utf-8")


def _download_gcs_bytes(uri: str) -> bytes:
    from google.api_core.exceptions import NotFound
    from google.cloud import storage

    bucket_name, blob_name = _split_gcs_uri(uri)
    try:
        return storage.Client().bucket(bucket_name).blob(blob_name).download_as_bytes()
    except NotFound as exc:
        raise FileNotFoundError(uri) from exc


def _split_gcs_uri(uri: str) -> tuple[str, str]:
    if not uri.startswith("gs://"):
        raise ValueError(f"Expected gs:// URI, got {uri}")
    bucket, _, blob = uri.removeprefix("gs://").partition("/")
    if not bucket:
        raise ValueError(f"GCS URI is missing bucket: {uri}")
    return bucket, blob


if __name__ == "__main__":
    raise SystemExit(main())
