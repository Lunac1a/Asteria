from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str
    app_host: str
    app_port: int

    frontend_url: str
    database_url: str

    jwt_secret_key: str
    jwt_algorithm: str
    jwt_expire_minutes: int

    class Config:
        env_file = ".env"


settings = Settings()