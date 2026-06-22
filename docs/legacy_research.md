# Legacy Research Notes

These pieces are retained as reference material. They are not the active project direction while the repo focuses on the Polymarket x Kalshi FIFA scanner.

## Prediction Market Data Profile

Notebook:

```bash
jupyter notebook notebooks/01_prediction_market_data_profile.ipynb
```

Purpose:

- Retrieve small Polymarket and Kalshi market/orderbook/trade snapshots.
- Save raw JSON under `data/raw/`.
- Save normalized market, orderbook, and trade tables under `data/processed/`.

## Polymarket On-Chain Viability

Notebook:

```bash
jupyter notebook notebooks/02_polymarket_onchain_viability.ipynb
```

Purpose:

- Sample Polygon `OrderFilled` logs.
- Decode current and legacy Polymarket exchange event shapes.
- Join observed token IDs to Gamma metadata.
- Produce tiny local `orderfilled`, `trades`, `quant`, and `users` outputs.

Set `POLYGON_RPC_URL` to use a private Polygon RPC endpoint.

## Phase 1 AWS Historical ETL

Notebook:

```bash
jupyter notebook notebooks/03_phase1_aws_historical_etl_model.ipynb
```

Infrastructure and batch code:

```text
infra/
etl/
src/prediction_market/aws_etl/
sql/
docker/
```

Purpose:

```text
Gamma API + Polygon OrderFilled logs
        -> Python ETL container
        -> AWS Batch
        -> S3 Bronze / Silver / Gold
        -> Glue Data Catalog
        -> Athena SQL
```

Local dry-run entrypoint:

```bash
prediction-market-etl --job fetch_markets --lake-uri data/aws_lake --run-id local-markets --max-markets 100
```

## Polymarket Sportsbook Arbitrage Feasibility

Notebook:

```bash
jupyter notebook notebooks/04_polymarket_bookmaker_arbitrage_feasibility.ipynb
```

Purpose:

- Parse sportsbook decimal odds.
- Remove two-way overround.
- Match events to Polymarket outcome-token orderbooks.
- Score whether Polymarket best ask is below sportsbook-derived fair probability after buffers.

Optional live captures:

```bash
RUN_LIVE_POLYMARKET_CAPTURE=1 jupyter notebook notebooks/04_polymarket_bookmaker_arbitrage_feasibility.ipynb

.venv/bin/python -m playwright install chromium
RUN_LIVE_ODDSPORTAL_CAPTURE=1 jupyter notebook notebooks/04_polymarket_bookmaker_arbitrage_feasibility.ipynb
```

## Reference Checkout

`Polymarket_data/` is a local comparison checkout used during earlier research. It is ignored by git and can be moved out of this repo when no longer needed.
