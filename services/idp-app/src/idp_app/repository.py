import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from idp_contracts.debug import DebugRequestEvent, DebugTimelineEvent
from idp_contracts.enums import JobStatus
from idp_contracts.jobs import JobListItem

from idp_app.db.models import (
    DebugRequestEventRow,
    ExtractionResult,
    FraudResult,
    Job,
    JobArtifact,
    JobResult,
    JobStageRow,
    PromptProfile,
    Rule,
)


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


async def list_jobs_recent(
    session: AsyncSession,
    *,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[JobListItem], int]:
    total_result = await session.execute(select(func.count()).select_from(Job))
    total = int(total_result.scalar_one())
    result = await session.execute(
        select(Job).order_by(Job.created_at.desc()).offset(offset).limit(limit)
    )
    jobs = [
        JobListItem(
            job_id=row.id,
            tenant_id=row.tenant_id,
            status=row.status if isinstance(row.status, JobStatus) else JobStatus(row.status),
            created_at=row.created_at,
            updated_at=row.updated_at,
            invoice_type=row.invoice_type,
        )
        for row in result.scalars().all()
    ]
    return jobs, total


async def get_job_detail(session: AsyncSession, job_id: uuid.UUID) -> Job | None:
    result = await session.execute(
        select(Job)
        .where(Job.id == job_id)
        .options(
            selectinload(Job.fraud_result),
            selectinload(Job.extraction_result),
            selectinload(Job.job_result),
            selectinload(Job.stages),
            selectinload(Job.artifacts),
        )
    )
    return result.scalar_one_or_none()


async def get_prompt_profile(session: AsyncSession, profile_id: uuid.UUID) -> PromptProfile | None:
    result = await session.execute(select(PromptProfile).where(PromptProfile.id == profile_id))
    return result.scalar_one_or_none()


async def get_prompt_profile_by_name(
    session: AsyncSession,
    name: str,
    *,
    enabled_only: bool = True,
) -> PromptProfile | None:
    q = select(PromptProfile).where(PromptProfile.name == name)
    if enabled_only:
        q = q.where(PromptProfile.enabled.is_(True))
    q = q.order_by(PromptProfile.version.desc())
    result = await session.execute(q.limit(1))
    return result.scalar_one_or_none()


async def update_job_status(session: AsyncSession, job_id: uuid.UUID, status: JobStatus | str) -> None:
    job = await get_job(session, job_id)
    if job:
        job.status = JobStatus(status) if isinstance(status, str) else status
        job.updated_at = datetime.now(timezone.utc)
        await session.commit()


async def _close_open_stages(session: AsyncSession, job_id: uuid.UUID, error_code: str | None = None) -> None:
    now = datetime.now(timezone.utc)
    await session.execute(
        update(JobStageRow)
        .where(JobStageRow.job_id == job_id, JobStageRow.ended_at.is_(None))
        .values(ended_at=now, error_code=error_code)
    )


async def record_job_progress(
    session: AsyncSession,
    job_id: uuid.UUID,
    *,
    status: JobStatus | str | None = None,
    stage: str | None = None,
    end_stage: bool = False,
    artifacts: list[tuple[str, str]] | None = None,
    metadata: dict | None = None,
    error_code: str | None = None,
) -> None:
    job = await get_job(session, job_id)
    if not job:
        return

    if end_stage:
        await _close_open_stages(session, job_id, error_code=error_code)

    if status is not None:
        job.status = JobStatus(status) if isinstance(status, str) else status
        job.updated_at = datetime.now(timezone.utc)

    if stage:
        session.add(
            JobStageRow(
                job_id=job_id,
                stage=stage,
                metadata_=metadata or {},
                error_code=error_code,
            )
        )

    if artifacts:
        for artifact_type, gcs_uri in artifacts:
            session.add(
                JobArtifact(
                    job_id=job_id,
                    artifact_type=artifact_type,
                    gcs_uri=gcs_uri,
                )
            )

    await session.commit()


async def save_fraud(session: AsyncSession, job_id: uuid.UUID, data: dict) -> None:
    fr = FraudResult(job_id=job_id, **data)
    session.merge(fr)
    await session.commit()


async def save_extraction(session: AsyncSession, job_id: uuid.UUID, data: dict) -> ExtractionResult:
    existing = await session.get(ExtractionResult, job_id)
    if existing:
        for key, value in data.items():
            setattr(existing, key, value)
        row = existing
    else:
        row = ExtractionResult(job_id=job_id, **data)
        session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


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


async def save_debug_event(session: AsyncSession, event: DebugRequestEvent) -> DebugRequestEventRow:
    row = DebugRequestEventRow(
        trace_id=event.trace_id,
        job_id=event.job_id,
        service=event.service,
        step=event.step,
        direction=event.direction,
        duration_ms=event.duration_ms,
        status=event.status,
        request=event.request,
        response=event.response,
        error=event.error,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def list_debug_events(session: AsyncSession, job_id: uuid.UUID) -> list[DebugTimelineEvent]:
    result = await session.execute(
        select(DebugRequestEventRow)
        .where(DebugRequestEventRow.job_id == job_id)
        .order_by(DebugRequestEventRow.started_at, DebugRequestEventRow.id)
    )
    rows = result.scalars().all()
    return [
        DebugTimelineEvent(
            id=row.id,
            trace_id=row.trace_id,
            job_id=row.job_id,
            service=row.service,
            step=row.step,
            direction=row.direction,
            started_at=row.started_at,
            duration_ms=row.duration_ms,
            status=row.status,
            request=row.request or {},
            response=row.response or {},
            error=row.error,
        )
        for row in rows
    ]
