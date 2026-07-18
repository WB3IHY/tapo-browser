# CLAUDE.md

Guidance for working in this repo. Keep it current as the project evolves.

## What this is

**Tapo Camera Manager** — a local, single-user web app to manage TP-Link Tapo cameras:
add multiple cameras and browse/download SD-card recordings. FastAPI backend + vanilla-JS
UI, bound to `127.0.0.1` only. The TP-Link account password is stored **unencrypted** in
local SQLite by design (personal home use).

**Current focus:** prototyping recording **thumbnail previews** and **direct playback**
(stream a recording segment without downloading the whole file first) — the two things
the official Tapo Android app can do that this project and
[HomeAssistant-Tapo-Control](https://github.com/JurajNyiri/HomeAssistant-Tapo-Control)
can't yet. The intent is to work out the approach here, then port it into
HomeAssistant-Tapo-Control (a proper HA custom integration, also built on `pytapo`).

Live view and the camera-controls panel (night vision, privacy, LED, motion, flip, PTZ,
alarm, etc.) that were previously part of this app **have been removed** — see git history
around the "Partial strip-down" pivot if you need to resurrect any of that. go2rtc is no
longer a dependency of this project.

## Run & develop

- `./run.sh` / `run.bat` — the launcher users run. Wraps `uv run --python 3.13 python -m tapo_cli`.
- `uv sync --python 3.13` — install/refresh dependencies.
- `uv run uvicorn tapo_cli.app:app --host 127.0.0.1 --port 8077 --reload` — dev server.
- `TAPO_PORT`, `TAPO_MAX_DOWNLOADS`, `TAPO_OPEN_BROWSER` — env overrides (`config.py`).
- Health: `GET /api/health`. OpenAPI: `/openapi.json`. `.claude/launch.json` defines the `tapo` preview server on 8090.

## Credential model (IMPORTANT — modern Tapo firmware)

- The **only** secret per camera is the **TP-Link account password** (column `account_password`). Username is implicit.
- **Control API (pytapo):** `Tapo(host, "admin", account_password, cloudPassword=account_password)` — admin login takes the **plaintext** account password; the same secret decrypts recording downloads and (per the thumbnail/playback prototype work) the recorded-media session.
- The Tapo app's separate **"Camera Account"** (Advanced Settings) is for **RTSP only** and is **not used** here. RTSP support was never added.

## Must-knows

- **Python 3.13 only.** `pytapo` breaks on 3.14. Pinned in `pyproject.toml` and the launchers — always `uv ... --python 3.13`.
- `pytapo` omits an **`aiofiles`** dependency it imports — declared explicitly here.
- pytapo's recording `Convert` shells out to bare **`ffmpeg` and `ffprobe`** via PATH — both are provisioned into `bin/` and `bin/` is prepended to PATH in the app lifespan (not only `__main__`). `ffmpeg`/`ffprobe` prefer a working **system** binary already on PATH (`prefer_system=True` in `bootstrap/binaries.py`) and only download a pinned copy as a fallback.
- **`bin/` and `data/` are gitignored and regenerated.** Never commit them or credentials.
- **pytapo's `pytapo.media_stream` module already has most of what the thumbnail/playback prototype needs** — see "Thumbnail + playback prototype" below before reaching for anything more exotic (like decompiling the Tapo APK).

## Architecture (where things live)

- `app.py` — FastAPI app + lifespan: ensure `ffmpeg`/`ffprobe` binaries → prepend `bin/` to PATH. `create_app()` wires `app.state.{settings,clients,downloads}`.
- `bootstrap/binaries.py` — downloads/locates `ffmpeg`/`ffprobe` (prefers a working system binary, else pinned downloads from `sources.py`; caches same-URL archives).
- `tapo/` — `client.py` (`TapoClientCache` sync→async bridge via `run_blocking`), `info.py` (connection test + SD parsing + `friendly_error`), `recordings.py` (list days/segments), `downloader.py` (`DownloadManager`: background download-to-disk jobs via `pytapo.media_stream.downloader.Downloader`).
- `api/` — routers under `/api`: `cameras`, `recordings`, `downloads`.
- `db/` — raw `sqlite3` (no ORM), WAL, migrate-on-start from `schema.sql`.
- `web/` — Jinja `index.html` + vanilla JS (`app.js`, `recordings.js`, `api.js`).

## Conventions

- pytapo is **synchronous** — always call it via `tapo.client.run_blocking(fn, ...)` (thread pool), never directly in async code.
- Cache pytapo clients per camera (`TapoClientCache`); `.invalidate(id)` after edits/deletes.
- UI-facing errors go through `tapo.info.friendly_error()`.

## Gotchas (learned the hard way)

- `Downloader` builds its output path as `outputDirectory + filename`, so the directory string **must end with a path separator**.
- With `from __future__ import annotations`, FastAPI resolves handler type hints against **module globals** — import `Request` etc. at module level or a `request: Request` param becomes a query parameter (HTTP 422).
- **Renames:** after renaming a shared symbol, grep ALL call sites — a stale caller has bitten this project before (only caught during live testing).
- **Tapo login lockout:** several bad-credential attempts trigger `Temporary Suspension: try again in N seconds` (~tens of minutes). Attempts during the lockout do NOT reset the timer, but failures *after* it clears re-arm it. **Rebooting the camera clears it instantly.** Don't auto-poll with wrong creds.
- **`getSDCard`** returns unit-suffixed strings (`"0B"`, `"29.7GB"`) and `status:"offline"` when no card — `info._size_to_mb` parses units; `_parse_sdcard` reports `sd_present`.
- **`getRecordingsList` raises `STORAGE_NOT_EXIST (-71114)`** when there's no SD card — mapped to a friendly message.
- **Recording timestamps are in the camera's own (possibly wrong) clock, not true UTC.** `getRecordings`/`getRecordingsList` return `startTime`/`endTime` computed from the camera's internal clock; if a camera's timezone/DST is misconfigured, its times are skewed by exactly that amount — seen in practice on one camera whose recordings listed an hour off vs. the Tapo app, while other cameras were correct. `pytapo.getTimeCorrection()` returns the delta (`now - camera's reported now`) to fix this: `true_time = raw_time + time_correction`. The **raw**, uncorrected `start_time`/`end_time` must still be sent back to the camera for playback/thumbnail/download requests (it looks up recordings using its own clock) — only apply the correction to values shown to the user. Both display paths apply it: `api/recordings.py`'s `start_label`/`end_label` for the recordings browser, and `DownloadOut.start_label`/`end_label` (computed via `api/downloads.py`'s `_out()` helper, and `DownloadManager._snapshot()` for the SSE progress stream) for the downloads panel.

## Thumbnail + playback prototype (current work)

Goal: recording-segment thumbnails and direct streaming playback (no full download first),
matching what the official Tapo Android app does. **`pytapo`'s `media_stream` module already
does most of the reverse-engineering work** — check there before considering decompiling the
Tapo APK:

- `pytapo.getMediaSession(StreamType.Stream)` opens an encrypted media session to the camera.
  Sending a `{"type": "request", "params": {"playback": {"start_time": ..., "end_time": ...,
  "channels": [0, 1], "event_type": [1, 2], "client_id": tapo.getUserID()}, "method": "get"}}`
  payload streams a recorded segment's mpeg-ts video (see `pytapo/media_stream/downloaderv2.py`
  — `DownloaderV2._stream_to_ffmpeg`). A live-view "preview" payload (no start/end time; see
  `pytapo/media_stream/streamer.py`) is the mechanism `Streamer` (the now-removed live view)
  used, for reference/comparison only.
- `pytapo.media_stream.downloaderv2.DownloaderV2` wraps that "playback" session and pipes it
  through `ffmpeg`, supporting `mode="hls"` (playable segments on disk, no full-file download —
  this is the direct-playback mechanism), `mode="pipe"`, and `mode="mp4"`. **Its own comment
  flags it as unfinished** ("misses a lot of stuff — jsons for finish of the stream, retrys,
  reverse compatibility etc..."), so treat it as a starting point to validate against a real
  camera, not a finished dependency to trust blindly.
- Both `Streamer` and `DownloaderV2` accept `ff_args={"-frames:v": "1"}` to grab a single decoded
  frame — the likely basis for thumbnail generation (there's no evidence cameras store separate
  thumbnail images; the app almost certainly decodes a frame client-side, same as this would).
- `getRecordings`/`getRecordingsUTC` (already used by `tapo/recordings.py`) return each segment's
  raw dict `search_video_results` un-pruned by pytapo, but `recordings.py`'s `_collect_segments`
  currently discards everything except `startTime`/`endTime` — worth re-checking the raw response
  against a live camera for any fields it's dropping (event type, "important" flag, etc.) that
  might be useful for the thumbnail/playback UI.
- No real camera has been used to validate `DownloaderV2` yet as of this note — do that before
  building API/UI around it. See "Testing notes" below.

## Testing notes

- Test against a real Tapo camera on your LAN; downloads (and the thumbnail/playback prototype)
  can't be fully verified without one — downloads also need a working SD card.
- Verify binaries: `bin/ffmpeg -version`, `bin/ffprobe -version`.
- Never put real camera or TP-Link credentials into committed files.
