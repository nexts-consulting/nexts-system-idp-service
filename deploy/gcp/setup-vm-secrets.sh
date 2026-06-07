#!/usr/bin/env bash
# Run on local machine (with gcloud auth) before SSH to app VM.
# Creates preprocess SA key, grants Cloud SQL client to app SA, optional Secret Manager sync.
set -euo pipefail

PROJECT="${GCP_PROJECT_ID:-nexts-system-idp-service}"
REGION="${GCP_REGION:-asia-southeast1}"
ENV_PREFIX="${IDP_ENV_PREFIX:-dev}"
NAME_PREFIX="idp-${ENV_PREFIX}"

APP_SA="${NAME_PREFIX}-app@${PROJECT}.iam.gserviceaccount.com"
PREPROCESS_SA="${NAME_PREFIX}-preprocess@${PROJECT}.iam.gserviceaccount.com"
SECRETS_DIR="${SECRETS_DIR:-./deploy/gcp/keys}"
KEY_FILE="${SECRETS_DIR}/preprocess-sa.json"

mkdir -p "$SECRETS_DIR"

echo "==> Grant Cloud SQL Client to app SA (for cloud-sql-proxy on VM)"
gcloud projects add-iam-policy-binding "$PROJECT" \
  --member="serviceAccount:${APP_SA}" \
  --role="roles/cloudsql.client" \
  --condition=None \
  --quiet

echo "==> Create preprocess SA key: $KEY_FILE"
if [[ ! -f "$KEY_FILE" ]]; then
  gcloud iam service-accounts keys create "$KEY_FILE" \
    --iam-account="$PREPROCESS_SA" \
    --project="$PROJECT"
else
  echo "Key already exists, skipping."
fi

echo "==> Upload key + models dir to app VM (adjust user/host)"
APP_VM_IP="${APP_VM_IP:-34.21.226.104}"
echo "  scp $KEY_FILE user@${APP_VM_IP}:/opt/idp/secrets/preprocess-sa.json"
echo "  gsutil cp gs://${NAME_PREFIX}-artifacts/models/mit_unet_doctamper_best.pt /opt/idp/models/  # on VM"

if [[ -f deploy/docker/.env.prod ]]; then
  DB_URL="$(grep '^DATABASE_URL=' deploy/docker/.env.prod | cut -d= -f2-)"
  CALLBACK="$(grep '^CALLBACK_HMAC_SECRET=' deploy/docker/.env.prod | cut -d= -f2-)"
  echo "==> Sync Secret Manager (optional)"
  echo "$DB_URL" | gcloud secrets versions add "${NAME_PREFIX}-database-url" --data-file=- --project="$PROJECT" 2>/dev/null || \
    echo "  (secret ${NAME_PREFIX}-database-url — add version manually if needed)"
  echo -n "$CALLBACK" | gcloud secrets versions add "${NAME_PREFIX}-callback-hmac-secret" --data-file=- --project="$PROJECT" 2>/dev/null || \
    echo "  (secret ${NAME_PREFIX}-callback-hmac-secret — add version manually if needed)"
fi

echo "Done. Copy $KEY_FILE to VM /opt/idp/secrets/ before docker compose up."
