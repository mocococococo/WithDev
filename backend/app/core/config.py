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
    slack_client_id: str | None
    slack_client_secret: str | None
    slack_redirect_uri: str | None
    frontend_base_url: str
    aiboard_allowed_service_account: str | None
    aiboard_expected_audience: str | None
    aiboard_api_base_url: str | None
    aiboard_frontend_base_url: str | None

    def __init__(self) -> None:
        self.app_name = os.getenv("APP_NAME", "WithDev Backend")
        self.gemini_api_key = os.getenv("GEMINI_API_KEY")
        self.gemini_model = os.getenv("GEMINI_MODEL")
        self.cors_allowed_origins = _parse_csv_env("CORS_ALLOWED_ORIGINS")
        self.database_url = os.getenv(
            "DATABASE_URL",
            "postgresql+asyncpg://withdev:withdev@127.0.0.1:5432/withdev",
        )
        self.slack_client_id = os.getenv("SLACK_CLIENT_ID")
        self.slack_client_secret = os.getenv("SLACK_CLIENT_SECRET")
        self.slack_redirect_uri = os.getenv("SLACK_REDIRECT_URI")
        self.frontend_base_url = os.getenv("FRONTEND_BASE_URL", "http://127.0.0.1:5173")
        self.aiboard_allowed_service_account = os.getenv("AIBOARD_ALLOWED_SERVICE_ACCOUNT")
        self.aiboard_expected_audience = os.getenv("AIBOARD_EXPECTED_AUDIENCE")
        self.aiboard_api_base_url = os.getenv("AIBOARD_API_BASE_URL")
        self.aiboard_frontend_base_url = os.getenv("AIBOARD_FRONTEND_BASE_URL")


def _parse_csv_env(name: str) -> list[str]:
    value = os.getenv(name, "")
    return [item.strip() for item in value.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
