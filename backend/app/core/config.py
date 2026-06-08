import os
from functools import lru_cache

from dotenv import load_dotenv


load_dotenv()


class Settings:
    app_name: str
    gemini_api_key: str | None
    gemini_model: str | None
    cors_allowed_origins: list[str]
    database_url: str

    def __init__(self) -> None:
        self.app_name = os.getenv("APP_NAME", "WithDev Backend")
        self.gemini_api_key = os.getenv("GEMINI_API_KEY")
        self.gemini_model = os.getenv("GEMINI_MODEL")
        self.cors_allowed_origins = _parse_csv_env("CORS_ALLOWED_ORIGINS")
        self.database_url = os.getenv(
            "DATABASE_URL",
            "postgresql+asyncpg://withdev:withdev@127.0.0.1:5432/withdev",
        )


def _parse_csv_env(name: str) -> list[str]:
    value = os.getenv(name, "")
    return [item.strip() for item in value.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
