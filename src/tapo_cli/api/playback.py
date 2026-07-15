"""Direct playback: start/stop a camera-side streaming session and serve its
growing HLS output (playlist + segments) to the browser.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from ..db.repo import CameraRepo
from ..models import PlaybackStartRequest, PlaybackStartResponse
from ..tapo.info import friendly_error

router = APIRouter(tags=["playback"])
_repo = CameraRepo()

_MEDIA_TYPES = {".m3u8": "application/vnd.apple.mpegurl", ".ts": "video/mp2t"}

# ffmpeg's cold-start (session opened, first HLS segment not flushed yet —
# see CLAUDE.md, up to ~20s) means the playlist file genuinely doesn't exist
# on disk for a while. Two tempting alternatives to "wait for it" both
# proved wrong in practice: a 404 is treated by hls.js as "this resource
# doesn't exist" and isn't retried regardless of retry-policy config, and a
# syntactically-valid-but-empty placeholder playlist is treated as an
# immediately fatal levelEmptyError on the very first load (hls.js can't
# determine codecs/compatibility without at least one real fragment).
# Instead we hold the request open and poll for the real file — turning
# "doesn't exist yet" into "slow to respond", which needs no special
# client-side handling at all.
_PLAYLIST_WAIT_SECONDS = 30.0
_PLAYLIST_POLL_INTERVAL_SECONDS = 0.25


@router.post("/cameras/{camera_id}/playback", response_model=PlaybackStartResponse, status_code=201)
async def start_playback(camera_id: int, payload: PlaybackStartRequest, request: Request) -> PlaybackStartResponse:
    cam = _repo.get(camera_id)
    if cam is None:
        raise HTTPException(404, "Camera not found")
    manager = request.app.state.playback
    try:
        session_id = await manager.start(cam, payload.start_time, payload.end_time)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, friendly_error(exc))
    session = manager.get(session_id)
    playlist_name = session.playlist_path.name if session is not None else "master.m3u8"
    return PlaybackStartResponse(session_id=session_id, playlist_url=f"/api/playback/{session_id}/{playlist_name}")


@router.delete("/playback/{session_id}", status_code=204)
async def stop_playback(session_id: str, request: Request) -> None:
    await request.app.state.playback.stop(session_id)


@router.get("/playback/{session_id}/{filename}")
async def playback_file(session_id: str, filename: str, request: Request) -> FileResponse:
    if "/" in filename or ".." in filename:
        raise HTTPException(400, "Invalid filename")
    session = request.app.state.playback.get(session_id)
    if session is None:
        raise HTTPException(404, "Playback session not found")
    suffix = "." + filename.rsplit(".", 1)[-1] if "." in filename else ""
    media_type = _MEDIA_TYPES.get(suffix)
    if media_type is None:
        raise HTTPException(404, "Not found")
    path = session.playlist_path.parent / filename

    # Three playlists now (master, video, audio - see playback.py's
    # docstring for why video/audio are separate HLS renditions), not just
    # one - all three can have the same "ffmpeg hasn't produced it yet"
    # cold-start race the master alone used to need this for. The master
    # itself always exists immediately (written synchronously, not
    # ffmpeg-produced), so this is a no-op wait for it in practice.
    if not path.exists() and suffix == ".m3u8":
        deadline = asyncio.get_event_loop().time() + _PLAYLIST_WAIT_SECONDS
        while not path.exists() and asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(_PLAYLIST_POLL_INTERVAL_SECONDS)
            if request.app.state.playback.get(session_id) is None:
                raise HTTPException(404, "Playback session stopped")

    if not path.exists():
        raise HTTPException(404, "Not found")

    headers = None
    if suffix == ".m3u8":
        # Playlists are rewritten in place every time a new segment
        # flushes — unlike .ts segments (immutable once named), they must
        # never be served from any cache along the way.
        headers = {"Cache-Control": "no-store"}
    return FileResponse(path, media_type=media_type, headers=headers)
