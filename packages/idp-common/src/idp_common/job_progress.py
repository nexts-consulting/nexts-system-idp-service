"""Fire-and-forget job progress reporting to idp-app."""

from __future__ import annotations

import httpx
import structlog
from idp_contracts.jobs import JobProgressEvent

logger = structlog.get_logger()


async def report_job_progress(app_internal_url: str, event: JobProgressEvent) -> bool:
    url = f"{app_internal_url.rstrip('/')}/internal/v1/jobs/progress"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=event.model_dump(mode="json"))
            if resp.status_code >= 400:
                logger.warning(
                    "job_progress_failed",
                    job_id=str(event.job_id),
                    status_code=resp.status_code,
                    body=resp.text[:300],
                )
                return False
        return True
    except Exception as e:
        logger.warning("job_progress_failed", job_id=str(event.job_id), error=str(e))
        return False
