"""S3 helpers for private Mizan documents (presigned download URLs)."""
from __future__ import annotations

import logging
from typing import Optional

from django.conf import settings

logger = logging.getLogger(__name__)

DEFAULT_PRESIGNED_EXPIRY_SECONDS = 900


def s3_media_enabled() -> bool:
    return bool(getattr(settings, "AWS_STORAGE_BUCKET_NAME", "") or "")


def generate_presigned_url(
    s3_key: str,
    *,
    expires_in: int = DEFAULT_PRESIGNED_EXPIRY_SECONDS,
) -> str:
    """Return a temporary download URL for a private S3 object."""
    key = (s3_key or "").lstrip("/")
    if not key:
        return ""

    bucket = getattr(settings, "AWS_STORAGE_BUCKET_NAME", "")
    if not bucket:
        return ""

    try:
        import boto3

        client = boto3.client(
            "s3",
            region_name=getattr(settings, "AWS_S3_REGION_NAME", None) or None,
        )
        return client.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=expires_in,
        )
    except Exception:
        logger.exception("generate_presigned_url failed for key=%s", key)
        return ""


def file_field_download_url(
    file_field,
    *,
    request=None,
    expires_in: int = DEFAULT_PRESIGNED_EXPIRY_SECONDS,
) -> str:
    """Resolve a FileField/ImageField to a client-usable URL.

    When S3 is active, returns a fresh presigned URL. Otherwise falls back to
    local MEDIA URLs (absolute when *request* is provided).
    """
    if not file_field:
        return ""

    name = getattr(file_field, "name", "") or ""
    if s3_media_enabled() and name:
        url = generate_presigned_url(name, expires_in=expires_in)
        if url:
            return url

    url = file_field.url
    if request and url and not url.startswith(("http://", "https://")):
        return request.build_absolute_uri(url)
    return url or ""
