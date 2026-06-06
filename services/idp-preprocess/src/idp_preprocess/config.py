from idp_common.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "idp-preprocess"
    host: str = "0.0.0.0"
    port: int = 8002
    doctamper_checkpoint: str = "/models/mit_unet_doctamper_best.pt"
    tamper_pixel_threshold: float = 0.0001
    max_concurrent_downloads: int = 32
    max_download_bytes: int = 20 * 1024 * 1024
    max_image_edge: int = 4096
    encoder_name: str = "mit_b2"
    fraud_enabled: bool = True
    consumer_name: str = "preprocess-1"
    # Inherit from BaseServiceSettings: firebase_project_id, firebase_allowed_buckets,
    # allowed_image_hosts, gcs_bucket, gcs_emulator_host

    def parsed_allowed_buckets(self) -> set[str] | None:
        raw = self.firebase_allowed_buckets
        if not raw:
            return None
        return {b.strip() for b in raw.split(",") if b.strip()}

    def parsed_allowed_hosts(self) -> set[str] | None:
        raw = self.allowed_image_hosts
        if not raw:
            return None
        return {h.strip() for h in raw.split(",") if h.strip()}
