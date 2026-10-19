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
    PERSISTENT_QUERY_WORKER_ENABLED: bool = False
    PERSISTENT_QUERY_STARTUP_TIMEOUT_SECONDS: int = Field(default=30, ge=1, le=90)
    PERSISTENT_QUERY_RESTART_LIMIT: int = Field(default=3, ge=0, le=5)
    PERSISTENT_QUERY_RESTART_BACKOFF_SECONDS: float = Field(default=1, ge=.1, le=30)
    PERSISTENT_QUERY_RESTART_WINDOW_SECONDS: float = Field(default=600, ge=1, le=86400)
    PERSISTENT_QUERY_FAILED_CYCLE_LIMIT: int = Field(default=3, ge=1, le=10)
    PROVIDER_IS_SIMULATED: bool = False
    LLM_BACKEND: Literal["provider", "test"] = "provider"
    RUNTIME_METRICS_ENABLED: bool = True
    CITATION_AUDIT_ENABLED: bool = False  # Explicit rollout after provider-specific evaluation.
    CITATION_AUDIT_TIMEOUT_SECONDS: int = Field(default=18, ge=3, le=20)
    GENERATION_JSON_SCHEMA_PROFILES: list[dict[str, str]] = Field(default_factory=list)
    LLM_ALLOWED_BASE_URLS: str = "https://integrate.api.nvidia.com/v1"
    INDEX_TIMEOUT_SECONDS: int = Field(default=90, ge=1, le=90)
    QUERY_TIMEOUT_SECONDS: int = Field(default=30, ge=1, le=30)
    CHAT_SINGLEFLIGHT_WAIT_SECONDS: float = Field(default=30, ge=0.1, le=90)
    SESSION_LOCK_WAIT_SECONDS: float = Field(default=1, ge=0.01, le=10)
    SESSION_CHAT_CONCURRENCY_ENABLED: bool = False
    CHAT_SINGLEFLIGHT_PER_REQUEST_LIMIT: int = Field(default=4, ge=0)
    CHAT_SINGLEFLIGHT_GLOBAL_LIMIT: int = Field(default=8, ge=0)
    RETRIEVAL_MIN_SCORE: float = Field(default=0.35, ge=0, le=1)
    RETRIEVAL_LEXICAL_RESCUE: bool = True
    MAX_UPLOAD_BYTES: int = Field(default=10 * 1024 * 1024, ge=1, le=10 * 1024 * 1024)
    MAX_WORKSPACE_CHUNKS: int = Field(default=2000, ge=400, le=2000)
    RECOVERY_ADMIN_USER_IDS: list[str] = Field(default_factory=list)
    RECOVERY_SINGLE_PROCESS_CONFIRMED: bool = False
    WORKSPACE_DELETE_MAINTENANCE_MODE: bool = False
    STARTUP_RECOVERY_ENABLED: bool = False

    class Config:
        env_file = ".env"


settings = Settings()
