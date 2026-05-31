from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator


class DatabaseConfig(BaseModel):
    url: str = "sqlite:///./data/homesolar.sqlite"


class PollingConfig(BaseModel):
    live_seconds: int = Field(default=60, ge=5)
    alarm_seconds: int | None = Field(default=1800, ge=30)
    info_seconds: int | None = Field(default=3600, ge=60)


class BasicAuthConfig(BaseModel):
    type: Literal["basic"] = "basic"
    username_env: str
    password_env: str


class InverterConfig(BaseModel):
    id: str
    name: str
    type: Literal["apsystems_ez1d", "kostal_html"]
    base_url: str
    enabled: bool = True
    timezone: str = "Europe/London"
    polling: PollingConfig = Field(default_factory=PollingConfig)
    auth: BasicAuthConfig | None = None

    @field_validator("base_url")
    @classmethod
    def strip_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")


class CollectorConfig(BaseModel):
    enabled: bool = True
    request_timeout_seconds: float = Field(default=10.0, gt=0)


class EmailConfig(BaseModel):
    enabled: bool = False
    host: str = "192.168.0.5"
    port: int = Field(default=25, ge=1, le=65535)
    use_tls: bool = False
    from_address: str = "alexandrosm77@gmail.com"
    send_hour_local: int = Field(default=5, ge=0, le=23)
    check_interval_seconds: int = Field(default=600, ge=30)
    timeout_seconds: float = Field(default=15.0, gt=0)


class WebConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = Field(default=8000, ge=1, le=65535)
    base_path: str = ""
    auth: BasicAuthConfig | None = None

    @field_validator("base_path")
    @classmethod
    def normalize_base_path(cls, value: str) -> str:
        value = value.strip()
        if not value or value == "/":
            return ""
        return f"/{value.strip('/')}"


class AppConfig(BaseModel):
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    collector: CollectorConfig = Field(default_factory=CollectorConfig)
    web: WebConfig = Field(default_factory=WebConfig)
    email: EmailConfig = Field(default_factory=EmailConfig)
    inverters: list[InverterConfig]


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path)
    with config_path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return AppConfig.model_validate(data)
