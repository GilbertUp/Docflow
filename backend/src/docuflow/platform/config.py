"""Environment-based configuration. Secrets have no defaults, so nothing insecure can ship."""

from __future__ import annotations

import base64
import binascii
from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MIN_KEY_BYTES = 32


def decode_key(value: str) -> bytes:
    """Decode a base64 key and require at least 256 bits."""
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("key must be valid base64") from exc
    if len(raw) < MIN_KEY_BYTES:
        raise ValueError(f"key must decode to at least {MIN_KEY_BYTES} bytes")
    return raw


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DOCUFLOW_", extra="ignore")

    environment: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"

    database_url: str = Field(description="Runtime role; must not be able to bypass RLS")
    platform_database_url: str = Field(description="Cross-tenant role, used by operators only")
    migration_database_url: str | None = None

    redis_url: str

    s3_endpoint: str
    s3_region: str = "us-east-1"
    s3_bucket: str = "docuflow"
    s3_access_key: SecretStr
    s3_secret_key: SecretStr

    master_key: SecretStr = Field(description="Base64 root key used to derive tenant keys")
    audit_hmac_key: SecretStr = Field(description="Base64 key held outside the database")

    job_max_attempts: int = 5
    job_heartbeat_timeout_seconds: int = 300
    job_retry_base_seconds: int = 30

    @field_validator("master_key", "audit_hmac_key")
    @classmethod
    def _validate_key(cls, value: SecretStr) -> SecretStr:
        decode_key(value.get_secret_value())
        return value

    @model_validator(mode="after")
    def _validate_separation(self) -> Settings:
        if self.master_key.get_secret_value() == self.audit_hmac_key.get_secret_value():
            raise ValueError("master_key and audit_hmac_key must be different")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
