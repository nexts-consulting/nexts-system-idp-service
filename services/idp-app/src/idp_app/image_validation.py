"""Validate image_urls on job creation."""

from idp_common.storage_uri import parse_image_reference


def validate_image_urls(urls: list[str]) -> list[str]:
    """Parse each URL; raise ValueError with index if invalid."""
    errors: list[str] = []
    for i, url in enumerate(urls):
        try:
            parse_image_reference(url)
        except ValueError as e:
            errors.append(f"image_urls[{i}]: {e}")
    if errors:
        raise ValueError("; ".join(errors))
    return urls
