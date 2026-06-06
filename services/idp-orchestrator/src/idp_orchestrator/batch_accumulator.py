import asyncio
import time
import uuid
from dataclasses import dataclass, field
from uuid import UUID

from idp_contracts.jobs import BatchExtractionItem


@dataclass
class PendingJob:
    job_id: str
    tenant_id: str
    gcs_uri: str
    prompt: str = ""
    prompt_mode: str | None = None
    response_schema: dict | None = None
    invoice_type: str | None = None
    added_at: float = field(default_factory=time.time)


class BatchAccumulator:
    def __init__(self, max_size: int = 48, max_wait_ms: int = 500) -> None:
        self.max_size = max_size
        self.max_wait_sec = max_wait_ms / 1000.0
        self._queue: list[PendingJob] = []
        self._lock = asyncio.Lock()
        self._flush_event = asyncio.Event()

    async def add(self, job: PendingJob) -> None:
        async with self._lock:
            self._queue.append(job)
            if len(self._queue) >= self.max_size:
                self._flush_event.set()

    async def wait_and_flush(self) -> list[PendingJob]:
        while True:
            async with self._lock:
                if not self._queue:
                    await asyncio.sleep(0.05)
                    continue
                oldest_wait = time.time() - self._queue[0].added_at
                if len(self._queue) >= self.max_size or oldest_wait >= self.max_wait_sec:
                    batch = self._queue[: self.max_size]
                    self._queue = self._queue[self.max_size :]
                    self._flush_event.clear()
                    return batch
            try:
                await asyncio.wait_for(self._flush_event.wait(), timeout=self.max_wait_sec)
            except asyncio.TimeoutError:
                pass

    def pending_count(self) -> int:
        return len(self._queue)

    def to_batch_request(self, jobs: list[PendingJob]) -> tuple[str, list[BatchExtractionItem]]:
        batch_id = str(uuid.uuid4())
        items = [
            BatchExtractionItem(
                job_id=UUID(job.job_id) if isinstance(job.job_id, str) else job.job_id,
                gcs_uri=job.gcs_uri,
                prompt=job.prompt,
                prompt_mode=job.prompt_mode,
                response_schema=job.response_schema,
            )
            for job in jobs
        ]
        return batch_id, items
