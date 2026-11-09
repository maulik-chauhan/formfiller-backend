"""OTP delivery provider port and adapters.

The service depends only on the :class:`OtpDeliveryProvider` interface. A mock
provider is used for local development and tests; it retains the last code per
recipient *in memory only* so tests can read it, and it NEVER logs or retains
codes when running in a production environment. A production SMS adapter
interface is provided whose credentials come exclusively from the environment.

OTP codes are never written to logs in production by any adapter.
"""

from __future__ import annotations

import abc
import logging

from app.config import get_settings

logger = logging.getLogger("app.auth.otp_delivery")


class OtpDeliveryProvider(abc.ABC):
    """Port for delivering a one-time passcode to a recipient."""

    @abc.abstractmethod
    async def send_otp(self, *, mobile_e164: str, code: str, challenge_id: str) -> None:
        """Deliver ``code`` to ``mobile_e164``. Implementations must not log the code."""


class MockOtpDeliveryProvider(OtpDeliveryProvider):
    """In-memory provider for development/tests.

    Stores the last code per recipient so automated tests can retrieve it via
    :meth:`get_last_code`. In production it stores and logs nothing.
    """

    def __init__(self) -> None:
        self._last_by_mobile: dict[str, str] = {}

    async def send_otp(self, *, mobile_e164: str, code: str, challenge_id: str) -> None:
        if get_settings().is_production:
            # Never retain or log codes in production.
            return
        self._last_by_mobile[mobile_e164] = code

    def get_last_code(self, mobile_e164: str) -> str | None:
        """Return the most recent code sent to ``mobile_e164`` (tests only)."""
        if get_settings().is_production:
            return None
        return self._last_by_mobile.get(mobile_e164)

    def clear(self) -> None:
        self._last_by_mobile.clear()


class ConsoleOtpDeliveryProvider(OtpDeliveryProvider):
    """Developer convenience provider that logs the code OUTSIDE production only."""

    async def send_otp(self, *, mobile_e164: str, code: str, challenge_id: str) -> None:
        if get_settings().is_production:
            return
        masked_mobile = f"{mobile_e164[:3]}******{mobile_e164[-2:]}"
        logger.info("DEV OTP for %s (challenge %s): %s", masked_mobile, challenge_id, code)


class ProductionSmsProvider(OtpDeliveryProvider):
    """Adapter interface for a real SMS gateway.

    Credentials are read from the environment (``SMS_PROVIDER_*``). The concrete
    HTTP integration with a licensed Indian SMS gateway is a pending production
    task; ``send_otp`` raises until wired, and never logs the code.
    """

    def __init__(self) -> None:
        settings = get_settings()
        self._api_key = settings.sms_provider_api_key
        self._api_secret = settings.sms_provider_api_secret
        self._sender_id = settings.sms_provider_sender_id
        if not (self._api_key and self._api_secret and self._sender_id):
            raise RuntimeError("SMS provider selected but SMS_PROVIDER_* credentials are not set")

    async def send_otp(self, *, mobile_e164: str, code: str, challenge_id: str) -> None:
        raise NotImplementedError("Concrete SMS gateway integration is a pending production task")


_mock_singleton = MockOtpDeliveryProvider()


def get_otp_provider() -> OtpDeliveryProvider:
    """Resolve the configured OTP delivery provider."""
    provider = get_settings().otp_delivery_provider.lower()
    if provider == "console":
        return ConsoleOtpDeliveryProvider()
    if provider == "sms":
        return ProductionSmsProvider()
    return _mock_singleton


def get_mock_provider() -> MockOtpDeliveryProvider:
    """Return the shared mock provider (test helper)."""
    return _mock_singleton
