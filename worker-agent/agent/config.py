from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    worker_host: str = "127.0.0.1"
    worker_port: int = 9108
    worker_token: str = "dev-worker-token"
    plugins_root: Path = Path("../plugins")
    # 插件监听端口池
    port_range_start: int = 19000
    port_range_end: int = 19999
    health_timeout_seconds: float = 30.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
