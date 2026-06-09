"""Live-stream info endpoint + a same-origin reverse proxy to go2rtc.

The browser never talks to go2rtc directly. Everything goes through
``/api/go2rtc/*`` so it shares the app's origin (no CORS issues, go2rtc's port
stays internal and may change). The ``<video-stream>`` web component uses a
WebSocket (``/api/go2rtc/api/ws``) for WebRTC signaling and MSE media, and
derives its HLS endpoint from the same base.
"""

from __future__ import annotations

import asyncio
import logging

import httpx
import websockets
from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response, StreamingResponse

from ..db.repo import CameraRepo
from ..models import StreamInfo
from ..streaming.go2rtc import Go2rtcProcess

log = logging.getLogger("tapo_cli.api.stream")
router = APIRouter(tags=["stream"])
_repo = CameraRepo()

_GO2RTC_PREFIX = "/api/go2rtc"
_HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "host", "content-length",
}


def _go2rtc(request_or_ws) -> Go2rtcProcess:
    return request_or_ws.app.state.go2rtc


@router.get("/cameras/{camera_id}/stream/info", response_model=StreamInfo)
async def stream_info(camera_id: int) -> StreamInfo:
    cam = _repo.get(camera_id)
    if cam is None:
        raise HTTPException(404, "Camera not found")
    slug = cam["slug"]
    base = f"{_GO2RTC_PREFIX}/api"
    return StreamInfo(
        name=slug,
        ws_src=f"{base}/ws?src={slug}",
        snapshot_url=f"{base}/frame.jpeg?src={slug}",
    )


# --------------------------------------------------------------------------- #
# HTTP reverse proxy: /api/go2rtc/{path} -> go2rtc /{path}
# --------------------------------------------------------------------------- #
@router.api_route("/go2rtc/{path:path}", methods=["GET", "HEAD"])
async def proxy_http(path: str, request: Request) -> Response:
    go2rtc = _go2rtc(request)
    url = f"{go2rtc.api_base}/{path}"
    client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=None))
    upstream = await client.send(
        client.build_request(request.method, url, params=request.query_params),
        stream=True,
    )
    headers = {
        k: v for k, v in upstream.headers.items() if k.lower() not in _HOP_BY_HOP
    }

    async def body_iter():
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(
        body_iter(),
        status_code=upstream.status_code,
        headers=headers,
        media_type=upstream.headers.get("content-type"),
    )


# --------------------------------------------------------------------------- #
# WebSocket reverse proxy: /api/go2rtc/api/ws -> go2rtc /api/ws
# --------------------------------------------------------------------------- #
@router.websocket("/go2rtc/api/ws")
async def proxy_ws(client_ws: WebSocket) -> None:
    go2rtc = _go2rtc(client_ws)
    query = client_ws.scope.get("query_string", b"").decode()
    upstream_url = f"ws://127.0.0.1:{go2rtc.port}/api/ws" + (f"?{query}" if query else "")

    await client_ws.accept()
    try:
        upstream = await websockets.connect(upstream_url, max_size=None)
    except Exception as exc:  # noqa: BLE001
        log.warning("go2rtc ws connect failed: %s", exc)
        await client_ws.close()
        return

    async def client_to_upstream() -> None:
        try:
            while True:
                msg = await client_ws.receive()
                if msg["type"] == "websocket.disconnect":
                    break
                if msg.get("text") is not None:
                    await upstream.send(msg["text"])
                elif msg.get("bytes") is not None:
                    await upstream.send(msg["bytes"])
        except WebSocketDisconnect:
            pass

    async def upstream_to_client() -> None:
        try:
            async for message in upstream:
                if isinstance(message, (bytes, bytearray)):
                    await client_ws.send_bytes(bytes(message))
                else:
                    await client_ws.send_text(message)
        except Exception:  # noqa: BLE001 — upstream closed
            pass

    t1 = asyncio.create_task(client_to_upstream())
    t2 = asyncio.create_task(upstream_to_client())
    done, pending = await asyncio.wait({t1, t2}, return_when=asyncio.FIRST_COMPLETED)
    for t in pending:
        t.cancel()
    await upstream.close()
    try:
        await client_ws.close()
    except RuntimeError:
        pass
