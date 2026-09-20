"""Photo storage for trip memories. Uses Cloudflare R2 (S3-compatible) when
configured; otherwise falls back to local disk under app/uploads/ for local
dev and tests, same fallback spirit as app/email.py. Render's own filesystem
is ephemeral (wiped on every redeploy), so R2 is required in production —
this fallback exists purely so nothing here needs real cloud credentials to
run locally or in CI.
"""
from __future__ import annotations

from pathlib import Path

import boto3
from botocore.client import Config as BotoConfig

from app.config import (
    R2_ACCESS_KEY_ID,
    R2_ACCOUNT_ID,
    R2_BUCKET_NAME,
    R2_SECRET_ACCESS_KEY,
)

LOCAL_UPLOADS_DIR = Path(__file__).resolve().parent / "uploads" / "memories"

R2_CONFIGURED = bool(R2_ACCOUNT_ID and R2_ACCESS_KEY_ID and R2_SECRET_ACCESS_KEY and R2_BUCKET_NAME)


def _r2_client():
    return boto3.client(
        "s3",
        endpoint_url=f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
        aws_access_key_id=R2_ACCESS_KEY_ID,
        aws_secret_access_key=R2_SECRET_ACCESS_KEY,
        config=BotoConfig(signature_version="s3v4"),
        region_name="auto",
    )


def backend_name() -> str:
    return "r2" if R2_CONFIGURED else "local"


def save_photo(key: str, data: bytes, content_type: str) -> None:
    if R2_CONFIGURED:
        _r2_client().put_object(Bucket=R2_BUCKET_NAME, Key=key, Body=data, ContentType=content_type)
        return
    path = LOCAL_UPLOADS_DIR / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def delete_photo(key: str) -> None:
    if R2_CONFIGURED:
        _r2_client().delete_object(Bucket=R2_BUCKET_NAME, Key=key)
        return
    path = LOCAL_UPLOADS_DIR / key
    path.unlink(missing_ok=True)


def photo_url(key: str) -> str | None:
    """A short-lived, directly-fetchable URL for an R2-stored photo, or None
    when running on the local-disk fallback (the caller streams bytes itself
    in that case instead of redirecting)."""
    if not R2_CONFIGURED:
        return None
    return _r2_client().generate_presigned_url(
        "get_object", Params={"Bucket": R2_BUCKET_NAME, "Key": key}, ExpiresIn=600
    )


def local_photo_path(key: str) -> Path:
    return LOCAL_UPLOADS_DIR / key
