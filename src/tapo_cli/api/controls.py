"""Camera controls API (night vision, privacy, LED, motion, flip, alarm)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..db.repo import CameraRepo
from ..tapo import controls as controls_mod
from ..tapo.client import run_blocking
from ..tapo.info import friendly_error

router = APIRouter(tags=["controls"])
_repo = CameraRepo()


class ControlSet(BaseModel):
    value: Any  # bool for toggles, str for selects


def _cam_or_404(camera_id: int) -> dict:
    cam = _repo.get(camera_id)
    if cam is None:
        raise HTTPException(404, "Camera not found")
    return cam


@router.get("/cameras/{camera_id}/controls")
async def get_controls(camera_id: int, request: Request) -> list[dict[str, Any]]:
    cam = _cam_or_404(camera_id)
    tapo = await request.app.state.clients.get(cam)
    try:
        return await run_blocking(controls_mod.probe_controls, tapo, camera_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, friendly_error(exc))


@router.post("/cameras/{camera_id}/controls/{key}")
async def set_control(camera_id: int, key: str, body: ControlSet, request: Request) -> dict[str, Any]:
    cam = _cam_or_404(camera_id)
    tapo = await request.app.state.clients.get(cam)
    try:
        new_value = await run_blocking(controls_mod.apply_control, tapo, key, body.value)
    except KeyError:
        raise HTTPException(404, "Unknown control")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, friendly_error(exc))
    return {"key": key, "value": new_value}
