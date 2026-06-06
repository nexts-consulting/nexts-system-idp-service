#!/usr/bin/env python
"""
Hóa đơn: trích xuất + đánh giá giống internlm_invoice_pipeline.py (cùng schema JSON / metrics),
nhưng inference qua LMDeploy (pipeline + batch prompts) thay vì vLLM OpenAI API.

Throughput mỗi lần chạy: Wall time (s), Requests/sec, Output tokens/s, Total output tokens.
Tùy chọn --compare-throughput: đo thêm mode còn lại (single vs batch) và ghi IMPROVE.
"""
from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Sequence, Tuple

import torch

from internlm_invoice_pipeline import (
    InternLMExtractor,
    InvoiceEvaluator,
    build_gt_index,
    canonicalize_invoice,
    list_images,
    read_json,
    write_json,
)

try:
    from lmdeploy import GenerationConfig, PytorchEngineConfig, TurbomindEngineConfig, pipeline
    from lmdeploy.vl import load_image
except ImportError as e:  # pragma: no cover
    raise ImportError(
        "Cần lmdeploy (và timm cho InternVL). Ví dụ: pip install lmdeploy timm"
    ) from e

InferenceMode = Literal["single", "batch"]
EngineBackend = Literal["pytorch", "turbomind"]


@dataclass
class ThroughputMetrics:
    wall_time_s: float
    num_requests: int
    requests_per_sec: float
    output_tokens_per_sec: float
    total_output_tokens: int
    inference_mode: str
    batch_size: Optional[int]
    model_path: str


def _normalize_pipe_output(out: Any) -> List[Any]:
    if isinstance(out, list):
        return out
    return [out]


def _response_text(r: Any) -> str:
    t = getattr(r, "text", None)
    if t is not None:
        return str(t)
    c = getattr(r, "content", None)
    if c is not None:
        return str(c)
    return str(r)


def _sum_output_tokens(results: Sequence[Any]) -> Tuple[int, List[int]]:
    total = 0
    lens: List[int] = []
    for r in results:
        n = getattr(r, "generate_token_len", None)
        if n is None:
            n = 0
        try:
            n = int(n)
        except (TypeError, ValueError):
            n = 0
        lens.append(n)
        total += n
    return total, lens


def _build_backend(
    backend: EngineBackend,
    *,
    tp: int,
    session_len: int,
    cache_max_entry_count: float,
    model_format: str,
) -> Any:
    if backend == "pytorch":
        return PytorchEngineConfig(session_len=session_len, tp=tp)
    return TurbomindEngineConfig(
        tp=tp,
        cache_max_entry_count=cache_max_entry_count,
        session_len=session_len,
        model_format=model_format,
    )


def create_lmdeploy_pipe(
    model_path: str,
    *,
    backend: EngineBackend = "pytorch",
    tp: int = 1,
    session_len: int = 32768,
    cache_max_entry_count: float = 0.8,
    model_format: str = "hf",
) -> Any:
    bc = _build_backend(
        backend,
        tp=tp,
        session_len=session_len,
        cache_max_entry_count=cache_max_entry_count,
        model_format=model_format,
    )
    return pipeline(model_path, backend_config=bc)


def _warmup(
    pipe: Any,
    sample_path: Path,
    system_prompt: str,
    gen_config: GenerationConfig,
) -> None:
    img = load_image(str(sample_path))
    user_line = "Extract this invoice into the exact JSON schema."
    _ = pipe(
        [(system_prompt + "\n\n" + user_line, img)],
        gen_config=gen_config,
    )
    if torch.cuda.is_available():
        torch.cuda.synchronize()


def _infer_single_dataset(
    pipe: Any,
    images: List[Path],
    system_prompt: str,
    gen_config: GenerationConfig,
    *,
    model_path: str = "",
    sync_cuda: bool = True,
) -> Tuple[List[Dict[str, Any]], ThroughputMetrics, float]:
    """Tuần tự từng ảnh; đo wall time và token (không tính warmup)."""
    if sync_cuda and torch.cuda.is_available():
        torch.cuda.synchronize()
    t0 = time.perf_counter()
    results: List[Dict[str, Any]] = []
    total_tokens = 0
    user_line = "Extract this invoice into the exact JSON schema."
    combined = system_prompt + "\n\n" + user_line
    for img_path in images:
        t_req = time.perf_counter()
        try:
            img = load_image(str(img_path))
            raw = pipe((combined, img), gen_config=gen_config)
            lat_ms = (time.perf_counter() - t_req) * 1000.0
            out = _normalize_pipe_output(raw)[0]
            raw_text = _response_text(out)
            tok = int(getattr(out, "generate_token_len", 0) or 0)
            total_tokens += tok
            try:
                pred = InternLMExtractor._extract_json(raw_text)
            except Exception:
                pred = {}
            results.append(
                {
                    "file_name": img_path.name,
                    "prediction": canonicalize_invoice(pred),
                    "latency_ms": lat_ms,
                    "raw_response": raw_text,
                    "_gen_tokens": tok,
                }
            )
        except Exception as e:
            results.append(
                {
                    "file_name": img_path.name,
                    "prediction": canonicalize_invoice({}),
                    "latency_ms": (time.perf_counter() - t_req) * 1000.0,
                    "raw_response": "",
                    "error": str(e),
                }
            )
    if sync_cuda and torch.cuda.is_available():
        torch.cuda.synchronize()
    elapsed = time.perf_counter() - t0
    n = len(images)
    return _finalize_results(
        results,
        elapsed,
        n,
        total_tokens,
        "single",
        None,
        model_path,
    )


def _infer_batch_dataset(
    pipe: Any,
    images: List[Path],
    system_prompt: str,
    gen_config: GenerationConfig,
    batch_size: int,
    *,
    model_path: str = "",
    sync_cuda: bool = True,
) -> Tuple[List[Dict[str, Any]], ThroughputMetrics, float]:
    """Batch prompts: mỗi chunk gọi pipe(list). latency_ms ≈ chunk_time / chunk_len."""
    if batch_size < 1:
        raise ValueError("batch_size must be >= 1")
    results: List[Dict[str, Any]] = []
    user_line = "Extract this invoice into the exact JSON schema."
    line = system_prompt + "\n\n" + user_line
    if sync_cuda and torch.cuda.is_available():
        torch.cuda.synchronize()
    t0 = time.perf_counter()
    total_tokens = 0
    for start in range(0, len(images), batch_size):
        chunk = images[start : start + batch_size]
        t_chunk = time.perf_counter()
        try:
            prompts: List[Tuple[str, Any]] = [
                (line, load_image(str(p))) for p in chunk
            ]
            raw = pipe(prompts, gen_config=gen_config)
            chunk_elapsed = time.perf_counter() - t_chunk
            outs = _normalize_pipe_output(raw)
            if len(outs) != len(chunk):
                raise RuntimeError(
                    f"Batch output length {len(outs)} != batch chunk size {len(chunk)}"
                )
            per_ms = (chunk_elapsed / max(len(chunk), 1)) * 1000.0
            for img_path, out in zip(chunk, outs):
                raw_text = _response_text(out)
                tok = int(getattr(out, "generate_token_len", 0) or 0)
                total_tokens += tok
                try:
                    pred = InternLMExtractor._extract_json(raw_text)
                except Exception:
                    pred = {}
                results.append(
                    {
                        "file_name": img_path.name,
                        "prediction": canonicalize_invoice(pred),
                        "latency_ms": per_ms,
                        "raw_response": raw_text,
                        "_gen_tokens": tok,
                    }
                )
        except Exception as e:
            per_ms = (time.perf_counter() - t_chunk) * 1000.0 / max(len(chunk), 1)
            for img_path in chunk:
                results.append(
                    {
                        "file_name": img_path.name,
                        "prediction": canonicalize_invoice({}),
                        "latency_ms": per_ms,
                        "raw_response": "",
                        "error": str(e),
                    }
                )
    if sync_cuda and torch.cuda.is_available():
        torch.cuda.synchronize()
    elapsed = time.perf_counter() - t0
    n = len(images)
    return _finalize_results(
        results,
        elapsed,
        n,
        total_tokens,
        "batch",
        batch_size,
        model_path,
    )


def _finalize_results(
    results: List[Dict[str, Any]],
    elapsed: float,
    n: int,
    total_tokens: int,
    mode: str,
    batch_size: Optional[int],
    model_path: str,
) -> Tuple[List[Dict[str, Any]], ThroughputMetrics, float]:
    rps = n / elapsed if elapsed > 0 else 0.0
    tps = total_tokens / elapsed if elapsed > 0 else 0.0
    metrics = ThroughputMetrics(
        wall_time_s=elapsed,
        num_requests=n,
        requests_per_sec=rps,
        output_tokens_per_sec=tps,
        total_output_tokens=total_tokens,
        inference_mode=mode,
        batch_size=batch_size,
        model_path=model_path,
    )
    return results, metrics, elapsed


def _timed_pass_single_only(
    pipe: Any,
    images: List[Path],
    system_prompt: str,
    gen_config: GenerationConfig,
) -> ThroughputMetrics:
    """Chỉ đo throughput, không trả prediction (dùng khi compare)."""
    if torch.cuda.is_available():
        torch.cuda.synchronize()
    t0 = time.perf_counter()
    total_tokens = 0
    for img_path in images:
        img = load_image(str(img_path))
        user_line = "Extract this invoice into the exact JSON schema."
        combined = system_prompt + "\n\n" + user_line
        raw = pipe((combined, img), gen_config=gen_config)
        out = _normalize_pipe_output(raw)[0]
        total_tokens += int(getattr(out, "generate_token_len", 0) or 0)
    if torch.cuda.is_available():
        torch.cuda.synchronize()
    elapsed = time.perf_counter() - t0
    n = len(images)
    return ThroughputMetrics(
        wall_time_s=elapsed,
        num_requests=n,
        requests_per_sec=n / elapsed if elapsed > 0 else 0.0,
        output_tokens_per_sec=total_tokens / elapsed if elapsed > 0 else 0.0,
        total_output_tokens=total_tokens,
        inference_mode="single",
        batch_size=None,
        model_path="",
    )


def _timed_pass_batch_only(
    pipe: Any,
    images: List[Path],
    system_prompt: str,
    gen_config: GenerationConfig,
    batch_size: int,
) -> ThroughputMetrics:
    if torch.cuda.is_available():
        torch.cuda.synchronize()
    t0 = time.perf_counter()
    total_tokens = 0
    for start in range(0, len(images), batch_size):
        chunk = images[start : start + batch_size]
        prompts = [
            (
                system_prompt + "\n\n" + "Extract this invoice into the exact JSON schema.",
                load_image(str(p)),
            )
            for p in chunk
        ]
        raw = pipe(prompts, gen_config=gen_config)
        for out in _normalize_pipe_output(raw):
            total_tokens += int(getattr(out, "generate_token_len", 0) or 0)
    if torch.cuda.is_available():
        torch.cuda.synchronize()
    elapsed = time.perf_counter() - t0
    n = len(images)
    return ThroughputMetrics(
        wall_time_s=elapsed,
        num_requests=n,
        requests_per_sec=n / elapsed if elapsed > 0 else 0.0,
        output_tokens_per_sec=total_tokens / elapsed if elapsed > 0 else 0.0,
        total_output_tokens=total_tokens,
        inference_mode="batch",
        batch_size=batch_size,
        model_path="",
    )


def _metrics_to_dict(m: ThroughputMetrics, model_path: str) -> Dict[str, Any]:
    d = asdict(m)
    d["model_path"] = model_path
    return d


def compute_improve(single: ThroughputMetrics, batch: ThroughputMetrics) -> Dict[str, Any]:
    """
    IMPROVE: hệ số tốc độ (batch so với single). Wall time: single_wall / batch_wall (>1 = batch nhanh hơn).
    """
    sw, bw = single.wall_time_s, batch.wall_time_s
    rs, rb = single.requests_per_sec, batch.requests_per_sec
    ts, tb = single.output_tokens_per_sec, batch.output_tokens_per_sec

    def ratio(a: float, b: float) -> Optional[float]:
        if b <= 0:
            return None
        return a / b

    return {
        "wall_time_speedup_single_over_batch": ratio(sw, bw),  # >1: batch nhanh hơn (ít giây hơn)
        "requests_per_sec_ratio_batch_over_single": ratio(rb, rs),
        "output_tokens_per_sec_ratio_batch_over_single": ratio(tb, ts),
        "total_output_tokens_single": single.total_output_tokens,
        "total_output_tokens_batch": batch.total_output_tokens,
        "note": "Token đếm từ LMDeploy Response.generate_token_len; hai pass có thể lệch nhẹ do sampling.",
    }


def run_pipeline_lmdeploy(
    image_folder: Path,
    ground_truth_path: Path,
    output_dir: Path,
    model_path: str,
    modes: List[str],
    *,
    inference_mode: InferenceMode = "single",
    batch_size: int = 4,
    max_new_tokens: int = 1024,
    temperature: float = 0.0,
    compare_throughput: bool = False,
    backend: EngineBackend = "pytorch",
    tp: int = 1,
    session_len: int = 32768,
    cache_max_entry_count: float = 0.8,
    model_format: str = "hf",
    pipe: Optional[Any] = None,
    close_pipe_when_done: bool = True,
) -> None:
    """
    Nếu truyền `pipe` (đã `create_lmdeploy_pipe`), không tải lại model — dùng cho Colab/Jupyter:
    load một lần, đổi `image_folder` / modes và gọi lại. Chỉ `close()` khi tự tạo pipe và
    `close_pipe_when_done=True`.
    """
    gt_items = read_json(ground_truth_path)
    if not isinstance(gt_items, list):
        raise ValueError("Ground truth JSON must be a list of invoice objects.")
    gt_by_file = build_gt_index(gt_items)

    images = [p for p in list_images(image_folder) if p.name in gt_by_file]
    if not images:
        raise ValueError(f"No images with GT found in: {image_folder}")

    gen_config = GenerationConfig(
        max_new_tokens=max_new_tokens,
        temperature=temperature,
        top_p=1.0,
    )

    created_pipe = pipe is None
    if created_pipe:
        pipe = create_lmdeploy_pipe(
            model_path,
            backend=backend,
            tp=tp,
            session_len=session_len,
            cache_max_entry_count=cache_max_entry_count,
            model_format=model_format,
        )
    try:
        ablation_summary: Dict[str, Any] = {}

        for mode in modes:
            system_prompt = InternLMExtractor._prompt_for_mode(mode)
            _warmup(pipe, images[0], system_prompt, gen_config)
            predictions: List[Dict[str, Any]] = []
            debug_raw: List[Dict[str, Any]] = []
            throughput_compare: Optional[Dict[str, Any]] = None

            if inference_mode == "single":
                rows, primary_metrics, _ = _infer_single_dataset(
                    pipe, images, system_prompt, gen_config, model_path=model_path
                )
            else:
                rows, primary_metrics, _ = _infer_batch_dataset(
                    pipe,
                    images,
                    system_prompt,
                    gen_config,
                    batch_size,
                    model_path=model_path,
                )

            for row in rows:
                pred_item: Dict[str, Any] = {
                    "file_name": row["file_name"],
                    "prediction": row["prediction"],
                    "latency_ms": row["latency_ms"],
                }
                if "error" in row:
                    pred_item["error"] = row["error"]
                predictions.append(pred_item)
                dbg: Dict[str, Any] = {
                    "file_name": row["file_name"],
                    "mode": mode,
                    "raw_response": row["raw_response"],
                }
                if "error" in row:
                    dbg["error"] = row["error"]
                debug_raw.append(dbg)

            alt_single: Optional[ThroughputMetrics] = None
            alt_batch: Optional[ThroughputMetrics] = None
            if compare_throughput:
                if inference_mode == "single":
                    alt_batch = _timed_pass_batch_only(
                        pipe, images, system_prompt, gen_config, batch_size
                    )
                    alt_batch = ThroughputMetrics(
                        **{**asdict(alt_batch), "model_path": model_path}
                    )
                    throughput_compare = {
                        "single": _metrics_to_dict(primary_metrics, model_path),
                        "batch": _metrics_to_dict(alt_batch, model_path),
                        "IMPROVE": compute_improve(primary_metrics, alt_batch),
                        "batch_size_used": batch_size,
                        "prompt_mode": mode,
                    }
                else:
                    alt_single = _timed_pass_single_only(
                        pipe, images, system_prompt, gen_config
                    )
                    alt_single = ThroughputMetrics(
                        **{**asdict(alt_single), "model_path": model_path}
                    )
                    throughput_compare = {
                        "single": _metrics_to_dict(alt_single, model_path),
                        "batch": _metrics_to_dict(primary_metrics, model_path),
                        "IMPROVE": compute_improve(alt_single, primary_metrics),
                        "batch_size_used": batch_size,
                        "prompt_mode": mode,
                    }

            evaluator = InvoiceEvaluator(gt_by_file)
            report = evaluator.evaluate(predictions)
            report["throughput_metrics"] = _metrics_to_dict(primary_metrics, model_path)
            if throughput_compare is not None:
                report["throughput_comparison"] = throughput_compare

            ablation_summary[mode] = report

            pred_path = output_dir / f"predictions_{mode}.json"
            report_path = output_dir / f"evaluation_{mode}.json"
            raw_path = output_dir / f"raw_responses_{mode}.json"
            write_json(pred_path, predictions)
            write_json(report_path, report)
            write_json(raw_path, debug_raw)
            if throughput_compare is not None:
                write_json(
                    output_dir / f"throughput_comparison_{mode}.json",
                    throughput_compare,
                )
                _print_throughput_table(throughput_compare)

        write_json(output_dir / "ablation_summary.json", ablation_summary)
    finally:
        if created_pipe and close_pipe_when_done and hasattr(pipe, "close"):
            pipe.close()


def _fmt_imp(x: Any) -> str:
    if x is None:
        return "n/a"
    if isinstance(x, (int, float)):
        return f"{x:.2f}x"
    return str(x)


def _print_throughput_table(comp: Dict[str, Any]) -> None:
    s = comp["single"]
    b = comp["batch"]
    imp = comp["IMPROVE"]
    print("\n=== Throughput (LMDeploy) ===")
    print(f"{'Metric':<28} | {'Single':<18} | {'Batch':<18} | {'IMPROVE'}")
    print("-" * 88)
    print(
        f"{'Wall time (s)':<28} | {s['wall_time_s']:<18.3f} | {b['wall_time_s']:<18.3f} | "
        f"{_fmt_imp(imp.get('wall_time_speedup_single_over_batch'))}"
    )
    print(
        f"{'Requests/sec':<28} | {s['requests_per_sec']:<18.2f} | {b['requests_per_sec']:<18.2f} | "
        f"{_fmt_imp(imp.get('requests_per_sec_ratio_batch_over_single'))}"
    )
    print(
        f"{'Output tokens/s':<28} | {s['output_tokens_per_sec']:<18.2f} | {b['output_tokens_per_sec']:<18.2f} | "
        f"{_fmt_imp(imp.get('output_tokens_per_sec_ratio_batch_over_single'))}"
    )
    print(
        f"{'Total output tokens':<28} | {s['total_output_tokens']:<18} | {b['total_output_tokens']:<18} |"
    )
    print()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Invoice LMDeploy pipeline: same eval as invoice_pipeline + batch/single throughput."
    )
    p.add_argument("--image_folder", required=True, type=Path)
    p.add_argument("--ground_truth_json", required=True, type=Path)
    p.add_argument("--output_dir", type=Path, default=Path("outputs_lmdeploy"))
    p.add_argument(
        "--model_path",
        required=True,
        help="HF repo hoặc local path, e.g. OpenGVLab/InternVL3_5-8B hoặc internlm/CapRL-InternVL3.5-8B",
    )
    p.add_argument(
        "--modes",
        nargs="+",
        default=["base", "reasoning", "reasoning_vir"],
        choices=["base", "reasoning", "reasoning_vir"],
    )
    p.add_argument(
        "--inference-mode",
        choices=("single", "batch"),
        default="single",
        help="single: từng ảnh; batch: list prompts theo --batch-size",
    )
    p.add_argument(
        "--batch-size",
        type=int,
        default=4,
        help="Kích thước batch khi --inference-mode batch (hoặc khi đo pass batch trong --compare-throughput)",
    )
    p.add_argument(
        "--compare-throughput",
        action="store_true",
        help="Sau pass chính, đo thêm mode kia (single↔batch) và ghi IMPROVE + throughput_comparison.json",
    )
    p.add_argument("--max-new-tokens", type=int, default=1024)
    p.add_argument("--temperature", type=float, default=0.0)
    p.add_argument(
        "--backend",
        choices=("pytorch", "turbomind"),
        default="pytorch",
        help="PytorchEngineConfig (mặc định) hoặc TurbomindEngineConfig",
    )
    p.add_argument("--tp", type=int, default=1, help="Tensor parallel; 38B thường tp=2")
    p.add_argument("--session-len", type=int, default=32768)
    p.add_argument("--cache-max-entry-count", type=float, default=0.8)
    p.add_argument("--model-format", type=str, default="hf", help="Turbomind: hf / awq / ...")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    run_pipeline_lmdeploy(
        image_folder=args.image_folder,
        ground_truth_path=args.ground_truth_json,
        output_dir=args.output_dir,
        model_path=args.model_path,
        modes=args.modes,
        inference_mode=args.inference_mode,
        batch_size=args.batch_size,
        max_new_tokens=args.max_new_tokens,
        temperature=args.temperature,
        compare_throughput=args.compare_throughput,
        backend=args.backend,
        tp=args.tp,
        session_len=args.session_len,
        cache_max_entry_count=args.cache_max_entry_count,
        model_format=args.model_format,
    )
    print("Pipeline LMDeploy completed. Check output_dir for predictions, evaluation, throughput.")


if __name__ == "__main__":
    main()
