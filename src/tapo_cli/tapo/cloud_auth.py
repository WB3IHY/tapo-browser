"""TP-Link cloud login, for the WebRTC playback path only.

The current Tapo Android app's own reverse-engineered request shape
(POST https://n-wap.i.tplinkcloud.com/api/v1/account, full field set incl.
appVersion/platform/refreshTokenNeeded) consistently got error -20107
"Parameter is invalid" even after matching the decompiled wire-level
LoginReq class field-for-field - some requirement wasn't recovered from
static analysis alone. Switched to the older, simpler endpoint and request
shape used by petretiandrea/plugp100 (a real, actively-used open-source Tapo
library) instead: POST https://wap.tplinkcloud.com with just
appType/cloudUserName/cloudPassword/terminalUUID. This is a known-working
reference rather than another guess.
"""

from __future__ import annotations

import ssl
import uuid
from dataclasses import dataclass
from pathlib import Path

import certifi
import httpx

# TP-Link's cloud hosts (*.tplinkcloud.com, *.tplinkra.com) pin against their
# own private root CA, not a publicly-trusted one - a plain certifi-only
# client fails TLS verification against them. This bundle, extracted from
# the Tapo Android APK's own mergedCA.pem, contains that private root
# alongside the standard public CAs the app also trusts. Loaded together
# with certifi's bundle (not instead of it) so both pinned and
# normally-trusted hosts work through the same client.
_TPLINK_CA_BUNDLE = Path(__file__).parent / "vendor" / "tplink_ca_bundle.pem"


def _build_ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context(cafile=certifi.where())
    ctx.load_verify_locations(cafile=str(_TPLINK_CA_BUNDLE))
    return ctx


LOGIN_URL = "https://wap.tplinkcloud.com"
APP_TYPE = "Tapo_Android"


class CloudAuthError(Exception):
    """Login failed for a reason other than needing MFA."""

    def __init__(self, message: str, error_code: int | None = None) -> None:
        super().__init__(message)
        self.error_code = error_code


class CloudAuthMFARequired(CloudAuthError):
    """The account has 2FA/MFA enabled - this flow doesn't support it yet."""

    def __init__(self, message: str, mfa_process_id: str | None, supported_types: list | None) -> None:
        super().__init__(message)
        self.mfa_process_id = mfa_process_id
        self.supported_types = supported_types


@dataclass
class CloudSession:
    token: str
    refresh_token: str | None
    account_id: str | None
    app_server_url: str | None
    email: str


def new_terminal_uuid() -> str:
    """Generate a per-install identifier, persisted after first login (see
    CloudAccountRepo) rather than regenerated per request - mimics a real
    app install's stable device ID."""
    return str(uuid.uuid4())


async def login(email: str, password: str, terminal_uuid: str) -> CloudSession:
    body = {
        "method": "login",
        "params": {
            "appType": APP_TYPE,
            "cloudUserName": email,
            "cloudPassword": password,
            "terminalUUID": terminal_uuid,
        },
    }
    async with httpx.AsyncClient(timeout=15.0, verify=_build_ssl_context()) as client:
        resp = await client.post(LOGIN_URL, json=body)
        resp.raise_for_status()
        data = resp.json()

    error_code = data.get("error_code", 0)
    result = data.get("result") or {}

    if error_code != 0:
        msg = data.get("msg") or f"Cloud login failed (error_code={error_code})"
        if result.get("MFAProcessId") or result.get("supportedMFATypes"):
            raise CloudAuthMFARequired(
                "This account requires two-factor authentication, which isn't "
                "supported yet.",
                mfa_process_id=result.get("MFAProcessId"),
                supported_types=result.get("supportedMFATypes"),
            )
        raise CloudAuthError(msg, error_code=error_code)

    token = result.get("token")
    if not token:
        raise CloudAuthError("Cloud login response had no token")

    return CloudSession(
        token=token,
        refresh_token=result.get("refreshToken"),
        account_id=result.get("accountId"),
        app_server_url=result.get("appServerUrl"),
        email=result.get("email") or email,
    )
