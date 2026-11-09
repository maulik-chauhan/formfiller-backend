"""Enum / constants coverage and 19-table inventory guard."""

from __future__ import annotations

from app.core import constants as c
from app.db.models import CANONICAL_TABLES, Base


def test_canonical_table_count_is_19() -> None:
    assert len(CANONICAL_TABLES) == 19
    assert len(set(CANONICAL_TABLES)) == 19


def test_metadata_matches_canonical_inventory() -> None:
    mapped = set(Base.metadata.tables.keys())
    assert mapped == set(CANONICAL_TABLES)


def test_credit_bucket_values() -> None:
    assert set(c.CreditBucket) == {
        c.CreditBucket.FREE_LIFETIME,
        c.CreditBucket.STANDARD_20,
        c.CreditBucket.SIGNATURE_50,
        c.CreditBucket.SIGNATURE_UPGRADE_30,
        c.CreditBucket.ESIGN,
    }


def test_lease_status_round_trip() -> None:
    assert c.LeaseStatus("EXPIRED_RELEASED") is c.LeaseStatus.EXPIRED_RELEASED
    assert str(c.LeaseStatus.ISSUED) == "ISSUED"


def test_admin_upload_roles() -> None:
    assert c.UserRole.TEMPLATE_CURATOR in c.ADMIN_TEMPLATE_UPLOAD_ROLES
    assert c.UserRole.SUPER_ADMIN in c.ADMIN_TEMPLATE_UPLOAD_ROLES
    assert c.UserRole.CITIZEN not in c.ADMIN_TEMPLATE_UPLOAD_ROLES


def test_no_commercial_prices_in_constants() -> None:
    # Rupee amounts and quota counts must not leak into the enum module.
    import inspect

    source = inspect.getsource(c)
    for forbidden in ("2000", "5000", "3000", "₹"):
        assert forbidden not in source
