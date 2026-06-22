#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GCP_DIR="$ROOT_DIR/infra/gcp"

PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null || true)}"
REGION="${REGION:-us-central1}"
RUN_MANUAL_SNAPSHOT="${RUN_MANUAL_SNAPSHOT:-0}"

if [[ -z "$PROJECT_ID" ]]; then
  echo "PROJECT_ID is required. Set it or run: gcloud config set project <project-id>" >&2
  exit 1
fi

cd "$ROOT_DIR"

if [[ ! -f "$GCP_DIR/terraform.tfvars" ]]; then
  cp "$GCP_DIR/terraform.tfvars.example" "$GCP_DIR/terraform.tfvars"
fi

python - "$GCP_DIR/terraform.tfvars" "$PROJECT_ID" "$REGION" <<'PY'
from pathlib import Path
import re
import sys

path = Path(sys.argv[1])
project_id = sys.argv[2]
region = sys.argv[3]
text = path.read_text(encoding="utf-8")
text = re.sub(r'project_id\s*=\s*"[^"]*"', f'project_id  = "{project_id}"', text)
text = re.sub(r'region\s*=\s*"[^"]*"', f'region      = "{region}"', text)
path.write_text(text, encoding="utf-8")
PY

echo "Using project=$PROJECT_ID region=$REGION"
echo "Cost guardrail: scheduler_paused stays controlled by infra/gcp/terraform.tfvars."

terraform -chdir="$GCP_DIR" init
terraform -chdir="$GCP_DIR" apply -auto-approve \
  -target=google_project_service.required \
  -target=google_artifact_registry_repository.scanner

IMAGE="$(terraform -chdir="$GCP_DIR" output -raw artifact_registry_repository)/fifa-scanner:latest"
echo "Building scanner image with Cloud Build: $IMAGE"
gcloud builds submit \
  --project "$PROJECT_ID" \
  --config "$ROOT_DIR/cloudbuild.gcp-scanner.yaml" \
  --substitutions "_IMAGE=$IMAGE" \
  "$ROOT_DIR"

terraform -chdir="$GCP_DIR" apply -auto-approve

echo
echo "Deployment outputs:"
terraform -chdir="$GCP_DIR" output

if [[ "$RUN_MANUAL_SNAPSHOT" == "1" ]]; then
  JOB_NAME="$(terraform -chdir="$GCP_DIR" output -raw cloud_run_job_name)"
  OUTPUT_URI="$(terraform -chdir="$GCP_DIR" output -raw gcs_output_uri)"
  echo "Running one manual snapshot job: $JOB_NAME"
  gcloud run jobs execute "$JOB_NAME" --project "$PROJECT_ID" --region "$REGION" --wait
  echo "Latest GCS artifacts:"
  gsutil ls -r "$OUTPUT_URI/processed/latest/**" || true
fi
