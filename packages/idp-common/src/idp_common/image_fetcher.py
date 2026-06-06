"""Download job images from HTTP, GCS, or Firebase Storage."""

from __future__ import annotations

import asyncio
import time
import httpx
from google.cloud import storage

from idp_common.metrics import MetricsRegistry
from idp_common.storage_uri import ImageSourceType, ParsedImageRef, parse_image_reference


class ImageFetcher:
    """
    Fetches image bytes for preprocess (and reuse elsewhere).

    Firebase Storage buckets are GCS buckets; access via google-cloud-storage
    with Application Default Credentials (Firebase/GCP service account).
    """

    def __init__(
        self,
        max_concurrent: int = 32,
        max_bytes: int = 20 * 1024 * 1024,
        timeout: float = 30.0,
        metrics: MetricsRegistry | None = None,
        env: str = "dev",
        service_label: str = "idp-preprocess",
        allowed_buckets: set[str] | None = None,
        allowed_http_hosts: set[str] | None = None,
        gcs_client: storage.Client | None = None,
        prefer_gcs_api_for_firebase_https: bool = True,
    ) -> None:
        self._sem = asyncio.Semaphore(max_concurrent)
        self.max_bytes = max_bytes
        self.timeout = timeout
        self.metrics = metrics
        self.env = env
        self.service_label = service_label
        self.allowed_buckets = allowed_buckets
        self.allowed_http_hosts = allowed_http_hosts
        self._gcs = gcs_client or storage.Client()
        self.prefer_gcs_api_for_firebase_https = prefer_gcs_api_for_firebase_https

    async def fetch_all(self, urls: list[str]) -> list[bytes]:
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            tasks = [self._fetch_one(client, url) for url in urls]
            return await asyncio.gather(*tasks)

    async def _fetch_one(self, client: httpx.AsyncClient, url: str) -> bytes:
        async with self._sem:
            start = time.perf_counter()
            source = "unknown"
            try:
                ref = parse_image_reference(url)
                source = ref.source_type.value
                if ref.source_type in (
                    ImageSourceType.GCS,
                    ImageSourceType.FIREBASE,
                ):
                    data = await asyncio.to_thread(self._download_gcs_blob, ref.bucket, ref.object_path)
                elif ref.source_type == ImageSourceType.FIREBASE_HTTPS:
                    if self.prefer_gcs_api_for_firebase_https and ref.bucket and ref.object_path:
                        self._check_bucket_allowed(ref.bucket)
                        try:
                            data = await asyncio.to_thread(
                                self._download_gcs_blob, ref.bucket, ref.object_path
                            )
                        except Exception:
                            data = await self._download_http(client, ref.http_url or url)
                    else:
                        data = await self._download_http(client, ref.http_url or url)
                else:
                    data = await self._download_http(client, ref.http_url or url)

                if len(data) > self.max_bytes:
                    raise ValueError(f"Image exceeds max size {self.max_bytes}")
                self._observe_download(start, source)
                return data
            except Exception as e:
                self._record_failure(type(e).__name__)
                raise

    def _check_bucket_allowed(self, bucket: str) -> None:
        if self.allowed_buckets is not None and bucket not in self.allowed_buckets:
            raise ValueError(f"Bucket not allowed: {bucket}")

    def _download_gcs_blob(self, bucket_name: str | None, object_path: str | None) -> bytes:
        if not bucket_name or not object_path:
            raise ValueError("Missing bucket or object path for GCS/Firebase download")
        self._check_bucket_allowed(bucket_name)
        blob = self._gcs.bucket(bucket_name).blob(object_path)
        return blob.download_as_bytes()

    async def _download_http(self, client: httpx.AsyncClient, url: str) -> bytes:
        from urllib.parse import urlparse

        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"Unsupported HTTP URL: {url}")
        if self.allowed_http_hosts and parsed.hostname not in self.allowed_http_hosts:
            raise ValueError(f"Host not allowed: {parsed.hostname}")
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.content

    def _observe_download(self, start: float, source: str) -> None:
        if not self.metrics:
            return
        self.metrics.download_duration.labels(
            service=self.service_label, env=self.env
        ).observe(time.perf_counter() - start)

    def _record_failure(self, error_class: str) -> None:
        if not self.metrics:
            return
        self.metrics.download_failures.labels(
            service=self.service_label,
            env=self.env,
            error_class=error_class,
        ).inc()
