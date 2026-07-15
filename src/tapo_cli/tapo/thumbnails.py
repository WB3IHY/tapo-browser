"""Thumbnail generation for recording segments: a single decoded JPEG frame
grabbed directly from the camera, without downloading the segment first.

Uses the same "playback" media session as ``tapo/playback.py``, requested for
a short window around a segment's ``start_time``, with ffmpeg told to decode
and re-encode just the first frame (``-frames:v 1``) to JPEG. ffmpeg exits on
its own once that frame is written, so this is a short-lived, one-shot
operation — unlike ``PlaybackSession``, there's no long-running process to
manage.

Caching (whether a thumbnail for a given segment already exists on disk) is
deliberately not this module's concern — it returns JPEG bytes and leaves
persistence to the caller, the same separation ``tapo/downloader.py`` keeps
between the download mechanism and job bookkeeping.
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess

from pytapo import Tapo
from pytapo.media_stream._utils import StreamType

log = logging.getLogger("tapo_cli.thumbnails")

# A generous window, NOT "just enough for one frame": a narrow end_time (e.g.
# start_time + 3) makes the camera loop-replay that short window repeatedly
# instead of stopping, and every loop restart is a PTS discontinuity ffmpeg's
# decoder has to discard and resync from — confirmed against a real camera to
# push single-frame latency from well under 1s to ~18s. ffmpeg's -frames:v 1
# still exits after exactly one decoded frame regardless of window size, so a
# wide window costs nothing and avoids the loop entirely.
_END_TIME_HORIZON_SECONDS = 3600


async def generate_thumbnail(tapo: Tapo, start_time: int, timeout: float = 25.0, window_size: int = 500) -> bytes:
    """Grab a single JPEG frame near ``start_time`` from recorded footage.

    Returns JPEG bytes, or raises on failure (no footage at that time, camera
    unreachable, etc).
    """
    # Prime getUserID()'s cache off the event loop first — see playback.py's
    # docstring for why calling it for the first time from inside a running
    # loop crashes pytapo's internal handler.
    await asyncio.get_event_loop().run_in_executor(None, tapo.getUserID)

    ffmpeg = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-loglevel", "error",
        "-probesize", "32",
        "-analyzeduration", "0",
        "-f", "mpegts", "-i", "pipe:0",
        "-map", "0:v:0",
        "-frames:v", "1",
        # -c:v defaults to "copy" if unset, which just wraps the raw H.264
        # NAL data instead of producing an actual JPEG — this must be explicit.
        "-c:v", "mjpeg",
        "-f", "image2",
        "pipe:1",
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        # Short-lived, single-frame process — not worth piping stderr through
        # asyncio's line reader (see playback.py's docstring on that crash).
        stderr=subprocess.DEVNULL,
    )

    media_session = tapo.getMediaSession(StreamType.Stream)
    media_session.set_window_size(window_size)
    payload = json.dumps(
        {
            "type": "request",
            "params": {
                "playback": {
                    "client_id": tapo.getUserID(),
                    "channels": [0, 1],
                    "scale": "1/1",
                    "start_time": str(start_time),
                    "end_time": str(start_time + _END_TIME_HORIZON_SECONDS),
                    "event_type": [1, 2],
                },
                "method": "get",
            },
        }
    )

    async def feed() -> None:
        ts_buffer = bytearray()
        try:
            async with media_session:
                async for resp in media_session.transceive(payload, no_data_timeout=timeout):
                    if resp.mimetype != "video/mp2t":
                        continue

                    ts_buffer += resp.plaintext
                    while len(ts_buffer) >= 188 and ts_buffer[0] != 0x47:
                        pos = ts_buffer.find(0x47, 1)
                        if pos == -1:
                            ts_buffer.clear()
                            break
                        ts_buffer = ts_buffer[pos:]

                    while len(ts_buffer) >= 188:
                        packet = ts_buffer[:188]
                        ts_buffer = ts_buffer[188:]
                        if ffmpeg.stdin.is_closing():
                            return
                        ffmpeg.stdin.write(packet)

                    try:
                        await asyncio.wait_for(ffmpeg.stdin.drain(), timeout=15.0)
                    except (ConnectionResetError, BrokenPipeError, asyncio.TimeoutError):
                        return
        except Exception:  # noqa: BLE001
            log.exception("Thumbnail feed failed")
        finally:
            if not ffmpeg.stdin.is_closing():
                ffmpeg.stdin.close()

    feed_task = asyncio.create_task(feed())
    try:
        jpeg_bytes = await asyncio.wait_for(ffmpeg.stdout.read(), timeout=timeout)
    finally:
        feed_task.cancel()
        try:
            await feed_task
        except asyncio.CancelledError:
            pass
        if not ffmpeg.stdin.is_closing():
            ffmpeg.stdin.close()
        try:
            await asyncio.wait_for(ffmpeg.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            ffmpeg.kill()
            await ffmpeg.wait()

    if not jpeg_bytes:
        raise RuntimeError("No thumbnail frame received")
    return jpeg_bytes
