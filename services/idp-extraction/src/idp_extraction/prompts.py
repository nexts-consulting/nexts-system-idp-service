"""System prompts for Vietnamese invoice extraction (from references/internlm_invoice_pipeline.py)."""

from idp_extraction.invoice_schema import PROMPT_MODES


def build_system_prompt(mode: str = "reasoning_vir") -> str:
    if mode not in PROMPT_MODES:
        raise ValueError(f"Unsupported prompt mode: {mode}. Choose from {PROMPT_MODES}")

    base = (
        "You are an expert OCR + IE model for Vietnamese invoices.\n"
        "Extract invoice information and return ONLY valid JSON.\n"
        "Use this strict schema:\n"
        "{\n"
        '  "store_name": "string",\n'
        '  "date": "dd/mm/yyyy",\n'
        '  "time": "hh:mm",\n'
        '  "bill_number": "string",\n'
        '  "products": [{"product_name":"string","product_code":"string|null","quantity":number,'
        '"product_price":number,"unit":"string|null","line_amount":number}],\n'
        '  "total_amount": number,\n'
        '  "discount": number|null,\n'
        '  "customer_payment": number,\n'
        '  "cash": number|null,\n'
        '  "change": number\n'
        "}\n"
        "Rules:\n"
        "- Keep Vietnamese text correctly.\n"
        "- For numeric fields (quantity, product_price, line_amount, total_amount, discount, "
        "customer_payment, cash, change):\n"
        "  * Output pure numbers only (no currency symbol, no spaces).\n"
        "  * Normalize thousand separators: 16.000 -> 16000, 16,000 -> 16000.\n"
        "  * Do not truncate by separator (never output 16 when source is 16.000).\n"
        "  * Keep decimal values only when they are true decimals (e.g. 1.5).\n"
        "- If unknown optional field: null.\n"
        "- If unknown required field, use empty string for text and 0 for numbers.\n"
        "- Return JSON only, no markdown."
    )

    if mode == "base":
        return base
    if mode == "reasoning":
        return (
            base
            + "\nThink step-by-step internally:\n"
            "- First identify product table region.\n"
            "- Then extract only valid product rows.\n"
            "- Then extract summary fields separately.\n"
        )
    # reasoning_vir
    return (
        base
        + "\nApply visual-invariant reasoning (ViR):\n"
        "- Detect table structure (columns: item | unit price | quantity | amount)\n"
        "- Separate table region vs summary region\n"
        "- Cross-check totals and fix misclassified rows\n"
    )
