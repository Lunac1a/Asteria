from pydantic import BaseModel, Field, field_validator
from typing import Literal
from app.core.config import settings


def validate_provider_url(value: str) -> str:
    value = value.strip().rstrip("/")
    if value not in {
        url.strip().rstrip("/") for url in settings.LLM_ALLOWED_BASE_URLS.split(",")
    }:
        raise ValueError("Provider URL is not in the server allowlist")
    return value


class LLMSettingsCreate(BaseModel):
    provider: Literal["nvidia", "sub2api", "openai_compatible"] = "nvidia"
    api_key: str | None = Field(default=None, min_length=1, max_length=4096)
    model_name: str = Field(min_length=1, max_length=200)
    base_url: str

    @field_validator("api_key", "model_name")
    @classmethod
    def nonblank(cls, value):
        if value is not None and not value.strip():
            raise ValueError("Value cannot be blank")
        return value.strip() if value else value

    @field_validator("base_url")
    @classmethod
    def allowed_url(cls, value):
        return validate_provider_url(value)


class LLMSettingsResponse(BaseModel):
    provider: str
    model_name: str
    base_url: str
    has_api_key: bool
