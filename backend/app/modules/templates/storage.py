"""Blank-PDF object storage port and a local filesystem adapter.

Only validated *blank* government PDFs are ever stored. The port exposes a stable
object key and a download URL used in the signed bundle manifest. A production
deployment swaps the local adapter for an S3-compatible / CDN signer.
"""

from __future__ import annotations

import abc
import pathlib

from app.config import get_settings


class ObjectStoragePort(abc.ABC):
    @abc.abstractmethod
    def store_blank_pdf(self, *, object_key: str, data: bytes) -> str:
        """Persist ``data`` under ``object_key`` and return the stored key."""

    @abc.abstractmethod
    def build_download_url(self, object_key: str) -> str:
        """Return a retrieval URL for ``object_key`` (CDN/signed in production)."""

    @abc.abstractmethod
    def read_blank_pdf(self, object_key: str) -> bytes | None:
        """Return stored bytes for ``object_key`` or ``None`` if absent."""


class LocalObjectStorage(ObjectStoragePort):
    """Filesystem-backed storage rooted at ``settings.template_storage_dir``."""

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

    def build_download_url(self, object_key: str) -> str:
        return f"{self._cdn_base}/{object_key}"

    def read_blank_pdf(self, object_key: str) -> bytes | None:
        path = self._path(object_key)
        return path.read_bytes() if path.exists() else None


def object_key_for(blank_pdf_sha256: str) -> str:
    """Deterministic content-addressed key for a blank PDF."""
    return f"blank_pdfs/{blank_pdf_sha256}.pdf"


def get_storage() -> ObjectStoragePort:
    return LocalObjectStorage()
