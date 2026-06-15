from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl

from idp_contracts.enums import JobStage, JobStatus


class CreateJobRequest(BaseModel):
    image_urls: list[str] = Field(
        ...,
        min_length=1,
        description=(
            "Image locations: https:// (HTTP), gs:// (GCS), firebase:// (Firebase Storage), "
            "or https://firebasestorage.googleapis.com/... download URL"
        ),
    )
    invoice_type: str | None = None
    prompt_profile_id: UUID | None = None
    callback_url: HttpUrl | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    tenant_id: str = "default"
    prompt_mode: str | None = Field(
        default=None,
        description="Extraction prompt mode: base | reasoning | reasoning_vir (default from extraction service)",
    )


class CreateJobResponse(BaseModel):
    job_id: UUID
    status: JobStatus


class FraudResult(BaseModel):
    tamper_ratio: float
    tamper_pixels: int
    total_pixels: int
    predicted_tampered: bool
    mask_gcs_uri: str | None = None


class JobArtifactRecord(BaseModel):
    artifact_type: str
    gcs_uri: str
    created_at: datetime


class JobStageRecord(BaseModel):
    stage: str
    started_at: datetime
    ended_at: datetime | None = None
    error_code: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class JobDetailResponse(BaseModel):
    job_id: UUID
    tenant_id: str
    status: JobStatus
    image_urls: list[str]
    invoice_type: str | None
    prompt_profile_id: UUID | None
    callback_url: str | None
    metadata: dict[str, Any]
    fraud_result: FraudResult | None = None
    extraction_result: dict[str, Any] | None = None
    rules_result: dict[str, Any] | None = None
    stages: list[JobStageRecord] = Field(default_factory=list)
    artifacts: list[JobArtifactRecord] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class JobArtifactInput(BaseModel):
    artifact_type: str
    gcs_uri: str


class JobProgressEvent(BaseModel):
    job_id: UUID
    tenant_id: str = "default"
    status: JobStatus | None = None
    stage: JobStage | None = None
    end_stage: bool = False
    artifacts: list[JobArtifactInput] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None


class PreprocessTaskMessage(BaseModel):
    job_id: UUID
    tenant_id: str
    image_urls: list[str]
    invoice_type: str | None = None
    trace_id: UUID | None = None


class PreprocessResultMessage(BaseModel):
    job_id: UUID
    tenant_id: str
    status: JobStatus
    normalized_gcs_uri: str | None = None
    original_gcs_uri: str | None = None
    fraud_result: FraudResult | None = None
    error: str | None = None


class BatchExtractionItem(BaseModel):
    job_id: UUID
    gcs_uri: str
    prompt: str = ""
    prompt_mode: str | None = None
    response_schema: dict[str, Any] | None = None
    trace_id: UUID | None = None


class BatchExtractionRequest(BaseModel):
    batch_id: str
    items: list[BatchExtractionItem] = Field(..., max_length=48)
    model: str = "OpenGVLab/InternVL3_5-8B-Flash"


class BatchExtractionResultItem(BaseModel):
    job_id: UUID
    raw_json: dict[str, Any] | None = None
    validated_json: dict[str, Any] | None = None
    error: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0


class BatchExtractionResponse(BaseModel):
    batch_id: str
    results: list[BatchExtractionResultItem]
    total_time_seconds: float = 0.0


class JobCompletionWebhook(BaseModel):
    job_id: UUID
    tenant_id: str
    status: JobStatus
    fraud_result: FraudResult | None = None
    """Validated JSON used by rules engine (alias: validated_json in DB)."""
    extraction_result: dict[str, Any] | None = None
    """Raw model output before/alongside validation."""
    raw_json: dict[str, Any] | None = None
    normalized_gcs_uri: str | None = None
    error: str | None = None
    batch_id: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model_version: str | None = None
