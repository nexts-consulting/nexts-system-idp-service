#!/usr/bin/env bash
# Start idp-extraction on RunPod (after lmdeploy is healthy on :23333).
set -euo pipefail

ROOT="${IDP_ROOT:-$(cd "$(dirname "$0")/../.." && pwd)}"
cd "$ROOT/services/idp-extraction"

export MOCK_MODE="${MOCK_MODE:-false}"
export LMDEPLOY_URL="${LMDEPLOY_URL:-http://127.0.0.1:23333}"
export GCS_BUCKET="${GCS_BUCKET:-idp-dev-artifacts}"
export DEFAULT_PROMPT_MODE="${DEFAULT_PROMPT_MODE:-reasoning_vir}"
export MODEL_NAME="${MODEL_NAME:-OpenGVLab/InternVL3_5-8B-Flash}"

# Ensure workspace packages are importable (editable install recommended once per pod).
if ! python -c "import idp_extraction" 2>/dev/null; then
  echo "Installing idp-extraction and workspace deps..."
  pip install -q uv
  uv pip install --system -e "$ROOT/packages/idp-contracts" -e "$ROOT/packages/idp-common" -e .
fi

echo "Starting idp-extraction on :8003 (MOCK_MODE=$MOCK_MODE, LMDEPLOY_URL=$LMDEPLOY_URL)"
exec python -m idp_extraction.main
