# Poly x Kalshi

Research-only cross-market scanner for FIFA / World Cup prediction-market price gaps between Polymarket and Kalshi.

The current MVP discovers FIFA-related market candidates, expands Polymarket World Cup event pages into child markets, scans Kalshi `KXWCGAME` events, uses manually approved cross-venue mappings, polls live YES/NO orderbooks, flags conservative complementary-buy arbitrage, and logs snapshots locally for backtesting. It does not place trades, manage private keys, auto-approve mappings, run WebSockets, or require cloud infrastructure.

## Cross-Market FIFA Scanner

Open the notebook:

```bash
cd /Users/qisongqiao/Warehouse/cv/project_simulation/prediction_market
source .venv/bin/activate
jupyter notebook notebooks/05_cross_market_fifa_arbitrage_scanner.ipynb
```

Manual mapping is the alert gate. Fill [config/fifa_market_mappings.csv](/Users/qisongqiao/Warehouse/cv/project_simulation/prediction_market/config/fifa_market_mappings.csv) with approved pairs only after checking draw handling, extra time, penalties, and settlement notes.

Run one snapshot:

```bash
poly-x-kalshi-fifa-snapshot
```

If the mapping CSV is empty, start from the approval workbench files written by the snapshot:

```text
data/fifa_arbitrage/processed/latest/approval_candidates.csv
data/fifa_arbitrage/processed/latest/suggested_mappings.csv
```

`approval_candidates.csv` lists each discovered venue market with market type, event title, event date, normalized event match key, outcome label, settlement summary, token IDs, rules text, raw payload, and liquidity hints. `suggested_mappings.csv` proposes high-confidence Polymarket/Kalshi pairs using exact event/outcome keys for game-winner markets, but every row stays `review_required`; copy only verified pairs into `config/fifa_market_mappings.csv` and set `status=approved`. The files under `processed/` are cumulative history; `processed/latest/` is just the newest run.

Run the local scheduler loop:

```bash
poly-x-kalshi-fifa-watch --interval-seconds 60
```

For a short smoke run:

```bash
poly-x-kalshi-fifa-watch --max-ticks 2 --interval-seconds 5
```

The scanner writes:

```text
data/fifa_arbitrage/
├── raw/
│   ├── polymarket/
│   └── kalshi/
├── processed/
│   ├── venue_market_candidates.parquet
│   ├── approval_candidates.parquet
│   ├── suggested_mappings.parquet
│   ├── manual_mappings_snapshot.parquet
│   ├── orderbook_snapshots.parquet
│   ├── arbitrage_alerts.parquet
│   ├── scanner_runs.parquet
│   └── latest/
│       ├── approval_candidates.csv
│       └── suggested_mappings.csv
└── alerts/
    └── arbitrage_alerts.jsonl
```

Optional live smoke tests:

```bash
RUN_LIVE_FIFA_ARBITRAGE_TESTS=1 python -m pytest tests/test_fifa_arbitrage_live.py
```

## Legacy Data Profile

Notebook-first local prototype for comparing small Polymarket and Kalshi prediction-market data snapshots.

The project retrieves active/open markets, current orderbooks, and recent REST trades, then writes raw JSON plus normalized Parquet/CSV tables under `data/`. There is intentionally no CLI and no API service; the notebook is the main workflow.

It also includes a Polymarket-only on-chain viability notebook that mirrors the bundled `Polymarket_data` project at small scale: fetch recent Polygon `OrderFilled` logs, decode them, join observed token IDs to Gamma market metadata, and produce `orderfilled`, `trades`, `quant`, and `users` outputs.

## Run

```bash
cd /Users/qisongqiao/Warehouse/cv/project_simulation/prediction_market
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python -m ipykernel install --user --name prediction-market-data-profile
jupyter notebook notebooks/01_prediction_market_data_profile.ipynb
```

The notebook writes:

```text
data/
├── raw/
│   ├── polymarket_markets.json
│   ├── kalshi_markets.json
│   ├── polymarket_orderbooks.json
│   ├── kalshi_orderbooks.json
│   ├── polymarket_trades.json
│   └── kalshi_trades.json
└── processed/
    ├── markets.parquet
    ├── markets.csv
    ├── orderbook_snapshots.parquet
    ├── orderbook_snapshots.csv
    ├── trades.parquet
    └── trades.csv
```

## Tests

```bash
cd /Users/qisongqiao/Warehouse/cv/project_simulation/prediction_market
python -m pytest
```

Normal tests use mocked HTTP responses. Optional live API smoke tests are skipped unless `RUN_LIVE_MARKET_TESTS=1`.

## Polymarket Sportsbook Arbitrage Feasibility

Open:

```bash
jupyter notebook notebooks/04_polymarket_bookmaker_arbitrage_feasibility.ipynb
```

This research-only notebook follows the core idea from the Kacho Polymarket arbitrage writeup: parse sportsbook decimal odds, remove two-way overround, match events to Polymarket outcome-token orderbooks, and score whether the Polymarket best ask is below the sportsbook-derived fair probability after buffers. It does not place trades or handle credentials.

By default the notebook runs from saved fixtures and synthetic Polymarket-like orderbooks, then writes:

```text
data/arbitrage/
├── raw/
│   ├── oddsportal/
│   └── polymarket/
└── processed/
    ├── odds_quotes.parquet
    ├── polymarket_books.parquet
    ├── matched_events.parquet
    ├── arbitrage_opportunities.parquet
    ├── arbitrage_sensitivity.parquet
    └── arbitrage_verdict.csv
```

Optional live Polymarket capture:

```bash
RUN_LIVE_POLYMARKET_CAPTURE=1 \
  jupyter notebook notebooks/04_polymarket_bookmaker_arbitrage_feasibility.ipynb
```

Optional live OddsPortal capture requires a Playwright browser install and is best-effort only:

```bash
.venv/bin/python -m playwright install chromium
RUN_LIVE_ODDSPORTAL_CAPTURE=1 \
  jupyter notebook notebooks/04_polymarket_bookmaker_arbitrage_feasibility.ipynb
```

Normal tests use saved HTML/JSON fixtures. Optional live arbitrage smoke tests are skipped unless `RUN_LIVE_ARBITRAGE_TESTS=1`.

## Polymarket On-Chain Viability

Open:

```bash
jupyter notebook notebooks/02_polymarket_onchain_viability.ipynb
```

The notebook writes a tiny recent-block sample below:

```text
data/onchain_sample/
├── raw/
│   ├── summary.json
│   ├── orderfilled_logs.json
│   └── gamma_markets.json
├── processed/
│   ├── orderfilled.parquet
│   ├── market_token_mapping.parquet
│   ├── trades.parquet
│   ├── quant.parquet
│   └── users.parquet
└── latest_result/
    ├── orderfilled.csv
    ├── market_token_mapping.csv
    ├── trades.csv
    ├── quant.csv
    └── users.csv
```

Set `POLYGON_RPC_URL` before launching Jupyter if you want to use a private Polygon RPC endpoint. Without it, the helper tries public endpoints. The code supports both the older v1 Polymarket exchange event shape and current v2 event shape; recent samples default to current v2 contracts.

## Phase 1 AWS Historical ETL

Phase 1 is Polymarket-only historical ETL:

```text
Gamma API + Polygon OrderFilled logs
        -> Python ETL container
        -> AWS Batch
        -> S3 Bronze / Silver / Gold
        -> Glue Data Catalog
        -> Athena SQL
```

No Kalshi, WebSockets, orderbook reconstruction, dbt, Spark, Snowflake, Redshift, or API service is included in this phase.

For a detailed model walkthrough, open:

```bash
jupyter notebook notebooks/03_phase1_aws_historical_etl_model.ipynb
```

That notebook documents the Bronze/Silver/Gold lake layout, table grains, join keys, trade normalization rules, daily aggregate model, AWS Batch flow, Athena validation checks, and known metadata gaps from the one-day validation run.

### Local Dry Run

Run the same job entrypoint against a local lake folder before AWS:

```bash
cd /Users/qisongqiao/Warehouse/cv/project_simulation/prediction_market
source .venv/bin/activate

prediction-market-etl \
  --job fetch_markets \
  --lake-uri data/aws_lake \
  --run-id local-markets \
  --max-markets 100

prediction-market-etl \
  --job fetch_orderfilled \
  --lake-uri data/aws_lake \
  --run-id local-fills \
  --start-block 88051313 \
  --end-block 88051315 \
  --batch-size 1 \
  --max-events 250

prediction-market-etl \
  --job normalize_trades \
  --lake-uri data/aws_lake \
  --run-id local-normalize

prediction-market-etl \
  --job build_market_daily \
  --lake-uri data/aws_lake \
  --run-id local-daily
```

The local lake mirrors the AWS S3 layout:

```text
data/aws_lake/
├── bronze/polymarket/
├── silver/polymarket/
└── gold/polymarket/
```

### Docker

```bash
docker build -f docker/Dockerfile -t prediction-market-etl:latest .
```

After Terraform creates ECR, tag and push:

```bash
aws ecr get-login-password --region <region> \
  | docker login --username AWS --password-stdin <account>.dkr.ecr.<region>.amazonaws.com

docker tag prediction-market-etl:latest <ecr_repository_url>:latest
docker push <ecr_repository_url>:latest
```

### Terraform IaC

Terraform lives in `infra/` and creates:

```text
S3 data lake bucket
Athena results bucket and workgroup
ECR repository
AWS Batch Fargate compute environment, queue, and job definition
IAM roles and policies
CloudWatch log group
Secrets Manager Polygon RPC secret
Glue database and external tables
```

Example:

```bash
cd infra
terraform init
terraform plan \
  -var='batch_subnet_ids=["subnet-..."]' \
  -var='batch_security_group_ids=["sg-..."]'
terraform apply \
  -var='batch_subnet_ids=["subnet-..."]' \
  -var='batch_security_group_ids=["sg-..."]'
```

Set the Polygon RPC URL in Secrets Manager after apply, or pass `polygon_rpc_secret_value` for a dev deployment.

### AWS Batch Runs

The Batch job definition entrypoint is `prediction-market-etl`. Override the command per job:

```bash
aws batch submit-job \
  --job-name fetch-markets-smoke \
  --job-queue <batch_job_queue_arn> \
  --job-definition <batch_job_definition_arn> \
  --container-overrides '{
    "command": ["--job","fetch_markets","--run-id","aws-markets-smoke","--max-markets","100"]
  }'

aws batch submit-job \
  --job-name fetch-orderfilled-smoke \
  --job-queue <batch_job_queue_arn> \
  --job-definition <batch_job_definition_arn> \
  --container-overrides '{
    "command": ["--job","fetch_orderfilled","--run-id","aws-fills-smoke","--start-block","88051313","--end-block","88051315","--batch-size","1","--max-events","250"]
  }'

aws batch submit-job \
  --job-name normalize-trades-smoke \
  --job-queue <batch_job_queue_arn> \
  --job-definition <batch_job_definition_arn> \
  --container-overrides '{
    "command": ["--job","normalize_trades","--run-id","aws-normalize-smoke"]
  }'

aws batch submit-job \
  --job-name build-market-daily-smoke \
  --job-queue <batch_job_queue_arn> \
  --job-definition <batch_job_definition_arn> \
  --container-overrides '{
    "command": ["--job","build_market_daily","--run-id","aws-daily-smoke"]
  }'
```

### Bounded Backfill Driver

Use `prediction-market-backfill` to submit a block range as retryable AWS Batch chunks and then run the scoped downstream jobs. Prefer a fresh validation prefix for each bounded run so old smoke files cannot inflate aggregates.

Dry-run the plan first:

```bash
prediction-market-backfill \
  --region us-east-1 \
  --job-queue arn:aws:batch:us-east-1:699200216006:job-queue/prediction-market-dev-etl \
  --job-definition arn:aws:batch:us-east-1:699200216006:job-definition/prediction-market-dev-etl:1 \
  --lake-uri s3://prediction-market-dev-699200216006-us-east-1-data/validation/backfill-test-001 \
  --run-id backfill-test-001 \
  --start-block 88052514 \
  --end-block 88052516 \
  --chunk-size 1000 \
  --rpc-batch-size 1000 \
  --date-start 2026-06-06 \
  --date-end 2026-06-06 \
  --dry-run
```

Submit and wait for the full sequence:

```bash
prediction-market-backfill \
  --region us-east-1 \
  --job-queue arn:aws:batch:us-east-1:699200216006:job-queue/prediction-market-dev-etl \
  --job-definition arn:aws:batch:us-east-1:699200216006:job-definition/prediction-market-dev-etl:1 \
  --lake-uri s3://prediction-market-dev-699200216006-us-east-1-data/validation/backfill-test-001 \
  --run-id backfill-test-001 \
  --start-block 88052514 \
  --end-block 88052516 \
  --chunk-size 1000 \
  --rpc-batch-size 1000 \
  --date-start 2026-06-06 \
  --date-end 2026-06-06 \
  --poll-seconds 30
```

The driver runs:

```text
chunked fetch_orderfilled jobs
  -> targeted fetch_markets from observed token IDs
  -> scoped normalize_trades
  -> scoped build_market_daily
```

For a larger 1-day backfill, keep the same pattern but increase the block range and choose a chunk size that your RPC provider can handle. The transform layer deduplicates trades by `transaction_hash + log_index`, but isolated S3 prefixes are still recommended for validation.

The targeted Gamma market lookup queries both default/open markets and `closed=true` markets, with paginated token batches. This is necessary for historical slices because short-lived markets, such as crypto Up/Down intervals, may already be closed by the time the metadata job runs.

### Athena

Terraform creates Glue tables for:

```text
dim_market
dim_outcome
fact_trades
fact_market_daily
```

Sample SQL and data-quality checks are in `sql/`.

For isolated validation prefixes, create temporary Athena tables that point at only one run:

```bash
prediction-market-athena-validation \
  --region us-east-1 \
  --database prediction_market_dev \
  --work-group prediction-market-dev-phase1 \
  --lake-uri s3://prediction-market-dev-699200216006-us-east-1-data/validation/backfill-test-001 \
  --table-prefix v_backfill_test_001 \
  --replace \
  --checks
```

This creates tables named like:

```text
v_backfill_test_001_dim_market
v_backfill_test_001_dim_outcome
v_backfill_test_001_fact_trades
v_backfill_test_001_fact_market_daily
```

The helper auto-detects `date=YYYY-MM-DD` partitions under the validation `fact_trades` prefix and applies that date range to validation checks. Use `--dry-run` to print the DDL/check SQL without executing it.
