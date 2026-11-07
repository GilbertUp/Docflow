"""ADR 0006 spike: proves the features the plan needs on a real backend.

Runs only when DOCUFLOW_TEST_S3_ENDPOINT is set, because a simulator cannot prove this.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import boto3
import pytest
from botocore.config import Config
from botocore.exceptions import ClientError

pytestmark = [
    pytest.mark.conformance,
    pytest.mark.skipif(
        "DOCUFLOW_TEST_S3_ENDPOINT" not in os.environ,
        reason="needs a real S3 backend",
    ),
]


@pytest.fixture(scope="module")
def client() -> Any:
    return boto3.client(
        "s3",
        endpoint_url=os.environ["DOCUFLOW_TEST_S3_ENDPOINT"],
        region_name="us-east-1",
        aws_access_key_id=os.environ["DOCUFLOW_TEST_S3_ACCESS_KEY"],
        aws_secret_access_key=os.environ["DOCUFLOW_TEST_S3_SECRET_KEY"],
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        ),
    )


def new_bucket(client: Any, *, lock: bool = False) -> str:
    name = f"conformance-{uuid.uuid4().hex[:12]}"
    client.create_bucket(Bucket=name, ObjectLockEnabledForBucket=lock)
    return name


def test_versioning_keeps_earlier_versions(client: Any) -> None:
    bucket = new_bucket(client)
    client.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={"Status": "Enabled"})
    first = client.put_object(Bucket=bucket, Key="k", Body=b"one")
    client.put_object(Bucket=bucket, Key="k", Body=b"two")
    body = client.get_object(Bucket=bucket, Key="k", VersionId=first["VersionId"])["Body"].read()
    assert body == b"one"


def test_object_lock_blocks_deleting_a_locked_version(client: Any) -> None:
    bucket = new_bucket(client, lock=True)
    put = client.put_object(
        Bucket=bucket,
        Key="k",
        Body=b"record",
        ObjectLockMode="COMPLIANCE",
        ObjectLockRetainUntilDate=datetime.now(UTC) + timedelta(minutes=5),
    )
    with pytest.raises(ClientError):
        client.delete_object(Bucket=bucket, Key="k", VersionId=put["VersionId"])
    assert client.get_object(Bucket=bucket, Key="k")["Body"].read() == b"record"


def test_multipart_upload_completes(client: Any) -> None:
    bucket = new_bucket(client)
    upload = client.create_multipart_upload(Bucket=bucket, Key="big")
    part_body = b"x" * (5 * 1024 * 1024)
    parts = [
        {
            "ETag": client.upload_part(
                Bucket=bucket,
                Key="big",
                PartNumber=number,
                UploadId=upload["UploadId"],
                Body=part_body,
            )["ETag"],
            "PartNumber": number,
        }
        for number in (1, 2)
    ]
    client.complete_multipart_upload(
        Bucket=bucket,
        Key="big",
        UploadId=upload["UploadId"],
        MultipartUpload={"Parts": parts},
    )
    assert client.head_object(Bucket=bucket, Key="big")["ContentLength"] == 2 * len(part_body)
