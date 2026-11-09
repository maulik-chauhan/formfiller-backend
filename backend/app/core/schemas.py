"""Shared Pydantic v2 base models."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    """Base for all request bodies — rejects unknown fields (``extra='forbid'``)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ResponseModel(BaseModel):
    """Base for response bodies (permissive on read, serialised from ORM)."""

    model_config = ConfigDict(from_attributes=True)
