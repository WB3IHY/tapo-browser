"""Browse recording days + segments stored on a camera's SD card."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Request

from ..db.repo import CameraRepo
from ..models import RecordingDay, RecordingSegment
from ..tapo import recordings
from ..tapo.info import friendly_error

router = APIRouter(tags=["recordings"])
_repo = CameraRepo()


def _cam_or_404(camera_id: int) -> dict:
    cam = _repo.get(camera_id)
    if cam is None:
        raise HTTPException(404, "Camera not found")
    return cam


@router.get("/cameras/{camera_id}/recordings/days", response_model=list[RecordingDay])
async def list_days(camera_id: int, request: Request, start: str | None = None, end: str | None = None):
    cam = _cam_or_404(camera_id)
    cache = request.app.state.clients
    start = start or "20000101"
    end = end or datetime.now().strftime("%Y%m%d")
    try:
        days = await recordings.list_days(cache, cam, start, end)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, friendly_error(exc))
    return [RecordingDay(date=d) for d in days]


@router.get("/cameras/{camera_id}/recordings/segments", response_model=list[RecordingSegment])
async def list_segments(camera_id: int, date: str, request: Request):
    cam = _cam_or_404(camera_id)
    cache = request.app.state.clients
    try:
        segs = await recordings.list_segments(cache, cam, date)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, friendly_error(exc))

    out: list[RecordingSegment] = []
    for s in segs:
        st, et = s["start_time"], s["end_time"]
        out.append(
            RecordingSegment(
                start_time=st,
                end_time=et,
                duration_sec=max(0, et - st),
                start_label=datetime.fromtimestamp(st).strftime("%H:%M:%S"),
                end_label=datetime.fromtimestamp(et).strftime("%H:%M:%S"),
            )
        )
    return out
