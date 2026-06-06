#!/bin/bash
set -euo pipefail

MODEL="${MODEL:-OpenGVLab/InternVL3_5-8B-Flash}"
PORT="${PORT:-23333}"

echo "Starting lmdeploy for ${MODEL} on port ${PORT}"
lmdeploy serve api_server "${MODEL}" \
  --server-port "${PORT}" \
  --tp 1 \
  --session-len 8192
