"""Manage active PlaybackSession objects.

Only one player is ever shown in the UI at a time, so starting a new session
for a camera stops whatever was already running for it first — otherwise a
user scrubbing between segments without explicitly closing the player would
leak sessions against the camera's own (unknown, likely limited) concurrent
session capacity.
"""

from __future__ import annotations

import logging
import secrets
import shutil
from typing import Any

log = logging.getLogger("tapo_cli.playback_manager")

from .. import paths
from .client import TapoClientCache
from .media_session_limiter import MediaSessionLimiter
from .playback import PlaybackSession

# No padding: the camera's stream carries continuous, monotonically
# increasing timestamps straight across the gap into whatever recording
# comes next (see CLAUDE.md) — ffmpeg's -t duration is measured against
# those same timestamps, not wall-clock or segment metadata. Confirmed
# against a real camera: even a small +2s padding here made playback bleed
# a few seconds into the next segment in the list, because ffmpeg has no way
# to know a real-world boundary was crossed — it just keeps counting
# whatever timestamps keep arriving. Bounding exactly to the segment's own
# duration is correct; any positive padding is not.
_DURATION_PADDING_SECONDS = 0


class PlaybackManager:
    def __init__(self, clients: TapoClientCache, media_limiter: MediaSessionLimiter) -> None:
        self._clients = clients
        # Shared with ThumbnailCache — see its docstring. Acquired at high
        # priority (jumps ahead of any queued thumbnail work) and held for
        # the whole session lifetime (acquired in start(), released in
        # stop()), not just around start(), since the session keeps a media
        # connection open the entire time it's playing.
        self._limiter = media_limiter
        self._sessions: dict[str, PlaybackSession] = {}
        self._dirs: dict[str, Any] = {}
        self._by_camera: dict[int, str] = {}
        # session_ids whose camera connection + limiter slot have already
        # been released — guards against double-releasing the limiter when
        # both the natural-completion path and an explicit stop() run.
        self._camera_released: set[str] = set()

    async def start(self, cam: dict[str, Any], start_time: int, end_time: int) -> str:
        await self.stop_for_camera(cam["id"])

        await self._limiter.acquire(high_priority=True)
        try:
            tapo = await self._clients.get(cam)
            session_id = secrets.token_hex(8)
            out_dir = paths.PLAYBACK_DIR / cam["slug"] / session_id
            duration = max(0, end_time - start_time) + _DURATION_PADDING_SECONDS
            log.info(
                "Starting playback session %s: start_time=%s end_time=%s duration_seconds=%s tapo_id=%s",
                session_id, start_time, end_time, duration, id(tapo),
            )
            session = PlaybackSession(tapo, start_time, out_dir, duration_seconds=duration)
            await session.start()
        except Exception:
            self._limiter.release()
            raise

        self._sessions[session_id] = session
        self._dirs[session_id] = out_dir
        self._by_camera[cam["id"]] = session_id
        # The camera connection can finish on its own (a bounded clip played
        # through, or an error) without the client ever calling stop() —
        # release the limiter slot immediately when that happens, so the
        # next thumbnail or playback isn't blocked on a session nobody is
        # watching anymore. Deliberately do NOT delete the HLS files or drop
        # this session's bookkeeping here: the browser may still be fetching
        # the tail end of the clip via GET /api/playback/{id}/{filename},
        # which needs get(session_id) to keep resolving — a real, observed
        # bug when this used to call the full stop() (and its rmtree)
        # immediately, racing the client and producing hls.js levelLoadError
        # mid-playback. Full cleanup (files + bookkeeping) still happens via
        # the normal stop() path: closing the player, or starting a
        # different segment.
        session.add_done_callback(lambda: self._release_camera_slot(session_id))
        return session_id

    def _release_camera_slot(self, session_id: str) -> None:
        if session_id in self._camera_released:
            return
        self._camera_released.add(session_id)
        self._limiter.release()

    def get(self, session_id: str) -> PlaybackSession | None:
        return self._sessions.get(session_id)

    async def stop(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        out_dir = self._dirs.pop(session_id, None)
        for cam_id, sid in list(self._by_camera.items()):
            if sid == session_id:
                del self._by_camera[cam_id]
        if session is None:
            return
        await session.stop()
        self._release_camera_slot(session_id)  # no-op if already released naturally
        self._camera_released.discard(session_id)
        if out_dir is not None:
            shutil.rmtree(out_dir, ignore_errors=True)

    async def stop_for_camera(self, camera_id: int) -> None:
        session_id = self._by_camera.get(camera_id)
        if session_id:
            await self.stop(session_id)

    async def stop_all(self) -> None:
        for session_id in list(self._sessions.keys()):
            await self.stop(session_id)
