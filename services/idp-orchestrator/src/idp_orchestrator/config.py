from idp_common.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "idp-orchestrator"
    host: str = "0.0.0.0"
    port: int = 8001
    batch_max_size: int = 48
    batch_max_wait_ms: int = 500
    extraction_url: str = "http://idp-extraction:8003"
    app_internal_url: str = "http://idp-app:8000"
    runpod_warm_key: str = "idp:runpod:warm"
    circuit_failure_threshold: int = 5
    circuit_window_seconds: int = 60
    circuit_open_seconds: int = 30
    consumer_name: str = "orchestrator-1"
