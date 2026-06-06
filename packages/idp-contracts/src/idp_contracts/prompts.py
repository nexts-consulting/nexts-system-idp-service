from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class CreatePromptProfileRequest(BaseModel):
    name: str
    invoice_type: str
    version: str = "1.0.0"
    system_prompt: str
    user_template: str
    json_schema: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class PromptProfileResponse(BaseModel):
    id: UUID
    name: str
    invoice_type: str
    version: str
    system_prompt: str
    user_template: str
    json_schema: dict[str, Any]
    enabled: bool
    created_at: datetime
