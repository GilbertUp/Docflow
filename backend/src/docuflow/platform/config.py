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

    max_upload_bytes: int = Field(default=100 * 1024 * 1024, gt=0)
    max_import_bytes: int = Field(default=512 * 1024 * 1024, gt=0)
    import_max_entries: int = Field(default=1000, gt=0)
    import_max_expanded_mb: int = Field(default=1024, gt=0)
    # Text extraction (F04). Languages are Tesseract codes, for example eng, fra, deu, chi_sim.
    ocr_default_languages: str = "eng,fra"
    ocr_max_languages_per_run: int = Field(default=5, gt=0)
    ocr_dpi: int = Field(default=300, ge=72, le=600)
    ocr_max_pages: int = Field(default=500, gt=0)
    ocr_page_timeout_seconds: int = Field(default=180, gt=0)
    tesseract_binary: str = "tesseract"
    # "none" is for development and tests. Production must scan.
    scanner: Literal["none", "clamav"] = "none"
    clamav_host: str = "clamav"
    clamav_port: int = 3310
    clamav_timeout_seconds: float = Field(default=60.0, gt=0)

    # Email (F13). Without a host, notifications stay in the app and email ends as failed.
    smtp_host: str | None = None
    smtp_port: int = Field(default=587, gt=0, lt=65536)
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_from: str = "docuflow@localhost"
    smtp_starttls: bool = True

    # The AI service for generated answers (F18). With none, the assistant returns extracts.
    # An organization must also switch generated answers on, and starts with them off.
    ai_provider: Literal["none", "openai_compatible", "anthropic"] = "none"
    ai_base_url: str | None = None
    ai_model: str | None = None
    ai_api_key: SecretStr | None = None
    ai_timeout_seconds: float = Field(default=60.0, gt=0)

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
        if self.environment == "production" and self.scanner == "none":
            raise ValueError("production needs a malware scanner: set DOCUFLOW_SCANNER=clamav")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
