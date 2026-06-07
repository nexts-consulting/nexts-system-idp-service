import asyncio
from contextlib import asynccontextmanager

import structlog
import uvicorn
from fastapi import FastAPI, HTTPException
from idp_common.gcs import GCSClient
from idp_common.health import router as health_router
from idp_common.http_middleware import HttpAccessLogMiddleware
from idp_common.logging import configure_logging
from idp_common.metrics import MetricsRegistry
from idp_common.redis_client import RedisStreams
from idp_common.tracing import setup_tracing

from idp_preprocess.config import Settings
from idp_preprocess.models.doctamper import DocTamperModel
from idp_preprocess.worker import PreprocessWorker

settings = Settings()
configure_logging(settings.log_level, settings.service_name)
setup_tracing(settings.service_name, settings.otel_exporter_endpoint)
metrics = MetricsRegistry(settings.service_name)
logger = structlog.get_logger()

worker: PreprocessWorker | None = None
worker_task: asyncio.Task | None = None
supervisor_task: asyncio.Task | None = None


async def _supervise_worker() -> None:
    global worker_task
    assert worker is not None
    worker._running = True
    while worker._running:
        worker_task = asyncio.create_task(worker.run_forever())
        try:
            await worker_task
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("preprocess_worker_crashed")
        if not worker._running:
            break
        logger.warning("preprocess_worker_restarting")
        await asyncio.sleep(2)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global worker, worker_task, supervisor_task
    logger.info(
        "preprocess_starting",
        fraud_enabled=settings.fraud_enabled,
        gcs_bucket=settings.gcs_bucket,
        redis_host=settings.redis_url.rsplit("@", 1)[-1],
    )
    redis = RedisStreams(settings.redis_url)
    gcs = GCSClient(settings.gcs_bucket, settings.gcs_emulator_host)
    doctamper = None
    if settings.fraud_enabled:
        try:
            doctamper = DocTamperModel(
                settings.doctamper_checkpoint,
                settings.encoder_name,
                settings.tamper_pixel_threshold,
            )
            logger.info("doctamper_loaded", checkpoint=settings.doctamper_checkpoint)
        except Exception as e:
            logger.warning("doctamper_not_loaded", error=str(e))
    else:
        logger.info("fraud_disabled", skip_doctamper_load=True)
    worker = PreprocessWorker(settings, redis, gcs, metrics, doctamper)
    supervisor_task = asyncio.create_task(_supervise_worker())
    yield
    if worker:
        worker.stop()
    if supervisor_task:
        supervisor_task.cancel()
        try:
            await supervisor_task
        except asyncio.CancelledError:
            pass
    await redis.close()


app = FastAPI(title="IDP Preprocess", lifespan=lifespan)
app.add_middleware(HttpAccessLogMiddleware)
app.include_router(health_router)


@app.get("/ready")
async def ready() -> dict[str, str]:
    """Liveness for compose: HTTP up is not enough — Redis worker must be running."""
    if supervisor_task is None or supervisor_task.done():
        raise HTTPException(503, "preprocess supervisor not running")
    if worker_task is None or worker_task.done():
        exc = worker_task.exception() if worker_task and not worker_task.cancelled() else None
        raise HTTPException(503, f"preprocess worker not running: {exc}")
    return {"status": "ready", "worker": "running"}


if __name__ == "__main__":
    uvicorn.run(app, host=settings.host, port=settings.port)
