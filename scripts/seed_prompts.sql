INSERT INTO prompt_profiles (name, invoice_type, version, system_prompt, user_template, json_schema)
VALUES (
    'vietnamese_invoice_reasoning_vir',
    'receipt',
    '2.0.0',
    '',
    'Extract this invoice into the exact JSON schema.',
    '{
        "x-prompt-mode": "reasoning_vir",
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
                        "line_amount": {"type": "number"}
                    }
                }
            },
            "total_amount": {"type": "number"},
            "discount": {"type": ["number", "null"]},
            "customer_payment": {"type": "number"},
            "cash": {"type": ["number", "null"]},
            "change": {"type": "number"}
        }
    }'::jsonb
)
ON CONFLICT (name, version) DO NOTHING;

INSERT INTO rules (tenant_id, name, priority, condition_json, action_json, enabled)
VALUES
(
    'default',
    'block_fraud',
    10,
    '{"==": [{"var": "fraud.predicted_tampered"}, true]}',
    '{"set": {"blocked": true, "reason": "fraud_detected"}}',
    true
),
(
    'default',
    'require_total',
    50,
    '{"missing": ["extraction.total_amount"]}',
    '{"set": {"validation_warning": "missing_total"}}',
    true
);
