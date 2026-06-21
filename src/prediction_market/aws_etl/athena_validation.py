from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import boto3

from .io import is_s3_uri, parse_s3_uri
from .paths import join_uri
from .schemas import (
    DIM_MARKET_COLUMNS,
    DIM_OUTCOME_COLUMNS,
    FACT_MARKET_DAILY_COLUMNS,
    FACT_TRADES_COLUMNS,
)

ATHENA_TYPES = {
    "avg_price": "double",
    "block_number": "bigint",
    "category": "string",
    "close_price": "double",
    "close_time": "string",
    "condition_id": "string",
    "daily_trade_count": "bigint",
    "daily_volume": "double",
    "date": "string",
    "fee_amount": "double",
    "hour": "int",
    "ingested_at": "string",
    "liquidity": "double",
    "maker": "string",
    "maker_direction": "string",
    "market_id": "string",
    "max_price": "double",
    "min_price": "double",
    "open_time": "string",
    "order_hash": "string",
    "outcome_id": "string",
    "outcome_index": "int",
    "outcome_name": "string",
    "price": "double",
    "question": "string",
    "raw_payload_path": "string",
    "resolution_time": "string",
    "side": "string",
    "slug": "string",
    "source_type": "string",
    "status": "string",
    "taker": "string",
    "taker_direction": "string",
    "timestamp": "bigint",
    "token_amount": "double",
    "token_id": "string",
    "trade_id": "string",
    "transaction_hash": "string",
    "unique_makers": "bigint",
    "unique_takers": "bigint",
    "usd_amount": "double",
    "volume": "double",
}

VALIDATION_TABLES = {
    "dim_market": DIM_MARKET_COLUMNS,
    "dim_outcome": DIM_OUTCOME_COLUMNS,
    "fact_trades": FACT_TRADES_COLUMNS,
    "fact_market_daily": FACT_MARKET_DAILY_COLUMNS,
}

PARTITIONED_TABLES = {"fact_trades", "fact_market_daily"}
DATE_PARTITION_PATTERN = re.compile(r"date=(\d{4}-\d{2}-\d{2})(?:/|$)")


@dataclass(frozen=True)
class AthenaQueryResult:
    query_execution_id: str
    state: str
    rows: list[dict[str, str]]


def sanitize_table_prefix(value: str) -> str:
    prefix = re.sub(r"[^a-zA-Z0-9_]+", "_", value.strip().lower()).strip("_")
    if not prefix:
        prefix = "validation"
    if not re.match(r"^[a-zA-Z_]", prefix):
        prefix = f"v_{prefix}"
    return prefix


def validation_table_names(table_prefix: str) -> dict[str, str]:
    prefix = sanitize_table_prefix(table_prefix)
    return {name: f"{prefix}_{name}" for name in VALIDATION_TABLES}


def build_validation_table_ddls(
    lake_uri: str,
    database: str,
    table_prefix: str,
    projection_date_range: str = "2020-01-01,NOW",
) -> dict[str, str]:
    base_uri = lake_uri.rstrip("/")
    names = validation_table_names(table_prefix)
    return {
        logical_name: build_external_table_ddl(
            database=database,
            table_name=table_name,
            columns=columns,
            location=join_uri(base_uri, "gold", "polymarket", logical_name),
            partitioned=logical_name in PARTITIONED_TABLES,
            projection_date_range=projection_date_range,
        )
        for logical_name, table_name in names.items()
        for columns in [VALIDATION_TABLES[logical_name]]
    }


def build_external_table_ddl(
    database: str,
    table_name: str,
    columns: list[str],
    location: str,
    partitioned: bool,
    projection_date_range: str = "2020-01-01,NOW",
) -> str:
    data_columns = [column for column in columns if not (partitioned and column == "date")]
    column_sql = ",\n  ".join(f"{quote_identifier(column)} {ATHENA_TYPES[column]}" for column in data_columns)
    partition_sql = "\nPARTITIONED BY (`date` string)" if partitioned else ""
    table_properties = {
        "classification": "parquet",
    }
    if partitioned:
        table_properties.update(
            {
                "projection.enabled": "true",
                "projection.date.type": "date",
                "projection.date.range": projection_date_range,
                "projection.date.format": "yyyy-MM-dd",
                "storage.location.template": join_uri(location, "date=${date}") + "/",
            }
        )

    properties_sql = ",\n  ".join(f"'{key}'='{value}'" for key, value in table_properties.items())
    return f"""CREATE EXTERNAL TABLE IF NOT EXISTS {quote_identifier(database)}.{quote_identifier(table_name)} (
  {column_sql}
){partition_sql}
STORED AS PARQUET
LOCATION '{location.rstrip("/")}/'
TBLPROPERTIES (
  {properties_sql}
)"""


def quote_identifier(value: str) -> str:
    return f"`{value.replace('`', '``')}`"


def athena_table_reference(database: str, table_name: str) -> str:
    for value, label in ((database, "database"), (table_name, "table")):
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", value):
            raise ValueError(f"Unsafe {label} identifier for Athena SELECT: {value}")
    return f"{database}.{table_name}"


def create_validation_tables(
    lake_uri: str,
    database: str,
    table_prefix: str,
    work_group: str | None = None,
    output_location: str | None = None,
    region: str | None = None,
    replace: bool = False,
    run_checks: bool = False,
    date_start: str | None = None,
    date_end: str | None = None,
    dry_run: bool = False,
    poll_seconds: float = 2.0,
    timeout_seconds: int = 300,
    athena_client: Any | None = None,
) -> dict[str, Any]:
    names = validation_table_names(table_prefix)
    ddls = build_validation_table_ddls(lake_uri=lake_uri, database=database, table_prefix=table_prefix)
    discovered_dates = discover_date_partitions(join_uri(lake_uri, "gold", "polymarket", "fact_trades"))
    resolved_date_start = date_start or (min(discovered_dates) if discovered_dates else None)
    resolved_date_end = date_end or (max(discovered_dates) if discovered_dates else None)

    if dry_run:
        check_queries = build_validation_check_queries(database, names, resolved_date_start, resolved_date_end)
        return {
            "database": database,
            "lake_uri": lake_uri.rstrip("/"),
            "table_names": names,
            "date_start": resolved_date_start,
            "date_end": resolved_date_end,
            "ddl": ddls,
            "checks": check_queries if run_checks else {},
            "dry_run": True,
        }

    client = athena_client or boto3.client("athena", region_name=region)
    executed: list[dict[str, str]] = []
    if replace:
        for table_name in names.values():
            drop_sql = f"DROP TABLE IF EXISTS {quote_identifier(database)}.{quote_identifier(table_name)}"
            result = execute_athena_query(
                client,
                drop_sql,
                database=database,
                work_group=work_group,
                output_location=output_location,
                poll_seconds=poll_seconds,
                timeout_seconds=timeout_seconds,
            )
            executed.append({"query_execution_id": result.query_execution_id, "statement": "drop", "table": table_name})

    for logical_name, ddl in ddls.items():
        result = execute_athena_query(
            client,
            ddl,
            database=database,
            work_group=work_group,
            output_location=output_location,
            poll_seconds=poll_seconds,
            timeout_seconds=timeout_seconds,
        )
        executed.append(
            {"query_execution_id": result.query_execution_id, "statement": "create", "table": names[logical_name]}
        )

    check_results = {}
    if run_checks:
        for check_name, sql in build_validation_check_queries(
            database,
            names,
            resolved_date_start,
            resolved_date_end,
        ).items():
            try:
                result = execute_athena_query(
                    client,
                    sql,
                    database=database,
                    work_group=work_group,
                    output_location=output_location,
                    poll_seconds=poll_seconds,
                    timeout_seconds=timeout_seconds,
                    fetch_results=True,
                )
            except Exception as exc:
                raise RuntimeError(f"Athena validation check failed: {check_name}") from exc
            check_results[check_name] = result.rows

    return {
        "database": database,
        "lake_uri": lake_uri.rstrip("/"),
        "table_names": names,
        "date_start": resolved_date_start,
        "date_end": resolved_date_end,
        "executed": executed,
        "checks": check_results,
    }


def build_validation_check_queries(
    database: str,
    table_names: dict[str, str],
    date_start: str | None = None,
    date_end: str | None = None,
) -> dict[str, str]:
    fact_trades = athena_table_reference(database, table_names["fact_trades"])
    fact_market_daily = athena_table_reference(database, table_names["fact_market_daily"])
    dim_outcome = athena_table_reference(database, table_names["dim_outcome"])
    trades_where = date_where_clause(date_start, date_end, alias=None)
    daily_where = date_where_clause(date_start, date_end, alias=None)
    join_where = date_where_clause(date_start, date_end, alias="t")

    return {
        "dim_counts": f"""
SELECT
  (SELECT COUNT(*) FROM {athena_table_reference(database, table_names["dim_market"])}) AS dim_market_count,
  (SELECT COUNT(*) FROM {dim_outcome}) AS dim_outcome_count
""".strip(),
        "fact_trade_counts": f"""
SELECT
  COUNT(*) AS fact_trade_count,
  COUNT(DISTINCT trade_id) AS distinct_trade_ids,
  SUM(usd_amount) AS usd_volume
FROM {fact_trades}
{trades_where}
""".strip(),
        "duplicate_trade_ids": f"""
SELECT COUNT(*) AS duplicate_trade_id_count
FROM (
  SELECT trade_id
  FROM {fact_trades}
  {trades_where}
  GROUP BY trade_id
  HAVING COUNT(*) > 1
)
""".strip(),
        "null_market_ids": f"""
SELECT COUNT(*) AS null_market_id_rows
FROM {fact_trades}
{trades_where}
{where_joiner(trades_where)} (market_id IS NULL OR market_id = '')
""".strip(),
        "mapping_coverage": f"""
SELECT
  COUNT(*) AS fact_trade_count,
  SUM(CASE WHEN t.market_id IS NOT NULL AND t.market_id <> '' THEN 1 ELSE 0 END) AS rows_with_market_id,
  SUM(CASE WHEN o.outcome_id IS NOT NULL THEN 1 ELSE 0 END) AS rows_with_outcome_match,
  100.0 * SUM(CASE WHEN t.market_id IS NOT NULL AND t.market_id <> '' THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0) AS market_id_coverage_pct,
  100.0 * SUM(CASE WHEN o.outcome_id IS NOT NULL THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0) AS outcome_match_coverage_pct
FROM {fact_trades} t
LEFT JOIN {dim_outcome} o
  ON t.outcome_id = o.outcome_id
{join_where}
""".strip(),
        "daily_totals": f"""
SELECT
  SUM(daily_trade_count) AS daily_trade_count_sum,
  SUM(daily_volume) AS daily_volume_sum
FROM {fact_market_daily}
{daily_where}
""".strip(),
        "unmatched_outcomes": f"""
SELECT COUNT(*) AS unmatched_outcomes
FROM {fact_trades} t
LEFT JOIN {dim_outcome} o
  ON t.outcome_id = o.outcome_id
{join_where}
{where_joiner(join_where)} o.outcome_id IS NULL
""".strip(),
    }


def date_where_clause(date_start: str | None, date_end: str | None, alias: str | None) -> str:
    column = f"{alias}.date" if alias else "date"
    if date_start and date_end:
        return f"WHERE {column} BETWEEN '{date_start}' AND '{date_end}'"
    if date_start:
        return f"WHERE {column} >= '{date_start}'"
    if date_end:
        return f"WHERE {column} <= '{date_end}'"
    return ""


def where_joiner(existing_where: str) -> str:
    return "AND" if existing_where else "WHERE"


def execute_athena_query(
    client: Any,
    query: str,
    database: str,
    work_group: str | None = None,
    output_location: str | None = None,
    poll_seconds: float = 2.0,
    timeout_seconds: int = 300,
    fetch_results: bool = False,
) -> AthenaQueryResult:
    request: dict[str, Any] = {
        "QueryString": query,
        "QueryExecutionContext": {"Database": database},
    }
    if work_group:
        request["WorkGroup"] = work_group
    if output_location:
        request["ResultConfiguration"] = {"OutputLocation": output_location}

    query_execution_id = client.start_query_execution(**request)["QueryExecutionId"]
    state = wait_for_athena_query(
        client,
        query_execution_id=query_execution_id,
        poll_seconds=poll_seconds,
        timeout_seconds=timeout_seconds,
    )
    rows = fetch_athena_rows(client, query_execution_id) if fetch_results else []
    return AthenaQueryResult(query_execution_id=query_execution_id, state=state, rows=rows)


def wait_for_athena_query(
    client: Any,
    query_execution_id: str,
    poll_seconds: float = 2.0,
    timeout_seconds: int = 300,
) -> str:
    deadline = time.monotonic() + timeout_seconds
    while True:
        execution = client.get_query_execution(QueryExecutionId=query_execution_id)["QueryExecution"]
        status = execution["Status"]
        state = status["State"]
        if state == "SUCCEEDED":
            return state
        if state in {"FAILED", "CANCELLED"}:
            reason = status.get("StateChangeReason", "No reason returned")
            raise RuntimeError(f"Athena query {query_execution_id} {state}: {reason}")
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Athena query {query_execution_id} did not finish in {timeout_seconds}s")
        time.sleep(poll_seconds)


def fetch_athena_rows(client: Any, query_execution_id: str) -> list[dict[str, str]]:
    paginator = client.get_paginator("get_query_results")
    rows: list[dict[str, str]] = []
    headers: list[str] | None = None

    for page in paginator.paginate(QueryExecutionId=query_execution_id):
        for raw_row in page["ResultSet"].get("Rows", []):
            values = [cell.get("VarCharValue", "") for cell in raw_row.get("Data", [])]
            if headers is None:
                headers = values
                continue
            rows.append(dict(zip(headers, values, strict=False)))
    return rows


def discover_date_partitions(fact_table_uri: str) -> list[str]:
    if is_s3_uri(fact_table_uri):
        bucket, prefix = parse_s3_uri(fact_table_uri)
        client = boto3.client("s3")
        paginator = client.get_paginator("list_objects_v2")
        dates: set[str] = set()
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix.rstrip("/") + "/"):
            for item in page.get("Contents", []):
                match = DATE_PARTITION_PATTERN.search(item["Key"])
                if match:
                    dates.add(match.group(1))
        return sorted(dates)

    path = Path(fact_table_uri)
    if not path.exists():
        return []
    dates = set()
    for candidate in path.rglob("date=*"):
        match = DATE_PARTITION_PATTERN.search(str(candidate))
        if match:
            dates.add(match.group(1))
    return sorted(dates)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create Athena tables for an isolated validation lake prefix.")
    parser.add_argument("--lake-uri", required=True, help="Validation lake prefix, e.g. s3://bucket/validation/run-001")
    parser.add_argument("--database", required=True, help="Glue/Athena database name")
    parser.add_argument("--table-prefix", required=True, help="Prefix for generated table names")
    parser.add_argument("--work-group", help="Athena workgroup")
    parser.add_argument("--output-location", help="Optional Athena results S3 location")
    parser.add_argument("--region", default=None)
    parser.add_argument("--replace", action="store_true", help="Drop existing generated tables before creating them")
    parser.add_argument("--checks", action="store_true", help="Run validation queries after creating tables")
    parser.add_argument("--date-start", help="Optional lower date bound for validation checks")
    parser.add_argument("--date-end", help="Optional upper date bound for validation checks")
    parser.add_argument("--dry-run", action="store_true", help="Print DDL/check SQL without executing Athena queries")
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--timeout-seconds", type=int, default=300)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = create_validation_tables(
        lake_uri=args.lake_uri,
        database=args.database,
        table_prefix=args.table_prefix,
        work_group=args.work_group,
        output_location=args.output_location,
        region=args.region,
        replace=args.replace,
        run_checks=args.checks,
        date_start=args.date_start,
        date_end=args.date_end,
        dry_run=args.dry_run,
        poll_seconds=args.poll_seconds,
        timeout_seconds=args.timeout_seconds,
    )
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
