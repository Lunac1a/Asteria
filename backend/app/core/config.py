from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    APP_NAME: str
    APP_HOST: str
    APP_PORT: int

    FRONTEND_URL: str
    DATABASE_URL: str

    JWT_SECRET_KEY: str
    JWT_ALGORITHM: str
    JWT_EXPIRE_MINUTES: int

    NVIDIA_API_KEY: str
    NVIDIA_API_BASE: str = "https://integrate.api.nvidia.com/v1"
    NVIDIA_CHAT_MODEL: str

    ENCRYPTION_KEY: str

    class Config:
        env_file = ".env"


settings = Settings()