from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class DebugRequestEvent(BaseModel):
    trace_id: UUID
    job_id: UUID | None = None
    service: str
    step: str
    direction: Literal["inbound", "outbound", "internal"]
    duration_ms: int | None = None
    status: Literal["success", "error"] | None = None
    request: dict[str, Any] | None = None
    response: dict[str, Any] | None = None
    error: str | None = None


class DebugTimelineEvent(BaseModel):
    id: int
    trace_id: UUID
    job_id: UUID | None
    service: str
    step: str
    direction: str
    started_at: datetime
    duration_ms: int | None
    status: str | None
    request: dict[str, Any] = Field(default_factory=dict)
    response: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class DebugTimelineResponse(BaseModel):
    job_id: UUID
    trace_id: UUID | None = None
    events: list[DebugTimelineEvent] = Field(default_factory=list)
