# Poly x Kalshi

Research-only cross-market scanner for prediction-market price gaps between Polymarket and Kalshi.

The current production burn-in is the FIFA / World Cup scanner workflow:

```text
discover equivalent football markets
-> review suggested Polymarket/Kalshi mappings
-> approve only verified pairs
-> poll live orderbooks
-> log conservative cross-market alerts
```

No automatic trading, private keys, or WebSockets are used in the current MVP. Local runs use a watch loop; the GCP path uses cost-conscious scheduled one-shot snapshots.

## Quick Start

```bash
cd /Users/qisongqiao/Warehouse/cv/project_simulation/prediction_market
source .venv/bin/activate
pip install -e ".[dev]"
```

Run one discovery and scoring snapshot:

```bash
poly-x-kalshi-fifa-snapshot
```

Run the local polling loop:

```bash
poly-x-kalshi-fifa-watch --no-discovery --interval-seconds 60
```

Short smoke loop:

```bash
poly-x-kalshi-fifa-watch --no-discovery --max-ticks 2 --interval-seconds 5
```

Launch the first monitoring dashboard over the latest local ETL outputs:

```bash
pip install -e ".[dev,dashboard,gcp]"
poly-x-kalshi-dashboard
```

The dashboard can also read the cloud ETL output directly from GCS:

```bash
POLY_X_KALSHI_DASHBOARD_SOURCE=gs://poly-x-kalshi-dev-poly-x-kalshi-scanner/cross_sports_arbitrage \
POLY_X_KALSHI_REVIEW_MAPPING_PATH=gs://poly-x-kalshi-dev-poly-x-kalshi-scanner/cross_sports_arbitrage/manual_review/approved_mappings/current.csv \
  poly-x-kalshi-dashboard
```

## Cloud Database Plan

The next cloud architecture uses Cloud SQL for PostgreSQL as the operational approval database, GCS as the raw snapshot lake, and Cloud Run/Scheduler for daily incremental active-universe syncs. The design covers venue event tables, venue market/outcome tables, event-pair approvals, market-pair approvals, Vertex AI candidate generation, and expiration handling.

See:

```text
docs/cloud_database_design.md
infra/gcp/sql/prediction_market_schema.sql
```

## Cross-Sports Discovery

Use the broader sports scanner to look for more Polymarket/Kalshi pairs across soccer, basketball, baseball, hockey, football, tennis, combat sports, golf, racing, and related sports categories. This path is discovery-first: it writes review tables, but alerts still require manual approval in a separate mapping file.

```bash
poly-x-kalshi-sports-snapshot --discovery-only
```

For a full cloud-backed Review Queue refresh, keep broad search enabled and let it run:

```bash
unset ALL_PROXY all_proxy
export HTTP_PROXY=http://127.0.0.1:7897
export HTTPS_PROXY=http://127.0.0.1:7897
export POLY_X_KALSHI_DASHBOARD_SOURCE=gs://poly-x-kalshi-dev-poly-x-kalshi-scanner/cross_sports_arbitrage

poly-x-kalshi-sports-snapshot \
  --discovery-only \
  --output-dir "$POLY_X_KALSHI_DASHBOARD_SOURCE" \
  --mapping-path gs://poly-x-kalshi-dev-poly-x-kalshi-scanner/cross_sports_arbitrage/manual_review/approved_mappings/current.csv \
  --run-id manual-review-discovery-$(date -u +%Y%m%dT%H%M%SZ) \
  --market-limit 1500 \
  --page-size 200 \
  --embedding-min-score 58 \
  --embedding-top-k 5 \
  --semantic-embedding-provider off
```

`--discovery-only` refreshes active event candidates and manual review tables without pulling orderbooks or overwriting the latest price/signal tables. The scanner also writes vector-ranked `suggested_mappings` for bulk review. Use `--no-general-search` only for quick debugging when you want venue sports tags/series without the broad all-market crawl.

Suggestion tuning:

- `--embedding-min-score`: lower for more recall and more noise, raise for fewer suggestions.
- `--embedding-top-k`: max vector-generated suggestions per Polymarket row.
- `--semantic-embedding-provider`: keep `off` for the local deterministic matcher, use `vertex-gemini` to add Gemini semantic reranking.
- `--semantic-embedding-dim`, `--semantic-top-k`, `--semantic-min-score`: Gemini/local semantic cache dimension, per-row suggestion cap, and semantic threshold.
- `--semantic-batch-size`, `--semantic-batch-sleep-seconds`: lower/raise these when Vertex reports token-per-minute quota errors.
- `--semantic-cache-flush-batches`: writes `processed/latest/market_embeddings.*` during long Gemini runs so interrupted runs still preserve progress.
- `--semantic-max-embedding-texts`: caps only new uncached Gemini texts for a bounded test run; `0` means unlimited.
- `suggestion_method=rules`: strict exact/fuzzy rule match.
- `suggestion_method=embedding`: vector bulk-filter candidate that still requires manual approval.
- `suggestion_method=semantic`: second-stage semantic candidate from cached canonical market embeddings.
- All suggested pairs still pass deterministic hard gates for sport, market type, date/event, outcome, totals metric/line, and bundled-market exclusion before they appear in the Review Queue.

To run the optional Gemini semantic layer locally or in Cloud Run, install the GCP extra, authenticate to the target project, and enable the provider:

```bash
poly-x-kalshi-sports-snapshot \
  --discovery-only \
  --output-dir "$POLY_X_KALSHI_DASHBOARD_SOURCE" \
  --mapping-path gs://poly-x-kalshi-dev-poly-x-kalshi-scanner/cross_sports_arbitrage/manual_review/approved_mappings/current.csv \
  --run-id semantic-discovery-$(date -u +%Y%m%dT%H%M%SZ) \
  --market-limit 1500 \
  --page-size 200 \
  --semantic-embedding-provider vertex-gemini \
  --semantic-embedding-dim 768 \
  --semantic-top-k 20 \
  --semantic-min-score 72 \
  --semantic-batch-size 64 \
  --semantic-batch-sleep-seconds 5 \
  --semantic-retry-initial-seconds 60 \
  --semantic-max-retries 8 \
  --semantic-cache-flush-batches 2
```

The semantic layer writes `processed/latest/market_embeddings.*` and reuses rows whose canonical text hash, model, and dimension have not changed. Terraform keeps the Cloud Run discovery job at `off` by default; set `sports_discovery_semantic_embedding_provider = "vertex-gemini"` when you intentionally want daily Vertex AI calls.

If Vertex returns `429 Quota exceeded for ... embed_content_input_tokens_per_minute_per_base_model`, rerun with a smaller batch or longer sleep, for example `--semantic-batch-size 32 --semantic-batch-sleep-seconds 10`. Already cached embeddings will be reused on the next run. For a smoke test, add `--semantic-max-embedding-texts 500`.

The broad scanner writes to:

```text
data/cross_sports_arbitrage/
├── processed/latest/approval_candidates.csv
├── processed/latest/suggested_mappings.csv
├── processed/latest/orderbook_snapshots.csv
├── processed/latest/strategy_signals.csv
└── processed/latest/scanner_runs.csv
```

The approval gate can be the repo seed CSV for local experiments:

```text
config/cross_sports_market_mappings.csv
```

For the cloud/manual-review workflow, use the GCS mapping store instead:

```text
gs://<scanner-bucket>/cross_sports_arbitrage/manual_review/approved_mappings/current.csv
```

In the first broad scan, the system found thousands of sports candidates but only approve-ready exact Polymarket/Kalshi pairs in World Cup match-winner markets. That is useful evidence: non-FIFA sports overlap exists in the raw candidate universe, but the current Kalshi/Polymarket live products are often different shapes, such as parlays, totals, props, or multi-game markets. Treat non-exact suggestions as research leads, not trading candidates.

## Cost-Conscious GCP Run

The GCP deployment is designed to avoid an always-on worker. Cloud Scheduler triggers Cloud Run Jobs on fixed schedules, each job runs one snapshot, writes durable files to GCS, and exits.

Default resources are intentionally small:

```text
Cloud Run Job: 1 vCPU, 512Mi, max 300 seconds
Cloud Scheduler: paused by default
GCS raw retention: 14 days
FIFA output path: gs://<bucket>/fifa_arbitrage/
Cross-sports output path: gs://<bucket>/cross_sports_arbitrage/
```

The Cloud SQL daily active-universe pipeline is heavier because it fetches all
open Polymarket/Kalshi markets, writes the normalized current state, marks
expired rows, and generates Vertex AI match candidates:

```text
Daily pipeline Cloud Run Job: 4 vCPU, 16Gi, max 14,400 seconds
Default daily schedule: 07:30 UTC
Kalshi event-market fetch workers: 8
Cloud SQL tier used for dev burn-in: db-custom-2-7680
```

Create a Terraform variables file:

```bash
cd infra/gcp
cp terraform.tfvars.example terraform.tfvars
# edit project_id, region, and scheduler_paused
```

One-command deployment from a machine that can reach GCP control-plane APIs:

```bash
cd /Users/qisongqiao/Warehouse/cv/project_simulation/prediction_market
PROJECT_ID=poly-x-kalshi REGION=us-central1 ./scripts/deploy_gcp_scanner.sh
```

To also run one manual smoke snapshot after deployment:

```bash
PROJECT_ID=poly-x-kalshi REGION=us-central1 RUN_MANUAL_SNAPSHOT=1 ./scripts/deploy_gcp_scanner.sh
```

To run the football/cross-sports ETL smoke path in cloud after deployment:

```bash
PROJECT_ID=poly-x-kalshi REGION=us-central1 RUN_MANUAL_SPORTS_SNAPSHOT=1 ./scripts/deploy_gcp_scanner.sh
```

If local networking blocks `serviceusage.googleapis.com`, run the same commands from Google Cloud Shell.

Manual deployment steps are:

Bootstrap required APIs and Artifact Registry first:

```bash
terraform init
terraform apply \
  -target=google_project_service.required \
  -target=google_artifact_registry_repository.scanner
```

Build and push the scanner image after Artifact Registry exists. Cloud Build avoids needing a local Docker daemon:

```bash
cd /Users/qisongqiao/Warehouse/cv/project_simulation/prediction_market
IMAGE="$(terraform -chdir=infra/gcp output -raw artifact_registry_repository)/fifa-scanner:latest"
gcloud builds submit \
  --project poly-x-kalshi \
  --config cloudbuild.gcp-scanner.yaml \
  --substitutions "_IMAGE=$IMAGE" .
```

If Docker Desktop is running, this local build path also works:

```bash
IMAGE="$(terraform -chdir=infra/gcp output -raw artifact_registry_repository)/fifa-scanner:latest"
REGION="$(echo "$IMAGE" | cut -d- -f1-2)"

gcloud auth configure-docker "${REGION}-docker.pkg.dev"
docker buildx build --platform linux/amd64 \
  -f docker/Dockerfile.gcp-scanner \
  -t "$IMAGE" \
  --push .
```

Deploy the runtime resources:

```bash
terraform -chdir=infra/gcp plan
terraform -chdir=infra/gcp apply
```

Run one manual cloud snapshot:

```bash
terraform -chdir=infra/gcp output -raw manual_run_command
gcloud run jobs execute "$(terraform -chdir=infra/gcp output -raw cloud_run_job_name)" \
  --region us-central1 \
  --wait
```

Run one manual football/cross-sports cloud snapshot:

```bash
terraform -chdir=infra/gcp output -raw sports_manual_run_command
gcloud run jobs execute "$(terraform -chdir=infra/gcp output -raw sports_cloud_run_job_name)" \
  --region us-central1 \
  --wait
```

Inspect GCS outputs:

```bash
gsutil ls -r "$(terraform -chdir=infra/gcp output -raw gcs_output_uri)"
gsutil ls -r "$(terraform -chdir=infra/gcp output -raw sports_gcs_output_uri)"
```

When ready to collect continuously, set `scheduler_paused = false` or `sports_scheduler_paused = false` and apply again. To refresh the manual review queue once per day, set `sports_discovery_scheduler_paused = false`. For a cheaper burn-in, use `schedule = "*/5 * * * *"` or `sports_schedule = "*/5 * * * *"`.

For the Cloud SQL daily active-universe pipeline, set `daily_pipeline_scheduler_paused = false`.
It uses the `poly-x-kalshi-dev-daily-pipeline` Cloud Run Job and can be run manually with:

```bash
gcloud run jobs execute "$(terraform -chdir=infra/gcp output -raw daily_pipeline_cloud_run_job_name)" \
  --region us-central1 \
  --wait
```

## Monitoring Dashboard

The dashboard is the first OddPool-style product surface. It reads scanner outputs, not exchange APIs, so it can inspect local burn-ins and cloud scheduled runs without changing the ETL path.

```bash
poly-x-kalshi-dashboard
```

It shows:

- latest run health and row counts
- approved mapping count
- best current net edges and executable alert rows
- historical viability metrics across appendable ETL outputs
- pair recommendations: `monitor`, `watch`, `pause`, or `needs_more_data`
- discovery coverage funnel by market type: candidates, suggested pairs, approved pairs, and recommendation states
- keyword coverage by sport/league terms such as soccer, tennis, MLB, or World Cup
- pair ranking by best edge, positive-edge rate, alert rate, and depth
- buffer / threshold sensitivity across historical gross prices
- exclusion reasons such as `edge_below_threshold` and `insufficient_depth`
- pair-level signal history
- normalized YES/NO orderbook snapshots
- an active-only manual review queue with separate Polymarket and Kalshi search lists

By default it reads:

```text
data/cross_sports_arbitrage/processed/latest/
```

Turn on `Use full history when available` in the sidebar to read appendable files such as:

```text
data/cross_sports_arbitrage/processed/strategy_signals.parquet
data/cross_sports_arbitrage/processed/orderbook_snapshots.parquet
data/cross_sports_arbitrage/processed/scanner_runs.parquet
```

To point it at GCS, use either the sidebar source box or:

```bash
POLY_X_KALSHI_DASHBOARD_SOURCE="$(terraform -chdir=infra/gcp output -raw sports_gcs_output_uri)" \
POLY_X_KALSHI_REVIEW_MAPPING_PATH="$(terraform -chdir=infra/gcp output -raw sports_review_mapping_uri)" \
  poly-x-kalshi-dashboard
```

Use the **Review Queue** tab to manually match events:

- review vector-ranked suggested pairs first
- approve, reject, or keep suggested pairs in review
- search active Polymarket events on the left
- search active Kalshi events on the right
- select one row from each venue
- verify settlement details, draw handling, overtime/extra-time, penalties/tiebreaks, and cancellation rules
- save the row as `approved`, `needs_review`, or `rejected`

The page writes the current mapping file and a timestamped history snapshot. The scheduled sports scanner reads only `approved` mappings whose `lifecycle_status` is `active`.

For viability review, start with the `Viability` tab:

- `Alert Rate`: how often the conservative filters found executable complementary-buy gaps.
- `Positive Edge Rate`: how often raw net edge was positive, even if depth or other filters blocked it.
- `Best Net Edge` and `P95 Net Edge`: whether observed gaps are large enough to survive fees, slippage, and execution risk.
- `Pair Recommendations`: operational labels for what to do next with each approved mapping.
- `Pair Ranking`: which approved pairs are worth continuing to monitor, and which are consistently too tight or too illiquid.
- `Buffer Sensitivity`: whether historical gross prices would have qualified under looser/tighter total buffer, minimum edge, and depth assumptions.

Use the `Coverage` tab before adding new market families:

- `Discovery Coverage Funnel`: where discovered candidates drop out before becoming suggested or approved pairs.
- `suggestion_rate`: suggested mappings divided by discovered candidates for that market type.
- `approval_rate`: approved mappings divided by suggested mappings for that market type.
- `Keyword Coverage`: which sports/leagues have raw inventory but little mapped monitoring coverage.

Recommendation labels mean:

- `monitor`: conservative alerts appeared; keep this pair in active monitoring.
- `watch`: no full alert yet, but positive or near-break-even edges appeared with enough depth.
- `pause`: pair is consistently below threshold or too illiquid.
- `needs_more_data`: fewer than three snapshots, so the sample is not meaningful yet.

## Viability Reports

For a non-interactive burn-in summary, generate a Markdown report from the same ETL outputs:

```bash
poly-x-kalshi-viability-report \
  --source data/cross_sports_arbitrage \
  --output reports/cross_sports_viability.md
```

Export machine-readable recommendations for the next review cycle:

```bash
poly-x-kalshi-viability-report \
  --source data/cross_sports_arbitrage \
  --output reports/cross_sports_viability.md \
  --recommendations-csv reports/pair_recommendations.csv \
  --recommendations-json reports/pair_recommendations.json
```

Export only pairs worth active follow-up:

```bash
poly-x-kalshi-viability-report \
  --source data/cross_sports_arbitrage \
  --recommendation-action monitor \
  --recommendation-action watch \
  --recommendations-csv reports/active_pair_queue.csv
```

To report on cloud history:

```bash
POLY_SOURCE="$(terraform -chdir=infra/gcp output -raw sports_gcs_output_uri)"
poly-x-kalshi-viability-report \
  --source "$POLY_SOURCE" \
  --output reports/cloud_cross_sports_viability.md
```

The report includes a verdict, core rates, coverage funnel, keyword coverage, pair recommendations, top pairs, exclusion reasons, buffer sensitivity, and run trend. Use it after a scheduled burn-in window to decide whether to expand mappings, loosen/tighten buffers, or pause a market family.

## Approval Workflow

Manual mapping is the safety gate. The bot can suggest pairs, but alerts only run for rows marked `approved` in [config/fifa_market_mappings.csv](config/fifa_market_mappings.csv).

After a snapshot, review:

```text
data/fifa_arbitrage/processed/latest/approval_candidates.csv
data/fifa_arbitrage/processed/latest/suggested_mappings.csv
```

`approval_candidates.csv` has one row per discovered venue market, including:

- `market_type`
- `event_title`
- `event_date`
- `event_match_key`
- `outcome_label`
- settlement/rules text
- Polymarket token IDs when available
- raw payload and liquidity hints

`suggested_mappings.csv` proposes high-confidence pairs using exact event/outcome keys for game-winner markets. Every row remains `review_required`; copy only verified rows into `config/fifa_market_mappings.csv` and set `status=approved`.

For football winner markets, verify:

- draw/Tie handling
- regular time vs extra time
- penalties
- cancellation/postponement rules
- whether the event and outcome are genuinely identical

## Main Files

```text
src/prediction_market/fifa_arbitrage.py       # bot discovery, mapping, snapshots, watch loop
src/prediction_market/dashboard_data.py       # dashboard loaders and summaries
apps/monitor_dashboard.py                     # Streamlit monitoring dashboard
config/fifa_market_mappings.csv              # manually approved mapping gate
config/cross_sports_market_mappings.csv      # active approved cross-sports monitoring gate
notebooks/05_cross_market_fifa_arbitrage_scanner.ipynb
notebooks/06_poly_x_kalshi_architecture_design.ipynb
tests/test_fifa_arbitrage.py
tests/test_fifa_arbitrage_live.py
```

Runtime outputs are ignored by git:

```text
data/fifa_arbitrage/
├── raw/
├── processed/
│   ├── latest/
│   ├── approval_candidates.*
│   ├── suggested_mappings.*
│   ├── orderbook_snapshots.*
│   ├── arbitrage_alerts.*
│   ├── strategy_signals.*
│   └── scanner_runs.*
└── alerts/
    └── arbitrage_alerts.jsonl
```

## Tests

```bash
.venv/bin/python -m pytest tests/test_fifa_arbitrage.py
.venv/bin/python -m pytest
```

Normal tests use mocked HTTP responses. Optional live checks are gated:

```bash
RUN_LIVE_FIFA_ARBITRAGE_TESTS=1 .venv/bin/python -m pytest tests/test_fifa_arbitrage_live.py
```

## Legacy Research

Earlier notebooks and AWS ETL work are still in the repo as reference, but they are not the active product direction. See [docs/legacy_research.md](docs/legacy_research.md).
