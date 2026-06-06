"""Canonicalize Vietnamese invoice JSON (from references/internlm_invoice_pipeline.py)."""

from __future__ import annotations

import copy
import re
import unicodedata
from datetime import datetime
from typing import Any

from idp_extraction.invoice_schema import CANONICAL_NUMERIC_FIELDS

REMOVE_ACCENTS_FOR_EVAL = True
RELATIVE_NUMERIC_TOLERANCE = 0.02
ABS_NUMERIC_TOLERANCE = 1e-3


def normalize_whitespace(text: str | None) -> str:
    if text is None:
        return ""
    s = str(text)
    if REMOVE_ACCENTS_FOR_EVAL:
        s = remove_vietnamese_accents(s)
    return re.sub(r"\s+", " ", s).strip().lower()


def remove_vietnamese_accents(text: str | None) -> str:
    if text is None:
        return ""
    s = str(text)
    s = s.replace("Đ", "D").replace("đ", "d")
    normalized = unicodedata.normalize("NFD", s)
    return "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")


def normalize_numeric(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s:
        return None

    s = re.sub(r"[^\d,\.\-]", "", s)
    if s in {"", "-", ".", "-."}:
        return None

    has_dot = "." in s
    has_comma = "," in s

    if has_dot and has_comma:
        last_dot = s.rfind(".")
        last_comma = s.rfind(",")
        if last_dot > last_comma:
            s = s.replace(",", "")
        else:
            s = s.replace(".", "")
            s = s.replace(",", ".")
    elif has_dot and not has_comma:
        dot_groups = s.split(".")
        if len(dot_groups) > 1 and all(g.isdigit() and len(g) == 3 for g in dot_groups[1:]):
            s = "".join(dot_groups)
    elif has_comma and not has_dot:
        comma_groups = s.split(",")
        if len(comma_groups) > 1 and all(g.isdigit() and len(g) == 3 for g in comma_groups[1:]):
            s = "".join(comma_groups)
        else:
            s = s.replace(",", ".")

    try:
        return float(s)
    except ValueError:
        return None


def normalize_vn_number_literal(token: str) -> str:
    s = token.strip()
    if not re.fullmatch(r"-?\d[\d\.,]*", s):
        return token

    sign = ""
    if s.startswith("-"):
        sign = "-"
        s = s[1:]

    has_dot = "." in s
    has_comma = "," in s

    if has_dot and has_comma:
        last_dot = s.rfind(".")
        last_comma = s.rfind(",")
        if last_dot > last_comma:
            s = s.replace(",", "")
        else:
            s = s.replace(".", "")
            s = s.replace(",", ".")
    elif has_dot and not has_comma:
        groups = s.split(".")
        if len(groups) > 1 and all(g.isdigit() and len(g) == 3 for g in groups[1:]):
            s = "".join(groups)
    elif has_comma and not has_dot:
        groups = s.split(",")
        if len(groups) > 1 and all(g.isdigit() and len(g) == 3 for g in groups[1:]):
            s = "".join(groups)
        else:
            s = s.replace(",", ".")

    return sign + s


def normalize_date(date_str: Any) -> str:
    if date_str is None:
        return ""
    s = normalize_whitespace(str(date_str))
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%y", "%d-%m-%y"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.strftime("%d/%m/%Y")
        except ValueError:
            continue
    return s


def normalize_time(time_str: Any) -> str:
    if time_str is None:
        return ""
    s = normalize_whitespace(str(time_str))
    m = re.search(r"(\d{1,2}):(\d{2})", s)
    if not m:
        return s
    hh = int(m.group(1))
    mm = int(m.group(2))
    if 0 <= hh <= 23 and 0 <= mm <= 59:
        return f"{hh:02d}:{mm:02d}"
    return s


def canonicalize_invoice(inv: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    out["store_name"] = normalize_whitespace(inv.get("store_name"))
    out["date"] = normalize_date(inv.get("date"))
    out["time"] = normalize_time(inv.get("time"))
    out["bill_number"] = normalize_whitespace(inv.get("bill_number"))

    products = inv.get("products") or []
    canonical_products = []
    for p in products:
        if not isinstance(p, dict):
            continue
        canonical_products.append(
            {
                "product_name": normalize_whitespace(p.get("product_name")),
                "product_code": normalize_whitespace(p.get("product_code")) or None,
                "quantity": normalize_numeric(p.get("quantity")),
                "product_price": normalize_numeric(p.get("product_price")),
                "unit": normalize_whitespace(p.get("unit")) or None,
                "line_amount": normalize_numeric(p.get("line_amount")),
            }
        )
    out["products"] = canonical_products

    for key in CANONICAL_NUMERIC_FIELDS:
        out[key] = normalize_numeric(inv.get(key))
    return out


def safe_float_equal(
    a: float | None,
    b: float | None,
    *,
    rtol: float = 0.0,
    atol: float = ABS_NUMERIC_TOLERANCE,
) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    aa, bb = float(a), float(b)
    diff = abs(aa - bb)
    if rtol <= 0.0:
        return diff <= atol
    scale = max(abs(aa), abs(bb), 1e-12)
    return diff <= max(atol, rtol * scale)


def fix_line_amount(product: dict[str, Any]) -> dict[str, Any]:
    out = dict(product)
    qn = normalize_numeric(out.get("quantity"))
    pn = normalize_numeric(out.get("product_price"))
    if qn is None or pn is None:
        return out
    expected = qn * pn
    ln = normalize_numeric(out.get("line_amount"))
    dynamic_atol = max(1.0, abs(expected) * RELATIVE_NUMERIC_TOLERANCE)
    if ln is None:
        out["line_amount"] = expected
        return out
    if safe_float_equal(ln, expected, rtol=RELATIVE_NUMERIC_TOLERANCE, atol=dynamic_atol):
        out["line_amount"] = expected
    return out


def apply_rule_based_product_corrections(invoice: dict[str, Any]) -> dict[str, Any]:
    inv = copy.deepcopy(invoice)
    prods = inv.get("products") or []
    inv["products"] = [fix_line_amount(dict(p)) for p in prods if isinstance(p, dict)]
    return inv


def postprocess_extraction(raw: dict[str, Any]) -> dict[str, Any]:
    """Canonicalize + rule-based line amount fixes (production path)."""
    canonical = canonicalize_invoice(raw)
    return apply_rule_based_product_corrections(canonical)
