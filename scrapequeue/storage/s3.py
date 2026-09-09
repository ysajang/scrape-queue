"""Result storage on any S3-compatible endpoint (MinIO locally).

Results are not served through the API process: the download endpoint returns a
presigned URL with a short TTL, so a large CSV never occupies an API worker.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from scrapequeue.core.settings import get_settings


@lru_cache
def get_client() -> Any:
    settings = get_settings()
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint,
        aws_access_key_id=settings.s3_access_key.get_secret_value(),
        aws_secret_access_key=settings.s3_secret_key.get_secret_value(),
        config=Config(signature_version="s3v4", retries={"max_attempts": 3, "mode": "standard"}),
        region_name="us-east-1",
    )


def ensure_bucket() -> None:
    client = get_client()
    bucket = get_settings().s3_bucket
    try:
        client.head_bucket(Bucket=bucket)
    except ClientError:
        client.create_bucket(Bucket=bucket)


def put_bytes(key: str, data: bytes, content_type: str) -> int:
    ensure_bucket()
    get_client().put_object(
        Bucket=get_settings().s3_bucket, Key=key, Body=data, ContentType=content_type
    )
    return len(data)


def presign(key: str, ttl: int | None = None) -> str:
    settings = get_settings()
    return str(
        get_client().generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.s3_bucket, "Key": key},
            ExpiresIn=ttl or settings.s3_presign_ttl_seconds,
        )
    )
