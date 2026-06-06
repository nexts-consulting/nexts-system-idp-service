import json
import re
from typing import Any

from jsonschema import Draft7Validator

from idp_extraction.invoice_schema import JSON_NUMERIC_KEYS
from idp_extraction.invoice_normalize import normalize_vn_number_literal


def preprocess_numeric_literals(raw_text: str) -> str:
    """Fix VN thousand separators in JSON numeric literals before parse."""
    key_pattern = "|".join(re.escape(k) for k in JSON_NUMERIC_KEYS)
    pattern = re.compile(
        rf'("(?P<key>{key_pattern})"\s*:\s*)(?P<num>-?\d[\d\.,]*)(?P<tail>\s*[,}}])'
    )

    def _replace(m: re.Match[str]) -> str:
        prefix = m.group(1)
        num = m.group("num")
        tail = m.group("tail")
        fixed = normalize_vn_number_literal(num)
        return f"{prefix}{fixed}{tail}"

    return pattern.sub(_replace, raw_text)


def extract_json_from_text(text: str) -> dict[str, Any] | None:
    """Parse model output to dict; handles markdown fences and VN numeric literals."""
    raw_text = text.strip()
    if raw_text.startswith("```"):
        raw_text = raw_text.strip("`")
        raw_text = raw_text.replace("json", "", 1).strip()
    raw_text = preprocess_numeric_literals(raw_text)
    try:
        parsed = json.loads(raw_text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        start = raw_text.find("{")
        end = raw_text.rfind("}")
        if start != -1 and end != -1 and start < end:
            try:
                parsed = json.loads(preprocess_numeric_literals(raw_text[start : end + 1]))
                return parsed if isinstance(parsed, dict) else None
            except json.JSONDecodeError:
                return None
    return None


def validate_against_schema(
    data: dict[str, Any], schema: dict[str, Any] | None
) -> tuple[dict[str, Any] | None, str | None]:
    if not schema:
        return data, None
    validator = Draft7Validator(schema)
    errors = sorted(validator.iter_errors(data), key=lambda e: e.path)
    if errors:
        return None, "; ".join(e.message for e in errors[:3])
    return data, None
