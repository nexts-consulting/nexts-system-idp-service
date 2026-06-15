import asyncio
import time
import uuid

import redis.asyncio as redis
import structlog
from idp_common.debug_audit import DebugAuditClient, redact_url
from idp_common.job_progress import report_job_progress
from idp_contracts.enums import JobStage, JobStatus
from idp_contracts.jobs import (
    FraudResult,
    JobArtifactInput,
    JobProgressEvent,
    PreprocessResultMessage,
    PreprocessTaskMessage,
)

from idp_preprocess.config import Settings
from idp_common.image_fetcher import ImageFetcher
from idp_preprocess.downloader import normalize_image
from idp_preprocess.models.doctamper import DocTamperModel

logger = structlog.get_logger()


class PreprocessWorker:
    def __init__(
        self,
        settings: Settings,
        redis_streams,
        gcs,
        metrics,
        doctamper: DocTamperModel | None,
    ) -> None:
        self.settings = settings
        self.redis = redis_streams
        self.gcs = gcs
        self.metrics = metrics
        self.doctamper = doctamper
        from google.cloud import storage

        gcs_client = storage.Client(project=settings.firebase_project_id or None)
        self.image_fetcher = ImageFetcher(
            max_concurrent=settings.max_concurrent_downloads,
            max_bytes=settings.max_download_bytes,
            metrics=metrics,
            env=settings.env,
            service_label=settings.service_name,
            allowed_buckets=settings.parsed_allowed_buckets(),
            allowed_http_hosts=settings.parsed_allowed_hosts(),
            gcs_client=gcs_client,
        )
        self.audit = DebugAuditClient(
            settings.app_internal_url,
            settings.service_name,
            enabled=settings.debug_audit_enabled,
        )
        self._running = False

    async def _report_progress(
        self,
        job_id: uuid.UUID,
        tenant_id: str,
        *,
        status: JobStatus | None = None,
        stage: JobStage | None = None,
        end_stage: bool = False,
        artifacts: list[JobArtifactInput] | None = None,
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
                artifacts=artifacts or [],
                metadata=metadata or {},
                error_code=error_code,
            ),
        )

    async def run_forever(self) -> None:
        self._running = True
        await self.redis.ensure_groups()
        consumer = self.settings.consumer_name
        reclaimed = await self.redis.reclaim_stale(
            self.redis.PREPROCESS_TASKS,
            self.redis.PREPROCESS_GROUP,
            consumer,
        )
        if reclaimed:
            logger.info("preprocess_reclaimed_stale", count=len(reclaimed))
        logger.info("preprocess_worker_started", consumer=consumer)
        idle_polls = 0
        while self._running:
            try:
                messages = await self.redis.read_group(
                    self.redis.PREPROCESS_TASKS,
                    self.redis.PREPROCESS_GROUP,
                    consumer,
                    count=5,
                    block_ms=5000,
                )
            except (
                redis.ConnectionError,
                redis.TimeoutError,
                redis.ResponseError,
                OSError,
            ) as e:
                logger.warning("preprocess_redis_error", error=str(e))
                try:
                    await self.redis.reconnect()
                except Exception:
                    logger.exception("preprocess_redis_recovery_failed")
                await asyncio.sleep(1)
                continue
            except Exception:
                logger.exception("preprocess_read_unexpected")
                await asyncio.sleep(1)
                continue

            if reclaimed:
                messages = reclaimed + messages
                reclaimed = []

            if not messages:
                idle_polls += 1
                if idle_polls % 12 == 0:
                    logger.info(
                        "preprocess_worker_heartbeat",
                        queue_len=await self.redis.stream_length(self.redis.PREPROCESS_TASKS),
                        pending=await self.redis.get_stream_lag(
                            self.redis.PREPROCESS_TASKS,
                            self.redis.PREPROCESS_GROUP,
                        ),
                    )
                continue
            idle_polls = 0
            for msg_id, data in messages:
                try:
                    task = PreprocessTaskMessage.model_validate(data)
                    logger.info("preprocess_task_started", job_id=str(task.job_id))
                    trace_id = task.trace_id or task.job_id
                    await self.audit.log(
                        trace_id=trace_id,
                        job_id=task.job_id,
                        step="redis.consume preprocess_tasks",
                        direction="internal",
                        status="success",
                        request={"stream": self.redis.PREPROCESS_TASKS, "msg_id": msg_id},
                    )
                    result = await self.process_task(task)
                    logger.info(
                        "preprocess_task_done",
                        job_id=str(task.job_id),
                        status=result.status.value,
                    )
                    await self.redis.publish(
                        self.redis.PREPROCESS_RESULTS,
                        result.model_dump(mode="json"),
                    )
                    await self.audit.log(
                        trace_id=trace_id,
                        job_id=task.job_id,
                        step="redis.publish preprocess_results",
                        direction="internal",
                        status="success",
                        request={"stream": self.redis.PREPROCESS_RESULTS},
                        response={"status": result.status.value},
                    )
                    await self.redis.ack(
                        self.redis.PREPROCESS_TASKS,
                        self.redis.PREPROCESS_GROUP,
                        msg_id,
                    )
                except Exception as e:
                    logger.exception("preprocess_failed", error=str(e), msg_id=msg_id)
                    job_id = data.get("job_id", str(uuid.uuid4()))
                    fail = PreprocessResultMessage(
                        job_id=job_id,
                        tenant_id=data.get("tenant_id", "default"),
                        status=JobStatus.FAILED,
                        error=str(e),
                    )
                    await self.redis.publish(
                        self.redis.PREPROCESS_RESULTS,
                        fail.model_dump(mode="json"),
                    )
                    await self.redis.ack(
                        self.redis.PREPROCESS_TASKS,
                        self.redis.PREPROCESS_GROUP,
                        msg_id,
                    )

    async def process_task(self, task: PreprocessTaskMessage) -> PreprocessResultMessage:
        start = time.perf_counter()
        job_id = str(task.job_id)
        trace_id = task.trace_id or task.job_id
        await self._report_progress(
            task.job_id,
            task.tenant_id,
            status=JobStatus.DOWNLOADING,
            stage=JobStage.DOWNLOAD,
            end_stage=True,
        )
        dl_start = time.perf_counter()
        images = await self.image_fetcher.fetch_all(task.image_urls)
        await self.audit.log(
            trace_id=trace_id,
            job_id=task.job_id,
            step="download images",
            direction="outbound",
            duration_ms=int((time.perf_counter() - dl_start) * 1000),
            status="success",
            request={"urls": [redact_url(u) for u in task.image_urls], "count": len(task.image_urls)},
            response={"image_count": len(images), "bytes": sum(len(i) for i in images)},
        )
        await self._report_progress(
            task.job_id,
            task.tenant_id,
            status=JobStatus.PREPROCESSING,
            stage=JobStage.PREPROCESS,
            end_stage=True,
        )
        primary = images[0]
        normalized = normalize_image(primary, self.settings.max_image_edge)
        gcs_start = time.perf_counter()
        original_uri = self.gcs.upload_bytes(
            f"jobs/{job_id}/original.jpg", primary, "image/jpeg"
        )
        normalized_uri = self.gcs.upload_bytes(
            f"jobs/{job_id}/normalized.jpg", normalized, "image/jpeg"
        )
        await self.audit.log(
            trace_id=trace_id,
            job_id=task.job_id,
            step="gcs.upload artifacts",
            direction="outbound",
            duration_ms=int((time.perf_counter() - gcs_start) * 1000),
            status="success",
            request={"original_bytes": len(primary), "normalized_bytes": len(normalized)},
            response={"original_uri": original_uri, "normalized_uri": normalized_uri},
        )
        self.metrics.observe_stage(self.settings.env, "download", time.perf_counter() - start)

        fraud_result: FraudResult | None = None
        status = JobStatus.READY_FOR_EXTRACTION

        if self.settings.fraud_enabled and self.doctamper:
            await self._report_progress(
                task.job_id,
                task.tenant_id,
                status=JobStatus.PREPROCESSING,
                stage=JobStage.FRAUD,
                end_stage=True,
            )
            t0 = time.perf_counter()
            infer = self.doctamper.infer_bytes(normalized)
            fraud_ms = int((time.perf_counter() - t0) * 1000)
            await self.audit.log(
                trace_id=trace_id,
                job_id=task.job_id,
                step="doctamper.infer",
                direction="internal",
                duration_ms=fraud_ms,
                status="success",
                request={"image_bytes": len(normalized)},
                response={
                    "tamper_ratio": infer["tamper_ratio"],
                    "predicted_tampered": infer["predicted_tampered"],
                },
            )
            self.metrics.observe_stage(self.settings.env, "fraud", time.perf_counter() - t0)
            self.metrics.tamper_ratio.labels(
                service=self.settings.service_name, env=self.settings.env
            ).observe(infer["tamper_ratio"])
            mask_uri = None
            if infer["predicted_tampered"]:
                mask_bytes = self.doctamper.mask_to_png_bytes(infer["mask_np"])
                mask_uri = self.gcs.upload_bytes(
                    f"jobs/{job_id}/fraud_mask.png", mask_bytes, "image/png"
                )
                self.metrics.fraud_detected.labels(
                    service=self.settings.service_name,
                    env=self.settings.env,
                    tenant_id=task.tenant_id,
                ).inc()
                status = JobStatus.FRAUD_DETECTED
            fraud_result = FraudResult(
                tamper_ratio=infer["tamper_ratio"],
                tamper_pixels=infer["tamper_pixels"],
                total_pixels=infer["total_pixels"],
                predicted_tampered=infer["predicted_tampered"],
                mask_gcs_uri=mask_uri,
            )

        artifacts = [
            JobArtifactInput(artifact_type="original", gcs_uri=original_uri),
            JobArtifactInput(artifact_type="normalized", gcs_uri=normalized_uri),
        ]
        if fraud_result and fraud_result.mask_gcs_uri:
            artifacts.append(
                JobArtifactInput(artifact_type="fraud_mask", gcs_uri=fraud_result.mask_gcs_uri)
            )
        await self._report_progress(
            task.job_id,
            task.tenant_id,
            status=status,
            stage=JobStage.PREPROCESS,
            end_stage=True,
            artifacts=artifacts,
            metadata={"duration_sec": round(time.perf_counter() - start, 3)},
        )

        return PreprocessResultMessage(
            job_id=task.job_id,
            tenant_id=task.tenant_id,
            status=status,
            normalized_gcs_uri=normalized_uri,
            original_gcs_uri=original_uri,
            fraud_result=fraud_result,
        )

    def stop(self) -> None:
        self._running = False
