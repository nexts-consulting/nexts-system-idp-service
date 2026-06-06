from idp_extraction.invoice_normalize import (
    canonicalize_invoice,
    normalize_numeric,
    postprocess_extraction,
)
from idp_extraction.prompts import build_system_prompt
from idp_extraction.schema_validator import extract_json_from_text, preprocess_numeric_literals


def test_normalize_vn_thousands():
    assert normalize_numeric("16.000") == 16000.0
    assert normalize_numeric("16,000") == 16000.0


def test_preprocess_numeric_literals_in_json():
    raw = '{"total_amount": 16.000, "quantity": 2}'
    fixed = preprocess_numeric_literals(raw)
    assert "16000" in fixed or "16.000" not in fixed.split("total_amount")[1][:20]


def test_extract_json_with_vn_numbers():
    text = '```json\n{"total_amount": 16.000, "products": []}\n```'
    data = extract_json_from_text(text)
    assert data is not None
    assert data["total_amount"] == 16000.0 or data["total_amount"] == 16000


def test_canonicalize_invoice():
    inv = {
        "store_name": "  Cửa Hàng ABC  ",
        "date": "01-01-2026",
        "time": "9:5",
        "bill_number": "HD001",
        "products": [
            {
                "product_name": "SP A",
                "quantity": "2",
                "product_price": "10.000",
                "line_amount": "20.000",
            }
        ],
        "total_amount": "20.000",
        "customer_payment": 20000,
        "change": 0,
    }
    out = postprocess_extraction(inv)
    assert out["total_amount"] == 20000.0
    assert out["products"][0]["line_amount"] == 20000.0


def test_prompt_modes():
    assert "ViR" in build_system_prompt("reasoning_vir") or "visual-invariant" in build_system_prompt(
        "reasoning_vir"
    )
    assert "step-by-step" in build_system_prompt("reasoning").lower()
