from pydantic import Field

from idp_common.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "idp-extraction"
    host: str = "0.0.0.0"
    # RunPod sets PORT=23333 for lmdeploy proxy — do not use generic PORT here.
    port: int = Field(default=8003, validation_alias="IDP_EXTRACTION_PORT")
    lmdeploy_url: str = "http://localhost:23333"
    lmdeploy_api_key: str = "EMPTY"
    mock_mode: bool = False
    model_name: str = "OpenGVLab/InternVL3_5-8B-Flash"
    default_prompt_mode: str = "reasoning_vir"
    max_new_tokens: int = 4096
    temperature: float = 0.0
    pushgateway_url: str | None = None
