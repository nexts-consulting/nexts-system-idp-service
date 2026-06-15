#!/usr/bin/env python
import argparse
import base64
import copy
import json
import mimetypes
import os
import re
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from jiwer import wer
from openai import OpenAI
from rapidfuzz import fuzz
from rapidfuzz.distance import Levenshtein


# All invoice numeric keys (canonicalization + exact_match for money fields).
CANONICAL_NUMERIC_FIELDS = ["total_amount", "customer_payment", "change", "discount", "cash"]
# Exact-match field accuracy + macro precision (does NOT include optional F1 fields below).
FIELD_EVAL_KEYS = [
    "store_name",
    "date",
    "time",
    "bill_number",
    "total_amount",
]
# F1 only; excluded from report "precision" (macro over FIELD_EVAL_KEYS).
OPTIONAL_F1_FIELDS = ["discount", "cash", "customer_payment", "change"]
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
REMOVE_ACCENTS_FOR_EVAL = True

# Relaxed evaluation defaults (industry-style tolerance + fuzzy text).
RELATIVE_NUMERIC_TOLERANCE = 0.02
ABS_NUMERIC_TOLERANCE = 1e-3
FUZZY_TEXT_THRESHOLD = 85.0  # token_sort_ratio 0–100
NUMERIC_SOFT_DENOM_SCALE = 0.10  # for partial numeric score tail beyond strict/relaxed


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def normalize_whitespace(text: Optional[str]) -> str:
    if text is None:
        return ""
    s = str(text)
    if REMOVE_ACCENTS_FOR_EVAL:
        s = remove_vietnamese_accents(s)
    return re.sub(r"\s+", " ", s).strip().lower()


def remove_vietnamese_accents(text: Optional[str]) -> str:
    if text is None:
        return ""
    s = str(text)
    s = s.replace("Đ", "D").replace("đ", "d")
    normalized = unicodedata.normalize("NFD", s)
    return "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")


def normalize_numeric(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s:
        return None

    # Keep only numeric-related symbols first.
    s = re.sub(r"[^\d,\.\-]", "", s)
    if s in {"", "-", ".", "-."}:
        return None

    # Heuristics for Vietnamese receipts:
    # - "16.000" should be interpreted as 16000 (thousand separator)
    # - "16,000" should also be 16000
    # - decimal values are still supported (e.g. "1.5", "1,5")
    has_dot = "." in s
    has_comma = "," in s

    if has_dot and has_comma:
        # Mixed separators: assume the right-most symbol is decimal separator,
        # the other symbol is thousands separator.
        last_dot = s.rfind(".")
        last_comma = s.rfind(",")
        if last_dot > last_comma:
            # decimal = dot, thousands = comma
            s = s.replace(",", "")
        else:
            # decimal = comma, thousands = dot
            s = s.replace(".", "")
            s = s.replace(",", ".")
    elif has_dot and not has_comma:
        dot_groups = s.split(".")
        # If all groups after first are exactly 3 digits, treat dots as thousands separators.
        if len(dot_groups) > 1 and all(g.isdigit() and len(g) == 3 for g in dot_groups[1:]):
            s = "".join(dot_groups)
    elif has_comma and not has_dot:
        comma_groups = s.split(",")
        # If all groups after first are exactly 3 digits, treat commas as thousands separators.
        if len(comma_groups) > 1 and all(g.isdigit() and len(g) == 3 for g in comma_groups[1:]):
            s = "".join(comma_groups)
        else:
            # likely decimal comma
            s = s.replace(",", ".")

    try:
        return float(s)
    except ValueError:
        return None


def normalize_vn_number_literal(token: str) -> str:
    """
    Normalize numeric literal text while preserving thousand-separator intent.
    Examples:
      43.800 -> 43800
      50,000 -> 50000
      1,5    -> 1.5
    """
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
            # decimal = dot, thousands = comma
            s = s.replace(",", "")
        else:
            # decimal = comma, thousands = dot
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


def canonicalize_invoice(inv: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    out["store_name"] = normalize_whitespace(inv.get("store_name"))
    out["date"] = normalize_date(inv.get("date"))
    out["time"] = normalize_time(inv.get("time"))
    out["bill_number"] = normalize_whitespace(inv.get("bill_number"))

    products = inv.get("products") or []
    canonical_products = []
    for p in products:
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
    a: Optional[float],
    b: Optional[float],
    *,
    rtol: float = 0.0,
    atol: float = ABS_NUMERIC_TOLERANCE,
) -> bool:
    """
    Float equality: strict when rtol=0 (legacy exact metrics).
    With rtol>0: pass if |a-b| <= max(atol, rtol * max(|a|,|b|, eps)) — common mixed relative/absolute check.
    """
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


def fuzzy_text_match(
    a: Any,
    b: Any,
    threshold: float = FUZZY_TEXT_THRESHOLD,
) -> Tuple[bool, float]:
    """Token-order–invariant fuzzy match; returns (passes_threshold, token_sort_ratio 0–100)."""
    sa = normalize_whitespace(str(a) if a is not None else "")
    sb = normalize_whitespace(str(b) if b is not None else "")
    if not sa and not sb:
        return True, 100.0
    ratio = float(fuzz.token_sort_ratio(sa, sb))
    return ratio >= threshold, ratio


def fix_line_amount(product: Dict[str, Any]) -> Dict[str, Any]:
    """
    If quantity * unit_price ≈ line_amount (relaxed), snap line_amount to q*p for consistency.
    If line_amount missing but q,p present, fill line_amount = q*p.
    """
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


def apply_rule_based_product_corrections(invoice: Dict[str, Any]) -> Dict[str, Any]:
    """Deep copy + per-line rule fixes (evaluation-side only; does not affect strict raw path)."""
    inv = copy.deepcopy(invoice)
    prods = inv.get("products") or []
    inv["products"] = [fix_line_amount(dict(p)) for p in prods]
    return inv


def match_products(
    gt_rows: List[Dict[str, Any]],
    pred_rows: List[Dict[str, Any]],
    name_threshold: float = FUZZY_TEXT_THRESHOLD,
) -> List[Tuple[int, int, float]]:
    """
    One-to-one greedy matching: sort all (gt_i, pred_j) pairs by name token_sort_ratio descending,
    assign each gt and pred at most once. No duplicate matching.
    Returns (gt_idx, pred_idx, name_ratio).
    """
    candidates: List[Tuple[float, int, int]] = []
    for i, g in enumerate(gt_rows):
        gn = g.get("product_name") or ""
        for j, p in enumerate(pred_rows):
            pn = p.get("product_name") or ""
            ratio = float(fuzz.token_sort_ratio(gn, pn)) if (gn or pn) else (100.0 if not gn and not pn else 0.0)
            if ratio >= name_threshold:
                candidates.append((ratio, i, j))
    candidates.sort(key=lambda t: t[0], reverse=True)
    used_g: set = set()
    used_p: set = set()
    pairs: List[Tuple[int, int, float]] = []
    for ratio, i, j in candidates:
        if i in used_g or j in used_p:
            continue
        used_g.add(i)
        used_p.add(j)
        pairs.append((i, j, ratio))
    return pairs


def numeric_field_score(gt_val: Any, pred_val: Any) -> float:
    """Partial credit in [0,1]: strict 1.0, relaxed match ~0.95, else linear decay by error scale."""
    g = normalize_numeric(gt_val)
    p = normalize_numeric(pred_val)
    if g is None and p is None:
        return 1.0
    if g is None or p is None:
        return 0.0
    if safe_float_equal(g, p, rtol=0.0, atol=ABS_NUMERIC_TOLERANCE):
        return 1.0
    if safe_float_equal(g, p, rtol=RELATIVE_NUMERIC_TOLERANCE, atol=ABS_NUMERIC_TOLERANCE):
        return 0.95
    scale = max(abs(g), 1.0)
    err = abs(g - p)
    return max(0.0, 1.0 - err / max(scale * NUMERIC_SOFT_DENOM_SCALE, 1e-9))


def text_field_score(gt_val: Any, pred_val: Any) -> float:
    _, ratio = fuzzy_text_match(gt_val, pred_val, threshold=0.0)
    return ratio / 100.0


def date_time_field_score(gt_val: Any, pred_val: Any, field: str) -> float:
    if field == "date":
        if normalize_date(gt_val) == normalize_date(pred_val):
            return 1.0
    else:
        if normalize_time(gt_val) == normalize_time(pred_val):
            return 1.0
    return text_field_score(gt_val, pred_val)


def invoice_field_partial_scores(gt: Dict[str, Any], pred: Dict[str, Any]) -> Dict[str, float]:
    scores: Dict[str, float] = {}
    for k in FIELD_EVAL_KEYS:
        if k in {"store_name", "bill_number"}:
            scores[k] = text_field_score(gt.get(k), pred.get(k))
        elif k == "date":
            scores[k] = date_time_field_score(gt.get(k), pred.get(k), "date")
        elif k == "time":
            scores[k] = date_time_field_score(gt.get(k), pred.get(k), "time")
        elif k == "total_amount":
            scores[k] = numeric_field_score(gt.get(k), pred.get(k))
        else:
            scores[k] = text_field_score(gt.get(k), pred.get(k))
    return scores


def product_pair_relaxed_scores(gt_row: Dict[str, Any], pred_row: Dict[str, Any]) -> Dict[str, float]:
    _, name_ratio = fuzzy_text_match(gt_row.get("product_name"), pred_row.get("product_name"), threshold=0.0)
    return {
        "name": name_ratio / 100.0,
        "quantity": numeric_field_score(gt_row.get("quantity"), pred_row.get("quantity")),
        "product_price": numeric_field_score(gt_row.get("product_price"), pred_row.get("product_price")),
        "line_amount": numeric_field_score(gt_row.get("line_amount"), pred_row.get("line_amount")),
    }


def relaxed_invoice_passes(
    gt: Dict[str, Any],
    pred: Dict[str, Any],
    pairs: List[Tuple[int, int, float]],
    gt_n: int,
) -> bool:
    """Binary relaxed success: all header fields pass fuzzy/approx + every GT line has a good match."""
    for k in FIELD_EVAL_KEYS:
        if k in {"store_name", "bill_number"}:
            ok, _ = fuzzy_text_match(gt.get(k), pred.get(k))
            if not ok:
                return False
        elif k == "date":
            if normalize_date(gt.get(k)) != normalize_date(pred.get(k)):
                ok, _ = fuzzy_text_match(gt.get(k), pred.get(k))
                if not ok:
                    return False
        elif k == "time":
            if normalize_time(gt.get(k)) != normalize_time(pred.get(k)):
                ok, _ = fuzzy_text_match(gt.get(k), pred.get(k))
                if not ok:
                    return False
        elif k == "total_amount":
            if not safe_float_equal(
                normalize_numeric(gt.get(k)),
                normalize_numeric(pred.get(k)),
                rtol=RELATIVE_NUMERIC_TOLERANCE,
                atol=ABS_NUMERIC_TOLERANCE,
            ):
                return False
        else:
            ok, _ = fuzzy_text_match(gt.get(k), pred.get(k))
            if not ok:
                return False

    matched_gt = {i for i, _, _ in pairs}
    if gt_n > 0 and matched_gt != set(range(gt_n)):
        return False
    gt_products = gt.get("products") or []
    pred_products = pred.get("products") or []
    for gi, pj, _ in pairs:
        g_row = gt_products[gi]
        p_row = pred_products[pj]
        s = product_pair_relaxed_scores(g_row, p_row)
        if s["quantity"] < 0.95 or s["product_price"] < 0.95 or s["line_amount"] < 0.95:
            return False
    return True


def weighted_invoice_score_from_parts(field_scores: Dict[str, float], product_row_scores: List[float]) -> float:
    fe = sum(field_scores.values()) / max(len(field_scores), 1)
    if not product_row_scores:
        return fe
    pe = sum(product_row_scores) / len(product_row_scores)
    return 0.5 * fe + 0.5 * pe


def exact_match(a: Any, b: Any, field: str) -> bool:
    if field in {"date"}:
        return normalize_date(a) == normalize_date(b)
    if field in {"time"}:
        return normalize_time(a) == normalize_time(b)
    if field in CANONICAL_NUMERIC_FIELDS:
        return safe_float_equal(
            normalize_numeric(a), normalize_numeric(b), rtol=0.0, atol=ABS_NUMERIC_TOLERANCE
        )
    return normalize_whitespace(str(a) if a is not None else "") == normalize_whitespace(
        str(b) if b is not None else ""
    )


def cer_score(ref: str, hyp: str) -> float:
    ref = ref or ""
    hyp = hyp or ""
    if len(ref) == 0:
        return 0.0 if len(hyp) == 0 else 1.0
    return Levenshtein.distance(ref, hyp) / len(ref)


def to_text_blob(invoice: Dict[str, Any]) -> str:
    return json.dumps(invoice, ensure_ascii=False, sort_keys=True)


@dataclass
class InferenceResult:
    prediction: Dict[str, Any]
    latency_ms: float
    raw_response: str


class InternLMExtractor:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model_name: str,
        temperature: float = 0.0,
        max_tokens: int = 1024,
    ) -> None:
        self.client = OpenAI(base_url=base_url, api_key=api_key)
        self.model_name = model_name
        self.temperature = temperature
        self.max_tokens = max_tokens

    @staticmethod
    def _image_to_data_url(image_path: Path) -> str:
        mime, _ = mimetypes.guess_type(str(image_path))
        if not mime:
            mime = "image/jpeg"
        with image_path.open("rb") as f:
            encoded = base64.b64encode(f.read()).decode("utf-8")
        return f"data:{mime};base64,{encoded}"

    @staticmethod
    def _prompt_for_mode(mode: str) -> str:
        base = (
            "You are an expert OCR + IE model for Vietnamese invoices.\n"
            "Extract invoice information and return ONLY valid JSON.\n"
            "Use this strict schema:\n"
            "{\n"
            '  "store_name": "string",\n'
            '  "date": "dd/mm/yyyy",\n'
            '  "time": "hh:mm",\n'
            '  "bill_number": "string",\n'
            '  "products": [{"product_name":"string","product_code":"string|null","quantity":number,"product_price":number,"unit":"string|null","line_amount":number}],\n'
            '  "total_amount": number,\n'
            '  "discount": number|null,\n'
            '  "customer_payment": number,\n'
            '  "cash": number|null,\n'
            '  "change": number\n'
            "}\n"
            "Rules:\n"
            "- Keep Vietnamese text correctly.\n"
            "- For numeric fields (quantity, product_price, line_amount, total_amount, discount, customer_payment, cash, change):\n"
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
              base +
              "\nThink step-by-step internally:\n"
              "- First identify product table region.\n"
              "- Then extract only valid product rows.\n"
              "- Then extract summary fields separately.\n"
          )

        if mode == "reasoning_vir":
          return (
              base +
                "\nApply visual-invariant reasoning (ViR) and strict extraction rules:\n"
                "- **Strict OCR Integrity**: Extract values EXACTLY as written on the receipt. DO NOT perform any mathematical calculations (e.g., do not multiply quantity by price to invent line_amount, do not sum up to invent total_amount). If a value is missing or unreadable, follow the schema rules.\n"
                "- **Table Structure Detection**: Identify rows clearly. Be careful with columns containing 'SL' (Quantity), 'VAT', and 'T.tiền' (Line Amount). Do not confuse the VAT percentage (e.g., '8%') with quantity or unit.\n"
                "- **Summary Fields Mapping**:\n"
                "  * 'total_amount': Must map to the FINAL actual payment amount that the customer has to pay (often labeled as 'Thanh toán', 'Tổng cộng thanh toán', or the final circled/highlighted total value). Do not mistake it for the subtotal before discount ('Tổng tiền').\n"
                "  * 'discount': Map to 'Chiết khấu' or 'Giảm giá' if present.\n"
                "  * 'customer_payment' & 'cash': Map to 'Tiền mặt' or 'Khách đưa'.\n"
                "  * 'change': Map to 'Tiền trả lại' or 'Tiền thừa'.\n"
                "- **Noise Filtering**: Ignore currency symbols like 'đ' or 'd' when parsing numbers. Do not let text formatting artifacts corrupt the numeric extraction (e.g., '8%' VAT should not become quantity 8)."
        )
        raise ValueError(f"Unsupported mode: {mode}")

    @staticmethod
    def _preprocess_numeric_literals(raw_text: str) -> str:
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

    @staticmethod
    def _extract_json(raw_text: str) -> Dict[str, Any]:
        raw_text = raw_text.strip()
        if raw_text.startswith("```"):
            raw_text = raw_text.strip("`")
            raw_text = raw_text.replace("json", "", 1).strip()
        raw_text = InternLMExtractor._preprocess_numeric_literals(raw_text)
        try:
            return json.loads(raw_text)
        except json.JSONDecodeError:
            start = raw_text.find("{")
            end = raw_text.rfind("}")
            if start != -1 and end != -1 and start < end:
                return json.loads(InternLMExtractor._preprocess_numeric_literals(raw_text[start : end + 1]))
            raise

    def infer_invoice(self, image_path: Path, mode: str) -> InferenceResult:
        prompt = self._prompt_for_mode(mode)
        img_data_url = self._image_to_data_url(image_path)
        t0 = time.perf_counter()
        resp = self.client.chat.completions.create(
            model=self.model_name,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            messages=[
                {"role": "system", "content": prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Extract this invoice into the exact JSON schema."},
                        {"type": "image_url", "image_url": {"url": img_data_url}},
                    ],
                },
            ],
        )
        latency_ms = (time.perf_counter() - t0) * 1000.0
        raw_text = resp.choices[0].message.content or "{}"
        pred = self._extract_json(raw_text)
        return InferenceResult(
            prediction=canonicalize_invoice(pred), latency_ms=latency_ms, raw_response=raw_text
        )


class InvoiceEvaluator:
    def __init__(self, gt_by_file: Dict[str, Dict[str, Any]]) -> None:
        self.gt_by_file = gt_by_file

    def evaluate(self, predictions: List[Dict[str, Any]]) -> Dict[str, Any]:
        total = len(predictions)
        if total == 0:
            zprod = {
                "name_similarity": 0.0,
                "quantity_accuracy": 0.0,
                "price_accuracy": 0.0,
                "line_amount_accuracy": 0.0,
            }
            return {
                "precision": 0.0,
                "cer": 0.0,
                "wer": 0.0,
                "latency_ms": {"mean": 0.0, "p50": 0.0, "p95": 0.0},
                "field_accuracy": {k: 0.0 for k in FIELD_EVAL_KEYS},
                "field_f1_optional": {k: 0.0 for k in OPTIONAL_F1_FIELDS},
                "product_accuracy": zprod.copy(),
                "product_accuracy_best_match": zprod.copy(),
                "invoice_accuracy": 0.0,
                "relaxed_invoice_accuracy": 0.0,
                "weighted_invoice_score": 0.0,
                "total_invoices": 0,
            }

        cer_vals: List[float] = []
        wer_vals: List[float] = []
        latencies: List[float] = []
        field_correct = {k: 0 for k in FIELD_EVAL_KEYS}
        invoice_all_correct = 0
        relaxed_invoice_ok = 0
        weighted_invoice_sum = 0.0
        tp_optional = {k: 0 for k in OPTIONAL_F1_FIELDS}
        fp_optional = {k: 0 for k in OPTIONAL_F1_FIELDS}
        fn_optional = {k: 0 for k in OPTIONAL_F1_FIELDS}

        prod_name_sim_sum = 0.0
        prod_qty_correct = 0
        prod_price_correct = 0
        prod_line_correct = 0
        prod_total_pairs = 0

        prod_bm_name_sum = 0.0
        prod_bm_qty_ok = 0
        prod_bm_price_ok = 0
        prod_bm_line_ok = 0
        prod_bm_gt_lines = 0

        for item in predictions:
            file_name = item["file_name"]
            pred = canonicalize_invoice(item["prediction"])
            gt = canonicalize_invoice(self.gt_by_file[file_name]["standardized"])
            pred_relaxed = apply_rule_based_product_corrections(pred)
            latencies.append(float(item["latency_ms"]))

            pred_blob = to_text_blob(pred)
            gt_blob = to_text_blob(gt)
            cer_vals.append(cer_score(gt_blob, pred_blob))
            wer_vals.append(wer(gt_blob, pred_blob))

            this_invoice_ok = True
            for k in FIELD_EVAL_KEYS:
                ok = exact_match(pred.get(k), gt.get(k), k)
                field_correct[k] += int(ok)
                if not ok:
                    this_invoice_ok = False

            for k in OPTIONAL_F1_FIELDS:
                pred_present = pred.get(k) is not None
                gt_present = gt.get(k) is not None
                if pred_present and gt_present and exact_match(pred.get(k), gt.get(k), k):
                    tp_optional[k] += 1
                elif pred_present and not gt_present:
                    fp_optional[k] += 1
                elif (not pred_present) and gt_present:
                    fn_optional[k] += 1
                elif pred_present and gt_present:
                    fp_optional[k] += 1
                    fn_optional[k] += 1

            gt_products = gt.get("products") or []
            pred_products = pred.get("products") or []
            n = min(len(gt_products), len(pred_products))
            if len(gt_products) != len(pred_products):
                this_invoice_ok = False
            for idx in range(n):
                gp = gt_products[idx]
                pp = pred_products[idx]
                gt_name = gp.get("product_name") or ""
                pr_name = pp.get("product_name") or ""
                max_len = max(len(gt_name), 1)
                name_sim = 1.0 - Levenshtein.distance(gt_name, pr_name) / max_len
                prod_name_sim_sum += max(0.0, name_sim)
                prod_qty_correct += int(
                    safe_float_equal(
                        normalize_numeric(gp.get("quantity")),
                        normalize_numeric(pp.get("quantity")),
                        rtol=0.0,
                        atol=ABS_NUMERIC_TOLERANCE,
                    )
                )
                prod_price_correct += int(
                    safe_float_equal(
                        normalize_numeric(gp.get("product_price")),
                        normalize_numeric(pp.get("product_price")),
                        rtol=0.0,
                        atol=ABS_NUMERIC_TOLERANCE,
                    )
                )
                prod_line_correct += int(
                    safe_float_equal(
                        normalize_numeric(gp.get("line_amount")),
                        normalize_numeric(pp.get("line_amount")),
                        rtol=0.0,
                        atol=ABS_NUMERIC_TOLERANCE,
                    )
                )
                prod_total_pairs += 1

            if this_invoice_ok:
                invoice_all_correct += 1

            pred_products_r = pred_relaxed.get("products") or []
            gt_n = len(gt_products)
            prod_bm_gt_lines += gt_n
            pairs = match_products(gt_products, pred_products_r)
            if relaxed_invoice_passes(gt, pred_relaxed, pairs, gt_n):
                relaxed_invoice_ok += 1

            field_partial = invoice_field_partial_scores(gt, pred_relaxed)
            match_by_gt: Dict[int, Tuple[int, float]] = {gi: (pj, r) for gi, pj, r in pairs}
            row_scores: List[float] = []
            for gi in range(gt_n):
                if gi not in match_by_gt:
                    row_scores.append(0.0)
                    continue
                pj, _r = match_by_gt[gi]
                pr_sc = product_pair_relaxed_scores(gt_products[gi], pred_products_r[pj])
                row_scores.append(sum(pr_sc.values()) / 4.0)

                prod_bm_name_sum += pr_sc["name"]
                prod_bm_qty_ok += int(pr_sc["quantity"] >= 0.95)
                prod_bm_price_ok += int(pr_sc["product_price"] >= 0.95)
                prod_bm_line_ok += int(pr_sc["line_amount"] >= 0.95)

            weighted_invoice_sum += weighted_invoice_score_from_parts(field_partial, row_scores)

        def pct(x: float, y: float) -> float:
            return 0.0 if y == 0 else float(x / y)

        field_accuracy = {k: pct(v, total) for k, v in field_correct.items()}
        field_precision = sum(field_correct.values()) / (total * len(FIELD_EVAL_KEYS))

        field_f1_optional = {}
        for k in OPTIONAL_F1_FIELDS:
            tp, fp, fn = tp_optional[k], fp_optional[k], fn_optional[k]
            p = 0.0 if (tp + fp) == 0 else tp / (tp + fp)
            r = 0.0 if (tp + fn) == 0 else tp / (tp + fn)
            f1 = 0.0 if (p + r) == 0 else 2 * p * r / (p + r)
            field_f1_optional[k] = f1

        lat_sorted = sorted(latencies)
        p50_idx = min(len(lat_sorted) - 1, int(0.50 * len(lat_sorted)))
        p95_idx = min(len(lat_sorted) - 1, int(0.95 * len(lat_sorted)))

        report = {
            "precision": field_precision,
            "cer": float(sum(cer_vals) / len(cer_vals)),
            "wer": float(sum(wer_vals) / len(wer_vals)),
            "latency_ms": {
                "mean": float(sum(latencies) / len(latencies)),
                "p50": float(lat_sorted[p50_idx]),
                "p95": float(lat_sorted[p95_idx]),
            },
            "field_accuracy": field_accuracy,
            "field_f1_optional": field_f1_optional,
            "product_accuracy": {
                "name_similarity": pct(prod_name_sim_sum, prod_total_pairs),
                "quantity_accuracy": pct(prod_qty_correct, prod_total_pairs),
                "price_accuracy": pct(prod_price_correct, prod_total_pairs),
                "line_amount_accuracy": pct(prod_line_correct, prod_total_pairs),
            },
            "product_accuracy_best_match": {
                "name_similarity": pct(prod_bm_name_sum, prod_bm_gt_lines),
                "quantity_accuracy": pct(prod_bm_qty_ok, prod_bm_gt_lines),
                "price_accuracy": pct(prod_bm_price_ok, prod_bm_gt_lines),
                "line_amount_accuracy": pct(prod_bm_line_ok, prod_bm_gt_lines),
            },
            "invoice_accuracy": pct(invoice_all_correct, total),
            "relaxed_invoice_accuracy": pct(relaxed_invoice_ok, total),
            "weighted_invoice_score": weighted_invoice_sum / total,
            "total_invoices": total,
        }
        return report


def build_gt_index(gt_items: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out = {}
    for item in gt_items:
        file_name = item.get("file_name")
        if not file_name:
            continue
        out[file_name] = item
    return out


def list_images(folder: Path) -> List[Path]:
    valid_ext = {".jpg", ".jpeg", ".png"}
    return sorted([p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in valid_ext])


def run_pipeline(
    image_folder: Path,
    ground_truth_path: Path,
    output_dir: Path,
    base_url: str,
    api_key: str,
    model_name: str,
    modes: List[str],
) -> None:
    gt_items = read_json(ground_truth_path)
    if not isinstance(gt_items, list):
        raise ValueError("Ground truth JSON must be a list of invoice objects.")
    gt_by_file = build_gt_index(gt_items)

    images = list_images(image_folder)
    if not images:
        raise ValueError(f"No images found in: {image_folder}")

    extractor = InternLMExtractor(base_url=base_url, api_key=api_key, model_name=model_name)
    evaluator = InvoiceEvaluator(gt_by_file)

    ablation_summary = {}

    for mode in modes:
        predictions: List[Dict[str, Any]] = []
        debug_raw: List[Dict[str, Any]] = []

        for img in images:
            print(f"Processing image: {img.name}")
            if img.name not in gt_by_file:
                continue
            try:
                infer = extractor.infer_invoice(img, mode=mode)
                predictions.append(
                    {
                        "file_name": img.name,
                        "prediction": infer.prediction,
                        "latency_ms": infer.latency_ms,
                    }
                )
                debug_raw.append(
                    {
                        "file_name": img.name,
                        "mode": mode,
                        "raw_response": infer.raw_response,
                    }
                )
            except Exception as e:
                predictions.append(
                    {
                        "file_name": img.name,
                        "prediction": canonicalize_invoice({}),
                        "latency_ms": 0.0,
                        "error": str(e),
                    }
                )

        report = evaluator.evaluate(predictions)
        ablation_summary[mode] = report

        pred_path = output_dir / f"predictions_{mode}.json"
        report_path = output_dir / f"evaluation_{mode}.json"
        raw_path = output_dir / f"raw_responses_{mode}.json"
        write_json(pred_path, predictions)
        write_json(report_path, report)
        write_json(raw_path, debug_raw)

    write_json(output_dir / "ablation_summary.json", ablation_summary)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Vietnamese Invoice Extraction + Evaluation with InternLM (vLLM/OpenAI-compatible API)."
    )
    parser.add_argument("--image_folder", required=True, type=Path, help="Path to invoice image folder")
    parser.add_argument("--ground_truth_json", required=True, type=Path, help="Path to standardized GT JSON")
    parser.add_argument("--output_dir", type=Path, default=Path("outputs"), help="Directory for predictions/reports")
    parser.add_argument("--base_url", default="http://localhost:8000/v1", help="vLLM OpenAI API base url")
    parser.add_argument("--api_key", default=os.getenv("OPENAI_API_KEY", "EMPTY"), help="API key")
    parser.add_argument("--model_name", required=True, help="Model served by vLLM, e.g. internlm/internvl")
    parser.add_argument(
        "--modes",
        nargs="+",
        default=["base", "reasoning", "reasoning_vir"],
        choices=["base", "reasoning", "reasoning_vir"],
        help="Ablation modes to run",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_pipeline(
        image_folder=args.image_folder,
        ground_truth_path=args.ground_truth_json,
        output_dir=args.output_dir,
        base_url=args.base_url,
        api_key=args.api_key,
        model_name=args.model_name,
        modes=args.modes,
    )
    print("Pipeline completed. Check output directory for predictions and reports.")


if __name__ == "__main__":
    main()
