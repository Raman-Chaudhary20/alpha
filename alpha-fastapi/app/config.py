from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    slack_bot_token: str = ""
    slack_app_token: str = ""
    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-120b"
    slack_sync_days: int = 7
    slack_sync_minutes: int = 2
    data_dir: str = "data"
    port: int = 8000
    debug: bool = False
    app_name: str = "Alpha - Slack AI Bot"
    app_version: str = "1.0.0"
    max_slack_chars: int = 30_000
    max_conversation_chars: int = 10_000
    max_members_chars: int = 8_000


@lru_cache
def get_settings() -> Settings:
    return Settings()
