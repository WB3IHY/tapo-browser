"""Download jobs: create, track, cancel, stream progress (SSE), serve the file."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse

from .. import paths
from ..db.repo import CameraRepo, DownloadRepo
from ..models import DownloadCreate, DownloadOut

router = APIRouter(tags=["downloads"])
_cams = CameraRepo()
_repo = DownloadRepo()


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


@router.post("/downloads", response_model=DownloadOut, status_code=201)
async def create_download(payload: DownloadCreate, request: Request) -> DownloadOut:
    cam = _cams.get(payload.camera_id)
    if cam is None:
        raise HTTPException(404, "Camera not found")
    if payload.end_time <= payload.start_time:
        raise HTTPException(400, "end_time must be after start_time")
    job = _repo.create(payload.camera_id, payload.date, payload.start_time, payload.end_time)
    await request.app.state.downloads.start(cam, job)
    return DownloadOut.from_row(_repo.get(job["id"]))


@router.get("/downloads", response_model=list[DownloadOut])
async def list_downloads(camera_id: int | None = None) -> list[DownloadOut]:
    return [DownloadOut.from_row(r) for r in _repo.list(camera_id)]


@router.get("/downloads/{download_id}", response_model=DownloadOut)
async def get_download(download_id: int) -> DownloadOut:
    row = _repo.get(download_id)
    if row is None:
        raise HTTPException(404, "Download not found")
    return DownloadOut.from_row(row)


@router.post("/downloads/{download_id}/cancel", response_model=DownloadOut)
async def cancel_download(download_id: int, request: Request) -> DownloadOut:
    row = _repo.get(download_id)
    if row is None:
        raise HTTPException(404, "Download not found")
    await request.app.state.downloads.cancel(download_id)
    return DownloadOut.from_row(_repo.get(download_id))


@router.get("/downloads/{download_id}/events")
async def download_events(download_id: int, request: Request) -> StreamingResponse:
    if _repo.get(download_id) is None:
        raise HTTPException(404, "Download not found")
    mgr = request.app.state.downloads

    async def gen():
        # 1) current state from the DB so a late subscriber catches up
        yield _sse(DownloadOut.from_row(_repo.get(download_id)).model_dump())
        row = _repo.get(download_id)
        if not mgr.is_active(download_id) and row["status"] in ("done", "error", "canceled"):
            yield _sse({"_done": True})
            return
        # 2) live updates
        q = mgr.subscribe(download_id)
        while True:
            if await request.is_disconnected():
                break
            try:
                item = await asyncio.wait_for(q.get(), timeout=15)
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
                continue
            if item.get("_done"):
                yield _sse({"_done": True})
                break
            yield _sse(item)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/downloads/{download_id}/file")
async def download_file(download_id: int) -> FileResponse:
    row = _repo.get(download_id)
    if row is None:
        raise HTTPException(404, "Download not found")
    if row["status"] != "done" or not row["file_path"]:
        raise HTTPException(409, "Download is not finished")
    path = Path(row["file_path"])
    if not path.is_absolute():
        path = paths.PROJECT_ROOT / path
    if not path.exists():
        raise HTTPException(404, "File missing on disk")
    return FileResponse(path, filename=path.name, media_type="video/mp4")
