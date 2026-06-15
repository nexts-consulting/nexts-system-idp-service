import base64
import time
from typing import Any
from uuid import UUID

import httpx
import structlog

from idp_common.debug_audit import DebugAuditClient, redact_url
from idp_extraction.invoice_schema import USER_EXTRACTION_LINE
from idp_extraction.prompts import build_system_prompt
from idp_extraction.schema_validator import extract_json_from_text

logger = structlog.get_logger()


class LmdeployClient:
    def __init__(
        self,
        base_url: str,
        model: str,
        mock_mode: bool = False,
        api_key: str = "EMPTY",
        max_new_tokens: int = 1024,
        temperature: float = 0.0,
        default_prompt_mode: str = "reasoning_vir",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.mock_mode = mock_mode
        self.api_key = api_key
        self.max_new_tokens = max_new_tokens
        self.temperature = temperature
        self.default_prompt_mode = default_prompt_mode

    def resolve_prompts(
        self,
        custom_system_prompt: str | None,
        prompt_mode: str | None,
    ) -> tuple[str, str]:
        """Return (system_prompt, user_message). Custom prompt overrides built-in modes."""
        if custom_system_prompt and custom_system_prompt.strip():
            system = custom_system_prompt.strip()
        else:
            mode = prompt_mode or self.default_prompt_mode
            system = build_system_prompt(mode)
        return system, USER_EXTRACTION_LINE

    async def extract_one(
        self,
        image_bytes: bytes,
        custom_system_prompt: str | None = None,
        prompt_mode: str | None = None,
        mime: str = "image/jpeg",
        *,
        trace_id: UUID | None = None,
        job_id: UUID | None = None,
        audit: DebugAuditClient | None = None,
    ) -> tuple[dict[str, Any] | None, str | None, int, int, float, str]:
        system_prompt, user_message = self.resolve_prompts(custom_system_prompt, prompt_mode)

        if self.mock_mode:
            raw, err = self._mock_extract()
            if audit and trace_id:
                await audit.log(
                    trace_id=trace_id,
                    job_id=job_id,
                    step="lmdeploy.chat_completions (mock)",
                    direction="outbound",
                    duration_ms=500,
                    status="success",
                    request={
                        "model": self.model,
                        "system_prompt_preview": system_prompt[:300],
                        "image_bytes": len(image_bytes),
                    },
                    response={"prompt_tokens": 512, "completion_tokens": 256, "mock": True},
                )
            return raw, err, 512, 256, 0.5, "{}"

        b64 = base64.b64encode(image_bytes).decode()
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_message},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{b64}"},
                        },
                    ],
                },
            ],
            "max_tokens": self.max_new_tokens,
            "temperature": self.temperature,
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        lm_url = f"{self.base_url}/v1/chat/completions"
        start = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=300.0) as client:
                resp = await client.post(lm_url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
        except Exception as e:
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            if audit and trace_id:
                await audit.log(
                    trace_id=trace_id,
                    job_id=job_id,
                    step="lmdeploy.chat_completions",
                    direction="outbound",
                    duration_ms=elapsed_ms,
                    status="error",
                    request={
                        "url": redact_url(lm_url),
                        "model": self.model,
                        "system_prompt_preview": system_prompt[:300],
                        "image_bytes": len(image_bytes),
                        "max_tokens": self.max_new_tokens,
                    },
                    error=str(e),
                )
            raise
        elapsed = time.perf_counter() - start
        choice = data["choices"][0]["message"]["content"] or ""
        usage = data.get("usage", {})
        parsed = extract_json_from_text(choice)
        if audit and trace_id:
            await audit.log(
                trace_id=trace_id,
                job_id=job_id,
                step="lmdeploy.chat_completions",
                direction="outbound",
                duration_ms=int(elapsed * 1000),
                status="success" if parsed else "error",
                request={
                    "url": redact_url(lm_url),
                    "model": self.model,
                    "system_prompt_preview": system_prompt[:300],
                    "image_bytes": len(image_bytes),
                    "max_tokens": self.max_new_tokens,
                },
                response={
                    "status_code": 200,
                    "prompt_tokens": usage.get("prompt_tokens", 0),
                    "completion_tokens": usage.get("completion_tokens", 0),
                    "raw_output_preview": choice[:500],
                },
                error=None if parsed else "failed to parse JSON from model output",
            )
        if not parsed:
            return (
                None,
                "failed to parse JSON from model output",
                usage.get("prompt_tokens", 0),
                usage.get("completion_tokens", 0),
                elapsed,
                choice,
            )
        return (
            parsed,
            None,
            usage.get("prompt_tokens", 0),
            usage.get("completion_tokens", 0),
            elapsed,
            choice,
        )

    @staticmethod
    def _mock_extract() -> tuple[dict[str, Any], None]:
        return (
            {
                "store_name": "CUA HANG MOCK",
                "date": "01/01/2026",
                "time": "10:30",
                "bill_number": "HD-001",
                "products": [
                    {
                        "product_name": "San pham A",
                        "product_code": None,
                        "quantity": 1,
                        "product_price": 100000,
                        "unit": "cai",
                        "line_amount": 100000,
                    }
                ],
                "total_amount": 100000,
                "discount": None,
                "customer_payment": 100000,
                "cash": None,
                "change": 0,
                "_mock": True,
            },
            None,
        )
