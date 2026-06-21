from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import boto3

from .paths import join_uri

TERMINAL_STATUSES = {"SUCCEEDED", "FAILED"}


@dataclass(frozen=True)
class BackfillConfig:
    region: str
    job_queue: str
    job_definition: str
    lake_uri: str
    run_id: str
    start_block: int
    end_block: int
    chunk_size: int
    rpc_batch_size: int = 1_000
    max_events: int | None = None
    date_start: str | None = None
    date_end: str | None = None
    wait: bool = True
    poll_seconds: float = 30.0
    max_wait_seconds: float | None = None
    fetch_only: bool = False
    dry_run: bool = False


def chunk_block_ranges(start_block: int, end_block: int, chunk_size: int) -> list[tuple[int, int]]:
    if start_block > end_block:
        raise ValueError("start_block must be <= end_block")
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")

    ranges = []
    current = start_block
    while current <= end_block:
        chunk_end = min(end_block, current + chunk_size - 1)
        ranges.append((current, chunk_end))
        current = chunk_end + 1
    return ranges


def build_backfill_plan(config: BackfillConfig) -> list[dict[str, Any]]:
    orderfilled_prefix = join_uri(config.lake_uri, "bronze", "polymarket", "orderfilled_raw")
    dim_market_prefix = join_uri(config.lake_uri, "gold", "polymarket", "dim_market")
    dim_outcome_prefix = join_uri(config.lake_uri, "gold", "polymarket", "dim_outcome")
    fact_trades_prefix = join_uri(config.lake_uri, "gold", "polymarket", "fact_trades")

    plan: list[dict[str, Any]] = []
    for start_block, end_block in chunk_block_ranges(config.start_block, config.end_block, config.chunk_size):
        command = [
            "--job",
            "fetch_orderfilled",
            "--lake-uri",
            config.lake_uri,
            "--run-id",
            f"fills-{start_block}-{end_block}",
            "--start-block",
            str(start_block),
            "--end-block",
            str(end_block),
            "--batch-size",
            str(config.rpc_batch_size),
        ]
        if config.max_events is not None:
            command.extend(["--max-events", str(config.max_events)])
        plan.append(
            {
                "stage": "fetch_orderfilled",
                "job_name": f"{config.run_id}-fills-{start_block}-{end_block}",
                "command": command,
                "start_block": start_block,
                "end_block": end_block,
            }
        )

    if config.fetch_only:
        return plan

    plan.append(
        {
            "stage": "fetch_markets",
            "job_name": f"{config.run_id}-token-markets",
            "command": [
                "--job",
                "fetch_markets",
                "--lake-uri",
                config.lake_uri,
                "--run-id",
                "token-markets",
                "--from-orderfilled-prefix",
                orderfilled_prefix,
            ],
        }
    )

    normalize_command = [
        "--job",
        "normalize_trades",
        "--lake-uri",
        config.lake_uri,
        "--run-id",
        "normalize",
        "--orderfilled-prefix",
        orderfilled_prefix,
        "--dim-market-prefix",
        dim_market_prefix,
        "--dim-outcome-prefix",
        dim_outcome_prefix,
        "--orderfilled-file-batch-size",
        "1",
    ]
    _append_date_args(normalize_command, config.date_start, config.date_end)
    plan.append({"stage": "normalize_trades", "job_name": f"{config.run_id}-normalize", "command": normalize_command})

    daily_command = [
        "--job",
        "build_market_daily",
        "--lake-uri",
        config.lake_uri,
        "--run-id",
        "daily",
        "--fact-trades-prefix",
        fact_trades_prefix,
        "--fact-file-batch-size",
        "1",
    ]
    _append_date_args(daily_command, config.date_start, config.date_end)
    plan.append({"stage": "build_market_daily", "job_name": f"{config.run_id}-daily", "command": daily_command})
    return plan


def submit_backfill(config: BackfillConfig, batch_client: Any | None = None) -> dict[str, Any]:
    plan = build_backfill_plan(config)
    summary: dict[str, Any] = {
        "run_id": config.run_id,
        "lake_uri": config.lake_uri,
        "start_block": config.start_block,
        "end_block": config.end_block,
        "chunk_size": config.chunk_size,
        "dry_run": config.dry_run,
        "jobs": [],
    }

    if config.dry_run:
        summary["jobs"] = plan
        return summary

    client = batch_client or boto3.client("batch", region_name=config.region)
    fetch_jobs = [job for job in plan if job["stage"] == "fetch_orderfilled"]
    downstream_jobs = [job for job in plan if job["stage"] != "fetch_orderfilled"]

    submitted_fetch = _submit_jobs(client, config, fetch_jobs)
    summary["jobs"].extend(submitted_fetch)
    if not config.wait:
        summary["pending_jobs"] = downstream_jobs
        return summary

    if config.wait and submitted_fetch:
        _wait_and_raise(client, submitted_fetch, config)

    for job in downstream_jobs:
        submitted = _submit_jobs(client, config, [job])
        summary["jobs"].extend(submitted)
        if config.wait:
            _wait_and_raise(client, submitted, config)

    return summary


def wait_for_jobs(
    batch_client: Any,
    submitted_jobs: list[dict[str, Any]],
    poll_seconds: float = 30.0,
    max_wait_seconds: float | None = None,
) -> list[dict[str, Any]]:
    remaining = {job["job_id"]: job for job in submitted_jobs}
    completed: dict[str, dict[str, Any]] = {}
    started_at = time.monotonic()

    while remaining:
        for batch in _chunks(list(remaining), 100):
            response = batch_client.describe_jobs(jobs=batch)
            for payload in response.get("jobs", []):
                job_id = payload["jobId"]
                status = payload.get("status", "UNKNOWN")
                if status in TERMINAL_STATUSES:
                    original = remaining.pop(job_id)
                    completed[job_id] = {
                        **original,
                        "status": status,
                        "status_reason": payload.get("statusReason"),
                        "attempts": payload.get("attempts", []),
                    }

        if remaining:
            if max_wait_seconds is not None and time.monotonic() - started_at > max_wait_seconds:
                pending = ", ".join(sorted(remaining))
                raise TimeoutError(f"Timed out waiting for Batch jobs: {pending}")
            time.sleep(poll_seconds)

    return [completed[job["job_id"]] for job in submitted_jobs]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Submit a bounded Phase 1 Polymarket AWS Batch backfill")
    parser.add_argument("--region", default=os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1")
    parser.add_argument("--job-queue", default=os.getenv("PREDICTION_MARKET_BATCH_JOB_QUEUE"), required=not bool(os.getenv("PREDICTION_MARKET_BATCH_JOB_QUEUE")))
    parser.add_argument(
        "--job-definition",
        default=os.getenv("PREDICTION_MARKET_BATCH_JOB_DEFINITION"),
        required=not bool(os.getenv("PREDICTION_MARKET_BATCH_JOB_DEFINITION")),
    )
    parser.add_argument("--lake-uri", required=True)
    parser.add_argument("--run-id", default=datetime.now(UTC).strftime("backfill-%Y%m%dT%H%M%SZ"))
    parser.add_argument("--start-block", type=int, required=True)
    parser.add_argument("--end-block", type=int, required=True)
    parser.add_argument("--chunk-size", type=int, default=10_000)
    parser.add_argument("--rpc-batch-size", type=int, default=1_000)
    parser.add_argument("--max-events", type=int)
    parser.add_argument("--date-start")
    parser.add_argument("--date-end")
    parser.add_argument("--poll-seconds", type=float, default=30.0)
    parser.add_argument("--max-wait-seconds", type=float)
    parser.add_argument("--fetch-only", action="store_true")
    parser.add_argument("--no-wait", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = BackfillConfig(
        region=args.region,
        job_queue=args.job_queue,
        job_definition=args.job_definition,
        lake_uri=args.lake_uri,
        run_id=args.run_id,
        start_block=args.start_block,
        end_block=args.end_block,
        chunk_size=args.chunk_size,
        rpc_batch_size=args.rpc_batch_size,
        max_events=args.max_events,
        date_start=args.date_start,
        date_end=args.date_end,
        wait=not args.no_wait,
        poll_seconds=args.poll_seconds,
        max_wait_seconds=args.max_wait_seconds,
        fetch_only=args.fetch_only,
        dry_run=args.dry_run,
    )
    print(json.dumps(submit_backfill(config), indent=2, sort_keys=True, default=str))
    return 0


def _submit_jobs(batch_client: Any, config: BackfillConfig, jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    submitted = []
    for job in jobs:
        response = batch_client.submit_job(
            jobName=job["job_name"],
            jobQueue=config.job_queue,
            jobDefinition=config.job_definition,
            containerOverrides={"command": job["command"]},
        )
        submitted.append(
            {
                **job,
                "job_id": response["jobId"],
                "job_arn": response.get("jobArn"),
                "status": "SUBMITTED",
            }
        )
    return submitted


def _wait_and_raise(batch_client: Any, submitted_jobs: list[dict[str, Any]], config: BackfillConfig) -> None:
    completed = wait_for_jobs(
        batch_client,
        submitted_jobs,
        poll_seconds=config.poll_seconds,
        max_wait_seconds=config.max_wait_seconds,
    )
    failed = [job for job in completed if job["status"] != "SUCCEEDED"]
    if failed:
        details = ", ".join(f"{job['job_name']}={job['status']}" for job in failed)
        raise RuntimeError(f"Batch backfill failed: {details}")
    for submitted, completed_job in zip(submitted_jobs, completed, strict=True):
        submitted.update(completed_job)


def _append_date_args(command: list[str], date_start: str | None, date_end: str | None) -> None:
    if date_start:
        command.extend(["--date-start", date_start])
    if date_end:
        command.extend(["--date-end", date_end])


def _chunks(items: list[str], size: int) -> list[list[str]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


if __name__ == "__main__":
    sys.exit(main())
