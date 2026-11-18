from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Slack: the bot (Socket Mode) ---
    slack_bot_token: str = ""
    slack_app_token: str = ""

    # --- Slack: each person's own sign-in, so Alpha can read their DMs ---
    # From the Slack app's Basic Information page.
    slack_client_id: str = ""
    slack_client_secret: str = ""
    # The address Slack sends people back to after they click Allow, e.g.
    # https://alpha.example.com (Slack requires https). The redirect URL to register in
    # the Slack app is <public_url>/api/v1/slack/oauth/callback.
    public_url: str = ""
    # Secret used to encrypt people's Slack sign-ins and to sign the sign-in link. Any long
    # random string; changing it makes every stored sign-in unreadable (people sign in again).
    token_key: str = ""

    # --- Who may ask about private chats ---
    # Slack user IDs (comma separated) treated as admins, in addition to the workspace's
    # own admins and owners.
    admin_user_ids: str = ""
    # Callers of the REST API who send this key in X-API-Key get admin answers.
    api_key: str = ""

    # --- Amazon Bedrock (Alpha's AWS account) ---
    aws_region: str = "us-east-1"
    aws_profile: str = ""
    bedrock_answer_model: str = "us.anthropic.claude-sonnet-4-6"
    bedrock_digest_model: str = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

    # --- Sync ---
    # How far back the first sync of a conversation reads (Slack Free shows 90 days).
    slack_sync_days: int = 90
    slack_sync_minutes: int = 2
    # Each sync also rereads this many days, to catch new thread replies and edits.
    slack_reread_days: int = 3
    memory_build_minutes: int = 10
    max_file_bytes: int = 15_000_000
    max_file_chars: int = 50_000

    data_dir: str = "data"
    port: int = 8000
    debug: bool = False
    app_name: str = "Alpha - Slack AI Bot"
    app_version: str = "2.0.0"
    max_slack_chars: int = 30_000
    max_conversation_chars: int = 10_000
    max_members_chars: int = 8_000

    @property
    def admin_ids(self) -> set[str]:
        return {part.strip() for part in self.admin_user_ids.split(",") if part.strip()}

    @property
    def redirect_uri(self) -> str:
        return self.public_url.rstrip("/") + "/api/v1/slack/oauth/callback"


@lru_cache
def get_settings() -> Settings:
    return Settings()
