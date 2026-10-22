"""List recording days + segments stored on a camera's SD card.

Listing uses the local API (Camera Account creds); only the actual *download*
of a segment needs the TP-Link cloud password. Response shapes vary, so parsing
digs for the fields it needs rather than assuming a fixed structure.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
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
    segs.sort(key=lambda s: s["start_time"], reverse=True)
    return segs


async def list_days(cache: TapoClientCache, cam: dict[str, Any], start_date: str, end_date: str) -> list[str]:
    client = await cache.get(cam)
    raw = await run_blocking(client.getRecordingsList, normalize_date(start_date), normalize_date(end_date))
    return sorted(_collect_days(raw), reverse=True)


async def list_segments(cache: TapoClientCache, cam: dict[str, Any], date: str) -> list[dict[str, int]]:
    client = await cache.get(cam)
    raw = await run_blocking(client.getRecordings, normalize_date(date))
    return _collect_segments(raw)


# alarm_type values seen from searchDetectionList (inferred from the Tapo app's colours).
EVENT_KINDS = {2: "motion", 6: "person"}
_EVENT_PAGE = 999


def _fetch_events(client: Any, start: int, end: int) -> list[dict[str, Any]]:
    """Raw-clock detection events in [start, end), paged in case a day exceeds one page."""
    events: list[dict[str, Any]] = []
    index = 0
    while True:
        resp = client.executeFunction(
            "searchDetectionList",
            {
                "playback": {
                    "search_detection_list": {
                        "start_index": index,
                        "channel": 0,
                        "start_time": start,
                        "end_time": end,
                        "end_index": index + _EVENT_PAGE,
                    }
                }
            },
        )
        page = (resp or {}).get("playback", {}).get("search_detection_list") or []
        for ev in page:
            try:
                events.append(
                    {
                        "start_time": int(ev["start_time"]),
                        "end_time": int(ev["end_time"]),
                        "alarm_type": int(ev.get("alarm_type", 0)),
                    }
                )
            except (KeyError, TypeError, ValueError):
                continue
        if len(page) <= _EVENT_PAGE:
            return events
        index += len(page)


def day_start(date: str) -> int:
    """Raw camera-clock timestamp of midnight starting `date` (the day getRecordings lists)."""
    return int(datetime.strptime(normalize_date(date), "%Y%m%d").timestamp())


async def list_events(cache: TapoClientCache, cam: dict[str, Any], date: str) -> list[dict[str, Any]]:
    """Detection events for one day, in the camera's raw clock, oldest first.

    The day is taken in the camera's own clock (same as getRecordings' date), so
    no time correction is applied here; the caller corrects for display only.
    """
    client = await cache.get(cam)
    start = day_start(date)
    end = int((datetime.fromtimestamp(start) + timedelta(days=1)).timestamp())
    events = await run_blocking(_fetch_events, client, start, end)
    events.sort(key=lambda e: e["start_time"])
    return events


async def time_correction(cache: TapoClientCache, cam: dict[str, Any]) -> int:
    client = await cache.get(cam)
    return int(await run_blocking(client.getTimeCorrection) or 0)
