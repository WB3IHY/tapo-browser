"""WebRTC-based recorded-segment playback via TP-Link's cloud-mediated
signaling (services-sync/sfu/request) - the opt-in alternative to the local
HTTP PlaybackSession (tapo/playback.py), which stays fully local.

Architecturally heavier than the local HTTP path, and worth knowing before
assuming this is a strict upgrade: WebRTC delivers already-*decoded* video
frames at the application layer (aiortc's track API has no way to access
encoded NALs directly for a received track), so producing HLS output here
means a real transcode (libx264 re-encode via aiortc's MediaRecorder/PyAV),
not a cheap stream-copy remux like PlaybackSession does. Real, ongoing CPU
cost per session, not just a one-time difference.

Not yet live-tested against a real camera - see the project's WebRTC
investigation notes.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Callable

from aiortc import RTCPeerConnection
from aiortc.contrib.media import MediaRecorder

from . import webrtc_sdp
from .webrtc_signaling import RtcApiClient

HLS_SEGMENT_SECONDS = 2


class WebRTCPlaybackSession:
    def __init__(self, token: str, terminal_uuid: str, device_id: str, output_dir: Path) -> None:
        self._token = token
        self._terminal_uuid = terminal_uuid
        self._device_id = device_id
        output_dir.mkdir(parents=True, exist_ok=True)
        self.playlist_path = output_dir / "stream.m3u8"
        self.error: str | None = None

        self._pc: RTCPeerConnection | None = None
        self._recorder: MediaRecorder | None = None
        self._running = False
        self._done_callbacks: list[Callable[[], None]] = []

    @property
    def running(self) -> bool:
        return self._running

    def add_done_callback(self, callback: Callable[[], None]) -> None:
        self._done_callbacks.append(callback)

    def _fire_done(self) -> None:
        self._running = False
        for cb in self._done_callbacks:
            cb()

    async def start(self) -> None:
        pc = RTCPeerConnection()
        self._pc = pc
        recorder = MediaRecorder(
            str(self.playlist_path),
            format="hls",
            options={
                "hls_time": str(HLS_SEGMENT_SECONDS),
                "hls_list_size": "0",
                "hls_flags": "append_list+program_date_time",
            },
        )
        self._recorder = recorder

        @pc.on("track")
        def on_track(track: object) -> None:
            if getattr(track, "kind", None) == "video":
                recorder.addTrack(track)  # type: ignore[arg-type]

        @pc.on("connectionstatechange")
        async def on_connectionstatechange() -> None:
            if pc.connectionState in ("failed", "closed") and self._running:
                self.error = self.error or f"WebRTC connection {pc.connectionState}"
                self._fire_done()

        offer_detail = await webrtc_sdp.build_offer(pc)

        client = RtcApiClient(self._token, self._terminal_uuid)
        session_id = uuid.uuid4().hex
        answer = await client.request_sfu(
            self._device_id, offer_detail, session_id, player_id=self._terminal_uuid
        )
        if answer.sdp_detail is None:
            raise RuntimeError(
                f"sfu/request returned no SDP answer (errorCode={answer.error_code}, message={answer.message})"
            )

        await webrtc_sdp.apply_answer(pc, answer.sdp_detail)
        await recorder.start()
        self._running = True

    async def stop(self) -> None:
        self._running = False
        if self._recorder is not None:
            await self._recorder.stop()
            self._recorder = None
        if self._pc is not None:
            await self._pc.close()
            self._pc = None
