import asyncio
from contextlib import asynccontextmanager

import structlog
import uvicorn
from fastapi import FastAPI
from idp_common.gcs import GCSClient
from idp_common.health import router as health_router
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    global worker, worker_task
    redis = RedisStreams(settings.redis_url)
    gcs = GCSClient(settings.gcs_bucket, settings.gcs_emulator_host)
    doctamper = None
    try:
        doctamper = DocTamperModel(
            settings.doctamper_checkpoint,
            settings.encoder_name,
            settings.tamper_pixel_threshold,
        )
    except Exception as e:
        logger.warning("doctamper_not_loaded", error=str(e))
    worker = PreprocessWorker(settings, redis, gcs, metrics, doctamper)
    worker_task = asyncio.create_task(worker.run_forever())
    yield
    if worker:
        worker.stop()
    if worker_task:
        worker_task.cancel()
    await redis.close()


app = FastAPI(title="IDP Preprocess", lifespan=lifespan)
app.include_router(health_router)


if __name__ == "__main__":
    uvicorn.run(app, host=settings.host, port=settings.port)
