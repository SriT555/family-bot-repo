"""Configuration loader for the Family Shopping Bot."""
import os
from pathlib import Path
from typing import List, Optional
import yaml
from pydantic import BaseModel, Field
from dotenv import load_dotenv

# Load .env file if exists
load_dotenv()


class TelegramConfig(BaseModel):
    """Telegram bot configuration."""
    bot_token: str = Field(..., description="Bot token from BotFather")
    webhook_url: str = Field(default="", description="Webhook URL for production")
    allowed_user_ids: List[int] = Field(default_factory=list, description="List of allowed user IDs")


class GoogleSheetsConfig(BaseModel):
    """Google Sheets configuration."""
    credentials_file: str = Field(default="credentials.json", description="Path to service account JSON")
    spreadsheet_id: str = Field(..., description="Google Sheets spreadsheet ID")
    sheet_name: str = Field(default="Sheet1", description="Sheet/tab name")


class AppConfig(BaseModel):
    """Application configuration."""
    host: str = Field(default="0.0.0.0", description="Server host")
    port: int = Field(default=8080, description="Server port")
    environment: str = Field(default="development", description="Environment: development or production")


class Config(BaseModel):
    """Main configuration container."""
    telegram: TelegramConfig
    google_sheets: GoogleSheetsConfig
    app: AppConfig = Field(default_factory=AppConfig)


def load_config(config_path: Optional[str] = None) -> Config:
    """
    Load configuration from YAML file and environment variables.

    Priority: Environment variables > config.yaml > defaults
    """
    config_file = config_path or os.getenv("CONFIG_PATH", "config.yaml")

    # Load from YAML file
    config_data = {}
    if Path(config_file).exists():
        with open(config_file, "r", encoding="utf-8") as f:
            config_data = yaml.safe_load(f) or {}

    # Override with environment variables
    env_overrides = _get_env_overrides()
    config_data = _deep_merge(config_data, env_overrides)

    return Config(**config_data)


def _get_env_overrides() -> dict:
    """Get configuration overrides from environment variables."""
    overrides = {}

    # Telegram
    if bot_token := os.getenv("TELEGRAM_BOT_TOKEN"):
        overrides.setdefault("telegram", {})["bot_token"] = bot_token
    if webhook_url := os.getenv("TELEGRAM_WEBHOOK_URL"):
        overrides.setdefault("telegram", {})["webhook_url"] = webhook_url
    if allowed_ids := os.getenv("TELEGRAM_ALLOWED_USER_IDS"):
        overrides.setdefault("telegram", {})["allowed_user_ids"] = [
            int(x.strip()) for x in allowed_ids.split(",") if x.strip()
        ]

    # Google Sheets
    if creds_file := os.getenv("GOOGLE_CREDENTIALS_FILE"):
        overrides.setdefault("google_sheets", {})["credentials_file"] = creds_file
    if spreadsheet_id := os.getenv("GOOGLE_SPREADSHEET_ID"):
        overrides.setdefault("google_sheets", {})["spreadsheet_id"] = spreadsheet_id
    if sheet_name := os.getenv("GOOGLE_SHEET_NAME"):
        overrides.setdefault("google_sheets", {})["sheet_name"] = sheet_name

    # App
    if host := os.getenv("APP_HOST"):
        overrides.setdefault("app", {})["host"] = host
    # Cloud Run sets PORT, also support APP_PORT for local dev
    # Cloud Run automatically sets PORT environment variable
    if port := os.getenv("PORT") or os.getenv("APP_PORT"):
        overrides.setdefault("app", {})["port"] = int(port)
    if env := os.getenv("APP_ENVIRONMENT"):
        overrides.setdefault("app", {})["environment"] = env

    return overrides


def _deep_merge(base: dict, override: dict) -> dict:
    """Deep merge two dictionaries."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


# Global config instance
_config: Optional[Config] = None


def get_config() -> Config:
    """Get the global configuration instance (singleton)."""
    global _config
    if _config is None:
        _config = load_config()
    return _config


def reload_config(config_path: Optional[str] = None) -> Config:
    """Reload configuration from file."""
    global _config
    _config = load_config(config_path)
    return _config