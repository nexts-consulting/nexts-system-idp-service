import asyncio

import pytest

from idp_orchestrator.batch_accumulator import BatchAccumulator, PendingJob


@pytest.mark.asyncio
async def test_flush_on_max_size():
    acc = BatchAccumulator(max_size=3, max_wait_ms=5000)
    for i in range(3):
        await acc.add(
            PendingJob(job_id=str(i), tenant_id="t", gcs_uri=f"gs://b/j{i}.jpg", prompt="p")
        )
    batch = await asyncio.wait_for(acc.wait_and_flush(), timeout=2.0)
    assert len(batch) == 3
