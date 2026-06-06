from idp_common.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "idp-extraction"
    host: str = "0.0.0.0"
    port: int = 8003
    lmdeploy_url: str = "http://localhost:23333"
    lmdeploy_api_key: str = "EMPTY"
    mock_mode: bool = True
    model_name: str = "OpenGVLab/InternVL3_5-8B-Flash"
    default_prompt_mode: str = "reasoning_vir"
    max_new_tokens: int = 1024
    temperature: float = 0.0
    pushgateway_url: str | None = None
