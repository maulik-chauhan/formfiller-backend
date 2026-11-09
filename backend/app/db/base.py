"""Declarative base and shared column types for SQLAlchemy 2.x models."""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import TIMESTAMP, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Project-wide declarative base.

    All models import this so a single ``Base.metadata`` drives Alembic
    autogenerate and test-database creation.
    """


def uuid_pk() -> Mapped[uuid.UUID]:
    """A UUID primary key column (application-generated)."""
    return mapped_column(primary_key=True, default=uuid.uuid4)


def created_at_col() -> Mapped[dt.datetime]:
    return mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())


def updated_at_col() -> Mapped[dt.datetime]:
    return mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
