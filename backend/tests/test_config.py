from __future__ import annotations

import base64
import os
from typing import Any

import pytest
from pydantic import ValidationError

from docuflow.platform.config import Settings, decode_key


def key() -> str:
    return base64.b64encode(os.urandom(32)).decode()


def base_values(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "database_url": "postgresql+psycopg://a@b/c",
        "platform_database_url": "postgresql+psycopg://a@b/c",
        "redis_url": "redis://r",
        "s3_endpoint": "http://s",
        "s3_access_key": "a",
        "s3_secret_key": "b",
        "master_key": key(),
        "audit_hmac_key": key(),
    }
    return values | overrides


def test_valid_settings_load() -> None:
    assert Settings(**base_values()).environment == "development"


def test_secrets_are_required(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name.startswith("DOCUFLOW_"):
            monkeypatch.delenv(name)
    with pytest.raises(ValidationError):
        Settings()  # type: ignore[call-arg]


def test_short_key_is_rejected() -> None:
    weak = base64.b64encode(b"short").decode()
    with pytest.raises(ValidationError, match="at least 32"):
        Settings(**base_values(master_key=weak))


def test_key_must_be_base64() -> None:
    with pytest.raises(ValueError, match="base64"):
        decode_key("not base64!!")


def test_audit_key_must_differ_from_master_key() -> None:
    shared = key()
    with pytest.raises(ValidationError, match="different"):
        Settings(**base_values(master_key=shared, audit_hmac_key=shared))


def test_secrets_do_not_appear_in_repr() -> None:
    settings = Settings(**base_values())
    assert settings.master_key.get_secret_value() not in repr(settings)
