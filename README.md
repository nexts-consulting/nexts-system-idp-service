# Nexts System IDP Service

Production intelligent document processing (IDP) for Vietnamese invoice extraction.

## Architecture

- **idp-app** — Public API, rules engine, database, customer callbacks
- **idp-orchestrator** — Job state machine, batch accumulator (max 48), circuit breaker
- **idp-preprocess** — URL download, image normalize, DocTamper fraud detection
- **idp-extraction** — InternVL via lmdeploy (RunPod in production)

See [docs/architecture.md](docs/architecture.md).  
**Source code structure (Vietnamese):** [docs/source-code-structure.md](docs/source-code-structure.md).  
**Image datasources (Firebase / GCS / HTTP):** [docs/image-sources.md](docs/image-sources.md).  
**Extraction pipeline (prompt + normalize):** [docs/extraction-pipeline.md](docs/extraction-pipeline.md).

## Quick start (local)

```bash
cp deploy/docker/.env.example deploy/docker/.env
docker compose -f deploy/docker/docker-compose.yml up --build
```

- App API: http://localhost:8000
- Orchestrator: http://localhost:8001
- Preprocess: http://localhost:8002
- Grafana: http://localhost:3000 (admin / admin)
- Prometheus: http://localhost:9090

```bash
./scripts/smoke_e2e.sh
```

## Packages

- `packages/idp-contracts` — Shared DTOs and OpenAPI spec
- `packages/idp-common` — GCS, Redis, metrics, tracing

## Deploy

- **Production guide (Vietnamese):** [docs/deploy-production.md](docs/deploy-production.md)
- **Infrastructure overview (Vietnamese):** [docs/infrastructure.md](docs/infrastructure.md)
- GCP: `deploy/terraform/gcp/`
- RunPod: `deploy/runpod/`
