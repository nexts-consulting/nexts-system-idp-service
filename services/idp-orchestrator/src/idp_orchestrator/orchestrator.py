import asyncio
import time
import uuid

import httpx
import redis.asyncio as redis
import structlog
from idp_common.debug_audit import DebugAuditClient, trace_headers
from idp_common.job_progress import report_job_progress
from idp_contracts.enums import JobStage, JobStatus
from idp_contracts.jobs import (
    BatchExtractionRequest,
    JobCompletionWebhook,
    JobProgressEvent,
    PreprocessResultMessage,
    PreprocessTaskMessage,
)

from idp_orchestrator.batch_accumulator import BatchAccumulator, PendingJob
from idp_orchestrator.circuit_breaker import CircuitBreaker
from idp_orchestrator.config import Settings

logger = structlog.get_logger()

DEFAULT_PROMPT_MODE = "reasoning"


class OrchestratorService:
    def __init__(self, settings: Settings, redis, metrics) -> None:
        self.settings = settings
        self.redis = redis
        self.metrics = metrics
        self.accumulator = BatchAccumulator(settings.batch_max_size, settings.batch_max_wait_ms)
        self.circuit = CircuitBreaker(
            settings.circuit_failure_threshold,
            settings.circuit_open_seconds,
        )
        self.audit = DebugAuditClient(
            settings.app_internal_url,
            settings.service_name,
            enabled=settings.debug_audit_enabled,
        )
        self._running = False

    async def start_job(
        self,
        job_id: uuid.UUID,
        tenant_id: str,
        image_urls: list[str],
        invoice_type: str | None = None,
        prompt: str | None = None,
        prompt_mode: str | None = None,
        response_schema: dict | None = None,
        trace_id: uuid.UUID | None = None,
    ) -> None:
        if trace_id:
            await self.redis.set_key(f"job:{job_id}:trace_id", str(trace_id))
        msg_id = await self.redis.publish(
            self.redis.PREPROCESS_TASKS,
            PreprocessTaskMessage(
                job_id=job_id,
                tenant_id=tenant_id,
                image_urls=image_urls,
                invoice_type=invoice_type,
                trace_id=trace_id,
            ).model_dump(mode="json"),
        )
        logger.info("job_enqueued", job_id=str(job_id), stream_msg_id=msg_id)
        if trace_id:
            await self.audit.log(
                trace_id=trace_id,
                job_id=job_id,
                step="redis.publish preprocess_tasks",
                direction="internal",
                status="success",
                request={
                    "stream": self.redis.PREPROCESS_TASKS,
                    "msg_id": msg_id,
                    "image_count": len(image_urls),
                },
            )
        await self.redis.set_key(f"job:{job_id}:prompt", prompt or "")
        await self.redis.set_key(
            f"job:{job_id}:prompt_mode", prompt_mode or DEFAULT_PROMPT_MODE
        )
        if response_schema:
            import json

            await self.redis.set_key(f"job:{job_id}:schema", json.dumps(response_schema))

    async def run_forever(self) -> None:
        self._running = True
        await self.redis.ensure_groups()
        asyncio.create_task(self._batch_loop())
        consumer = self.settings.consumer_name
        logger.info("orchestrator_worker_started", consumer=consumer)
        while self._running:
            try:
                messages = await self.redis.read_group(
                    self.redis.PREPROCESS_RESULTS,
                    self.redis.ORCHESTRATOR_GROUP,
                    consumer,
                    count=10,
                    block_ms=3000,
                )
            except (redis.ConnectionError, redis.ResponseError, OSError) as e:
                logger.warning("orchestrator_redis_error", error=str(e))
                try:
                    await self.redis.ensure_groups()
                except Exception:
                    logger.exception("orchestrator_redis_recovery_failed")
                await asyncio.sleep(1)
                continue
            for msg_id, data in messages:
                try:
                    result = PreprocessResultMessage.model_validate(data)
                    await self._handle_preprocess(result)
                    await self.redis.ack(
                        self.redis.PREPROCESS_RESULTS,
                        self.redis.ORCHESTRATOR_GROUP,
                        msg_id,
                    )
                except Exception as e:
                    logger.exception("orchestrator_preprocess_error", error=str(e))

    async def _handle_preprocess(self, result: PreprocessResultMessage) -> None:
        job_id = str(result.job_id)
        if result.status == JobStatus.FRAUD_DETECTED:
            await self._complete_job(
                result.job_id,
                result.tenant_id,
                JobStatus.FRAUD_DETECTED,
                fraud_result=result.fraud_result,
                normalized_gcs_uri=result.normalized_gcs_uri,
            )
            return
        if result.status == JobStatus.FAILED or not result.normalized_gcs_uri:
            await self._complete_job(
                result.job_id,
                result.tenant_id,
                JobStatus.FAILED,
                error=result.error or "preprocess failed",
            )
            return
        prompt = await self.redis.get_key(f"job:{job_id}:prompt") or ""
        prompt_mode = await self.redis.get_key(f"job:{job_id}:prompt_mode") or DEFAULT_PROMPT_MODE
        schema_raw = await self.redis.get_key(f"job:{job_id}:schema")
        trace_raw = await self.redis.get_key(f"job:{job_id}:trace_id")
        schema = None
        trace_id = uuid.UUID(trace_raw) if trace_raw else None
        if schema_raw:
            import json

            schema = json.loads(schema_raw)
        await self.accumulator.add(
            PendingJob(
                job_id=job_id,
                tenant_id=result.tenant_id,
                gcs_uri=result.normalized_gcs_uri,
                prompt=prompt,
                prompt_mode=prompt_mode,
                response_schema=schema,
                invoice_type=None,
                trace_id=str(trace_id) if trace_id else None,
            )
        )
        await self._report_progress(
            result.job_id,
            result.tenant_id,
            status=JobStatus.BATCHING,
            stage=JobStage.BATCH_WAIT,
            end_stage=True,
        )

    async def _report_progress(
        self,
        job_id: uuid.UUID,
        tenant_id: str,
        *,
        status: JobStatus | None = None,
        stage: JobStage | None = None,
        end_stage: bool = False,
        metadata: dict | None = None,
        error_code: str | None = None,
    ) -> None:
        await report_job_progress(
            self.settings.app_internal_url,
            JobProgressEvent(
                job_id=job_id,
                tenant_id=tenant_id,
                status=status,
                stage=stage,
                end_stage=end_stage,
                metadata=metadata or {},
                error_code=error_code,
            ),
        )

    async def _batch_loop(self) -> None:
        while self._running:
            jobs = await self.accumulator.wait_and_flush()
            if not jobs:
                continue
            batch_size = len(jobs)
            self.metrics.batch_size.labels(
                service=self.settings.service_name, env=self.settings.env
            ).observe(batch_size)
            self.metrics.batch_utilization.labels(
                service=self.settings.service_name, env=self.settings.env
            ).set(batch_size / 48.0)
            wait_time = time.time() - jobs[0].added_at
            self.metrics.batch_wait.labels(
                service=self.settings.service_name, env=self.settings.env
            ).observe(wait_time)
            if not self.circuit.allow_request():
                self.metrics.circuit_breaker.labels(
                    service=self.settings.service_name, env=self.settings.env
                ).set(self.circuit.state_value())
                for job in jobs:
                    await self._complete_job(
                        uuid.UUID(job.job_id),
                        job.tenant_id,
                        JobStatus.EXTRACTION_FAILED,
                        error="circuit breaker open",
                    )
                continue
            await self._send_batch(jobs)

    async def _send_batch(self, jobs: list[PendingJob]) -> None:
        batch_id, items = self.accumulator.to_batch_request(jobs)
        request = BatchExtractionRequest(batch_id=batch_id, items=items)
        for job in jobs:
            await self._report_progress(
                uuid.UUID(job.job_id),
                job.tenant_id,
                status=JobStatus.EXTRACTING,
                stage=JobStage.EXTRACT,
                end_stage=True,
                metadata={"batch_id": batch_id, "batch_size": len(jobs)},
            )
        start = time.perf_counter()
        ext_url = f"{self.settings.extraction_url}/v1/batch/extract"
        headers = trace_headers(jobs[0].trace_id or jobs[0].job_id, jobs[0].job_id)
        try:
            async with httpx.AsyncClient(timeout=300.0) as client:
                warm = await self.redis.get_key(self.settings.runpod_warm_key)
                self.metrics.runpod_warm.labels(
                    service=self.settings.service_name, env=self.settings.env
                ).set(1.0 if warm == "1" else 0.0)
                resp = await client.post(
                    ext_url,
                    json=request.model_dump(mode="json"),
                    headers=headers,
                )
                if resp.status_code >= 500:
                    raise httpx.HTTPStatusError("extraction error", request=resp.request, response=resp)
                resp.raise_for_status()
                data = resp.json()
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            for job in jobs:
                tid = uuid.UUID(job.trace_id) if job.trace_id else uuid.UUID(job.job_id)
                await self.audit.log(
                    trace_id=tid,
                    job_id=uuid.UUID(job.job_id),
                    step="POST /v1/batch/extract",
                    direction="outbound",
                    duration_ms=elapsed_ms,
                    status="success",
                    request={
                        "url": ext_url,
                        "batch_id": batch_id,
                        "batch_size": len(jobs),
                    },
                    response={
                        "status_code": resp.status_code,
                        "result_count": len(data.get("results", [])),
                        "total_time_seconds": data.get("total_time_seconds"),
                    },
                )
            self.circuit.record_success()
            elapsed = time.perf_counter() - start
            total_tokens = sum(
                r.get("prompt_tokens", 0) + r.get("completion_tokens", 0) for r in data.get("results", [])
            )
            if elapsed > 0:
                self.metrics.extraction_tps.labels(
                    service=self.settings.service_name, env=self.settings.env
                ).set(total_tokens / elapsed)
            tenant_by_job = {job.job_id: job.tenant_id for job in jobs}
            for item in data.get("results", []):
                jid = uuid.UUID(item["job_id"])
                tenant_id = tenant_by_job.get(str(jid), "default")
                validated = item.get("validated_json")
                raw = item.get("raw_json")
                if item.get("error"):
                    await self._complete_job(
                        jid,
                        tenant_id,
                        JobStatus.EXTRACTION_FAILED,
                        error=item["error"],
                        extraction_result=validated,
                        raw_json=raw,
                        batch_id=batch_id,
                        prompt_tokens=item.get("prompt_tokens", 0),
                        completion_tokens=item.get("completion_tokens", 0),
                        model_version=request.model,
                    )
                else:
                    await self._complete_job(
                        jid,
                        tenant_id,
                        JobStatus.EXTRACTED,
                        extraction_result=validated or raw,
                        raw_json=raw,
                        batch_id=batch_id,
                        prompt_tokens=item.get("prompt_tokens", 0),
                        completion_tokens=item.get("completion_tokens", 0),
                        model_version=request.model,
                    )
        except Exception as e:
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            for job in jobs:
                tid = uuid.UUID(job.trace_id) if job.trace_id else uuid.UUID(job.job_id)
                await self.audit.log(
                    trace_id=tid,
                    job_id=uuid.UUID(job.job_id),
                    step="POST /v1/batch/extract",
                    direction="outbound",
                    duration_ms=elapsed_ms,
                    status="error",
                    request={"url": ext_url, "batch_id": batch_id},
                    error=str(e),
                )
            self.circuit.record_failure()
            self.metrics.circuit_breaker.labels(
                service=self.settings.service_name, env=self.settings.env
            ).set(self.circuit.state_value())
            logger.exception("batch_extraction_failed", error=str(e))
            for job in jobs:
                await self._complete_job(
                    uuid.UUID(job.job_id),
                    job.tenant_id,
                    JobStatus.EXTRACTION_FAILED,
                    error=str(e),
                )

    async def _complete_job(
        self,
        job_id: uuid.UUID,
        tenant_id: str,
        status: JobStatus,
        fraud_result=None,
        extraction_result=None,
        raw_json=None,
        normalized_gcs_uri: str | None = None,
        error: str | None = None,
        batch_id: str | None = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        model_version: str | None = None,
    ) -> None:
        webhook = JobCompletionWebhook(
            job_id=job_id,
            tenant_id=tenant_id,
            status=status,
            fraud_result=fraud_result,
            extraction_result=extraction_result,
            raw_json=raw_json,
            normalized_gcs_uri=normalized_gcs_uri,
            error=error,
            batch_id=batch_id,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            model_version=model_version,
        )
        complete_url = f"{self.settings.app_internal_url}/internal/v1/jobs/complete"
        trace_raw = await self.redis.get_key(f"job:{job_id}:trace_id")
        trace_id = uuid.UUID(trace_raw) if trace_raw else job_id
        wh_start = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    complete_url,
                    json=webhook.model_dump(mode="json"),
                    headers=trace_headers(trace_id, job_id),
                )
                wh_status = "success" if resp.status_code < 400 else "error"
                await self.audit.log(
                    trace_id=trace_id,
                    job_id=job_id,
                    step="POST /internal/v1/jobs/complete",
                    direction="outbound",
                    duration_ms=int((time.perf_counter() - wh_start) * 1000),
                    status=wh_status,
                    request={
                        "url": complete_url,
                        "status": status.value,
                        "batch_id": batch_id,
                        "error": error,
                    },
                    response={"status_code": resp.status_code, "body_preview": resp.text[:300]},
                    error=None if wh_status == "success" else resp.text[:300],
                )
                if resp.status_code >= 400:
                    logger.error(
                        "app_webhook_failed",
                        job_id=str(job_id),
                        status_code=resp.status_code,
                        response_body=resp.text[:500],
                    )
        except Exception as e:
            await self.audit.log(
                trace_id=trace_id,
                job_id=job_id,
                step="POST /internal/v1/jobs/complete",
                direction="outbound",
                duration_ms=int((time.perf_counter() - wh_start) * 1000),
                status="error",
                request={"url": complete_url, "status": status.value},
                error=str(e),
            )
            logger.error("app_webhook_failed", job_id=str(job_id), error=str(e))

    def stop(self) -> None:
        self._running = False
