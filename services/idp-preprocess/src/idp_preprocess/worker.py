import asyncio
import time
import uuid

import structlog
from idp_contracts.enums import JobStatus
from idp_contracts.jobs import FraudResult, PreprocessResultMessage, PreprocessTaskMessage

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
        self._running = False

    async def run_forever(self) -> None:
        self._running = True
        await self.redis.ensure_groups()
        consumer = self.settings.consumer_name
        while self._running:
            messages = await self.redis.read_group(
                self.redis.PREPROCESS_TASKS,
                self.redis.PREPROCESS_GROUP,
                consumer,
                count=5,
                block_ms=5000,
            )
            for msg_id, data in messages:
                try:
                    task = PreprocessTaskMessage.model_validate(data)
                    result = await self.process_task(task)
                    await self.redis.publish(
                        self.redis.PREPROCESS_RESULTS,
                        result.model_dump(mode="json"),
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
        images = await self.image_fetcher.fetch_all(task.image_urls)
        primary = images[0]
        normalized = normalize_image(primary, self.settings.max_image_edge)
        original_uri = self.gcs.upload_bytes(
            f"jobs/{job_id}/original.jpg", primary, "image/jpeg"
        )
        normalized_uri = self.gcs.upload_bytes(
            f"jobs/{job_id}/normalized.jpg", normalized, "image/jpeg"
        )
        self.metrics.observe_stage(self.settings.env, "download", time.perf_counter() - start)

        fraud_result: FraudResult | None = None
        status = JobStatus.READY_FOR_EXTRACTION

        if self.settings.fraud_enabled and self.doctamper:
            t0 = time.perf_counter()
            infer = self.doctamper.infer_bytes(normalized)
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
