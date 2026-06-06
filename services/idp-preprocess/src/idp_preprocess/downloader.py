import asyncio
import time
from urllib.parse import urlparse

import httpx
from PIL import Image

from idp_common.metrics import MetricsRegistry


class ImageDownloader:
    def __init__(
        self,
        max_concurrent: int = 32,
        max_bytes: int = 20 * 1024 * 1024,
        timeout: float = 30.0,
        metrics: MetricsRegistry | None = None,
        env: str = "dev",
    ) -> None:
        self._sem = asyncio.Semaphore(max_concurrent)
        self.max_bytes = max_bytes
        self.timeout = timeout
        self.metrics = metrics
        self.env = env

    async def download_all(self, urls: list[str]) -> list[bytes]:
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            tasks = [self._download_one(client, url) for url in urls]
            return await asyncio.gather(*tasks)

    async def _download_one(self, client: httpx.AsyncClient, url: str) -> bytes:
        async with self._sem:
            start = time.perf_counter()
            try:
                if url.startswith("gs://"):
                    raise ValueError("Use signed HTTPS URL for GCS in preprocess worker")
                resp = await client.get(url)
                resp.raise_for_status()
                data = resp.content
                if len(data) > self.max_bytes:
                    raise ValueError(f"Image exceeds max size {self.max_bytes}")
                if self.metrics:
                    self.metrics.download_duration.labels(
                        service="idp-preprocess", env=self.env
                    ).observe(time.perf_counter() - start)
                return data
            except Exception as e:
                if self.metrics:
                    self.metrics.download_failures.labels(
                        service="idp-preprocess",
                        env=self.env,
                        error_class=type(e).__name__,
                    ).inc()
                raise


def normalize_image(data: bytes, max_edge: int = 4096) -> bytes:
    import io

    img = Image.open(io.BytesIO(data))
    img = img.convert("RGB")
    if hasattr(img, "_getexif"):
        img = ImageOps_exif_transpose(img)
    w, h = img.size
    scale = min(1.0, max_edge / max(w, h))
    if scale < 1.0:
        img = img.resize((int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=92)
    return buf.getvalue()


def ImageOps_exif_transpose(img: Image.Image) -> Image.Image:
    try:
        from PIL import ImageOps

        return ImageOps.exif_transpose(img)
    except Exception:
        return img


def validate_url_host(url: str, allowed_hosts: set[str] | None = None) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Unsupported URL scheme: {url}")
    if allowed_hosts and parsed.hostname not in allowed_hosts:
        raise ValueError(f"Host not allowed: {parsed.hostname}")
