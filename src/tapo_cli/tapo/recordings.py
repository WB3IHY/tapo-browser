"""List recording days + segments stored on a camera's SD card.

Listing uses the local API (Camera Account creds); only the actual *download*
of a segment needs the TP-Link cloud password. Response shapes vary, so parsing
digs for the fields it needs rather than assuming a fixed structure.
"""

from __future__ import annotations

import re
from typing import Any

from .client import TapoClientCache, run_blocking

_DATE_RE = re.compile(r"^\d{8}$")


def normalize_date(value: str) -> str:
    """Accept 'YYYY-MM-DD' or 'YYYYMMDD' and return 'YYYYMMDD'."""
    return value.replace("-", "").strip()


def _collect_days(search_results: Any) -> set[str]:
    days: set[str] = set()
    for item in search_results or []:
        if not isinstance(item, dict):
            continue
        for key, val in item.items():
            if isinstance(val, dict) and _DATE_RE.match(str(val.get("date", ""))):
                days.add(val["date"])
            elif _DATE_RE.match(str(key)):
                days.add(str(key))
    return days


def _collect_segments(search_video_results: Any) -> list[dict[str, int]]:
    segs: list[dict[str, int]] = []
    for item in search_video_results or []:
        if not isinstance(item, dict):
            continue
        for val in item.values():
            if isinstance(val, dict) and "startTime" in val and "endTime" in val:
                try:
                    st = int(val["startTime"])
                    et = int(val["endTime"])
                except (TypeError, ValueError):
                    continue
                segs.append({"start_time": st, "end_time": et})
    segs.sort(key=lambda s: s["start_time"])
    return segs


async def list_days(cache: TapoClientCache, cam: dict[str, Any], start_date: str, end_date: str) -> list[str]:
    client = await cache.get(cam)
    raw = await run_blocking(client.getRecordingsList, normalize_date(start_date), normalize_date(end_date))
    return sorted(_collect_days(raw), reverse=True)


async def list_segments(cache: TapoClientCache, cam: dict[str, Any], date: str) -> list[dict[str, int]]:
    client = await cache.get(cam)
    raw = await run_blocking(client.getRecordings, normalize_date(date))
    return _collect_segments(raw)


async def time_correction(cache: TapoClientCache, cam: dict[str, Any]) -> int:
    client = await cache.get(cam)
    return int(await run_blocking(client.getTimeCorrection) or 0)
