from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime

from .jobs import build_market_daily_job, fetch_markets_job, fetch_orderfilled_job, normalize_trades_job


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Phase 1 AWS historical Polymarket ETL job runner")
    parser.add_argument("--job", required=True, choices=["fetch_markets", "fetch_orderfilled", "normalize_trades", "build_market_daily"])
    parser.add_argument("--lake-uri", default=os.getenv("PREDICTION_MARKET_LAKE_URI", "data/aws_lake"))
    parser.add_argument("--run-id", default=datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"))
    parser.add_argument("--max-markets", type=int)
    parser.add_argument("--page-size", type=int, default=500)
    parser.add_argument("--token-ids", help="Comma-separated CLOB token IDs for targeted Gamma market lookup")
    parser.add_argument("--from-orderfilled-prefix", help="Parquet prefix to read decoded OrderFilled token IDs from")
    parser.add_argument("--start-block", type=int)
    parser.add_argument("--end-block", type=int)
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--max-events", type=int)
    parser.add_argument("--date-start")
    parser.add_argument("--date-end")
    parser.add_argument("--orderfilled-prefix", help="Decoded OrderFilled parquet prefix for normalize_trades")
    parser.add_argument("--orderfilled-file-batch-size", type=int, default=4)
    parser.add_argument("--dim-market-prefix", help="dim_market parquet prefix for normalize_trades")
    parser.add_argument("--dim-outcome-prefix", help="dim_outcome parquet prefix for normalize_trades")
    parser.add_argument("--fact-trades-prefix", help="fact_trades parquet prefix for build_market_daily")
    parser.add_argument("--fact-file-batch-size", type=int, default=8)
    parser.add_argument("--rpc-url")
    parser.add_argument("--rpc-secret-id", default=os.getenv("POLYGON_RPC_SECRET_ID"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.job == "fetch_markets":
        token_ids = [token.strip() for token in args.token_ids.split(",")] if args.token_ids else None
        result = fetch_markets_job(
            lake_uri=args.lake_uri,
            run_id=args.run_id,
            max_markets=args.max_markets,
            page_size=args.page_size,
            token_ids=token_ids,
            orderfilled_prefix=args.from_orderfilled_prefix,
        )
    elif args.job == "fetch_orderfilled":
        if args.start_block is None or args.end_block is None:
            raise SystemExit("--start-block and --end-block are required for fetch_orderfilled")
        result = fetch_orderfilled_job(
            lake_uri=args.lake_uri,
            run_id=args.run_id,
            start_block=args.start_block,
            end_block=args.end_block,
            batch_size=args.batch_size,
            max_events=args.max_events,
            rpc_url=args.rpc_url,
            rpc_secret_id=args.rpc_secret_id,
        )
    elif args.job == "normalize_trades":
        result = normalize_trades_job(
            lake_uri=args.lake_uri,
            run_id=args.run_id,
            date_start=args.date_start,
            date_end=args.date_end,
            orderfilled_prefix=args.orderfilled_prefix,
            dim_market_prefix=args.dim_market_prefix,
            dim_outcome_prefix=args.dim_outcome_prefix,
            orderfilled_file_batch_size=args.orderfilled_file_batch_size,
        )
    elif args.job == "build_market_daily":
        result = build_market_daily_job(
            lake_uri=args.lake_uri,
            run_id=args.run_id,
            date_start=args.date_start,
            date_end=args.date_end,
            fact_trades_prefix=args.fact_trades_prefix,
            fact_file_batch_size=args.fact_file_batch_size,
        )
    else:
        raise SystemExit(f"Unsupported job: {args.job}")

    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
