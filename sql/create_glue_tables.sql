-- Alternative manual Athena DDL. Terraform creates equivalent Glue tables.

CREATE DATABASE IF NOT EXISTS prediction_market_dev;

CREATE EXTERNAL TABLE IF NOT EXISTS prediction_market_dev.dim_market (
  market_id string,
  question string,
  slug string,
  category string,
  status string,
  open_time string,
  close_time string,
  resolution_time string,
  condition_id string,
  volume double,
  liquidity double,
  raw_payload_path string,
  ingested_at string
)
STORED AS PARQUET
LOCATION 's3://${data_bucket}/gold/polymarket/dim_market/';

CREATE EXTERNAL TABLE IF NOT EXISTS prediction_market_dev.dim_outcome (
  outcome_id string,
  market_id string,
  outcome_name string,
  outcome_index int,
  token_id string,
  side string,
  ingested_at string
)
STORED AS PARQUET
LOCATION 's3://${data_bucket}/gold/polymarket/dim_outcome/';

CREATE EXTERNAL TABLE IF NOT EXISTS prediction_market_dev.fact_trades (
  trade_id string,
  market_id string,
  outcome_id string,
  timestamp bigint,
  hour int,
  maker string,
  taker string,
  price double,
  token_amount double,
  usd_amount double,
  maker_direction string,
  taker_direction string,
  transaction_hash string,
  block_number bigint,
  order_hash string,
  fee_amount double,
  source_type string,
  ingested_at string,
  raw_payload_path string
)
PARTITIONED BY (date string)
STORED AS PARQUET
LOCATION 's3://${data_bucket}/gold/polymarket/fact_trades/';

CREATE EXTERNAL TABLE IF NOT EXISTS prediction_market_dev.fact_market_daily (
  market_id string,
  daily_volume double,
  daily_trade_count bigint,
  unique_makers bigint,
  unique_takers bigint,
  avg_price double,
  min_price double,
  max_price double,
  close_price double
)
PARTITIONED BY (date string)
STORED AS PARQUET
LOCATION 's3://${data_bucket}/gold/polymarket/fact_market_daily/';
