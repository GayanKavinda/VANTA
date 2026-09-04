from urllib.parse import urlparse

from app.utils.constants import SUPPORTED_SCHEMES


def is_valid_url(url: str) -> bool:
    if not url or not url.strip():
        return False

    try:
        result = urlparse(url.strip())

        return all([
            result.scheme in SUPPORTED_SCHEMES,
            bool(result.netloc),
        ])
    except Exception:
        return False


def is_empty_url(url: str) -> bool:
    return not url or not url.strip()


def get_url_scheme(url: str) -> str | None:
    try:
        parsed = urlparse(url.strip())
        return parsed.scheme if parsed.scheme else None
    except Exception:
        return None
