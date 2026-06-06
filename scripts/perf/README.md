# Performance benchmarks

## k6 load test

```bash
k6 run scripts/perf/k6_create_jobs.js
```

## Baseline comparison

After extraction batch completes, compare measured tokens/sec:

```bash
python scripts/perf/baseline_compare.py 400
```

Reference: `docs/performance/baseline.json` (InternVL3.5-8B-Flash, A100 80GB, BS=48 → 472.84 tok/s).

## CI integration

Run nightly against staging `APP_URL` and fail if `baseline_compare.py` exits non-zero.
