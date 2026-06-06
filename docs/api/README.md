# API Documentation

OpenAPI spec: [packages/idp-contracts/openapi/idp-app.openapi.yaml](../../packages/idp-contracts/openapi/idp-app.openapi.yaml)

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/v1/jobs` | Create job — `image_urls`: HTTP, `gs://`, `firebase://` ([image-sources.md](../image-sources.md)) |
| GET | `/v1/jobs/{job_id}` | Get job status and results |
| POST | `/v1/rules` | Create dynamic JSON Logic rule |
| POST | `/v1/prompt-profiles` | Create versioned prompt template |
| POST | `/internal/v1/jobs/complete` | Orchestrator completion webhook (internal) |
