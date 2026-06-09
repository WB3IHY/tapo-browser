"""Cameras CRUD + connection testing.

Any mutation regenerates the go2rtc config (so live streams stay in sync) and
invalidates the cached pytapo client for that camera.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request, Response

from ..db.repo import CameraRepo
from ..models import (
    CameraCreate,
    CameraOut,
    CameraUpdate,
    TestConnectionRequest,
    TestConnectionResult,
)
from ..tapo.info import test_connection

log = logging.getLogger("tapo_cli.api.cameras")
router = APIRouter(tags=["cameras"])
_repo = CameraRepo()


async def _reload_go2rtc(request: Request) -> None:
    go2rtc = getattr(request.app.state, "go2rtc", None)
    if go2rtc is not None:
        try:
            await go2rtc.reload()
        except Exception:  # noqa: BLE001
            log.exception("Failed to reload go2rtc after camera change")


def _invalidate(request: Request, camera_id: int) -> None:
    clients = getattr(request.app.state, "clients", None)
    if clients is not None:
        clients.invalidate(camera_id)


@router.get("/cameras", response_model=list[CameraOut])
async def list_cameras() -> list[CameraOut]:
    return [CameraOut.from_row(r) for r in _repo.list()]


@router.get("/cameras/{camera_id}", response_model=CameraOut)
async def get_camera(camera_id: int) -> CameraOut:
    row = _repo.get(camera_id)
    if row is None:
        raise HTTPException(404, "Camera not found")
    return CameraOut.from_row(row)


@router.post("/cameras", response_model=CameraOut, status_code=201)
async def create_camera(payload: CameraCreate, request: Request) -> CameraOut:
    row = _repo.create(payload.model_dump())
    await _reload_go2rtc(request)
    return CameraOut.from_row(row)


@router.put("/cameras/{camera_id}", response_model=CameraOut)
async def update_camera(camera_id: int, payload: CameraUpdate, request: Request) -> CameraOut:
    row = _repo.update(camera_id, payload.model_dump(exclude_unset=True))
    if row is None:
        raise HTTPException(404, "Camera not found")
    _invalidate(request, camera_id)
    await _reload_go2rtc(request)
    return CameraOut.from_row(row)


@router.delete("/cameras/{camera_id}", status_code=204)
async def delete_camera(camera_id: int, request: Request) -> Response:
    if not _repo.delete(camera_id):
        raise HTTPException(404, "Camera not found")
    _invalidate(request, camera_id)
    await _reload_go2rtc(request)
    return Response(status_code=204)


@router.post("/cameras/test-connection", response_model=TestConnectionResult)
async def test_new_camera(payload: TestConnectionRequest) -> TestConnectionResult:
    result = await test_connection(payload.model_dump())
    return TestConnectionResult(**result)


@router.post("/cameras/{camera_id}/test-connection", response_model=TestConnectionResult)
async def test_existing_camera(camera_id: int) -> TestConnectionResult:
    row = _repo.get(camera_id)
    if row is None:
        raise HTTPException(404, "Camera not found")
    result = await test_connection(row)
    return TestConnectionResult(**result)
