"""Parse image location references from jobs (HTTP, GCS, Firebase Storage)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from idp_contracts.compat import StrEnum
from urllib.parse import unquote, urlparse


class ImageSourceType(StrEnum):
    HTTP = "http"
    GCS = "gcs"
    FIREBASE = "firebase"
    FIREBASE_HTTPS = "firebase_https"  # firebasestorage.googleapis.com download URL


@dataclass(frozen=True)
class ParsedImageRef:
    source_type: ImageSourceType
    original: str
    bucket: str | None = None
    object_path: str | None = None
    http_url: str | None = None


# https://firebasestorage.googleapis.com/v0/b/{bucket}/o/{encoded_path}?...
_FIREBASE_STORAGE_RE = re.compile(
    r"^https://firebasestorage\.googleapis\.com/v0/b/([^/]+)/o/([^?]+)",
    re.IGNORECASE,
)
# https://storage.googleapis.com/{bucket}/{object_path}
_GCS_HTTPS_RE = re.compile(
    r"^https://storage\.googleapis\.com/([^/]+)/(.+)$",
    re.IGNORECASE,
)


def parse_image_reference(url: str) -> ParsedImageRef:
    """
    Supported formats:
    - https://... / http://...  (any host; optional allowlist at fetch time)
    - gs://bucket/object/path
    - firebase://bucket/object/path  (Firebase Storage via GCS API)
    - https://firebasestorage.googleapis.com/v0/b/.../o/...?alt=media
    - https://storage.googleapis.com/bucket/object (GCS public/signed style)
    """
    url = url.strip()
    if not url:
        raise ValueError("Empty image URL")

    if url.startswith("gs://"):
        _, rest = url.split("gs://", 1)
        bucket, _, path = rest.partition("/")
        if not bucket or not path:
            raise ValueError(f"Invalid gs:// URI: {url}")
        return ParsedImageRef(ImageSourceType.GCS, url, bucket=bucket, object_path=path)

    if url.startswith("firebase://"):
        _, rest = url.split("firebase://", 1)
        bucket, _, path = rest.partition("/")
        if not bucket or not path:
            raise ValueError(f"Invalid firebase:// URI: {url}")
        return ParsedImageRef(ImageSourceType.FIREBASE, url, bucket=bucket, object_path=path)

    parsed = urlparse(url)
    if parsed.scheme in ("http", "https"):
        m = _FIREBASE_STORAGE_RE.match(url)
        if m:
            bucket = m.group(1)
            object_path = unquote(m.group(2).replace("+", " "))
            return ParsedImageRef(
                ImageSourceType.FIREBASE_HTTPS,
                url,
                bucket=bucket,
                object_path=object_path,
                http_url=url,
            )
        m = _GCS_HTTPS_RE.match(url)
        if m:
            return ParsedImageRef(
                ImageSourceType.GCS,
                url,
                bucket=m.group(1),
                object_path=unquote(m.group(2)),
                http_url=url,
            )
        return ParsedImageRef(ImageSourceType.HTTP, url, http_url=url)

    raise ValueError(
        f"Unsupported image reference scheme: {url[:32]}... "
        "Use https://, gs://, firebase://, or Firebase Storage download URL"
    )
