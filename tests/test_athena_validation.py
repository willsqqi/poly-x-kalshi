from __future__ import annotations

from prediction_market.aws_etl.athena_validation import (
    build_validation_check_queries,
    build_validation_table_ddls,
    create_validation_tables,
    sanitize_table_prefix,
    validation_table_names,
)


def test_sanitize_table_prefix_keeps_athena_safe_names() -> None:
    assert sanitize_table_prefix("one-hour-test-20260610T144456Z") == "one_hour_test_20260610t144456z"
    assert sanitize_table_prefix("123 run") == "v_123_run"


def test_validation_table_ddls_target_validation_prefix_and_project_dates() -> None:
    ddls = build_validation_table_ddls(
        lake_uri="s3://bucket/validation/run-001/",
        database="prediction_market_dev",
        table_prefix="run-001",
    )

    assert "LOCATION 's3://bucket/validation/run-001/gold/polymarket/dim_market/'" in ddls["dim_market"]
    assert "`date` string" not in ddls["fact_trades"].split("PARTITIONED BY")[0]
    assert "PARTITIONED BY (`date` string)" in ddls["fact_trades"]
    assert "'storage.location.template'='s3://bucket/validation/run-001/gold/polymarket/fact_trades/date=${date}/'" in ddls[
        "fact_trades"
    ]


def test_validation_check_queries_include_date_filters() -> None:
    names = validation_table_names("unit")
    queries = build_validation_check_queries(
        database="prediction_market_dev",
        table_names=names,
        date_start="2026-06-06",
        date_end="2026-06-06",
    )

    assert "WHERE date BETWEEN '2026-06-06' AND '2026-06-06'" in queries["fact_trade_counts"]
    assert "WHERE t.date BETWEEN '2026-06-06' AND '2026-06-06'" in queries["unmatched_outcomes"]
    assert "COUNT(DISTINCT trade_id)" in queries["fact_trade_counts"]
    assert "market_id_coverage_pct" in queries["mapping_coverage"]


def test_create_validation_tables_replaces_tables_and_runs_checks(monkeypatch) -> None:
    monkeypatch.setattr("prediction_market.aws_etl.athena_validation.discover_date_partitions", lambda uri: ["2026-06-06"])
    client = FakeAthenaClient()

    result = create_validation_tables(
        lake_uri="s3://bucket/validation/run-001",
        database="prediction_market_dev",
        table_prefix="run-001",
        work_group="wg",
        region="us-east-1",
        replace=True,
        run_checks=True,
        poll_seconds=0,
        athena_client=client,
    )

    query_strings = [call["QueryString"] for call in client.started_queries]
    assert len(query_strings) == 15
    assert query_strings[0].startswith("DROP TABLE IF EXISTS")
    assert any("CREATE EXTERNAL TABLE IF NOT EXISTS `prediction_market_dev`.`run_001_fact_trades`" in query for query in query_strings)
    assert result["date_start"] == "2026-06-06"
    assert result["date_end"] == "2026-06-06"
    assert result["checks"]["fact_trade_counts"][0]["fact_trade_count"] == "1"


class FakeAthenaClient:
    def __init__(self) -> None:
        self.started_queries = []

    def start_query_execution(self, **kwargs):
        self.started_queries.append(kwargs)
        return {"QueryExecutionId": f"query-{len(self.started_queries)}"}

    def get_query_execution(self, QueryExecutionId):
        return {"QueryExecution": {"Status": {"State": "SUCCEEDED"}}}

    def get_paginator(self, name):
        assert name == "get_query_results"
        return FakeResultsPaginator()


class FakeResultsPaginator:
    def paginate(self, QueryExecutionId):
        return [
            {
                "ResultSet": {
                    "Rows": [
                        {"Data": [{"VarCharValue": "fact_trade_count"}]},
                        {"Data": [{"VarCharValue": "1"}]},
                    ]
                }
            }
        ]
