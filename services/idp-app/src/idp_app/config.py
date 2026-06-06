from idp_common.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "idp-app"
    host: str = "0.0.0.0"
    port: int = 8000
    orchestrator_url: str = "http://idp-orchestrator:8001"
    callback_hmac_secret: str = "on-U3ml1zJ7CK0Bkq8ANMeR3NHlNN-E-nesOdVEwFB4"
    callback_max_attempts: int = 5
