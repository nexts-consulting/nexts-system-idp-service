from prometheus_client import Counter, Gauge, Histogram, Info

SERVICE_INFO = Info("idp_service", "IDP service build info")


class MetricsRegistry:
    def __init__(self, service: str) -> None:
        labels = ["service", "env"]
        base = {"service": service}

        self.jobs_total = Counter(
            "idp_jobs_total",
            "Jobs by terminal status",
            labels + ["status", "tenant_id"],
        )
        self.stage_duration = Histogram(
            "idp_stage_duration_seconds",
            "Stage latency",
            labels + ["stage"],
            buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120),
        )
        self.batch_size = Histogram(
            "idp_batch_size",
            "Extraction batch sizes",
            labels,
            buckets=(1, 2, 4, 8, 16, 24, 32, 40, 48),
        )
        self.batch_wait = Histogram(
            "idp_batch_wait_seconds",
            "Time waiting in batch accumulator",
            labels,
            buckets=(0.05, 0.1, 0.2, 0.3, 0.5, 0.8, 1.0, 2.0),
        )
        self.batch_utilization = Gauge(
            "idp_batch_utilization_ratio",
            "Last batch size / 48",
            labels,
        )
        self.extraction_tokens = Counter(
            "idp_extraction_tokens_total",
            "VLM tokens",
            labels + ["token_type"],
        )
        self.extraction_tps = Gauge(
            "idp_extraction_tokens_per_second",
            "Tokens per second for last batch",
            labels,
        )
        self.runpod_warm = Gauge("idp_runpod_warm", "1 if RunPod endpoint warm", labels)
        self.circuit_breaker = Gauge(
            "idp_circuit_breaker_state",
            "0=closed 1=open 2=half_open",
            labels,
        )
        self.fraud_detected = Counter(
            "idp_fraud_detected_total",
            "Fraud short-circuit count",
            labels + ["tenant_id"],
        )
        self.tamper_ratio = Histogram(
            "idp_fraud_tamper_ratio",
            "Tamper pixel ratio distribution",
            labels,
            buckets=(0.0, 0.0001, 0.001, 0.01, 0.05, 0.1, 0.5),
        )
        self.download_duration = Histogram(
            "idp_download_duration_seconds",
            "Per-URL download latency",
            labels,
            buckets=(0.1, 0.25, 0.5, 1, 2, 5, 10, 30),
        )
        self.download_failures = Counter(
            "idp_download_failures_total",
            "Download failures",
            labels + ["error_class"],
        )
        self.schema_validation_failures = Counter(
            "idp_schema_validation_failures_total",
            "JSON schema validation failures",
            labels,
        )
        self.redis_stream_lag = Gauge(
            "idp_redis_stream_lag",
            "Pending messages in consumer group",
            labels + ["stream"],
        )
        self.callback_delivery = Counter(
            "idp_callback_delivery_total",
            "Customer webhook deliveries",
            labels + ["result"],
        )
        self.runpod_cold_start = Counter(
            "idp_runpod_cold_start_total",
            "RunPod cold start events",
            labels,
        )
        self._base = base

    def observe_stage(self, env: str, stage: str, seconds: float) -> None:
        self.stage_duration.labels(**self._base, env=env, stage=stage).observe(seconds)
