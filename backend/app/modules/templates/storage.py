"""Blank-PDF object storage port and adapters.

Only validated *blank* government PDFs are ever stored. The port exposes a stable
content-addressed object key and a download URL that is returned (never the bytes)
in the bundle response. The download URL is a trust boundary separate from the
government-source allowlist: it is built only from trusted configuration plus the
server-generated key, never from client or admin input.

Adapters
--------
* ``local``        filesystem + static CDN base URL; URLs do not expire.
* ``mock_signed``  filesystem + HMAC-signed expiring URLs. **Tests only** — it
                   exists to exercise the expiring-URL contract end to end.

A production object-store/CDN adapter (DEC-3) implements the same port:
``store_blank_pdf`` → PUT to the bucket, ``build_download_url`` → CDN URL or a
provider-signed expiring URL, ``supports_expiring_urls`` accordingly. The path
component MUST remain ``/blank_pdfs/<sha256>.pdf`` because clients pin it.
"""

from __future__ import annotations

import abc
import hashlib
import hmac
import pathlib
import time
from urllib.parse import parse_qs, urlsplit

from app.config import get_settings


class ObjectStoragePort(abc.ABC):
    #: True when ``build_download_url`` returns URLs that stop working after a TTL.
    supports_expiring_urls: bool = False

    @abc.abstractmethod
    def store_blank_pdf(self, *, object_key: str, data: bytes) -> str:
        """Persist ``data`` under ``object_key`` and return the stored key."""

    @abc.abstractmethod
    def build_download_url(self, object_key: str, expires_in_seconds: int | None = None) -> str:
        """Return a retrieval URL for ``object_key`` (CDN/signed in production)."""

    @abc.abstractmethod
    def read_blank_pdf(self, object_key: str) -> bytes | None:
        """Return stored bytes for ``object_key`` or ``None`` if absent."""


class LocalObjectStorage(ObjectStoragePort):
    """Filesystem-backed storage rooted at ``settings.template_storage_dir``."""

    supports_expiring_urls = False

    def __init__(self) -> None:
        self._root = pathlib.Path(get_settings().template_storage_dir)
        self._cdn_base = get_settings().template_cdn_base_url.rstrip("/")

    def _path(self, object_key: str) -> pathlib.Path:
        # object_key is server-generated (sha256-derived); contains no user path.
        return self._root / object_key

    def store_blank_pdf(self, *, object_key: str, data: bytes) -> str:
        path = self._path(object_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return object_key

    def build_download_url(self, object_key: str, expires_in_seconds: int | None = None) -> str:
        return f"{self._cdn_base}/{object_key}"

    def read_blank_pdf(self, object_key: str) -> bytes | None:
        path = self._path(object_key)
        return path.read_bytes() if path.exists() else None


class MockSignedUrlStorage(LocalObjectStorage):
    """Test adapter issuing HMAC-signed expiring URLs (``?expires=&sig=``)."""

    supports_expiring_urls = True

    def __init__(self) -> None:
        super().__init__()
        self._key = get_settings().template_mock_url_signing_key.encode("utf-8")
        self._default_ttl = get_settings().template_download_url_ttl_seconds

    def _sign(self, object_key: str, expires: int) -> str:
        message = f"{object_key}\n{expires}".encode()
        return hmac.new(self._key, message, hashlib.sha256).hexdigest()

    def build_download_url(self, object_key: str, expires_in_seconds: int | None = None) -> str:
        ttl = self._default_ttl if expires_in_seconds is None else expires_in_seconds
        expires = int(time.time()) + ttl
        return (
            f"{self._cdn_base}/{object_key}?expires={expires}&sig={self._sign(object_key, expires)}"
        )

    def verify_download_url(self, url: str, *, now: float | None = None) -> bool:
        """Validate a URL issued by this adapter (what a real CDN edge would do)."""
        parts = urlsplit(url)
        if f"{parts.scheme}://{parts.netloc}" != self._cdn_base:
            return False
        object_key = parts.path.lstrip("/")
        query = parse_qs(parts.query)
        try:
            expires = int(query["expires"][0])
            signature = query["sig"][0]
        except (KeyError, IndexError, ValueError):
            return False
        if (time.time() if now is None else now) >= expires:
            return False
        return hmac.compare_digest(signature, self._sign(object_key, expires))


def object_key_for(blank_pdf_sha256: str) -> str:
    """Deterministic content-addressed key for a blank PDF."""
    return f"blank_pdfs/{blank_pdf_sha256}.pdf"


def get_storage() -> ObjectStoragePort:
    provider = get_settings().template_storage_provider.strip().lower()
    if provider == "local":
        return LocalObjectStorage()
    if provider == "mock_signed":
        return MockSignedUrlStorage()
    raise RuntimeError(
        f"Unknown TEMPLATE_STORAGE_PROVIDER {provider!r}; expected 'local' or 'mock_signed'. "
        "A production object-store adapter has not been selected (DEC-3)."
    )
