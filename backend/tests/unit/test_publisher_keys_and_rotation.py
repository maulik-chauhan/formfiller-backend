"""Publisher key set, rotation and the v1/v2 digest formats."""

from __future__ import annotations

import base64
from collections.abc import Iterator

import pytest

from app.config import get_settings
from app.core import signing

PREV_SEED = base64.b64encode(bytes(range(100, 132))).decode()


@pytest.fixture
def rotated(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[str, str]]:
    """Simulate a rotation: yesterday's active key becomes today's previous key."""
    old_seed = get_settings().template_signing_ed25519_seed_b64
    old_kid = get_settings().template_signing_key_id
    old_digest = "ab" * 32
    old_signature, _ = signing.sign_publisher_digest(old_digest)
    monkeypatch.setenv("TEMPLATE_SIGNING_ED25519_SEED_B64", PREV_SEED)
    monkeypatch.setenv("TEMPLATE_SIGNING_KEY_ID", "ed25519-template-2027-v2")
    monkeypatch.setenv("TEMPLATE_SIGNING_PREV_SEED_B64", old_seed)
    monkeypatch.setenv("TEMPLATE_SIGNING_PREV_KEY_ID", old_kid)
    get_settings.cache_clear()
    signing.reset_key_cache()
    yield old_digest, old_signature
    monkeypatch.undo()
    get_settings.cache_clear()
    signing.reset_key_cache()


def test_key_set_has_one_active_key_with_raw_32_byte_public_key() -> None:
    keys = signing.publisher_key_set()
    assert [k.status for k in keys] == ["active"]
    assert keys[0].kid == get_settings().template_signing_key_id
    assert len(base64.b64decode(keys[0].public_key_b64)) == 32


def test_publisher_key_is_not_the_lease_or_access_key() -> None:
    from app.core.elt import public_key_set

    publisher = {k.public_key_b64 for k in signing.publisher_key_set()}
    lease_values = {value for entry in public_key_set() for value in entry.values()}
    assert publisher.isdisjoint(lease_values)
    assert {k.kid for k in signing.publisher_key_set()}.isdisjoint(lease_values)


def test_v2_digest_is_domain_separated_and_injective() -> None:
    base = {
        "template_id": "t",
        "slug": "a",
        "jurisdiction": "J",
        "category": "C",
        "official_form_code": "x|y",
        "semver": "1.0.0",
        "published_version_id": "v",
        "blank_pdf_sha256": "a" * 64,
        "manifest_sha256": "b" * 64,
        "page_count": 1,
    }
    digest = signing.template_bundle_digest_v2(**base)
    assert digest == signing.template_bundle_digest_v2(**base)
    # Moving a delimiter between adjacent fields must change the digest.
    shifted = {**base, "category": "C|x", "official_form_code": "y"}
    assert signing.template_bundle_digest_v2(**shifted) != digest
    for key in base:
        changed = {**base, key: (base[key] + 1) if key == "page_count" else base[key] + "z"}
        assert signing.template_bundle_digest_v2(**changed) != digest
    assert digest != signing.template_bundle_digest_v1(
        template_id="t",
        semver="1.0.0",
        blank_pdf_sha256="a" * 64,
        manifest_sha256="b" * 64,
        page_count=1,
    )


def test_sign_and_verify_roundtrip_and_tamper() -> None:
    digest = "cd" * 32
    signature, kid = signing.sign_publisher_digest(digest)
    assert signing.verify_publisher_digest(digest, signature, kid)
    assert not signing.verify_publisher_digest("ce" * 32, signature, kid)
    assert not signing.verify_publisher_digest(digest, signature, "unknown-kid")
    assert not signing.verify_publisher_digest(digest, "not-base64!", kid)


def test_rotation_keeps_previous_signatures_valid(rotated: tuple[str, str]) -> None:
    old_digest, old_signature = rotated
    keys = {k.status: k for k in signing.publisher_key_set()}
    assert set(keys) == {"active", "previous"}
    assert keys["active"].kid == "ed25519-template-2027-v2"
    assert signing.verify_publisher_digest(old_digest, old_signature, keys["previous"].kid)
    # The old signature must not verify under the new key id.
    assert not signing.verify_publisher_digest(old_digest, old_signature, keys["active"].kid)
    new_signature, new_kid = signing.sign_publisher_digest(old_digest)
    assert new_kid == "ed25519-template-2027-v2" and new_signature != old_signature


def test_retiring_the_previous_key_revokes_its_signatures(
    rotated: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    old_digest, old_signature = rotated
    old_kid = get_settings().template_signing_prev_key_id
    monkeypatch.setenv("TEMPLATE_SIGNING_PREV_SEED_B64", "")
    monkeypatch.setenv("TEMPLATE_SIGNING_PREV_KEY_ID", "")
    get_settings.cache_clear()
    signing.reset_key_cache()
    assert [k.status for k in signing.publisher_key_set()] == ["active"]
    assert not signing.verify_publisher_digest(old_digest, old_signature, old_kid)


def test_previous_key_requires_both_seed_and_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEMPLATE_SIGNING_PREV_SEED_B64", PREV_SEED)
    get_settings.cache_clear()
    signing.reset_key_cache()
    try:
        with pytest.raises(RuntimeError):
            signing.publisher_key_set()
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
        signing.reset_key_cache()
