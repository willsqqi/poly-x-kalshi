from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any

import pandas as pd

from . import all_active_market_snapshot, all_open_event_match, market_pair_semantic_review
from .cloud_db import (
    DEFAULT_SCHEMA_PATH,
    apply_schema,
    ingest_active_candidates,
    read_csv_path,
    read_table,
    seed_approved_market_pairs,
)
from .fifa_arbitrage import write_latest_processed_table
from .utils import utc_now_iso


DEFAULT_WORK_DIR = "/tmp/poly_x_kalshi_daily/cross_sports_arbitrage"
DEFAULT_APPROVED_MARKET_PAIRS = "data/cross_sports_arbitrage/manual_review/approved_market_pairs/current.csv"
APPROVED_EVENT_PAIR_TABLE = "all_open_event_possible_match_validity_gemini25"
CACHE_HYDRATION_TABLES = (
    "all_open_event_embeddings_gemini2",
    "market_pair_venue_fact_embeddings",
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the daily cloud active-universe and matching pipeline.")
    parser.add_argument("--work-dir", default=DEFAULT_WORK_DIR)
    parser.add_argument("--gcs-output", default="")
    parser.add_argument("--schema-path", default=DEFAULT_SCHEMA_PATH)
    parser.add_argument("--approved-market-pairs-path", default="")
    parser.add_argument("--skip-db", action="store_true")
    parser.add_argument("--skip-migration", action="store_true")
    parser.add_argument("--skip-fetch", action="store_true")
    parser.add_argument("--skip-event-candidates", action="store_true")
    parser.add_argument("--skip-market-candidates", action="store_true")
    parser.add_argument("--semantic-embedding-provider", default="vertex-gemini")
    parser.add_argument("--semantic-embedding-dim", type=int, default=768)
    parser.add_argument("--semantic-batch-size", type=int, default=32)
    parser.add_argument("--semantic-batch-sleep-seconds", type=float, default=3.0)
    parser.add_argument("--event-top-k", type=int, default=10)
    parser.add_argument("--market-top-k", type=int, default=1)
    parser.add_argument("--market-max-new-embeddings", type=int, default=0)
    parser.add_argument("--max-polymarket-markets", type=int, default=0)
    parser.add_argument("--max-kalshi-markets", type=int, default=0)
    parser.add_argument("--max-polymarket-events", type=int, default=0)
    parser.add_argument("--max-kalshi-events", type=int, default=0)
    parser.add_argument("--kalshi-event-market-workers", type=int, default=8)
    parser.add_argument("--sports-only", action="store_true", help="Restrict event and market matching to venue-tagged sports inventory.")
    args = parser.parse_args(argv)

    work_dir = Path(args.work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    run_id = f"cloud-daily-{utc_now_iso().replace(':', '').replace('-', '')}"
    summary: dict[str, Any] = {
        "run_id": run_id,
        "work_dir": str(work_dir),
        "gcs_output": args.gcs_output,
        "sports_only": bool(args.sports_only),
    }

    if not args.skip_db and not args.skip_migration:
        print("cloud daily: applying database schema", flush=True)
        apply_schema(schema_path=args.schema_path)
        summary["migration"] = "applied"

    if not args.skip_fetch:
        print("cloud daily: fetching active venue markets", flush=True)
        all_active_market_snapshot.main(
            [
                "--source",
                str(work_dir),
                "--output-dir",
                str(work_dir),
                "--max-polymarket-markets",
                str(args.max_polymarket_markets),
                "--max-kalshi-markets",
                str(args.max_kalshi_markets),
                "--kalshi-event-market-workers",
                str(args.kalshi_event_market_workers),
                *(["--sports-only"] if args.sports_only else []),
            ]
        )
    active = read_table(work_dir, "all_active_approval_candidates")
    summary["active_rows"] = int(len(active))

    if not args.skip_db:
        print(f"cloud daily: ingesting {len(active):,} active candidate rows into Cloud SQL", flush=True)
        summary["db_ingest"] = ingest_active_candidates(active, run_id=_active_run_id(active))
        print(f"cloud daily: db ingest complete {summary['db_ingest']}", flush=True)
        approved_path = args.approved_market_pairs_path or DEFAULT_APPROVED_MARKET_PAIRS
        if _path_exists(approved_path):
            print(f"cloud daily: seeding approved market pairs from {approved_path}", flush=True)
            approved_market_pairs = read_csv_path(approved_path)
            if args.sports_only:
                approved_market_pairs, sports_seed_filter = filter_approved_market_pairs_to_active_event_pairs(
                    approved_market_pairs,
                    active,
                )
                summary["sports_approved_market_pair_filter"] = sports_seed_filter
                print(f"cloud daily: sports approved seed filter {sports_seed_filter}", flush=True)
            summary["db_seed_approved"] = seed_approved_market_pairs(approved_market_pairs, reviewer="cloud_daily_pipeline")
            event_pairs = approved_market_pairs_to_event_pairs(approved_market_pairs)
            write_latest_processed_table(APPROVED_EVENT_PAIR_TABLE, event_pairs, work_dir)
            summary["approved_event_pairs_seeded_for_market_matching"] = int(len(event_pairs))
            print(f"cloud daily: approved seed complete {summary['db_seed_approved']}", flush=True)
        else:
            summary["db_seed_approved"] = {"skipped": f"approved file not found: {approved_path}"}

    if args.gcs_output:
        print(f"cloud daily: hydrating prior latest caches from {args.gcs_output}", flush=True)
        summary["cache_hydration"] = hydrate_latest_tables(args.gcs_output, work_dir, CACHE_HYDRATION_TABLES)
        print(f"cloud daily: cache hydration complete {summary['cache_hydration']}", flush=True)

    if not args.skip_event_candidates:
        print("cloud daily: generating event candidates", flush=True)
        all_open_event_match.main(
            [
                "--output-dir",
                str(work_dir),
                "--run-id",
                run_id,
                "--top-k",
                str(args.event_top_k),
                "--semantic-embedding-provider",
                args.semantic_embedding_provider,
                "--semantic-embedding-dim",
                str(args.semantic_embedding_dim),
                "--semantic-batch-size",
                str(args.semantic_batch_size),
                "--semantic-batch-sleep-seconds",
                str(args.semantic_batch_sleep_seconds),
                "--max-polymarket-events",
                str(args.max_polymarket_events),
                "--max-kalshi-events",
                str(args.max_kalshi_events),
                *(["--sports-only"] if args.sports_only else []),
            ]
        )

    if not args.skip_market_candidates and _latest_table_exists(work_dir, APPROVED_EVENT_PAIR_TABLE):
        print("cloud daily: generating market candidates", flush=True)
        market_pair_semantic_review.main(
            [
                "--source",
                str(work_dir),
                "--output-dir",
                str(work_dir),
                "--top-k",
                str(args.market_top_k),
                "--min-score",
                "0",
                "--embedding-provider",
                args.semantic_embedding_provider,
                "--embedding-dim",
                str(args.semantic_embedding_dim),
                "--embedding-batch-size",
                str(args.semantic_batch_size),
                "--max-new-embeddings",
                str(args.market_max_new_embeddings),
                "--ai-review-provider",
                "off",
                "--candidate-output-name",
                "market_pair_daily_candidates",
                "--reviewed-output-name",
                "market_pair_daily_reviewed",
                "--summary-output-name",
                "market_pair_daily_summary",
                "--coverage-output-name",
                "market_pair_daily_coverage",
            ]
        )
    elif not args.skip_market_candidates:
        summary["market_candidates"] = "skipped: approved event-pair validity table missing"

    if args.gcs_output:
        print(f"cloud daily: mirroring latest tables to {args.gcs_output}", flush=True)
        summary["gcs_mirror"] = mirror_latest_tables_to_gcs(work_dir, args.gcs_output)

    summary_frame = pd.DataFrame([{"metric": key, "value": json.dumps(value, sort_keys=True, default=str)} for key, value in summary.items()])
    write_latest_processed_table("cloud_daily_pipeline_summary", summary_frame, args.gcs_output or work_dir)
    print(json.dumps(summary, indent=2, sort_keys=True, default=str), flush=True)
    return 0


def approved_market_pairs_to_event_pairs(frame: pd.DataFrame) -> pd.DataFrame:
    columns = ["verdict", "pm_event_id", "ks_event_id", "pm_title", "ks_title", "review_reason"]
    if frame.empty or "event_match_key" not in frame.columns:
        return pd.DataFrame(columns=columns)
    data = frame.fillna("").copy()
    if "manual_decision" in data.columns:
        data = data[data["manual_decision"].astype(str).str.casefold().eq("approved")].copy()
    if "lifecycle_status" in data.columns:
        statuses = data["lifecycle_status"].astype(str).str.casefold()
        data = data[statuses.isin(["", "active"])].copy()
    rows: list[dict[str, str]] = []
    for _, row in data.iterrows():
        event_match_key = str(row.get("event_match_key") or "")
        if "__" not in event_match_key:
            continue
        pm_event_id, ks_event_id = event_match_key.split("__", 1)
        pm_event_id = pm_event_id.strip()
        ks_event_id = ks_event_id.strip()
        if not pm_event_id or not ks_event_id:
            continue
        rows.append(
            {
                "verdict": "valid",
                "pm_event_id": pm_event_id,
                "ks_event_id": ks_event_id,
                "pm_title": str(row.get("polymarket_event_title") or row.get("event_name") or "").strip(),
                "ks_title": str(row.get("kalshi_event_title") or "").strip(),
                "review_reason": "Derived from approved market-pair seed.",
            }
        )
    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(rows, columns=columns).drop_duplicates(subset=["pm_event_id", "ks_event_id"]).reset_index(drop=True)


def filter_approved_market_pairs_to_active_event_pairs(
    approved_market_pairs: pd.DataFrame,
    active_candidates: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, int]]:
    if approved_market_pairs.empty:
        return approved_market_pairs.copy(), {
            "approved_market_pair_rows_before": 0,
            "approved_market_pair_rows_after": 0,
            "derived_event_pairs_before": 0,
            "derived_event_pairs_after": 0,
            "active_polymarket_event_keys": 0,
            "active_kalshi_event_keys": 0,
            "filtered_out_rows": 0,
        }
    pm_event_keys, ks_event_keys = active_event_keys_by_venue(active_candidates)
    data = approved_market_pairs.fillna("").copy()
    before_event_pairs = _approved_event_pair_count(data)
    if "event_match_key" not in data.columns:
        filtered = data.iloc[0:0].copy()
    else:
        event_keys = data["event_match_key"].astype(str).str.split("__", n=1, expand=True)
        if event_keys.shape[1] < 2:
            filtered = data.iloc[0:0].copy()
        else:
            pm_keys = event_keys[0].str.strip()
            ks_keys = event_keys[1].str.strip()
            mask = pm_keys.isin(pm_event_keys) & ks_keys.isin(ks_event_keys)
            filtered = data[mask].copy()
    after_event_pairs = _approved_event_pair_count(filtered)
    return filtered.reset_index(drop=True), {
        "approved_market_pair_rows_before": int(len(data)),
        "approved_market_pair_rows_after": int(len(filtered)),
        "derived_event_pairs_before": int(before_event_pairs),
        "derived_event_pairs_after": int(after_event_pairs),
        "active_polymarket_event_keys": int(len(pm_event_keys)),
        "active_kalshi_event_keys": int(len(ks_event_keys)),
        "filtered_out_rows": int(len(data) - len(filtered)),
    }


def active_event_keys_by_venue(active_candidates: pd.DataFrame) -> tuple[set[str], set[str]]:
    pm_event_keys: set[str] = set()
    ks_event_keys: set[str] = set()
    if active_candidates.empty:
        return pm_event_keys, ks_event_keys
    for _, row in active_candidates.fillna("").iterrows():
        venue = str(row.get("venue") or "").strip().casefold()
        payload = _json_payload(row.get("raw_payload"))
        if venue == "polymarket":
            event_key = str(payload.get("_event_context_id") or "").strip()
            if event_key:
                pm_event_keys.add(event_key)
        elif venue == "kalshi":
            event_key = str(payload.get("_event_context_ticker") or payload.get("event_ticker") or "").strip()
            if event_key:
                ks_event_keys.add(event_key)
    return pm_event_keys, ks_event_keys


def _approved_event_pair_count(frame: pd.DataFrame) -> int:
    if frame.empty or "event_match_key" not in frame.columns:
        return 0
    keys = frame["event_match_key"].fillna("").astype(str)
    return int(keys[keys.str.contains("__", regex=False)].nunique())


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


def hydrate_latest_tables(source: str | Path, work_dir: Path, table_names: tuple[str, ...] | list[str]) -> dict[str, str]:
    hydrated: dict[str, str] = {}
    for table_name in table_names:
        try:
            frame = read_table(source, table_name)
        except FileNotFoundError:
            hydrated[table_name] = "missing"
            continue
        write_latest_processed_table(table_name, frame, work_dir)
        hydrated[table_name] = f"hydrated {len(frame):,} rows"
    return hydrated


def mirror_latest_tables_to_gcs(work_dir: Path, gcs_output: str) -> dict[str, str]:
    latest = work_dir / "processed" / "latest"
    mirrored: dict[str, str] = {}
    if not latest.exists():
        return mirrored
    for parquet_path in sorted(latest.glob("*.parquet")):
        name = parquet_path.stem
        frame = pd.read_parquet(parquet_path)
        paths = write_latest_processed_table(name, frame, gcs_output)
        mirrored[name] = str(paths.get("parquet", ""))
    return mirrored


def _active_run_id(frame: pd.DataFrame) -> str:
    if "run_id" in frame.columns and not frame.empty:
        return str(frame["run_id"].fillna("").astype(str).iloc[0])
    return f"active-ingest-{utc_now_iso().replace(':', '').replace('-', '')}"


def _latest_table_exists(work_dir: Path, table_name: str) -> bool:
    base = work_dir / "processed" / "latest" / table_name
    return base.with_suffix(".parquet").exists() or base.with_suffix(".csv").exists()


def _path_exists(path: str) -> bool:
    if path.startswith("gs://"):
        try:
            read_csv_path(path).head(1)
        except FileNotFoundError:
            return False
        return True
    return Path(path).exists()


def reset_work_dir(path: str | Path) -> None:
    target = Path(path)
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
