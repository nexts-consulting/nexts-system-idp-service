from pydantic_settings import BaseSettings, SettingsConfigDict


class BaseServiceSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    service_name: str = "idp"
    env: str = "dev"
    log_level: str = "INFO"
    redis_url: str = "redis://localhost:6379/0"
    database_url: str = "postgresql+asyncpg://idp:idp@localhost:5432/idp"
    gcs_bucket: str = "idp-artifacts-dev"
    gcs_emulator_host: str | None = None  # http://minio:9000 for local
    firebase_project_id: str | None = None
    firebase_allowed_buckets: str | None = None  # comma-separated allowlist
    allowed_image_hosts: str | None = None  # comma-separated HTTPS host allowlist
    otel_exporter_endpoint: str | None = None
    prometheus_multiproc_dir: str | None = None
    app_internal_url: str = "http://idp-app:8000"
    debug_audit_enabled: bool = True
