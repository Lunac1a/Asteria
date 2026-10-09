from pydantic_settings import BaseSettings
from typing import Literal
from pydantic import Field

class Settings(BaseSettings):
    APP_NAME: str
    APP_PORT: int

    FRONTEND_URL: str
    DATABASE_URL: str

    JWT_SECRET_KEY: str
    JWT_ALGORITHM: str
    JWT_EXPIRE_MINUTES: int


    ENCRYPTION_KEY: str

    LOCAL_DATA_DIR: str = "../data"
    EMBEDDING_BACKEND: Literal["fastembed", "test"] = "fastembed"
    EMBEDDING_MODEL: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    PROVIDER_IS_SIMULATED: bool = False
    LLM_BACKEND: Literal["provider", "test"] = "provider"
    RUNTIME_METRICS_ENABLED: bool = True
    CITATION_AUDIT_ENABLED: bool = False  # Explicit rollout after provider-specific evaluation.
    CITATION_AUDIT_TIMEOUT_SECONDS: int = Field(default=18, ge=3, le=20)
    GENERATION_JSON_SCHEMA_PROFILES: list[dict[str, str]] = Field(default_factory=list)
    LLM_ALLOWED_BASE_URLS: str = "https://integrate.api.nvidia.com/v1"
    INDEX_TIMEOUT_SECONDS: int = Field(default=90, ge=1, le=90)
    QUERY_TIMEOUT_SECONDS: int = Field(default=30, ge=1, le=30)
    RETRIEVAL_MIN_SCORE: float = Field(default=0.35, ge=0, le=1)
    RETRIEVAL_LEXICAL_RESCUE: bool = True
    MAX_UPLOAD_BYTES: int = Field(default=10 * 1024 * 1024, ge=1, le=10 * 1024 * 1024)
    MAX_WORKSPACE_CHUNKS: int = Field(default=2000, ge=400, le=2000)

    class Config:
        env_file = ".env"


settings = Settings()
