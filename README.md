# Poly x Kalshi

Research-only cross-market scanner for FIFA / World Cup prediction-market price gaps between Polymarket and Kalshi.

The current focus is the FIFA / World Cup scanner workflow:

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

## Cost-Conscious GCP Run

The GCP deployment is designed to avoid an always-on worker. Cloud Scheduler triggers a Cloud Run Job once per schedule tick, the job runs one `poly-x-kalshi-fifa-snapshot`, writes durable files to GCS, and exits.

Default resources are intentionally small:

```text
Cloud Run Job: 1 vCPU, 512Mi, max 300 seconds
Cloud Scheduler: paused by default
GCS raw retention: 14 days
Output path: gs://<bucket>/fifa_arbitrage/
```

Create a Terraform variables file:

```bash
cd infra/gcp
cp terraform.tfvars.example terraform.tfvars
# edit project_id, region, and scheduler_paused
```

Bootstrap required APIs and Artifact Registry first:

```bash
terraform init
terraform apply \
  -target=google_project_service.required \
  -target=google_artifact_registry_repository.scanner
```

Build and push the scanner image after Artifact Registry exists:

```bash
cd /Users/qisongqiao/Warehouse/cv/project_simulation/prediction_market
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

Inspect GCS outputs:

```bash
gsutil ls -r "$(terraform -chdir=infra/gcp output -raw gcs_output_uri)"
```

When ready to collect continuously, set `scheduler_paused = false` and apply again. For a cheaper burn-in, use `schedule = "*/5 * * * *"`.

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
config/fifa_market_mappings.csv              # manually approved mapping gate
notebooks/05_cross_market_fifa_arbitrage_scanner.ipynb
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
