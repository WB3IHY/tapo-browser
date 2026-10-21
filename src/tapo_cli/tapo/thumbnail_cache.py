"""Disk-cached, concurrency-limited thumbnails for recording segments.

Two sources, behind one on-disk JPEG cache keyed by (camera slug, start_time):

- **The camera's own stored thumbnails** (``pytapo.media_stream.snapshot``) —
  the picture the official app shows, fetched in bulk: ``prefetch()`` pulls a
  whole day in a few chunked media sessions at ~0.05s each, instead of one
  session per segment. Only detection-triggered recordings have one.
- **A decoded frame** (``thumbnails.generate_thumbnail()``) — a real ~15-20s
  camera round trip per segment, kept as the fallback for segments the camera
  has no stored thumbnail for, and capped per camera so a camera that has none
  at all can't trigger a flood of them (see CLAUDE.md on the media-session
  churn incidents).
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from pytapo.media_stream.snapshot import getRecordingSnapshots

from .. import paths
from .client import TapoClientCache
from .media_session_limiter import MediaSessionLimiter
from .thumbnails import generate_thumbnail

log = logging.getLogger("tapo_cli.thumbnail_cache")

# Snapshots per media session. 376 in one session was tested clean against a
# real camera; chunking keeps the shared limiter free for playback in between.
_PREFETCH_CHUNK = 100
_SNAPSHOT_TIMEOUT_SECONDS = 8
# A start_time the camera has no stored image for (e.g. a timed recording)
# makes it stay silent until the timeout — stop a chunk after a few of those.
_PREFETCH_MAX_CONSECUTIVE_MISSES = 3
# Longest a /thumbnail request waits for a prefetch that has it queued.
_PREFETCH_WAIT_SECONDS = 90.0
# Decoded-frame fallbacks allowed per camera between prefetches.
_FALLBACK_BUDGET = 25


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
        # camera id -> start_times a running prefetch has yet to resolve, so a
        # /thumbnail request for one waits for it instead of opening its own
        # session. A set per camera, plus a lock so prefetches don't overlap.
        self._pending: dict[int, set[int]] = {}
        self._prefetch_locks: dict[int, asyncio.Lock] = {}
        self._fallbacks_left: dict[int, int] = {}

    def _path(self, cam: dict[str, Any], start_time: int) -> Path:
        d = paths.THUMBNAILS_DIR / cam["slug"]
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{start_time}.jpg"

    def start_prefetch(self, cam: dict[str, Any], start_times: list[int]) -> None:
        """Fill the cache for ``start_times`` in the background, newest first.

        Returns at once; segments already cached are skipped.
        """
        todo = [st for st in sorted(set(start_times), reverse=True) if not self._path(cam, st).exists()]
        self._fallbacks_left[cam["id"]] = _FALLBACK_BUDGET
        if not todo:
            return
        self._pending.setdefault(cam["id"], set()).update(todo)
        asyncio.create_task(self._prefetch(cam, todo))

    async def _prefetch(self, cam: dict[str, Any], todo: list[int]) -> None:
        cid = cam["id"]
        pending = self._pending[cid]
        lock = self._prefetch_locks.setdefault(cid, asyncio.Lock())
        try:
            async with lock:
                tapo = await self._clients.get(cam)
                # See thumbnails.generate_thumbnail(): getUserID() does blocking
                # I/O the first time, which must not happen inside the loop.
                await asyncio.get_event_loop().run_in_executor(None, tapo.getUserID)
                for i in range(0, len(todo), _PREFETCH_CHUNK):
                    chunk = [st for st in todo[i : i + _PREFETCH_CHUNK] if not self._path(cam, st).exists()]
                    if chunk and not await self._fetch_chunk(cam, tapo, chunk, pending):
                        break
        except Exception:  # noqa: BLE001
            log.exception("Thumbnail prefetch failed for camera %s", cid)
        finally:
            # Anything unresolved (misses, errors) falls back to the slow path.
            pending.difference_update(todo)

    async def _fetch_chunk(self, cam: dict[str, Any], tapo: Any, chunk: list[int], pending: set[int]) -> bool:
        """One media session for ``chunk``. Returns False to stop the prefetch."""
        misses = 0
        async with self._limiter.normal():
            results = getRecordingSnapshots(tapo, chunk, timeout=_SNAPSHOT_TIMEOUT_SECONDS)
            i = 0
            async for jpeg in results:
                st = chunk[i]
                i += 1
                if jpeg:
                    misses = 0
                    path = self._path(cam, st)
                    tmp = path.with_suffix(".jpg.tmp")
                    tmp.write_bytes(jpeg)
                    tmp.replace(path)
                else:
                    misses += 1
                pending.discard(st)
                if misses >= _PREFETCH_MAX_CONSECUTIVE_MISSES:
                    log.info("Camera %s has no stored thumbnails near %s; stopping prefetch", cam["id"], st)
                    await results.aclose()
                    return False
        return True

    async def get(self, cam: dict[str, Any], start_time: int) -> Path:
        """Return a cached thumbnail's path, generating it first if needed."""
        path = self._path(cam, start_time)
        if path.exists():
            return path

        # A prefetch has this one queued: wait for it rather than opening a
        # second media session for the same camera.
        pending = self._pending.get(cam["id"], set())
        waited = 0.0
        while start_time in pending and waited < _PREFETCH_WAIT_SECONDS:
            await asyncio.sleep(0.25)
            waited += 0.25
        if path.exists():
            return path

        left = self._fallbacks_left.get(cam["id"], _FALLBACK_BUDGET)
        if left <= 0:
            raise RuntimeError("No stored thumbnail for this recording")
        self._fallbacks_left[cam["id"]] = left - 1

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
