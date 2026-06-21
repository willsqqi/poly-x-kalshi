DIM_MARKET_COLUMNS = [
    "market_id",
    "question",
    "slug",
    "category",
    "status",
    "open_time",
    "close_time",
    "resolution_time",
    "condition_id",
    "volume",
    "liquidity",
    "raw_payload_path",
    "ingested_at",
]

DIM_OUTCOME_COLUMNS = [
    "outcome_id",
    "market_id",
    "outcome_name",
    "outcome_index",
    "token_id",
    "side",
    "ingested_at",
]

FACT_TRADES_COLUMNS = [
    "trade_id",
    "market_id",
    "outcome_id",
    "timestamp",
    "date",
    "hour",
    "maker",
    "taker",
    "price",
    "token_amount",
    "usd_amount",
    "maker_direction",
    "taker_direction",
    "transaction_hash",
    "block_number",
    "order_hash",
    "fee_amount",
    "source_type",
    "ingested_at",
    "raw_payload_path",
]

FACT_MARKET_DAILY_COLUMNS = [
    "market_id",
    "date",
    "daily_volume",
    "daily_trade_count",
    "unique_makers",
    "unique_takers",
    "avg_price",
    "min_price",
    "max_price",
    "close_price",
]

