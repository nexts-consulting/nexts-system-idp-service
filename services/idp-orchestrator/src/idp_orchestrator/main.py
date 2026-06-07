import asyncio
from contextlib import asynccontextmanager
from uuid import UUID

import structlog
import uvicorn
from fastapi import FastAPI
from idp_common.health import router as health_router
from idp_common.http_middleware import HttpAccessLogMiddleware
from idp_common.logging import configure_logging
from idp_common.metrics import MetricsRegistry
from idp_common.redis_client import RedisStreams
from idp_common.tracing import setup_tracing
from pydantic import BaseModel

from idp_orchestrator.config import Settings
from idp_orchestrator.orchestrator import OrchestratorService

settings = Settings()
configure_logging(settings.log_level, settings.service_name)
setup_tracing(settings.service_name, settings.otel_exporter_endpoint)
metrics = MetricsRegistry(settings.service_name)
logger = structlog.get_logger()

orchestrator: OrchestratorService | None = None
orch_task: asyncio.Task | None = None


class StartJobRequest(BaseModel):
    job_id: UUID
    tenant_id: str = "default"
    image_urls: list[str]
    invoice_type: str | None = None
    prompt: str | None = None
    prompt_mode: str | None = None
    response_schema: dict | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global orchestrator, orch_task
    redis = RedisStreams(settings.redis_url)
    orchestrator = OrchestratorService(settings, redis, metrics)
    orch_task = asyncio.create_task(orchestrator.run_forever())

    def _on_worker_done(task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error("orchestrator_worker_stopped", error=str(exc))

    orch_task.add_done_callback(_on_worker_done)
    app.state.redis = redis
    yield
    if orchestrator:
        orchestrator.stop()
    if orch_task:
        orch_task.cancel()
    await redis.close()


app = FastAPI(title="IDP Orchestrator", lifespan=lifespan)
app.add_middleware(HttpAccessLogMiddleware)
app.include_router(health_router)


@app.post("/internal/v1/jobs/start")
async def start_job(req: StartJobRequest) -> dict:
    assert orchestrator
    await orchestrator.start_job(
        req.job_id,
        req.tenant_id,
        req.image_urls,
        req.invoice_type,
        req.prompt,
        req.prompt_mode,
        req.response_schema,
    )
    return {"status": "enqueued"}


@app.post("/internal/runpod/warm")
async def set_runpod_warm(warm: bool = True) -> dict:
    redis: RedisStreams = app.state.redis
    await redis.set_key(settings.runpod_warm_key, "1" if warm else "0")
    metrics.runpod_warm.labels(service=settings.service_name, env=settings.env).set(
        1.0 if warm else 0.0
    )
    return {"warm": warm}


if __name__ == "__main__":
    uvicorn.run(app, host=settings.host, port=settings.port)
