"""Env-driven configuration."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="YEELIGHT_",
        env_file=(".env", str(Path.home() / ".config" / "yeelight" / "state.env")),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    bind: str = Field("127.0.0.1:8767", description="uvicorn bind address")
    multicast_addr: str = Field(
        "239.255.255.250", description="Yeelight M-SEARCH multicast group"
    )
    multicast_port: int = Field(1982, description="Yeelight M-SEARCH multicast port")
    discover_timeout_s: int = Field(
        2, description="Seconds to collect M-SEARCH responses"
    )
    refresh_interval_s: int = Field(60, description="Per-bulb refresh cadence")
    discover_interval_s: int = Field(600, description="Full re-discover cadence")
    all_concurrency: int = Field(
        16, description="Max concurrent bulbs in an /bulb/all/{op} fan-out"
    )
    log_level: str = Field("INFO", description="Python logging level")
    state_dir: Path = Field(
        default_factory=lambda: Path.home() / ".config" / "yeelight",
        description="Where state.json lives",
    )

    @property
    def state_path(self) -> Path:
        return self.state_dir / "state.json"


def load() -> Settings:
    return Settings()  # type: ignore[call-arg]
