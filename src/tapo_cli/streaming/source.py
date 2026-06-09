"""Build the go2rtc stream source URL for a camera.

go2rtc has a native ``tapo://`` source that speaks Tapo's protocol directly (no
RTSP / Camera Account needed). It authenticates as ``admin`` and — unlike the
control API, which takes the plaintext account password — expects the password
as the **uppercase SHA-256 hash** of the TP-Link account password.
"""

from __future__ import annotations

import hashlib
from typing import Any


def cloud_password_hash(account_password: str) -> str:
    return hashlib.sha256((account_password or "").encode()).hexdigest().upper()


def tapo_source_url(cam: dict[str, Any]) -> str:
    host = cam["host"]
    port = cam.get("control_port") or 443
    hostpart = host if port == 443 else f"{host}:{port}"
    return f"tapo://admin:{cloud_password_hash(cam['account_password'])}@{hostpart}"
