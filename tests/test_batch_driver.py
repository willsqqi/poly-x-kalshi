from __future__ import annotations

from prediction_market.aws_etl.batch_driver import BackfillConfig, build_backfill_plan, chunk_block_ranges, submit_backfill


def test_chunk_block_ranges_are_inclusive() -> None:
    assert chunk_block_ranges(10, 19, 4) == [(10, 13), (14, 17), (18, 19)]


def test_backfill_plan_builds_scoped_downstream_commands() -> None:
    config = _config(start_block=10, end_block=14, chunk_size=3)

    plan = build_backfill_plan(config)

    assert [job["stage"] for job in plan] == [
        "fetch_orderfilled",
        "fetch_orderfilled",
        "fetch_markets",
        "normalize_trades",
        "build_market_daily",
    ]
    assert plan[0]["command"] == [
        "--job",
        "fetch_orderfilled",
        "--lake-uri",
        "s3://bucket/run",
        "--run-id",
        "fills-10-12",
        "--start-block",
        "10",
        "--end-block",
        "12",
        "--batch-size",
        "1000",
    ]
    normalize_command = plan[3]["command"]
    assert "--orderfilled-prefix" in normalize_command
    assert "s3://bucket/run/bronze/polymarket/orderfilled_raw" in normalize_command
    assert "--date-start" in normalize_command
    assert "2026-06-06" in normalize_command
    assert "--orderfilled-file-batch-size" in normalize_command
    assert normalize_command[normalize_command.index("--orderfilled-file-batch-size") + 1] == "1"
    daily_command = plan[4]["command"]
    assert "--fact-trades-prefix" in daily_command
    assert "s3://bucket/run/gold/polymarket/fact_trades" in daily_command
    assert "--fact-file-batch-size" in daily_command
    assert daily_command[daily_command.index("--fact-file-batch-size") + 1] == "1"


def test_submit_backfill_waits_and_submits_downstream_after_fetch_jobs() -> None:
    client = FakeBatchClient()
    summary = submit_backfill(_config(start_block=10, end_block=14, chunk_size=3, poll_seconds=0), batch_client=client)

    assert [job["stage"] for job in summary["jobs"]] == [
        "fetch_orderfilled",
        "fetch_orderfilled",
        "fetch_markets",
        "normalize_trades",
        "build_market_daily",
    ]
    assert all(job["status"] == "SUCCEEDED" for job in summary["jobs"])
    assert [call["jobName"] for call in client.submissions] == [job["job_name"] for job in summary["jobs"]]


def test_submit_backfill_no_wait_only_submits_fetch_jobs() -> None:
    client = FakeBatchClient()
    summary = submit_backfill(_config(start_block=10, end_block=14, chunk_size=3, wait=False), batch_client=client)

    assert [job["stage"] for job in summary["jobs"]] == ["fetch_orderfilled", "fetch_orderfilled"]
    assert [job["stage"] for job in summary["pending_jobs"]] == ["fetch_markets", "normalize_trades", "build_market_daily"]
    assert len(client.submissions) == 2


def _config(**overrides) -> BackfillConfig:
    values = {
        "region": "us-east-1",
        "job_queue": "queue",
        "job_definition": "definition",
        "lake_uri": "s3://bucket/run",
        "run_id": "unit",
        "start_block": 1,
        "end_block": 1,
        "chunk_size": 1,
        "date_start": "2026-06-06",
        "date_end": "2026-06-06",
    }
    values.update(overrides)
    return BackfillConfig(**values)


class FakeBatchClient:
    def __init__(self) -> None:
        self.submissions = []

    def submit_job(self, **kwargs):
        self.submissions.append(kwargs)
        index = len(self.submissions)
        return {"jobId": f"job-{index}", "jobArn": f"arn:job-{index}"}

    def describe_jobs(self, jobs):
        return {"jobs": [{"jobId": job_id, "status": "SUCCEEDED", "attempts": []} for job_id in jobs]}
