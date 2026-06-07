#!/usr/bin/env bash
# Run ON the GCP app VM after cloning repo and copying .env.prod + secrets.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
COMPOSE_DIR="$ROOT/deploy/docker"
ENV_FILE="$COMPOSE_DIR/.env.prod"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing $ENV_FILE — copy from laptop or run render-env-from-terraform.sh" >&2
  exit 1
fi

# shellcheck source=/dev/null
source "$ENV_FILE"

sudo mkdir -p "${SECRETS_DIR:-/opt/idp/secrets}" "${MODELS_DIR:-/opt/idp/models}"

if [[ ! -f "${SECRETS_DIR}/preprocess-sa.json" ]]; then
  echo "WARN: ${SECRETS_DIR}/preprocess-sa.json missing — preprocess GCS writes will fail." >&2
fi

if [[ ! -f "${MODELS_DIR}/mit_unet_doctamper_best.pt" ]]; then
  echo "Downloading DocTamper checkpoint from GCS..."
  gsutil cp "gs://${GCS_BUCKET}/models/mit_unet_doctamper_best.pt" "${MODELS_DIR}/" || true
fi

cd "$COMPOSE_DIR"
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build

echo ""
echo "=== Deployed ==="
echo "App:          http://${APP_VM_IP}:8000"
echo "Orchestrator: http://${APP_VM_IP}:8001"
echo "Preprocess:   http://${APP_VM_IP}:8002"
echo "Extraction:   ${EXTRACTION_URL}"
echo ""
echo "Warm RunPod + circuit breaker:"
echo "  curl -X POST \"http://127.0.0.1:8001/internal/runpod/warm?warm=true\""
echo ""
echo "Health:"
echo "  curl -s http://127.0.0.1:8000/health"
echo "  curl -s http://127.0.0.1:8001/health"
echo "  curl -s ${EXTRACTION_URL}/health"
