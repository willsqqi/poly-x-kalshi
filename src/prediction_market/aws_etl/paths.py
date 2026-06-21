from __future__ import annotations

import re
from dataclasses import dataclass


def clean_run_id(run_id: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.=-]+", "-", run_id.strip())
    return cleaned.strip("-") or "manual"


def join_uri(base_uri: str, *parts: object) -> str:
    base = str(base_uri).rstrip("/")
    suffix = "/".join(str(part).strip("/") for part in parts if str(part).strip("/"))
    return f"{base}/{suffix}" if suffix else base


@dataclass(frozen=True)
class LakePaths:
    base_uri: str

    @property
    def bronze_polymarket(self) -> str:
        return join_uri(self.base_uri, "bronze", "polymarket")

    @property
    def silver_polymarket(self) -> str:
        return join_uri(self.base_uri, "silver", "polymarket")

    @property
    def gold_polymarket(self) -> str:
        return join_uri(self.base_uri, "gold", "polymarket")

    def markets_raw_json(self, run_id: str) -> str:
        return join_uri(self.bronze_polymarket, "markets_raw", f"run_id={clean_run_id(run_id)}", "markets.json")

    def orderfilled_raw_json(self, run_id: str, start_block: int, end_block: int) -> str:
        return join_uri(
            self.bronze_polymarket,
            "orderfilled_raw",
            f"block_range={start_block}-{end_block}",
            f"run_id={clean_run_id(run_id)}",
            "orderfilled_logs.json",
        )

    def orderfilled_raw_parquet(self, run_id: str, start_block: int, end_block: int) -> str:
        return join_uri(
            self.bronze_polymarket,
            "orderfilled_raw",
            f"block_range={start_block}-{end_block}",
            f"run_id={clean_run_id(run_id)}",
            "orderfilled.parquet",
        )

    def checkpoint_json(self) -> str:
        return join_uri(self.bronze_polymarket, "checkpoints", "orderfilled_checkpoint.json")

    def silver_markets(self, run_id: str) -> str:
        return join_uri(self.silver_polymarket, "markets_normalized", f"part-{clean_run_id(run_id)}.parquet")

    def silver_trades_base(self) -> str:
        return join_uri(self.silver_polymarket, "trades_normalized")

    def dim_market(self, run_id: str) -> str:
        return join_uri(self.gold_polymarket, "dim_market", f"part-{clean_run_id(run_id)}.parquet")

    def dim_outcome(self, run_id: str) -> str:
        return join_uri(self.gold_polymarket, "dim_outcome", f"part-{clean_run_id(run_id)}.parquet")

    def fact_trades_base(self) -> str:
        return join_uri(self.gold_polymarket, "fact_trades")

    def fact_market_daily_base(self) -> str:
        return join_uri(self.gold_polymarket, "fact_market_daily")

