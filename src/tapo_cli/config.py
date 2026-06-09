"""Runtime settings, overridable via environment variables (TAPO_* prefix)."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    """Application settings. Bound to localhost by design (unencrypted creds)."""

    host: str = "127.0.0.1"
    port: int = 8077
    # go2rtc HTTP API port; may be bumped at startup if already in use.
    go2rtc_port: int = 1984
    # Max simultaneous recording downloads.
    max_concurrent_downloads: int = 2
    # Open the browser automatically on startup.
    open_browser: bool = True

    @classmethod
    def load(cls) -> "Settings":
        return cls(
            host=os.environ.get("TAPO_HOST", "127.0.0.1"),
            port=_int_env("TAPO_PORT", 8077),
            go2rtc_port=_int_env("TAPO_GO2RTC_PORT", 1984),
            max_concurrent_downloads=_int_env("TAPO_MAX_DOWNLOADS", 2),
            open_browser=os.environ.get("TAPO_OPEN_BROWSER", "1") != "0",
        )

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"
