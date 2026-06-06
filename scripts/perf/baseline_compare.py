#!/usr/bin/env python3
"""Compare Prometheus or manual tok/s against InternVL baseline."""

import json
import sys
from pathlib import Path

BASELINE_PATH = Path(__file__).resolve().parents[2] / "docs" / "performance" / "baseline.json"


def main() -> int:
    baseline = json.loads(BASELINE_PATH.read_text())
    optimal_bs = baseline["optimal_batch_size"]
    ref_tps = baseline["metrics_by_batch_size"][str(optimal_bs)]["tokens_per_sec"]
    threshold = ref_tps * baseline["alert_threshold_ratio"]

    measured = float(sys.argv[1]) if len(sys.argv) > 1 else 0.0
    print(f"Baseline BS={optimal_bs}: {ref_tps} tok/s")
    print(f"Threshold (85%): {threshold:.2f} tok/s")
    print(f"Measured: {measured:.2f} tok/s")

    if measured > 0 and measured < threshold:
        print("FAIL: throughput below threshold")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
