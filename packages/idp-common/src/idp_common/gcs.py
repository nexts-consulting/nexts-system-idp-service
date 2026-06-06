import io
import os
from pathlib import Path

from google.cloud import storage


class GCSClient:
    def __init__(self, bucket: str, emulator_host: str | None = None) -> None:
        self.bucket_name = bucket
        if emulator_host:
            host = emulator_host.replace("http://", "").replace("https://", "")
            os.environ["STORAGE_EMULATOR_HOST"] = host
            os.environ["GOOGLE_CLOUD_PROJECT"] = os.environ.get("GOOGLE_CLOUD_PROJECT", "local-dev")
        self._client = storage.Client()
        self._bucket = self._client.bucket(bucket)

    def upload_bytes(self, path: str, data: bytes, content_type: str = "image/jpeg") -> str:
        blob = self._bucket.blob(path)
        blob.upload_from_string(data, content_type=content_type)
        return f"gs://{self.bucket_name}/{path}"

    def upload_file(self, path: str, local_path: Path, content_type: str = "image/jpeg") -> str:
        blob = self._bucket.blob(path)
        blob.upload_from_filename(str(local_path), content_type=content_type)
        return f"gs://{self.bucket_name}/{path}"

    def download_to_bytes(self, gcs_uri: str) -> bytes:
        if gcs_uri.startswith("gs://"):
            _, rest = gcs_uri.split("gs://", 1)
            bucket_name, blob_path = rest.split("/", 1)
            blob = self._client.bucket(bucket_name).blob(blob_path)
        else:
            blob = self._bucket.blob(gcs_uri)
        return blob.download_as_bytes()

    def signed_url(self, gcs_uri: str, expiration_seconds: int = 3600) -> str:
        if gcs_uri.startswith("gs://"):
            _, rest = gcs_uri.split("gs://", 1)
            bucket_name, blob_path = rest.split("/", 1)
            blob = self._client.bucket(bucket_name).blob(blob_path)
        else:
            blob = self._bucket.blob(gcs_uri)
        return blob.generate_signed_url(expiration=expiration_seconds, version="v4")

    @staticmethod
    def uri_to_path(gcs_uri: str) -> str:
        if gcs_uri.startswith("gs://"):
            return gcs_uri.split("/", 3)[-1]
        return gcs_uri
