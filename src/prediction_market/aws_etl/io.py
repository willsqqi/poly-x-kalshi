from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import boto3
import pandas as pd
import pyarrow.parquet as pq
from pyarrow.lib import ArrowInvalid

from .paths import clean_run_id, join_uri


def is_s3_uri(uri: str) -> bool:
    return str(uri).startswith("s3://")


def parse_s3_uri(uri: str) -> tuple[str, str]:
    parsed = urlparse(uri)
    if parsed.scheme != "s3" or not parsed.netloc:
        raise ValueError(f"Not an S3 URI: {uri}")
    return parsed.netloc, parsed.path.lstrip("/")


def _s3_client():
    return boto3.client("s3")


def write_json(payload: Any, uri: str) -> str:
    if is_s3_uri(uri):
        bucket, key = parse_s3_uri(uri)
        _s3_client().put_object(
            Bucket=bucket,
            Key=key,
            Body=json.dumps(payload, indent=2, sort_keys=True, default=str).encode("utf-8"),
            ContentType="application/json",
        )
        return uri

    path = Path(uri)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    return uri


def read_json(uri: str) -> Any:
    if is_s3_uri(uri):
        bucket, key = parse_s3_uri(uri)
        response = _s3_client().get_object(Bucket=bucket, Key=key)
        return json.loads(response["Body"].read().decode("utf-8"))
    return json.loads(Path(uri).read_text(encoding="utf-8"))


def write_parquet(frame: pd.DataFrame, uri: str) -> str:
    if is_s3_uri(uri):
        bucket, key = parse_s3_uri(uri)
        with tempfile.NamedTemporaryFile(suffix=".parquet") as tmp:
            frame.to_parquet(tmp.name, index=False)
            _s3_client().upload_file(tmp.name, bucket, key)
        return uri

    path = Path(uri)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)
    return uri


def write_partitioned_parquet(
    frame: pd.DataFrame,
    base_uri: str,
    partition_column: str,
    run_id: str,
) -> list[str]:
    if partition_column not in frame.columns:
        raise ValueError(f"Missing partition column: {partition_column}")

    written: list[str] = []
    if frame.empty:
        uri = join_uri(base_uri, f"{partition_column}=__empty__", f"part-{clean_run_id(run_id)}.parquet")
        write_parquet(frame, uri)
        return [uri]

    for value, group in frame.groupby(partition_column, dropna=False):
        partition_value = "__null__" if pd.isna(value) else str(value)
        uri = join_uri(
            base_uri,
            f"{partition_column}={partition_value}",
            f"part-{clean_run_id(run_id)}.parquet",
        )
        write_parquet(group.reset_index(drop=True), uri)
        written.append(uri)
    return written


def read_parquet_dataset(uri: str, columns: list[str] | None = None) -> pd.DataFrame:
    paths = list_parquet_objects(uri)
    if not paths:
        return pd.DataFrame()
    frames = [read_parquet(path, columns=columns) for path in paths]
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def read_parquet(uri: str, columns: list[str] | None = None) -> pd.DataFrame:
    if is_s3_uri(uri):
        bucket, key = parse_s3_uri(uri)
        with tempfile.NamedTemporaryFile(suffix=".parquet") as tmp:
            _s3_client().download_file(bucket, key, tmp.name)
            return _read_local_parquet(tmp.name, columns=columns)
    return _read_local_parquet(uri, columns=columns)


def _read_local_parquet(path: str | Path, columns: list[str] | None = None) -> pd.DataFrame:
    try:
        return pd.read_parquet(path, columns=columns)
    except ArrowInvalid:
        if not columns:
            raise
        schema = pq.read_schema(path)
        if not schema.names:
            return pd.DataFrame(columns=columns)
        raise


def list_parquet_objects(uri: str) -> list[str]:
    if is_s3_uri(uri):
        bucket, prefix = parse_s3_uri(uri)
        client = _s3_client()
        paginator = client.get_paginator("list_objects_v2")
        paths: list[str] = []
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix.rstrip("/") + "/"):
            for item in page.get("Contents", []):
                key = item["Key"]
                if key.endswith(".parquet"):
                    paths.append(f"s3://{bucket}/{key}")
        return sorted(paths)

    path = Path(uri)
    if path.is_file() and path.suffix == ".parquet":
        return [str(path)]
    if not path.exists():
        return []
    return sorted(str(candidate) for candidate in path.rglob("*.parquet"))


def filter_by_date(frame: pd.DataFrame, date_start: str | None, date_end: str | None) -> pd.DataFrame:
    if frame.empty or "date" not in frame.columns:
        return frame
    result = frame
    if date_start:
        result = result[result["date"].astype(str) >= date_start]
    if date_end:
        result = result[result["date"].astype(str) <= date_end]
    return result.reset_index(drop=True)
