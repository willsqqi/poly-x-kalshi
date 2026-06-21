from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from .normalize import normalize_snapshot

RAW_FILENAMES = {
    "polymarket_markets": "polymarket_markets.json",
    "kalshi_markets": "kalshi_markets.json",
    "polymarket_orderbooks": "polymarket_orderbooks.json",
    "kalshi_orderbooks": "kalshi_orderbooks.json",
    "polymarket_trades": "polymarket_trades.json",
    "kalshi_trades": "kalshi_trades.json",
}


def ensure_data_dirs(output_dir: str | Path) -> dict[str, Path]:
    root = Path(output_dir)
    dirs = {
        "root": root,
        "raw": root / "raw",
        "processed": root / "processed",
        "logs": root.parent / "logs" if root.name == "data" else root / "logs",
    }
    for directory in dirs.values():
        directory.mkdir(parents=True, exist_ok=True)
    return dirs


def write_raw_payloads(raw_data: dict[str, Any], output_dir: str | Path) -> dict[str, Path]:
    raw_dir = ensure_data_dirs(output_dir)["raw"]
    written: dict[str, Path] = {}

    metadata = {key: value for key, value in raw_data.items() if key not in RAW_FILENAMES}
    metadata_path = raw_dir / "snapshot_metadata.json"
    metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True, default=str), encoding="utf-8")
    written["snapshot_metadata"] = metadata_path

    for key, filename in RAW_FILENAMES.items():
        path = raw_dir / filename
        path.write_text(json.dumps(raw_data.get(key, []), indent=2, sort_keys=True, default=str), encoding="utf-8")
        written[key] = path

    return written


def write_processed_tables(tables: dict[str, pd.DataFrame], output_dir: str | Path) -> dict[str, dict[str, Path]]:
    processed_dir = ensure_data_dirs(output_dir)["processed"]
    written: dict[str, dict[str, Path]] = {}

    for name, frame in tables.items():
        parquet_path = processed_dir / f"{name}.parquet"
        csv_path = processed_dir / f"{name}.csv"
        frame.to_parquet(parquet_path, index=False)
        frame.to_csv(csv_path, index=False)
        written[name] = {"parquet": parquet_path, "csv": csv_path}

    return written


def save_snapshot(raw_data: dict[str, Any], output_dir: str | Path = "data") -> dict[str, Any]:
    raw_paths = write_raw_payloads(raw_data, output_dir)
    tables = normalize_snapshot(raw_data)
    processed_paths = write_processed_tables(tables, output_dir)
    return {"raw_paths": raw_paths, "processed_paths": processed_paths, "tables": tables}
