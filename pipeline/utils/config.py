"""Configuration — all settings loaded from environment / .env file."""

from __future__ import annotations

import logging
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

log = logging.getLogger(__name__)

_DEFAULT_ENV_FILE = Path.home() / ".openclaw-3dprint" / "pipeline.env"


class Settings(BaseSettings):
    """Pipeline configuration — all values from env vars or .env file."""

    model_config = SettingsConfigDict(
        env_file=str(_DEFAULT_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Bot mode ──────────────────────────────────────────────────
    bot_mode: str = Field(
        default="feishu",
        description="'telegram', 'feishu', or 'dual'",
    )

    # ── Telegram ──────────────────────────────────────────────────
    telegram_bot_token: str = Field(default="")
    telegram_allowed_user_ids: str = Field(default="")

    # ── Feishu ────────────────────────────────────────────────────
    feishu_app_id: str = Field(default="")
    feishu_app_secret: str = Field(default="")
    feishu_chat_id: str = Field(default="")
    feishu_api_port: int = Field(default=8765)

    # ── LLM (OpenAI-compatible) ───────────────────────────────────
    openai_api_key: str = Field(description="LLM API key")
    openai_base_url: str = Field(
        default="https://api.openai.com/v1",
        description="API base URL",
    )
    openai_model: str = Field(default="gpt-4o")

    # ── 3D Generation ─────────────────────────────────────────────
    mesh_provider: str = Field(default="tripo", description="'tripo' or 'meshy'")
    tripo_api_key: str = Field(default="")
    meshy_api_key: str = Field(default="")

    # ── Slicer ────────────────────────────────────────────────────
    slicer_mode: str = Field(
        default="local",
        description="'local' (PrusaSlicer on this machine) or 'remote' (Bambu Studio on Windows)",
    )
    slicer_path: str = Field(
        default="/Applications/PrusaSlicer.app/Contents/MacOS/PrusaSlicer",
        description="Path to slicer binary (local mode)",
    )
    slicer_printer_profile: str = Field(default="")
    slicer_filament_profile: str = Field(default="")
    slicer_process_profile: str = Field(default="")

    # ── Remote slicer (Windows, optional) ─────────────────────────
    windows_host: str = Field(default="")
    windows_user: str = Field(default="")
    windows_port: int = Field(default=22)
    windows_ssh_key: str = Field(default="")
    windows_connect_timeout: int = Field(default=15)
    windows_stl_staging_dir: str = Field(default="")
    remote_slicer_path: str = Field(default="")
    remote_slicer_profiles_dir: str = Field(default="")

    # ── Bambu printer ─────────────────────────────────────────────
    bambu_printer_ip: str = Field(default="")
    bambu_printer_serial: str = Field(default="")
    bambu_printer_access_code: str = Field(default="")
    bambu_send_method: str = Field(
        default="ftp",
        description="'ftp' (direct FTPS from Mac) or 'studio' (Bambu Studio CLI on Windows)",
    )

    # ── Pipeline behaviour ────────────────────────────────────────
    staging_dir: str = Field(
        default=str(Path.home() / ".openclaw-3dprint" / "staging"),
    )
    mesh_poll_interval: int = Field(default=10)
    mesh_poll_timeout: int = Field(default=600)

    # ── Derived helpers ───────────────────────────────────────────

    @property
    def allowed_user_ids(self) -> set[int]:
        if not self.telegram_allowed_user_ids:
            return set()
        return {
            int(uid.strip())
            for uid in self.telegram_allowed_user_ids.split(",")
            if uid.strip()
        }

    def ensure_staging_dir(self) -> Path:
        p = Path(self.staging_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p


def load_settings() -> Settings:
    """Load and validate settings."""
    settings = Settings()  # type: ignore[call-arg]
    log.info("Config loaded (env file: %s)", _DEFAULT_ENV_FILE)
    settings.ensure_staging_dir()
    return settings
