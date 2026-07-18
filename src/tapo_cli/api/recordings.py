"""Browse recording days + segments stored on a camera's SD card."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

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
        tc = await recordings.time_correction(cache, cam)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, friendly_error(exc))

    # start_time/end_time are left as the camera's own raw clock values,
    # since that's what the camera expects back for playback/thumbnail/
    # download requests for this segment. Only the human-readable labels
    # are corrected for display - see getTimeCorrection() in CLAUDE.md.
    out: list[RecordingSegment] = []
    for s in segs:
        st, et = s["start_time"], s["end_time"]
        out.append(
            RecordingSegment(
                start_time=st,
                end_time=et,
                duration_sec=max(0, et - st),
                start_label=datetime.fromtimestamp(st + tc).strftime("%H:%M:%S"),
                end_label=datetime.fromtimestamp(et + tc).strftime("%H:%M:%S"),
            )
        )
    return out


@router.get("/cameras/{camera_id}/recordings/segments/{start_time}/thumbnail")
async def segment_thumbnail(camera_id: int, start_time: int, request: Request) -> FileResponse:
    cam = _cam_or_404(camera_id)
    cache = request.app.state.thumbnails
    try:
        path = await cache.get(cam, start_time)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, friendly_error(exc))
    return FileResponse(path, media_type="image/jpeg")
