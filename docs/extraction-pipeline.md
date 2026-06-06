# Extraction pipeline (InternLM / InternVL)

Aligned with `references/internlm_invoice_pipeline.py` and `references/internlm_invoice_pipeline_lmdeploy.py`.

## Prompt modes

| Mode | Description |
|------|-------------|
| `base` | Standard Vietnamese invoice JSON schema + numeric rules |
| `reasoning` | + step-by-step table/summary guidance |
| `reasoning_vir` | + visual-invariant reasoning (default in prod) |

Set via:

- Job API: `"prompt_mode": "reasoning_vir"` on `POST /v1/jobs`
- Env: `DEFAULT_PROMPT_MODE` on `idp-extraction`
- Prompt profile: `"x-prompt-mode"` key inside `json_schema`

## Post-processing

After model output:

1. `extract_json_from_text` — parse JSON, fix VN thousand separators in literals (`16.000` → `16000`)
2. `canonicalize_invoice` — normalize dates, times, text, numbers
3. `apply_rule_based_product_corrections` — snap `line_amount` ≈ `quantity * product_price`
4. JSON Schema validation (`INVOICE_JSON_SCHEMA` or profile schema)

## lmdeploy API

Uses OpenAI-compatible `/v1/chat/completions`:

- **system** message: built-in prompt or custom from `prompt_profile`
- **user** message: text + image (base64)

Config: `LMDEPLOY_URL`, `MOCK_MODE`, `MAX_NEW_TOKENS`, `TEMPERATURE`.

## Modules

| File | Role |
|------|------|
| `prompts.py` | `build_system_prompt(mode)` |
| `invoice_schema.py` | JSON schema + constants |
| `invoice_normalize.py` | `canonicalize_invoice`, `postprocess_extraction` |
| `schema_validator.py` | Parse + validate |
| `lmdeploy_client.py` | HTTP client |
