import asyncio
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

import httpx
import structlog
import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from idp_common.debug_audit import (
    DebugInboundMiddleware,
    bind_trace,
    trace_headers,
)
from idp_common.health import router as health_router
from idp_common.http_middleware import HttpAccessLogMiddleware
from idp_common.logging import configure_logging
from idp_common.metrics import MetricsRegistry
from idp_common.tracing import setup_tracing
from idp_contracts.debug import DebugRequestEvent, DebugTimelineResponse
from idp_contracts.enums import JobStage, JobStatus
from idp_contracts.jobs import (
    CreateJobRequest,
    CreateJobResponse,
    JobArtifactRecord,
    JobCompletionWebhook,
    JobDetailResponse,
    JobProgressEvent,
    JobStageRecord,
)
from idp_contracts.prompts import CreatePromptProfileRequest, PromptProfileResponse
from idp_contracts.rules import CreateRuleRequest, RuleResponse
from sqlalchemy.ext.asyncio import AsyncSession

from idp_app.callbacks import deliver_pending, enqueue_callback
from idp_app.config import Settings
from idp_app.db.session import get_db
from idp_app.prompt_resolver import resolve_prompt_from_profile
from idp_app.repository import (
    create_job,
    create_prompt_profile,
    create_rule,
    get_job,
    get_job_detail,
    get_prompt_profile,
    get_prompt_profile_by_name,
    list_debug_events,
    list_rules,
    record_job_progress,
    save_debug_event,
    save_extraction,
    save_fraud,
    save_job_result,
    update_job_status,
)
from idp_app.image_validation import validate_image_urls
from idp_app.rules_engine import apply_rules, build_rule_context

settings = Settings()
configure_logging(settings.log_level, settings.service_name)
setup_tracing(settings.service_name, settings.otel_exporter_endpoint)
metrics = MetricsRegistry(settings.service_name)
logger = structlog.get_logger()

callback_task: asyncio.Task | None = None
_STATIC_DIR = Path(__file__).resolve().parent / "static"


async def _persist_debug_event(event: DebugRequestEvent) -> None:
    if not settings.debug_audit_enabled:
        return
    from idp_app.db.session import SessionLocal

    async with SessionLocal() as session:
        await save_debug_event(session, event)


async def _callback_loop() -> None:
    from idp_app.db.session import SessionLocal

    while True:
        try:
            async with SessionLocal() as session:
                n = await deliver_pending(settings, session)
                if n:
                    metrics.callback_delivery.labels(
                        service=settings.service_name, env=settings.env, result="success"
                    ).inc(n)
        except Exception:
            pass
        await asyncio.sleep(5)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global callback_task
    callback_task = asyncio.create_task(_callback_loop())
    yield
    if callback_task:
        callback_task.cancel()


app = FastAPI(title="IDP App", lifespan=lifespan)
app.add_middleware(
    DebugInboundMiddleware,
    service_name=settings.service_name,
    on_event=_persist_debug_event,
    enabled=settings.debug_audit_enabled,
)
app.add_middleware(HttpAccessLogMiddleware)
app.include_router(health_router)


@app.post("/v1/jobs", response_model=CreateJobResponse, status_code=201)
async def create_job_endpoint(
    req: CreateJobRequest,
    db: AsyncSession = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> CreateJobResponse:
    try:
        validate_image_urls(req.image_urls)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    trace_id = uuid.uuid4()

    prompt_profile_id = req.prompt_profile_id
    if not prompt_profile_id and settings.default_prompt_profile_name:
        default_profile = await get_prompt_profile_by_name(db, settings.default_prompt_profile_name)
        if default_profile:
            prompt_profile_id = default_profile.id
            logger.info(
                "default_prompt_profile_applied",
                profile_name=default_profile.name,
                profile_id=str(default_profile.id),
            )

    job = await create_job(
        db,
        req.tenant_id,
        req.image_urls,
        req.invoice_type,
        prompt_profile_id,
        str(req.callback_url) if req.callback_url else None,
        {**req.metadata, "trace_id": str(trace_id)},
        idempotency_key,
    )
    bind_trace(trace_id, job.id)
    await _persist_debug_event(
        DebugRequestEvent(
            trace_id=trace_id,
            job_id=job.id,
            service=settings.service_name,
            step="POST /v1/jobs",
            direction="inbound",
            status="success",
            request={
                "tenant_id": req.tenant_id,
                "image_urls": req.image_urls,
                "invoice_type": req.invoice_type,
                "prompt_mode": req.prompt_mode,
            },
            response={"job_id": str(job.id), "status": JobStatus.PENDING.value},
        )
    )
    prompt = None
    prompt_mode = req.prompt_mode
    schema = None
    if prompt_profile_id:
        profile = await get_prompt_profile(db, prompt_profile_id)
        if profile:
            prompt, prompt_mode, schema = resolve_prompt_from_profile(profile, req.prompt_mode)
    async with httpx.AsyncClient(timeout=30.0) as client:
        orch_url = f"{settings.orchestrator_url}/internal/v1/jobs/start"
        payload = {
            "job_id": str(job.id),
            "tenant_id": job.tenant_id,
            "image_urls": job.image_urls,
            "invoice_type": job.invoice_type,
            "prompt": prompt,
            "prompt_mode": prompt_mode,
            "response_schema": schema,
            "trace_id": str(trace_id),
        }
        orch_start = time.perf_counter()
        try:
            resp = await client.post(
                orch_url,
                json=payload,
                headers=trace_headers(trace_id, job.id),
            )
            resp.raise_for_status()
            await _persist_debug_event(
                DebugRequestEvent(
                    trace_id=trace_id,
                    job_id=job.id,
                    service=settings.service_name,
                    step="POST /internal/v1/jobs/start",
                    direction="outbound",
                    duration_ms=int((time.perf_counter() - orch_start) * 1000),
                    status="success",
                    request={"url": orch_url, "body": payload},
                    response={"status_code": resp.status_code, "body": resp.json()},
                )
            )
        except Exception as e:
            await _persist_debug_event(
                DebugRequestEvent(
                    trace_id=trace_id,
                    job_id=job.id,
                    service=settings.service_name,
                    step="POST /internal/v1/jobs/start",
                    direction="outbound",
                    duration_ms=int((time.perf_counter() - orch_start) * 1000),
                    status="error",
                    request={"url": orch_url, "body": payload},
                    error=str(e),
                )
            )
            raise
    await record_job_progress(
        db,
        job.id,
        status=JobStatus.PENDING,
        stage=JobStage.PREPROCESS.value,
        metadata={"event": "enqueued_to_orchestrator"},
    )
    metrics.jobs_total.labels(
        service=settings.service_name, env=settings.env, status="PENDING", tenant_id=req.tenant_id
    ).inc()
    return CreateJobResponse(job_id=job.id, status=JobStatus.PENDING)


@app.get("/v1/jobs/{job_id}/debug-timeline", response_model=DebugTimelineResponse)
async def get_debug_timeline(job_id: UUID, db: AsyncSession = Depends(get_db)) -> DebugTimelineResponse:
    job = await get_job(db, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    events = await list_debug_events(db, job_id)
    trace_id_raw = job.metadata_.get("trace_id")
    trace_id = UUID(trace_id_raw) if trace_id_raw else (events[0].trace_id if events else None)
    return DebugTimelineResponse(job_id=job_id, trace_id=trace_id, events=events)


@app.get("/debug/jobs/{job_id}")
async def debug_job_page(job_id: UUID) -> FileResponse:
    html_path = _STATIC_DIR / "debug_job.html"
    if not html_path.is_file():
        raise HTTPException(404, "Debug page not found")
    return FileResponse(html_path, media_type="text/html")


@app.post("/internal/v1/debug/events")
async def ingest_debug_event(event: DebugRequestEvent, db: AsyncSession = Depends(get_db)) -> dict:
    await save_debug_event(db, event)
    return {"ok": True}


@app.get("/v1/jobs/{job_id}", response_model=JobDetailResponse)
async def get_job_endpoint(job_id: UUID, db: AsyncSession = Depends(get_db)) -> JobDetailResponse:
    job = await get_job_detail(db, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    fraud = None
    if job.fraud_result:
        fr = job.fraud_result
        from idp_contracts.jobs import FraudResult

        fraud = FraudResult(
            tamper_ratio=fr.tamper_ratio,
            tamper_pixels=fr.tamper_pixels,
            total_pixels=fr.total_pixels,
            predicted_tampered=fr.predicted_tampered,
            mask_gcs_uri=fr.mask_gcs_uri,
        )
    extraction = job.extraction_result.validated_json if job.extraction_result else None
    rules = job.job_result.rules_result if job.job_result else None
    return JobDetailResponse(
        job_id=job.id,
        tenant_id=job.tenant_id,
        status=job.status if isinstance(job.status, JobStatus) else JobStatus(job.status),
        image_urls=job.image_urls,
        invoice_type=job.invoice_type,
        prompt_profile_id=job.prompt_profile_id,
        callback_url=job.callback_url,
        metadata=job.metadata_,
        fraud_result=fraud,
        extraction_result=extraction,
        rules_result=rules,
        stages=[
            JobStageRecord(
                stage=s.stage,
                started_at=s.started_at,
                ended_at=s.ended_at,
                error_code=s.error_code,
                metadata=s.metadata_,
            )
            for s in job.stages
        ],
        artifacts=[
            JobArtifactRecord(
                artifact_type=a.artifact_type,
                gcs_uri=a.gcs_uri,
                created_at=a.created_at,
            )
            for a in job.artifacts
        ],
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


@app.post("/internal/v1/jobs/progress")
async def job_progress(event: JobProgressEvent, db: AsyncSession = Depends(get_db)) -> dict:
    job = await get_job(db, event.job_id)
    if not job:
        logger.warning("job_progress_not_found", job_id=str(event.job_id))
        raise HTTPException(404, "Job not found")
    await record_job_progress(
        db,
        event.job_id,
        status=event.status,
        stage=event.stage.value if event.stage else None,
        end_stage=event.end_stage,
        artifacts=[(a.artifact_type, a.gcs_uri) for a in event.artifacts],
        metadata=event.metadata,
        error_code=event.error_code,
    )
    logger.info(
        "job_progress_recorded",
        job_id=str(event.job_id),
        status=event.status.value if event.status else None,
        stage=event.stage.value if event.stage else None,
    )
    return {"ok": True}


@app.post("/internal/v1/jobs/complete")
async def job_complete(webhook: JobCompletionWebhook, db: AsyncSession = Depends(get_db)) -> dict:
    job = await get_job(db, webhook.job_id)
    if not job:
        logger.warning(
            "job_complete_not_found",
            job_id=str(webhook.job_id),
            tenant_id=webhook.tenant_id,
            status=webhook.status.value,
        )
        raise HTTPException(404, "Job not found")

    status = webhook.status
    if webhook.fraud_result:
        await save_fraud(
            db,
            webhook.job_id,
            {
                "tamper_ratio": webhook.fraud_result.tamper_ratio,
                "tamper_pixels": webhook.fraud_result.tamper_pixels,
                "total_pixels": webhook.fraud_result.total_pixels,
                "predicted_tampered": webhook.fraud_result.predicted_tampered,
                "mask_gcs_uri": webhook.fraud_result.mask_gcs_uri,
            },
        )
        if webhook.fraud_result.mask_gcs_uri:
            await record_job_progress(
                db,
                webhook.job_id,
                artifacts=[("fraud_mask", webhook.fraud_result.mask_gcs_uri)],
            )
        await record_job_progress(
            db,
            webhook.job_id,
            end_stage=True,
            stage=JobStage.FRAUD.value,
            status=JobStatus.FRAUD_DETECTED,
        )
    validated_json = webhook.extraction_result
    raw_json = webhook.raw_json if webhook.raw_json is not None else webhook.extraction_result
    if validated_json is not None or raw_json is not None:
        row = await save_extraction(
            db,
            webhook.job_id,
            {
                "validated_json": validated_json,
                "raw_json": raw_json,
                "batch_id": webhook.batch_id,
                "prompt_tokens": webhook.prompt_tokens,
                "completion_tokens": webhook.completion_tokens,
                "model_version": webhook.model_version,
            },
        )
        logger.info(
            "extraction_result_saved",
            job_id=str(webhook.job_id),
            batch_id=webhook.batch_id,
            has_validated=validated_json is not None,
            has_raw=raw_json is not None,
        )
    elif status in (JobStatus.EXTRACTED, JobStatus.EXTRACTION_FAILED):
        logger.warning(
            "extraction_result_missing",
            job_id=str(webhook.job_id),
            status=status.value,
            error=webhook.error,
        )

    has_extraction = validated_json is not None or raw_json is not None
    await record_job_progress(
        db,
        webhook.job_id,
        end_stage=True,
        stage=JobStage.EXTRACT.value,
        status=JobStatus.EXTRACTED if has_extraction and not webhook.error else webhook.status,
        metadata={
            **({"batch_id": webhook.batch_id} if webhook.batch_id else {}),
            **({"error": webhook.error} if webhook.error else {}),
        },
    )

    rules_db = await list_rules(db, webhook.tenant_id)
    context = build_rule_context(
        webhook.extraction_result,
        webhook.fraud_result.model_dump() if webhook.fraud_result else None,
        job.metadata_,
    )
    rules_result = apply_rules(
        [
            {
                "name": r.name,
                "priority": r.priority,
                "condition_json": r.condition_json,
                "action_json": r.action_json,
                "enabled": r.enabled,
            }
            for r in rules_db
        ],
        context,
    )
    final_status = JobStatus.COMPLETED
    if rules_result["actions"].get("blocked"):
        final_status = JobStatus.FRAUD_DETECTED if webhook.fraud_result else JobStatus.FAILED
    elif status in (JobStatus.EXTRACTION_FAILED, JobStatus.FAILED):
        final_status = status
    elif status == JobStatus.FRAUD_DETECTED:
        final_status = JobStatus.FRAUD_DETECTED
    else:
        final_status = JobStatus.COMPLETED

    final_payload = {
        "job_id": str(webhook.job_id),
        "status": final_status.value,
        "extraction": webhook.extraction_result,
        "fraud": webhook.fraud_result.model_dump() if webhook.fraud_result else None,
        "rules": rules_result,
    }
    await save_job_result(db, webhook.job_id, rules_result, final_payload)
    await record_job_progress(
        db,
        webhook.job_id,
        end_stage=True,
        stage=JobStage.RULES.value,
        status=JobStatus.RULES_APPLIED,
        metadata={"rules": rules_result},
    )
    await update_job_status(db, webhook.job_id, final_status.value)
    metrics.jobs_total.labels(
        service=settings.service_name,
        env=settings.env,
        status=final_status.value,
        tenant_id=webhook.tenant_id,
    ).inc()

    if job.callback_url:
        await enqueue_callback(db, webhook.job_id, job.callback_url, final_payload)
        await record_job_progress(
            db,
            webhook.job_id,
            stage=JobStage.CALLBACK.value,
            metadata={"callback_url": job.callback_url},
        )

    logger.info(
        "job_completed",
        job_id=str(webhook.job_id),
        final_status=final_status.value,
    )
    return {"status": final_status.value}


@app.post("/v1/rules", response_model=RuleResponse, status_code=201)
async def create_rule_endpoint(req: CreateRuleRequest, db: AsyncSession = Depends(get_db)) -> RuleResponse:
    rule = await create_rule(
        db,
        {
            "tenant_id": req.tenant_id,
            "name": req.name,
            "priority": req.priority,
            "condition_json": req.condition_json,
            "action_json": req.action_json,
            "enabled": req.enabled,
        },
    )
    return RuleResponse(
        id=rule.id,
        tenant_id=rule.tenant_id,
        name=rule.name,
        priority=rule.priority,
        condition_json=rule.condition_json,
        action_json=rule.action_json,
        enabled=rule.enabled,
        created_at=rule.created_at,
        updated_at=rule.updated_at,
    )


@app.post("/v1/prompt-profiles", response_model=PromptProfileResponse, status_code=201)
async def create_prompt_profile_endpoint(
    req: CreatePromptProfileRequest, db: AsyncSession = Depends(get_db)
) -> PromptProfileResponse:
    profile = await create_prompt_profile(
        db,
        {
            "name": req.name,
            "invoice_type": req.invoice_type,
            "version": req.version,
            "system_prompt": req.system_prompt,
            "user_template": req.user_template,
            "json_schema": req.json_schema,
            "enabled": req.enabled,
        },
    )
    return PromptProfileResponse(
        id=profile.id,
        name=profile.name,
        invoice_type=profile.invoice_type,
        version=profile.version,
        system_prompt=profile.system_prompt,
        user_template=profile.user_template,
        json_schema=profile.json_schema,
        enabled=profile.enabled,
        created_at=profile.created_at,
    )


if __name__ == "__main__":
    uvicorn.run(app, host=settings.host, port=settings.port)
