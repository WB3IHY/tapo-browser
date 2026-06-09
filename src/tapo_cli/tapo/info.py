"""Connection test + basic info parsing for a camera.

Tapo firmware responses vary between models/firmware, so parsing here is
deliberately defensive — it digs for the fields it wants and degrades to
``None`` rather than failing the whole probe.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from .client import build_tapo, run_blocking

log = logging.getLogger("tapo_cli.tapo")


def friendly_error(exc: Exception) -> str:
    text = str(exc).strip() or exc.__class__.__name__
    low = text.lower()
    if "temporary suspension" in low or "sec_left" in low:
        secs = "".join(c for c in text if c.isdigit())
        mins = f"~{round(int(secs) / 60)} min" if secs else "a while"
        return (
            f"The camera temporarily blocked logins after several failed attempts. "
            f"Try again in {mins} (it clears on its own, or reboot the camera)."
        )
    if "storage_not_exist" in low or "-71114" in low or "-71103" in low:
        return "No recordings found — the camera has no (working) microSD card, or storage is unavailable."
    if "authentication" in low or ("invalid" in low and ("stok" in low or "credential" in low or "password" in low)):
        return (
            "Login failed — check the TP-Link account password (the password for your "
            "Tapo / TP-Link app login). The local username is always 'admin'."
        )
    if "timed out" in low or "timeout" in low:
        return "Connection timed out — is the camera reachable on the network?"
    if "connection" in low and ("refused" in low or "reset" in low):
        return "Connection refused — check the host/IP and that the camera is on."
    return text


def _dig(d: Any, *keys: str) -> Any:
    cur = d
    for k in keys:
        if isinstance(cur, dict) and k in cur:
            cur = cur[k]
        else:
            return None
    return cur


def _parse_basic_info(raw: Any) -> dict[str, Any]:
    info = _dig(raw, "device_info", "basic_info")
    if not isinstance(info, dict):
        info = raw if isinstance(raw, dict) else {}
    return {
        "model": info.get("device_model") or info.get("model"),
        "firmware": info.get("sw_version") or info.get("fw_ver"),
        "mac": info.get("mac"),
        "alias": info.get("device_alias"),
    }


def _size_to_mb(value: Any) -> int | None:
    """Parse Tapo's unit-suffixed size strings ('0B', '29.7GB', '512MB') to MB."""
    if value is None:
        return None
    s = str(value).strip().upper().replace("IB", "B")
    m = re.match(r"^\s*([0-9]*\.?[0-9]+)\s*([KMGT]?)B?\s*$", s)
    if not m:
        return None
    num = float(m.group(1))
    mult = {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}[m.group(2)]
    return int(num * mult / (1024 * 1024))


def _parse_sdcard(raw: Any) -> dict[str, Any]:
    """hd_info is a list of {hd_info_N: {...}} entries; use the first."""
    entry: dict[str, Any] = {}
    if isinstance(raw, list) and raw:
        first = raw[0]
        if isinstance(first, dict):
            inner = next((v for v in first.values() if isinstance(v, dict)), None)
            entry = inner if inner is not None else first
    elif isinstance(raw, dict):
        entry = raw

    status = entry.get("status") or entry.get("detect_status")
    total = _size_to_mb(entry.get("total_space_accurate") or entry.get("total_space"))
    free = _size_to_mb(entry.get("free_space_accurate") or entry.get("free_space"))
    offline = str(status).lower() in ("offline", "unplug", "unformatted", "abnormal")
    present = bool(total) and not offline
    return {
        "sd_present": present,
        "sd_total_mb": total,
        "sd_free_mb": free,
        "sd_status": status,
    }


async def test_connection(cam: dict[str, Any]) -> dict[str, Any]:
    """Probe a camera. Returns a TestConnectionResult-shaped dict."""
    try:
        client = await run_blocking(build_tapo, cam)
    except Exception as exc:  # noqa: BLE001
        log.info("test_connection failed to authenticate %s: %s", cam.get("host"), exc)
        return {"online": False, "error": friendly_error(exc)}

    result: dict[str, Any] = {"online": True}
    try:
        result.update(_parse_basic_info(await run_blocking(client.getBasicInfo)))
    except Exception as exc:  # noqa: BLE001
        log.debug("getBasicInfo failed: %s", exc)
    try:
        result.update(_parse_sdcard(await run_blocking(client.getSDCard)))
    except Exception as exc:  # noqa: BLE001
        log.debug("getSDCard failed: %s", exc)
    return result
