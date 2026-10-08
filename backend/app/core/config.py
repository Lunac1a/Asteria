from pydantic_settings import BaseSettings
from typing import Literal
from pydantic import Field

class Settings(BaseSettings):
    APP_NAME: str
    APP_HOST: str
    APP_PORT: int

    FRONTEND_URL: str
    DATABASE_URL: str

    JWT_SECRET_KEY: str
    JWT_ALGORITHM: str
    JWT_EXPIRE_MINUTES: int

    NVIDIA_API_KEY: str = ""
    NVIDIA_API_BASE: str = "https://integrate.api.nvidia.com/v1"
    NVIDIA_CHAT_MODEL: str = ""

    ENCRYPTION_KEY: str

    LOCAL_DATA_DIR: str = "../data"
    EMBEDDING_BACKEND: Literal["fastembed", "test"] = "fastembed"
    EMBEDDING_MODEL: str = "BAAI/bge-small-en-v1.5"
    LLM_BACKEND: Literal["provider", "test"] = "provider"
    LLM_ALLOWED_BASE_URLS: str = "https://integrate.api.nvidia.com/v1"
    INDEX_TIMEOUT_SECONDS: int = Field(default=90, ge=1, le=90)
    QUERY_TIMEOUT_SECONDS: int = Field(default=30, ge=1, le=30)
    RETRIEVAL_MIN_SCORE: float = Field(default=0.45, ge=0, le=1)
    MAX_UPLOAD_BYTES: int = Field(default=10 * 1024 * 1024, ge=1, le=10 * 1024 * 1024)
    MAX_WORKSPACE_CHUNKS: int = Field(default=2000, ge=400, le=2000)

    class Config:
        env_file = ".env"


settings = Settings()
