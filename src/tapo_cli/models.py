"""Pydantic request/response models (the API contract)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
# Cameras
# --------------------------------------------------------------------------- #
class CameraCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    host: str = Field(min_length=1)
    # The TP-Link account password. Username is always "admin" for the local API.
    account_password: str = Field(min_length=1)
    control_port: int | None = None
    enabled: bool = True


class TestConnectionRequest(BaseModel):
    """Credentials to probe a camera before it's saved."""

    host: str = Field(min_length=1)
    account_password: str = Field(min_length=1)
    control_port: int | None = None


class CameraUpdate(BaseModel):
    """All optional — only provided fields are changed."""

    name: str | None = Field(default=None, min_length=1, max_length=80)
    host: str | None = None
    account_password: str | None = None
    control_port: int | None = None
    enabled: bool | None = None


class CameraOut(BaseModel):
    """Camera as returned to the UI — never includes the password."""

    id: int
    name: str
    slug: str
    host: str
    control_port: int | None
    enabled: bool
    has_password: bool
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "CameraOut":
        return cls(
            id=row["id"],
            name=row["name"],
            slug=row["slug"],
            host=row["host"],
            control_port=row["control_port"],
            enabled=bool(row["enabled"]),
            has_password=bool(row.get("account_password")),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


class TestConnectionResult(BaseModel):
    online: bool
    error: str | None = None
    model: str | None = None
    firmware: str | None = None
    mac: str | None = None
    alias: str | None = None
    sd_present: bool = False
    sd_total_mb: int | None = None
    sd_free_mb: int | None = None
    sd_status: str | None = None


# --------------------------------------------------------------------------- #
# Recordings
# --------------------------------------------------------------------------- #
class RecordingSegment(BaseModel):
    start_time: int  # unix ts
    end_time: int  # unix ts
    duration_sec: int
    start_label: str  # HH:MM:SS in local time
    end_label: str


class RecordingDay(BaseModel):
    date: str  # YYYYMMDD


# --------------------------------------------------------------------------- #
# Direct playback (stream a recording without downloading it first)
# --------------------------------------------------------------------------- #
class PlaybackStartRequest(BaseModel):
    start_time: int  # unix ts
    end_time: int  # unix ts; bounds playback to this segment, not open-ended


class PlaybackStartResponse(BaseModel):
    session_id: str
    playlist_url: str


class PlaybackStatus(BaseModel):
    running: bool
    error: str | None


# --------------------------------------------------------------------------- #
# Downloads
# --------------------------------------------------------------------------- #
class DownloadCreate(BaseModel):
    camera_id: int
    date: str = Field(pattern=r"^\d{8}$")
    start_time: int
    end_time: int


class DownloadOut(BaseModel):
    id: int
    camera_id: int
    date: str
    start_time: int
    end_time: int
    start_label: str  # HH:MM:SS in local time, camera-clock-corrected
    end_label: str
    status: str
    current_action: str | None
    progress_sec: float
    total_sec: float
    progress_pct: int
    file_path: str | None
    error: str | None
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: dict[str, Any], time_correction: int = 0) -> "DownloadOut":
        total = row.get("total_sec") or 0
        prog = row.get("progress_sec") or 0
        pct = int(min(100, (prog / total) * 100)) if total else (100 if row["status"] == "done" else 0)
        return cls(
            id=row["id"],
            camera_id=row["camera_id"],
            date=row["date"],
            start_time=row["start_time"],
            end_time=row["end_time"],
            start_label=datetime.fromtimestamp(row["start_time"] + time_correction).strftime("%H:%M:%S"),
            end_label=datetime.fromtimestamp(row["end_time"] + time_correction).strftime("%H:%M:%S"),
            status=row["status"],
            current_action=row.get("current_action"),
            progress_sec=prog,
            total_sec=total,
            progress_pct=pct,
            file_path=row.get("file_path"),
            error=row.get("error"),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
