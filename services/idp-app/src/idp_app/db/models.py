import uuid
from datetime import datetime

from idp_contracts.enums import JobStatus
from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Matches PostgreSQL type job_status from migrations/001_initial_schema.sql
JobStatusColumn = Enum(JobStatus, name="job_status", create_type=False)


class Base(DeclarativeBase):
    pass


class PromptProfile(Base):
    __tablename__ = "prompt_profiles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255))
    invoice_type: Mapped[str] = mapped_column(String(64))
    version: Mapped[str] = mapped_column(String(32), default="1.0.0")
    system_prompt: Mapped[str] = mapped_column(Text)
    user_template: Mapped[str] = mapped_column(Text)
    json_schema: Mapped[dict] = mapped_column(JSONB, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(128), default="default")
    status: Mapped[JobStatus] = mapped_column(JobStatusColumn, default=JobStatus.PENDING)
    image_urls: Mapped[list] = mapped_column(JSONB)
    invoice_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prompt_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("prompt_profiles.id"), nullable=True
    )
    callback_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    fraud_result: Mapped["FraudResult | None"] = relationship(back_populates="job", uselist=False)
    extraction_result: Mapped["ExtractionResult | None"] = relationship(back_populates="job", uselist=False)
    job_result: Mapped["JobResult | None"] = relationship(back_populates="job", uselist=False)
    stages: Mapped[list["JobStageRow"]] = relationship(back_populates="job", order_by="JobStageRow.started_at")
    artifacts: Mapped[list["JobArtifact"]] = relationship(back_populates="job", order_by="JobArtifact.created_at")


class JobStageRow(Base):
    __tablename__ = "job_stages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"))
    stage: Mapped[str] = mapped_column(String(32))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    job: Mapped[Job] = relationship(back_populates="stages")


class JobArtifact(Base):
    __tablename__ = "job_artifacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"))
    artifact_type: Mapped[str] = mapped_column(String(32))
    gcs_uri: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    job: Mapped[Job] = relationship(back_populates="artifacts")


class FraudResult(Base):
    __tablename__ = "fraud_results"

    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("jobs.id"), primary_key=True)
    tamper_ratio: Mapped[float] = mapped_column()
    tamper_pixels: Mapped[int] = mapped_column(Integer)
    total_pixels: Mapped[int] = mapped_column(Integer)
    predicted_tampered: Mapped[bool] = mapped_column(Boolean)
    mask_gcs_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    job: Mapped[Job] = relationship(back_populates="fraud_result")


class ExtractionResult(Base):
    __tablename__ = "extraction_results"

    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("jobs.id"), primary_key=True)
    batch_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    raw_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    validated_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    job: Mapped[Job] = relationship(back_populates="extraction_result")


class JobResult(Base):
    __tablename__ = "job_results"

    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("jobs.id"), primary_key=True)
    rules_result: Mapped[dict] = mapped_column(JSONB, default=dict)
    final_payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    job: Mapped[Job] = relationship(back_populates="job_result")


class Rule(Base):
    __tablename__ = "rules"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(128), default="default")
    name: Mapped[str] = mapped_column(String(255))
    priority: Mapped[int] = mapped_column(Integer, default=100)
    condition_json: Mapped[dict] = mapped_column(JSONB)
    action_json: Mapped[dict] = mapped_column(JSONB)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class OutboxCallback(Base):
    __tablename__ = "outbox_callbacks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("jobs.id"))
    callback_url: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSONB)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    next_retry_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DebugRequestEventRow(Base):
    __tablename__ = "debug_request_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=True
    )
    service: Mapped[str] = mapped_column(Text)
    step: Mapped[str] = mapped_column(Text)
    direction: Mapped[str] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str | None] = mapped_column(Text, nullable=True)
    request: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    response: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
