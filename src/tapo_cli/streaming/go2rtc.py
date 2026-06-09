"""Manage the go2rtc subprocess that bridges camera RTSP → browser-playable streams.

go2rtc reads a YAML config listing one named stream per enabled camera. We point
its ``ffmpeg.bin`` at our bundled binary so it can transcode the cameras' audio
(often G.711/PCMA) into AAC/Opus that browsers can play; video (H.264) passes
through untouched.

The config is regenerated and the process restarted whenever cameras change. A
restart is sub-second and, for a personal single-user app, an acceptable blip.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import subprocess
from pathlib import Path
from typing import Any

import httpx
import yaml

from .. import paths
from ..db.repo import CameraRepo
from .source import tapo_source_url

log = logging.getLogger("tapo_cli.go2rtc")


def find_free_port(preferred: int, attempts: int = 20) -> int:
    """Return ``preferred`` if bindable on 127.0.0.1, else the next free port."""
    for candidate in range(preferred, preferred + attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", candidate))
                return candidate
            except OSError:
                continue
    # Fall back to an OS-assigned port.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Go2rtcProcess:
    def __init__(self, preferred_port: int = 1984):
        self._preferred_port = preferred_port
        self.port: int = preferred_port
        # go2rtc's internal RTSP module (needed by the ffmpeg audio-transcode
        # source) — bound to localhost only.
        self.rtsp_port: int = 8554
        self._proc: subprocess.Popen[bytes] | None = None
        self._repo = CameraRepo()
        self._binary = paths.go2rtc_path()
        self._ffmpeg = paths.ffmpeg_path()
        self._config_path = paths.GO2RTC_CONFIG_PATH
        self._lock = asyncio.Lock()

    # ----- config ------------------------------------------------------- #
    def _build_config(self) -> dict[str, Any]:
        streams: dict[str, Any] = {}
        for cam in self._repo.list_enabled():
            slug = cam["slug"]
            # 1st source: go2rtc's native tapo:// (video passthrough). 2nd: ffmpeg
            # audio transcode so MSE (AAC) and WebRTC (Opus) clients get playable audio.
            streams[slug] = [
                tapo_source_url(cam),
                f"ffmpeg:{slug}#audio=aac#audio=opus",
            ]
        return {
            "api": {"listen": f"127.0.0.1:{self.port}"},
            # Bind the RTSP module to localhost. It must stay enabled (non-empty)
            # because the ffmpeg audio-transcode source pulls streams through it.
            "rtsp": {"listen": f"127.0.0.1:{self.rtsp_port}"},
            "webrtc": {"candidates": ["127.0.0.1:8555"]},
            "ffmpeg": {"bin": str(self._ffmpeg)},
            "log": {"format": "text", "level": "info"},
            "streams": streams,
        }

    def _write_config(self) -> None:
        cfg = self._build_config()
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        with self._config_path.open("w", encoding="utf-8") as fh:
            yaml.safe_dump(cfg, fh, default_flow_style=False, sort_keys=False, allow_unicode=True)

    # ----- lifecycle ---------------------------------------------------- #
    async def start(self) -> None:
        async with self._lock:
            self.port = find_free_port(self._preferred_port)
            self.rtsp_port = find_free_port(8554)
            self._write_config()
            await self._spawn()

    async def _spawn(self) -> None:
        log_fh = paths.GO2RTC_LOG_PATH.open("ab")
        self._proc = subprocess.Popen(
            [str(self._binary), "-config", str(self._config_path)],
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            cwd=str(paths.DATA_DIR),
        )
        log.info("go2rtc started (pid=%s) on 127.0.0.1:%s", self._proc.pid, self.port)
        await self._wait_ready()

    async def _wait_ready(self, timeout: float = 15.0) -> None:
        deadline = asyncio.get_event_loop().time() + timeout
        url = f"http://127.0.0.1:{self.port}/api/streams"
        async with httpx.AsyncClient(timeout=2.0) as client:
            while asyncio.get_event_loop().time() < deadline:
                if self._proc is not None and self._proc.poll() is not None:
                    raise RuntimeError(
                        f"go2rtc exited early (code {self._proc.returncode}); "
                        f"see {paths.GO2RTC_LOG_PATH}"
                    )
                try:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        log.info("go2rtc is ready")
                        return
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.25)
        log.warning("go2rtc did not report ready within %.0fs (continuing anyway)", timeout)

    async def reload(self) -> None:
        """Regenerate config from the current cameras and restart the process."""
        async with self._lock:
            await self._terminate()
            self._write_config()
            await self._spawn()

    async def _terminate(self) -> None:
        if self._proc is None:
            return
        proc = self._proc
        self._proc = None
        if proc.poll() is None:
            proc.terminate()
            try:
                await asyncio.to_thread(proc.wait, 5)
            except subprocess.TimeoutExpired:
                proc.kill()
                await asyncio.to_thread(proc.wait)
        log.info("go2rtc stopped")

    async def stop(self) -> None:
        async with self._lock:
            await self._terminate()

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    @property
    def api_base(self) -> str:
        return f"http://127.0.0.1:{self.port}"
