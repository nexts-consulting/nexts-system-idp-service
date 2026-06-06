import pytest

from idp_app.image_validation import validate_image_urls


def test_accepts_firebase_scheme():
    validate_image_urls(["firebase://bkt/path/img.jpg"])


def test_rejects_invalid():
    with pytest.raises(ValueError, match="image_urls\\[0\\]"):
        validate_image_urls(["ftp://x/y"])
