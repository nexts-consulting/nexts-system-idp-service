"""Resolve extraction prompt settings from a prompt profile row."""

from __future__ import annotations

from typing import Any

from idp_app.db.models import PromptProfile


def resolve_prompt_from_profile(
    profile: PromptProfile,
    prompt_mode_override: str | None = None,
) -> tuple[str | None, str | None, dict[str, Any] | None]:
    """
    Return (custom_system_prompt, prompt_mode, response_schema).

    When system_prompt is empty, custom_system_prompt is None so extraction uses
    build_system_prompt(prompt_mode) instead of a one-line user_template override.
    """
    schema = profile.json_schema if isinstance(profile.json_schema, dict) else {}
    mode = prompt_mode_override
    if not mode and schema:
        mode = schema.get("x-prompt-mode")

    if profile.system_prompt.strip():
        prompt = f"{profile.system_prompt}\n\n{profile.user_template}".strip()
    else:
        prompt = None

    response_schema = schema if schema else None
    return prompt, mode, response_schema
