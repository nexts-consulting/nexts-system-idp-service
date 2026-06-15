import time
from contextlib import asynccontextmanager
from uuid import UUID

import uvicorn
from fastapi import FastAPI, Request
from idp_common.debug_audit import DebugAuditClient, DebugInboundMiddleware, parse_job_id, parse_trace_id
from idp_common.gcs import GCSClient
from idp_common.health import router as health_router
from idp_common.logging import configure_logging
from idp_common.metrics import MetricsRegistry
from idp_common.tracing import setup_tracing
from idp_contracts.jobs import (
    BatchExtractionRequest,
    BatchExtractionResponse,
    BatchExtractionResultItem,
)

from idp_extraction.config import Settings
from idp_extraction.invoice_normalize import postprocess_extraction
from idp_extraction.invoice_schema import INVOICE_JSON_SCHEMA
from idp_extraction.lmdeploy_client import LmdeployClient
from idp_extraction.schema_validator import validate_against_schema

settings = Settings()
configure_logging(settings.log_level, settings.service_name)
setup_tracing(settings.service_name, settings.otel_exporter_endpoint)
metrics = MetricsRegistry(settings.service_name)
debug_audit = DebugAuditClient(
    settings.app_internal_url,
    settings.service_name,
    enabled=settings.debug_audit_enabled,
)

lmdeploy: LmdeployClient | None = None
gcs: GCSClient | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global lmdeploy, gcs
    lmdeploy = LmdeployClient(
        settings.lmdeploy_url,
        settings.model_name,
        settings.mock_mode,
        settings.lmdeploy_api_key,
        settings.max_new_tokens,
        settings.temperature,
        settings.default_prompt_mode,
    )
    gcs = GCSClient(settings.gcs_bucket, settings.gcs_emulator_host)
    yield


async def _forward_debug_event(event) -> None:
    await debug_audit.emit(event)


app = FastAPI(title="IDP Extraction", lifespan=lifespan)
app.add_middleware(
    DebugInboundMiddleware,
    service_name=settings.service_name,
    on_event=_forward_debug_event,
    enabled=settings.debug_audit_enabled,
)
app.include_router(health_router)


def _resolve_schema(item_schema: dict | None) -> dict:
    return item_schema if item_schema else INVOICE_JSON_SCHEMA


@app.post("/v1/batch/extract", response_model=BatchExtractionResponse)
async def batch_extract(request: BatchExtractionRequest, http_request: Request) -> BatchExtractionResponse:
    assert lmdeploy and gcs
    start = time.perf_counter()
    results: list[BatchExtractionResultItem] = []
    total_prompt = 0
    total_completion = 0
    header_trace = parse_trace_id(dict(http_request.headers))
    header_job = parse_job_id(dict(http_request.headers))

    for item in request.items:
        trace_id = item.trace_id or header_trace or header_job or item.job_id
        try:
            gcs_start = time.perf_counter()
            image_bytes = gcs.download_to_bytes(item.gcs_uri)
            await debug_audit.log(
                trace_id=trace_id,
                job_id=item.job_id,
                step="gcs.download",
                direction="outbound",
                duration_ms=int((time.perf_counter() - gcs_start) * 1000),
                status="success",
                request={"gcs_uri": item.gcs_uri},
                response={"bytes": len(image_bytes)},
            )
            raw, err, pt, ct, _, _raw_text = await lmdeploy.extract_one(
                image_bytes,
                custom_system_prompt=item.prompt if item.prompt else None,
                prompt_mode=item.prompt_mode,
                trace_id=trace_id,
                job_id=item.job_id,
                audit=debug_audit,
            )
            total_prompt += pt
            total_completion += ct
            if err or not raw:
                results.append(
                    BatchExtractionResultItem(job_id=item.job_id, error=err or "empty result")
                )
                metrics.schema_validation_failures.labels(
                    service=settings.service_name, env=settings.env
                ).inc()
                continue

            processed = postprocess_extraction(raw)
            schema = _resolve_schema(item.response_schema)
            validated, val_err = validate_against_schema(processed, schema)
            if val_err:
                metrics.schema_validation_failures.labels(
                    service=settings.service_name, env=settings.env
                ).inc()
                results.append(
                    BatchExtractionResultItem(
                        job_id=item.job_id,
                        raw_json=raw,
                        validated_json=processed,
                        error=val_err,
                        prompt_tokens=pt,
                        completion_tokens=ct,
                    )
                )
            else:
                results.append(
                    BatchExtractionResultItem(
                        job_id=item.job_id,
                        raw_json=raw,
                        validated_json=validated,
                        prompt_tokens=pt,
                        completion_tokens=ct,
                    )
                )
        except Exception as e:
            await debug_audit.log(
                trace_id=trace_id,
                job_id=item.job_id,
                step="batch.extract_item",
                direction="internal",
                status="error",
                request={"gcs_uri": item.gcs_uri, "batch_id": request.batch_id},
                error=str(e),
            )
            results.append(BatchExtractionResultItem(job_id=item.job_id, error=str(e)))

    elapsed = time.perf_counter() - start
    total_tokens = total_prompt + total_completion
    metrics.extraction_tokens.labels(
        service=settings.service_name, env=settings.env, token_type="prompt"
    ).inc(total_prompt)
    metrics.extraction_tokens.labels(
        service=settings.service_name, env=settings.env, token_type="completion"
    ).inc(total_completion)
    if elapsed > 0:
        metrics.extraction_tps.labels(
            service=settings.service_name, env=settings.env
        ).set(total_tokens / elapsed)
    metrics.batch_size.labels(service=settings.service_name, env=settings.env).observe(
        len(request.items)
    )

    return BatchExtractionResponse(
        batch_id=request.batch_id,
        results=results,
        total_time_seconds=elapsed,
    )


if __name__ == "__main__":
    uvicorn.run(app, host=settings.host, port=settings.port)
