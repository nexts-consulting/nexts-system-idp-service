import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idp_contracts.enums import JobStatus

from idp_app.db.models import ExtractionResult, FraudResult, Job, JobResult, PromptProfile, Rule


async def create_job(
    session: AsyncSession,
    tenant_id: str,
    image_urls: list[str],
    invoice_type: str | None,
    prompt_profile_id: uuid.UUID | None,
    callback_url: str | None,
    metadata: dict,
    idempotency_key: str | None,
) -> Job:
    if idempotency_key:
        existing = await session.execute(
            select(Job).where(Job.tenant_id == tenant_id, Job.idempotency_key == idempotency_key)
        )
        job = existing.scalar_one_or_none()
        if job:
            return job
    job = Job(
        tenant_id=tenant_id,
        status=JobStatus.PENDING,
        image_urls=image_urls,
        invoice_type=invoice_type,
        prompt_profile_id=prompt_profile_id,
        callback_url=callback_url,
        metadata_=metadata,
        idempotency_key=idempotency_key,
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)
    return job


async def get_job(session: AsyncSession, job_id: uuid.UUID) -> Job | None:
    result = await session.execute(select(Job).where(Job.id == job_id))
    return result.scalar_one_or_none()


async def update_job_status(session: AsyncSession, job_id: uuid.UUID, status: JobStatus | str) -> None:
    job = await get_job(session, job_id)
    if job:
        job.status = JobStatus(status) if isinstance(status, str) else status
        job.updated_at = datetime.utcnow()
        await session.commit()


async def save_fraud(session: AsyncSession, job_id: uuid.UUID, data: dict) -> None:
    fr = FraudResult(job_id=job_id, **data)
    session.merge(fr)
    await session.commit()


async def save_extraction(session: AsyncSession, job_id: uuid.UUID, data: dict) -> None:
    er = ExtractionResult(job_id=job_id, **data)
    session.merge(er)
    await session.commit()


async def save_job_result(session: AsyncSession, job_id: uuid.UUID, rules_result: dict, final_payload: dict) -> None:
    jr = JobResult(job_id=job_id, rules_result=rules_result, final_payload=final_payload)
    session.merge(jr)
    await session.commit()


async def list_rules(session: AsyncSession, tenant_id: str) -> list[Rule]:
    result = await session.execute(
        select(Rule).where(Rule.tenant_id == tenant_id, Rule.enabled.is_(True)).order_by(Rule.priority)
    )
    return list(result.scalars().all())


async def create_rule(session: AsyncSession, data: dict) -> Rule:
    rule = Rule(**data)
    session.add(rule)
    await session.commit()
    await session.refresh(rule)
    return rule


async def create_prompt_profile(session: AsyncSession, data: dict) -> PromptProfile:
    profile = PromptProfile(**data)
    session.add(profile)
    await session.commit()
    await session.refresh(profile)
    return profile


async def get_prompt_profile(session: AsyncSession, profile_id: uuid.UUID) -> PromptProfile | None:
    result = await session.execute(select(PromptProfile).where(PromptProfile.id == profile_id))
    return result.scalar_one_or_none()
