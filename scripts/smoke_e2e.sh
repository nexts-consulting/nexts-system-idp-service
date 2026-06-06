#!/usr/bin/env bash
set -euo pipefail

APP_URL="${APP_URL:-http://localhost:8000}"
# 1x1 red PNG
IMG_B64="iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
DATA_URL="data:image/png;base64,${IMG_B64}"

echo "==> Health checks"
for port in 8000 8001 8002 8003; do
  curl -sf "http://localhost:${port}/health" >/dev/null && echo "  OK :${port}" || echo "  FAIL :${port}"
done

echo "==> Create job"
RESP=$(curl -sf -X POST "${APP_URL}/v1/jobs" \
  -H "Content-Type: application/json" \
  -d "{\"image_urls\":[\"${DATA_URL}\"],\"invoice_type\":\"receipt\",\"tenant_id\":\"default\"}")
JOB_ID=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['job_id'])")
echo "  Job ID: ${JOB_ID}"

echo "==> Poll until terminal state (max 120s)"
for i in $(seq 1 60); do
  STATUS=$(curl -sf "${APP_URL}/v1/jobs/${JOB_ID}" | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")
  echo "  [${i}] status=${STATUS}"
  case "$STATUS" in
    COMPLETED|FRAUD_DETECTED|FAILED|EXTRACTION_FAILED) break ;;
  esac
  sleep 2
done

echo "==> Final job payload"
curl -sf "${APP_URL}/v1/jobs/${JOB_ID}" | python3 -m json.tool
echo "Smoke E2E finished."
