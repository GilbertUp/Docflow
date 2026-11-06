"""Builds the shared services from settings, once per process."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from redis import Redis

from docuflow.platform.audit import AuditLog
from docuflow.platform.config import Settings, decode_key, get_settings
from docuflow.platform.crypto import LocalKeyProvider
from docuflow.platform.db import Database, make_engine
from docuflow.platform.storage import EncryptedObjects, S3ObjectStore


@dataclass(frozen=True)
class Services:
    settings: Settings
    db: Database
    audit: AuditLog
    store: S3ObjectStore
    objects: EncryptedObjects
    redis: Redis


def build_services(settings: Settings) -> Services:
    store = S3ObjectStore(
        settings.s3_endpoint,
        settings.s3_region,
        settings.s3_bucket,
        settings.s3_access_key.get_secret_value(),
        settings.s3_secret_key.get_secret_value(),
    )
    keys = LocalKeyProvider(decode_key(settings.master_key.get_secret_value()))
    return Services(
        settings=settings,
        db=Database(
            make_engine(settings.database_url), make_engine(settings.platform_database_url)
        ),
        audit=AuditLog(decode_key(settings.audit_hmac_key.get_secret_value())),
        store=store,
        objects=EncryptedObjects(store, keys),
        redis=Redis.from_url(settings.redis_url, socket_connect_timeout=2),
    )


@lru_cache
def get_services() -> Services:
    return build_services(get_settings())
