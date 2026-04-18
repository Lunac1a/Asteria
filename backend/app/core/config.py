from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "PKC API"
    app_host: str = "127.0.0.1"
    app_port: int = 8000

    frontend_url: str = "http://localhost:3000"

    class Config:
        env_file = ".env"


settings = Settings()