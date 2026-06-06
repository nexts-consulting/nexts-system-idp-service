import pytest

from idp_common.storage_uri import ImageSourceType, parse_image_reference


def test_parse_gs_uri():
    ref = parse_image_reference("gs://my-bucket/invoices/a.jpg")
    assert ref.source_type == ImageSourceType.GCS
    assert ref.bucket == "my-bucket"
    assert ref.object_path == "invoices/a.jpg"


def test_parse_firebase_scheme():
    ref = parse_image_reference("firebase://myapp.appspot.com/receipts/1.png")
    assert ref.source_type == ImageSourceType.FIREBASE
    assert ref.bucket == "myapp.appspot.com"
    assert ref.object_path == "receipts/1.png"


def test_parse_firebase_https_url():
    url = (
        "https://firebasestorage.googleapis.com/v0/b/my-bucket/o/receipts%2Fphoto.jpg"
        "?alt=media&token=abc"
    )
    ref = parse_image_reference(url)
    assert ref.source_type == ImageSourceType.FIREBASE_HTTPS
    assert ref.bucket == "my-bucket"
    assert ref.object_path == "receipts/photo.jpg"


def test_parse_http():
    ref = parse_image_reference("https://cdn.example.com/img.jpg")
    assert ref.source_type == ImageSourceType.HTTP


def test_parse_storage_googleapis():
    ref = parse_image_reference("https://storage.googleapis.com/bkt/path/to/img.jpg")
    assert ref.source_type == ImageSourceType.GCS
    assert ref.bucket == "bkt"
    assert ref.object_path == "path/to/img.jpg"


def test_invalid_scheme():
    with pytest.raises(ValueError, match="Unsupported"):
        parse_image_reference("ftp://x/y")
