from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str
    app_host: str
    app_port: int

    frontend_url: str
    database_url: str

    class Config:
        env_file = ".env"


settings = Settings()