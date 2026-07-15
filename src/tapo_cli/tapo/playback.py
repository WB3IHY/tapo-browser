"""Direct playback of SD-card recordings, streamed live from the camera without
downloading the file first.

Opens the same "playback" media session pytapo's own ``Streamer``/``DownloaderV2``
use, at a given ``start_time``, and keeps consuming it. The camera does **not**
enforce ``end_time`` as a hard stop — a session just keeps delivering chronological
footage from ``start_time`` onward, skipping empty gaps automatically, for as long
as the client keeps reading. Confirmed against a real camera: a single session
streamed ~2 hours of real footage across a 57-minute gap between two recordings
without failing or needing to be reopened. See CLAUDE.md for the validation notes
this module is built on.

Deliberately NOT built on ``pytapo.media_stream.downloaderv2.DownloaderV2``:
- It hardcodes an HLS retention policy (``hls_list_size=3`` + ``delete_segments``)
  meant for a live-tailing view, which throws away early segments — wrong for a
  finished recording the UI needs to scrub anywhere in.
- Its internal ffmpeg-stderr log reader crashes on long-running streams
  (unbounded ``asyncio.StreamReader.readline()`` overflow once ffmpeg emits an
  overlong line). We sidestep this by not piping stderr through asyncio at all.

This module reuses the lower-level, already-correct session primitive
(``Tapo.getMediaSession`` / ``HttpMediaSession.transceive``) directly.

Audio and video are produced by TWO INDEPENDENT, single-input ffmpeg processes,
not muxed together in one process. Originally tried as one ffmpeg process with
two live pipe inputs (video + a second, explicitly-typed ``-f alaw`` input for
audio — see ``ts_demux.py`` for why audio needs its own hand-rolled MPEG-TS/PES
extractor in the first place, since the camera tags it with a private stream
type ffmpeg can't auto-detect). That turned out to be a real, confirmed ffmpeg
bug/limitation, not something tunable away: ffmpeg's handling of two
concurrent *live* pipe inputs to one process silently truncated output far
short of the requested duration — verbose logging showed 0 bytes ever read
from the second input despite real data being written to it, alongside a
"Thread message queue blocking" warning, then "No more output streams to
write to, finishing." reported as if it were normal completion. Confirmed via
extensive isolation testing (independent of thread_queue_size, of whether
audio was actually mapped/muxed, of probesize) that merely having a second
``-i`` open was sufficient to trigger it, and that reverting to a single input
always worked correctly for the same segments. See the project's
direct_playback_debugging memory for the full history.

The fix: each of video and audio gets its own ffmpeg process with exactly one
input (the pattern already proven reliable throughout this project), each
producing its own HLS rendition (video-only, audio-only). A small,
hand-written HLS master/multivariant playlist combines them via the standard
``EXT-X-MEDIA`` mechanism — the same way real-world adaptive-bitrate HLS
streams carry separate audio/video renditions — which ``hls.js``/browsers
handle natively, with no custom muxing needed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import time
from pathlib import Path
from typing import Callable

from pytapo import Tapo
from pytapo.media_stream._utils import StreamType

from . import ts_demux

log = logging.getLogger("tapo_cli.playback")

HLS_SEGMENT_SECONDS = 2
# No hls_list_size cap and no delete_segments: every segment produced this
# session stays on disk and in the playlist, so the whole thing is scrubbable
# end to end once finished (unlike DownloaderV2's live-tail rolling buffer).
_FFMPEG_HLS_ARGS = [
    "-f", "hls",
    "-hls_time", str(HLS_SEGMENT_SECONDS),
    "-hls_list_size", "0",
    "-hls_flags", "append_list+program_date_time",
]
# The camera doesn't enforce end_time, but the request payload still expects a
# value — pass something far enough out that it never becomes the real limit.
_END_TIME_HORIZON_SECONDS = 24 * 3600

# Written once, synchronously, at session start - not ffmpeg-produced. Points
# at the two independently-produced renditions by filename only (both live in
# the same output_dir the master itself is served from). CODECS is a
# best-effort hint (H.264 Main-ish profile/level, AAC-LC) - hls.js mainly
# relies on actually probing each rendition's own segments, not this string,
# so it doesn't need to be byte-exact for every camera model.
_MASTER_PLAYLIST = """#EXTM3U
#EXT-X-VERSION:6
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aud",NAME="audio",DEFAULT=YES,AUTOSELECT=YES,URI="audio.m3u8"
#EXT-X-STREAM-INF:BANDWIDTH=2000000,CODECS="avc1.4d0028,mp4a.40.2",AUDIO="aud"
video.m3u8
"""


class PlaybackSession:
    """One continuous camera-side playback stream, muxed to a growing pair of
    HLS renditions (video-only + audio-only, combined via a master playlist)
    on disk.

    ``duration_seconds``, when given, bounds each rendition's *output* to
    that much real content duration (via ffmpeg's own ``-t`` on each
    single-input process, which — since we stream-copy video and only
    lightly encode audio — tracks real PTS-based duration, not wall-clock
    time, so it stays accurate regardless of how fast or slow data arrives
    from the camera). Without it, the session is open-ended and keeps
    streaming chronologically for as long as something reads it — that mode
    exists for a future continuous-timeline UI; the current UI plays one
    specific clicked segment and must bound to it, both to match what the UI
    implies ("play this clip", same as the adjacent Download button) and
    because an open-ended session a user forgets to close holds a camera
    connection indefinitely.
    """

    def __init__(
        self,
        tapo: Tapo,
        start_time: int,
        output_dir: Path,
        duration_seconds: float | None = None,
        window_size: int = 500,
    ) -> None:
        self._tapo = tapo
        self._start_time = start_time
        self._output_dir = output_dir
        self._duration_seconds = duration_seconds
        self._window_size = window_size
        self._video_ffmpeg: asyncio.subprocess.Process | None = None
        self._audio_ffmpeg: asyncio.subprocess.Process | None = None
        self._feed_task: asyncio.Task | None = None
        self._running = False
        # External contract: what api/playback.py serves and PlaybackManager
        # waits on. Now the hand-written master playlist rather than an
        # ffmpeg-produced file - always exists immediately (written
        # synchronously in start()), unlike the two real renditions.
        self.playlist_path = output_dir / "master.m3u8"
        self._video_playlist_path = output_dir / "video.m3u8"
        self._audio_playlist_path = output_dir / "audio.m3u8"
        self.error: str | None = None

    async def start(self) -> None:
        self._output_dir.mkdir(parents=True, exist_ok=True)

        # pytapo's getUserID() does blocking network I/O the first time it's
        # called; if that first call happens synchronously from inside code
        # already running on this event loop (as it would if left to
        # _feed()'s own playback-payload construction), pytapo's internal
        # handler tries to spin up a second event loop and crashes with
        # "Cannot run the event loop while another loop is running". Priming
        # the cache here, off the loop, avoids that.
        await asyncio.get_event_loop().run_in_executor(None, self._tapo.getUserID)

        self.playlist_path.write_text(_MASTER_PLAYLIST)

        video_cmd = [
            "ffmpeg",
            "-loglevel", "warning",
            "-probesize", "32",
            "-analyzeduration", "0",
            "-f", "mpegts",
            "-i", "pipe:0",
            "-map", "0:v:0",
            "-c:v", "copy",
        ]
        audio_cmd = [
            "ffmpeg",
            "-loglevel", "warning",
            "-f", "alaw",
            "-ar", "8000",
            "-ac", "1",
            "-i", "pipe:0",
            # Browsers can't decode G.711 via HLS/MSE - unlike video, audio
            # needs a real transcode to AAC, not a stream copy.
            "-c:a", "aac",
        ]
        if self._duration_seconds is not None:
            video_cmd += ["-t", str(self._duration_seconds)]
            audio_cmd += ["-t", str(self._duration_seconds)]
        video_cmd += [*_FFMPEG_HLS_ARGS, str(self._video_playlist_path)]
        audio_cmd += [*_FFMPEG_HLS_ARGS, str(self._audio_playlist_path)]

        # Real files, not subprocess.PIPE: piping stderr through asyncio's
        # StreamReader.readline() is what crashes DownloaderV2 on long runs.
        video_log = (self._output_dir / "ffmpeg-video.log").open("ab")
        audio_log = (self._output_dir / "ffmpeg-audio.log").open("ab")

        self._video_ffmpeg = await asyncio.create_subprocess_exec(
            *video_cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=video_log,
        )
        self._audio_ffmpeg = await asyncio.create_subprocess_exec(
            *audio_cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=audio_log,
        )

        self._running = True
        self._feed_task = asyncio.create_task(self._feed())

    def add_done_callback(self, callback: Callable[[], None]) -> None:
        """Invoke ``callback()`` once the feed task ends, however it ends —
        a bounded session (see ``duration_seconds``) finishing on its own, an
        error, or an explicit stop(). Used by PlaybackManager so a session
        that finishes without anyone calling stop() (e.g. a clip played to
        completion in a tab the user then forgets about) still gets its
        camera connection and limiter slot released automatically.
        """
        assert self._feed_task is not None
        self._feed_task.add_done_callback(lambda _task: callback())

    async def _feed(self) -> None:
        media_session = self._tapo.getMediaSession(StreamType.Stream)
        media_session.set_window_size(self._window_size)
        payload = json.dumps(
            {
                "type": "request",
                "params": {
                    "playback": {
                        "client_id": self._tapo.getUserID(),
                        "channels": [0, 1],
                        "scale": "1/1",
                        "start_time": str(self._start_time),
                        "end_time": str(int(time.time()) + _END_TIME_HORIZON_SECONDS),
                        "event_type": [1, 2],
                        # "channels":[0,1] alone doesn't actually get audio
                        # included - confirmed live: without these two fields
                        # (matching the live-preview payload's shape), the
                        # camera's response is video-only despite requesting
                        # both channels.
                        "audio": ["default"],
                        "audio_config": {"encode_type": "G711alaw", "sample_rate": "8"},
                    },
                    "method": "get",
                },
            }
        )
        ts_buffer = bytearray()
        audio_extractor = ts_demux.AudioExtractor()
        try:
            async with media_session:
                # Real gaps between recordings can pause the underlying data
                # feed for tens of seconds (observed up to ~25-40s against a
                # real camera) without the session actually being over.
                async for resp in media_session.transceive(payload, no_data_timeout=60.0):
                    if not self._running:
                        break
                    if resp.mimetype != "video/mp2t":
                        # Camera-side JSON control messages (session ack,
                        # stream_status notifications). Worth keeping as a
                        # real log line, not just throwaway debug output -
                        # this project has repeatedly needed to distinguish
                        # "camera ended the session early" from "our own
                        # code stopped it" when a clip cuts off unexpectedly
                        # short, and this is the only place that's visible.
                        log.info("Non-video media session message: %r", resp.plaintext[:300])
                        continue

                    ts_buffer += resp.plaintext
                    while len(ts_buffer) >= 188 and ts_buffer[0] != 0x47:
                        pos = ts_buffer.find(0x47, 1)
                        if pos == -1:
                            ts_buffer.clear()
                            break
                        ts_buffer = ts_buffer[pos:]

                    audio_chunk = bytearray()
                    while len(ts_buffer) >= 188:
                        packet = ts_buffer[:188]
                        ts_buffer = ts_buffer[188:]
                        if not self._running or self._video_ffmpeg.stdin.is_closing():
                            break
                        self._video_ffmpeg.stdin.write(packet)
                        audio_chunk += audio_extractor.feed(packet)

                    try:
                        await asyncio.wait_for(self._video_ffmpeg.stdin.drain(), timeout=15.0)
                        if audio_chunk and not self._audio_ffmpeg.stdin.is_closing():
                            self._audio_ffmpeg.stdin.write(bytes(audio_chunk))
                            await asyncio.wait_for(self._audio_ffmpeg.stdin.drain(), timeout=15.0)
                    except (ConnectionResetError, BrokenPipeError, asyncio.TimeoutError):
                        self._running = False
                        break
        except Exception as exc:  # noqa: BLE001
            log.exception("Playback session feed failed")
            self.error = str(exc)
        finally:
            self._running = False
            for proc in (self._video_ffmpeg, self._audio_ffmpeg):
                if proc is not None and proc.stdin is not None and not proc.stdin.is_closing():
                    proc.stdin.close()

    async def stop(self) -> None:
        self._running = False
        if self._feed_task is not None:
            self._feed_task.cancel()
            try:
                await self._feed_task
            except asyncio.CancelledError:
                pass
        for proc in (self._video_ffmpeg, self._audio_ffmpeg):
            if proc is not None and proc.stdin is not None and not proc.stdin.is_closing():
                proc.stdin.close()
        for proc in (self._video_ffmpeg, self._audio_ffmpeg):
            if proc is None:
                continue
            try:
                await asyncio.wait_for(proc.wait(), timeout=10.0)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()

    @property
    def running(self) -> bool:
        return self._running
