from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class CreateRuleRequest(BaseModel):
    tenant_id: str = "default"
    name: str
    priority: int = 100
    condition_json: dict[str, Any]
    action_json: dict[str, Any]
    enabled: bool = True


class UpdateRuleRequest(BaseModel):
    name: str | None = None
    priority: int | None = None
    condition_json: dict[str, Any] | None = None
    action_json: dict[str, Any] | None = None
    enabled: bool | None = None


class RuleResponse(BaseModel):
    id: UUID
    tenant_id: str
    name: str
    priority: int
    condition_json: dict[str, Any]
    action_json: dict[str, Any]
    enabled: bool
    created_at: datetime
    updated_at: datetime
