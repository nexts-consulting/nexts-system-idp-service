"""Vietnamese invoice JSON schema (aligned with references/internlm_invoice_pipeline.py)."""

from typing import Any

PROMPT_MODES = ("base", "reasoning", "reasoning_vir")

JSON_NUMERIC_KEYS = [
    "quantity",
    "product_price",
    "line_amount",
    "total_amount",
    "discount",
    "customer_payment",
    "cash",
    "change",
]

CANONICAL_NUMERIC_FIELDS = ["total_amount", "customer_payment", "change", "discount", "cash"]

USER_EXTRACTION_LINE = "Extract this invoice into the exact JSON schema."

INVOICE_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["store_name", "date", "bill_number", "products", "total_amount"],
    "properties": {
        "store_name": {"type": "string"},
        "date": {"type": "string"},
        "time": {"type": "string"},
        "bill_number": {"type": "string"},
        "products": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["product_name", "quantity", "product_price", "line_amount"],
                "properties": {
                    "product_name": {"type": "string"},
                    "product_code": {"type": ["string", "null"]},
                    "quantity": {"type": "number"},
                    "product_price": {"type": "number"},
                    "unit": {"type": ["string", "null"]},
                    "line_amount": {"type": "number"},
                },
            },
        },
        "total_amount": {"type": "number"},
        "discount": {"type": ["number", "null"]},
        "customer_payment": {"type": "number"},
        "cash": {"type": ["number", "null"]},
        "change": {"type": "number"},
    },
}
