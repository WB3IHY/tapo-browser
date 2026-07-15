"""Disk-cached, concurrency-limited thumbnail generation.

Wraps ``thumbnails.generate_thumbnail()`` — a real ~15-20s camera round trip,
see CLAUDE.md's notes on the cold-start cost of opening a playback session —
with an on-disk JPEG cache keyed by (camera slug, start_time) and a semaphore
capping how many generations run at once. Safe to call once per segment when
a recordings list renders (the UI does exactly that, eagerly, in the
background) without hammering the camera or piling up concurrent sessions.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from .. import paths
from .client import TapoClientCache
from .media_session_limiter import MediaSessionLimiter
from .thumbnails import generate_thumbnail


class ThumbnailCache:
    def __init__(self, clients: TapoClientCache, media_limiter: MediaSessionLimiter) -> None:
        self._clients = clients
        # Shared with PlaybackManager: a camera media session (thumbnail
        # generation and direct playback both open one) is a scarce resource —
        # opening a thumbnail session while a playback session is active was
        # observed to make one of the two fail outright (502). Thumbnails
        # always acquire at normal priority so a batch of them queued in the
        # background can never make an actively-waited-on playback start wait
        # behind them.
        self._limiter = media_limiter
        self._locks: dict[tuple[int, int], asyncio.Lock] = {}

    def _path(self, cam: dict[str, Any], start_time: int) -> Path:
        d = paths.THUMBNAILS_DIR / cam["slug"]
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{start_time}.jpg"

    async def get(self, cam: dict[str, Any], start_time: int) -> Path:
        """Return a cached thumbnail's path, generating it first if needed."""
        path = self._path(cam, start_time)
        if path.exists():
            return path

        key = (cam["id"], start_time)
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            # Another caller may have generated it while we were waiting.
            if path.exists():
                return path
            async with self._limiter.normal():
                tapo = await self._clients.get(cam)
                jpeg = await generate_thumbnail(tapo, start_time)
            tmp = path.with_suffix(".jpg.tmp")
            tmp.write_bytes(jpeg)
            tmp.replace(path)
            self._locks.pop(key, None)
        return path
