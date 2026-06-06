from idp_extraction.schema_validator import extract_json_from_text, validate_against_schema


def test_extract_json_from_markdown():
    text = 'Here is the result:\n{"total_amount": 100}'
    data = extract_json_from_text(text)
    assert data["total_amount"] == 100


def test_validate_schema():
    schema = {"type": "object", "required": ["total_amount"], "properties": {"total_amount": {"type": "number"}}}
    valid, err = validate_against_schema({"total_amount": 1}, schema)
    assert valid is not None
    assert err is None
