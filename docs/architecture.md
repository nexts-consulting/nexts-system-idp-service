# IDP Production Architecture

See the project README and [source-code-structure.md](source-code-structure.md) for full details. Summary:

## Services

| Service | Port (local) | Responsibility |
|---------|--------------|----------------|
| idp-app | 8000 | API, rules, DB, callbacks |
| idp-orchestrator | 8001 | Batching, circuit breaker, workflow |
| idp-preprocess | 8002 | Download, fraud, GCS |
| idp-extraction | 8003 | VLM batch inference (RunPod prod) |

## Data flow

1. App creates job → notifies orchestrator
2. Orchestrator enqueues preprocess task (Redis Stream)
3. Preprocess downloads URLs, runs DocTamper, uploads to GCS
4. Orchestrator batches up to 48 jobs → extraction service
5. Orchestrator webhooks app → rules engine → customer callback

## Performance baseline

InternVL3.5-8B-Flash on A100 80GB: optimal batch size **48** (~472 tokens/sec).
See `docs/performance/baseline.json`.
