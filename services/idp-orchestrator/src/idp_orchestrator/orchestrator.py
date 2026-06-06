import asyncio
import time
import uuid

import httpx
import structlog
from idp_contracts.enums import JobStatus
from idp_contracts.jobs import (
    BatchExtractionRequest,
    JobCompletionWebhook,
    PreprocessResultMessage,
    PreprocessTaskMessage,
)

from idp_orchestrator.batch_accumulator import BatchAccumulator, PendingJob
from idp_orchestrator.circuit_breaker import CircuitBreaker
from idp_orchestrator.config import Settings

logger = structlog.get_logger()

DEFAULT_PROMPT_MODE = "reasoning_vir"


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
    ) -> None:
        await self.redis.publish(
            self.redis.PREPROCESS_TASKS,
            PreprocessTaskMessage(
                job_id=job_id,
                tenant_id=tenant_id,
                image_urls=image_urls,
                invoice_type=invoice_type,
            ).model_dump(mode="json"),
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
        while self._running:
            messages = await self.redis.read_group(
                self.redis.PREPROCESS_RESULTS,
                self.redis.ORCHESTRATOR_GROUP,
                consumer,
                count=10,
                block_ms=3000,
            )
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
        schema = None
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
            )
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
        start = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=300.0) as client:
                warm = await self.redis.get_key(self.settings.runpod_warm_key)
                self.metrics.runpod_warm.labels(
                    service=self.settings.service_name, env=self.settings.env
                ).set(1.0 if warm == "1" else 0.0)
                resp = await client.post(
                    f"{self.settings.extraction_url}/v1/batch/extract",
                    json=request.model_dump(mode="json"),
                )
                if resp.status_code >= 500:
                    raise httpx.HTTPStatusError("extraction error", request=resp.request, response=resp)
                resp.raise_for_status()
                data = resp.json()
            self.circuit.record_success()
            elapsed = time.perf_counter() - start
            total_tokens = sum(
                r.get("prompt_tokens", 0) + r.get("completion_tokens", 0) for r in data.get("results", [])
            )
            if elapsed > 0:
                self.metrics.extraction_tps.labels(
                    service=self.settings.service_name, env=self.settings.env
                ).set(total_tokens / elapsed)
            for item in data.get("results", []):
                jid = uuid.UUID(item["job_id"])
                if item.get("error"):
                    await self._complete_job(
                        jid, "default", JobStatus.EXTRACTION_FAILED, error=item["error"]
                    )
                else:
                    await self._complete_job(
                        jid,
                        "default",
                        JobStatus.EXTRACTED,
                        extraction_result=item.get("validated_json") or item.get("raw_json"),
                    )
        except Exception as e:
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
        normalized_gcs_uri: str | None = None,
        error: str | None = None,
    ) -> None:
        webhook = JobCompletionWebhook(
            job_id=job_id,
            tenant_id=tenant_id,
            status=status,
            fraud_result=fraud_result,
            extraction_result=extraction_result,
            normalized_gcs_uri=normalized_gcs_uri,
            error=error,
        )
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                await client.post(
                    f"{self.settings.app_internal_url}/internal/v1/jobs/complete",
                    json=webhook.model_dump(mode="json"),
                )
        except Exception as e:
            logger.error("app_webhook_failed", job_id=str(job_id), error=str(e))

    def stop(self) -> None:
        self._running = False
