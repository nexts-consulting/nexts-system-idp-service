"""Fire-and-forget debug request audit events for pipeline troubleshooting."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

import httpx
import structlog
from idp_contracts.debug import DebugRequestEvent
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

logger = structlog.get_logger()

TRACE_HEADER = "X-Trace-ID"
JOB_HEADER = "X-Job-ID"

_BASE64_DATA_URL = re.compile(r"data:[^;]+;base64,[A-Za-z0-9+/=]+")


def redact_url(url: str) -> str:
    parts = urlsplit(url)
    if parts.query:
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "<redacted>", parts.fragment))
    return url


def preview_body(data: Any, max_len: int = 500) -> Any:
    if data is None:
        return None
    if isinstance(data, str):
        text = _BASE64_DATA_URL.sub("<base64_redacted>", data)
        return text if len(text) <= max_len else text[:max_len] + "..."
    if isinstance(data, dict):
        out: dict[str, Any] = {}
        for key, value in data.items():
            lower = key.lower()
            if lower in {"authorization", "api_key", "api-key", "x-api-key"}:
                out[key] = "<redacted>"
            elif lower in {"image_url", "image_urls"} or (
                isinstance(value, str) and value.startswith("data:")
            ):
                out[key] = "<base64_redacted>"
            else:
                out[key] = preview_body(value, max_len)
        return out
    if isinstance(data, list):
        return [preview_body(item, max_len) for item in data[:20]]
    return data


def trace_headers(trace_id: UUID | str, job_id: UUID | str | None = None) -> dict[str, str]:
    headers = {TRACE_HEADER: str(trace_id)}
    if job_id is not None:
        headers[JOB_HEADER] = str(job_id)
    return headers


def bind_trace(trace_id: UUID | str, job_id: UUID | str | None = None) -> None:
    ctx: dict[str, str] = {"trace_id": str(trace_id)}
    if job_id is not None:
        ctx["job_id"] = str(job_id)
    structlog.contextvars.bind_contextvars(**ctx)


def parse_trace_id(headers: dict[str, str]) -> UUID | None:
    raw = headers.get(TRACE_HEADER) or headers.get(TRACE_HEADER.lower())
    if not raw:
        return None
    try:
        return UUID(raw)
    except ValueError:
        return None


def parse_job_id(headers: dict[str, str]) -> UUID | None:
    raw = headers.get(JOB_HEADER) or headers.get(JOB_HEADER.lower())
    if not raw:
        return None
    try:
        return UUID(raw)
    except ValueError:
        return None


class DebugAuditClient:
    def __init__(
        self,
        app_internal_url: str,
        service_name: str,
        *,
        enabled: bool = True,
    ) -> None:
        self.app_internal_url = app_internal_url.rstrip("/")
        self.service_name = service_name
        self.enabled = enabled

    async def emit(self, event: DebugRequestEvent) -> bool:
        if not self.enabled:
            return False
        url = f"{self.app_internal_url}/internal/v1/debug/events"
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(url, json=event.model_dump(mode="json"))
                if resp.status_code >= 400:
                    logger.warning(
                        "debug_audit_emit_failed",
                        step=event.step,
                        status_code=resp.status_code,
                        body=resp.text[:200],
                    )
                    return False
            return True
        except Exception as e:
            logger.warning("debug_audit_emit_failed", step=event.step, error=str(e))
            return False

    async def log(
        self,
        *,
        trace_id: UUID,
        job_id: UUID | None,
        step: str,
        direction: str,
        request: dict[str, Any] | None = None,
        response: dict[str, Any] | None = None,
        duration_ms: int | None = None,
        status: str | None = None,
        error: str | None = None,
    ) -> bool:
        event = DebugRequestEvent(
            trace_id=trace_id,
            job_id=job_id,
            service=self.service_name,
            step=step,
            direction=direction,  # type: ignore[arg-type]
            request=preview_body(request) if request else None,
            response=preview_body(response) if response else None,
            duration_ms=duration_ms,
            status=status,  # type: ignore[arg-type]
            error=error,
        )
        return await self.emit(event)

    @staticmethod
    def json_preview(payload: Any, max_len: int = 2000) -> dict[str, Any] | list[Any] | str | None:
        if payload is None:
            return None
        try:
            text = json.dumps(payload, default=str)
        except TypeError:
            text = str(payload)
        preview = preview_body(json.loads(text) if text.startswith(("{", "[")) else text, max_len)
        return preview if isinstance(preview, (dict, list, str)) else str(preview)


EventHandler = Callable[[DebugRequestEvent], Awaitable[None]]


class DebugInboundMiddleware(BaseHTTPMiddleware):
    """Log inbound HTTP requests for debug timeline (idp-app or any FastAPI service)."""

    def __init__(
        self,
        app,
        service_name: str,
        on_event: EventHandler,
        *,
        enabled: bool = True,
        path_prefixes: tuple[str, ...] = ("/v1/", "/internal/"),
        excluded_paths: tuple[str, ...] = ("/internal/v1/debug/events",),
    ) -> None:
        super().__init__(app)
        self.service_name = service_name
        self.on_event = on_event
        self.enabled = enabled
        self.path_prefixes = path_prefixes
        self.excluded_paths = excluded_paths

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if not self.enabled or not any(request.url.path.startswith(p) for p in self.path_prefixes):
            return await call_next(request)
        if any(request.url.path == p for p in self.excluded_paths):
            return await call_next(request)

        started = time.perf_counter()
        trace_id = parse_trace_id(dict(request.headers))
        job_id = parse_job_id(dict(request.headers))
        if not trace_id and not job_id:
            return await call_next(request)

        response = await call_next(request)
        duration_ms = int((time.perf_counter() - started) * 1000)
        status = "success" if response.status_code < 400 else "error"
        event = DebugRequestEvent(
            trace_id=trace_id or job_id,  # type: ignore[arg-type]
            job_id=job_id,
            service=self.service_name,
            step=f"{request.method} {request.url.path}",
            direction="inbound",
            duration_ms=duration_ms,
            status=status,
            request={
                "method": request.method,
                "path": request.url.path,
                "query": str(request.query_params) if request.query_params else None,
            },
            response={"status_code": response.status_code},
            error=None if status == "success" else f"HTTP {response.status_code}",
        )
        try:
            await self.on_event(event)
        except Exception as e:
            logger.warning("debug_inbound_middleware_failed", error=str(e))
        return response
