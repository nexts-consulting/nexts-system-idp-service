"""Push RunPod metrics to Prometheus Pushgateway before pod shutdown."""

import os
import time

import httpx


def push_tokens_per_second(value: float, pushgateway: str, job: str = "idp-extraction") -> None:
    payload = f"idp_extraction_tokens_per_second{{service=\"idp-extraction\"}} {value}\n"
    url = f"{pushgateway.rstrip('/')}/metrics/job/{job}"
    httpx.post(url, content=payload, headers={"Content-Type": "text/plain"}, timeout=10.0)


if __name__ == "__main__":
    pg = os.environ.get("PUSHGATEWAY_URL", "http://pushgateway:9091")
    tps = float(os.environ.get("LAST_TPS", "0"))
    push_tokens_per_second(tps, pg)
    print(f"Pushed tps={tps} to {pg}")
