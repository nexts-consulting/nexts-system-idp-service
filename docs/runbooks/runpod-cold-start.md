# RunPod Cold Start Runbook

## Symptoms

- Jobs stuck in `BATCHING` or `EXTRACTING`
- `idp_circuit_breaker_state` = 1 (OPEN)
- `idp_runpod_warm` = 0

## Steps

1. Start RunPod pod from template `deploy/runpod/pod-template.json`
2. Wait for lmdeploy health: `curl http://RUNPOD_HOST:23333/health` (or v1/models)
3. Set extraction env: `MOCK_MODE=false`, `LMDEPLOY_URL=http://localhost:23333`
4. Warm orchestrator: `POST http://orchestrator:8001/internal/runpod/warm?warm=true`
5. Verify `idp_extraction_tokens_per_second` approaches baseline (~472 @ BS=48)

## Scale to zero

When idle &gt; 30 min, stop RunPod pod. Orchestrator queues jobs in Redis until warm.
