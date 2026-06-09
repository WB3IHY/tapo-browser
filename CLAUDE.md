# CLAUDE.md

Guidance for working in this repo. Keep it current as the project evolves.

## What this is

**Tapo Camera Manager** — a local, single-user web app to manage TP-Link Tapo cameras:
add multiple cameras, watch live streams in the browser, and browse/download SD-card
recordings. FastAPI backend + vanilla-JS UI, bound to `127.0.0.1` only. The TP-Link
account password is stored **unencrypted** in local SQLite by design (personal home use).

## Run & develop

- `./run.sh` / `run.bat` — the launcher users run. Wraps `uv run --python 3.13 python -m tapo_cli`.
- `uv sync --python 3.13` — install/refresh dependencies.
- `uv run uvicorn tapo_cli.app:app --host 127.0.0.1 --port 8077 --reload` — dev server.
- `TAPO_PORT`, `TAPO_GO2RTC_PORT`, `TAPO_MAX_DOWNLOADS`, `TAPO_OPEN_BROWSER` — env overrides (`config.py`).
- Health: `GET /api/health`. OpenAPI: `/openapi.json`. `.claude/launch.json` defines the `tapo` preview server on 8090.

## Credential model (IMPORTANT — modern Tapo firmware)

- The **only** secret per camera is the **TP-Link account password** (column `account_password`). Username is implicit.
- **Control API (pytapo):** `Tapo(host, "admin", account_password, cloudPassword=account_password)` — admin login takes the **plaintext** account password; the same secret decrypts recording downloads.
- **Live stream (go2rtc):** native `tapo://admin:<HASH>@host` source, where `<HASH>` = **uppercase SHA-256 of the account password** (go2rtc wants the hash, NOT plaintext — opposite of pytapo). See `streaming/source.py`.
- The Tapo app's separate **"Camera Account"** (Advanced Settings) is for **RTSP only** and is **not used** here. RTSP support was intentionally dropped (could be re-added as an optional stream source).

## Must-knows

- **Python 3.13 only.** `pytapo` breaks on 3.14. Pinned in `pyproject.toml` and the launchers — always `uv ... --python 3.13`.
- `pytapo` omits an **`aiofiles`** dependency it imports — declared explicitly here.
- pytapo's recording `Convert` shells out to bare **`ffmpeg` and `ffprobe`** via PATH — both are provisioned into `bin/` and `bin/` is prepended to PATH in the app lifespan (not only `__main__`).
- **`bin/` and `data/` are gitignored and regenerated.** Never commit them or credentials.

## Architecture (where things live)

- `app.py` — FastAPI app + lifespan: ensure binaries → prepend `bin/` to PATH → start/stop go2rtc. `create_app()` wires `app.state.{settings,clients,downloads,go2rtc}`.
- `bootstrap/binaries.py` — downloads/locates `ffmpeg`,`ffprobe`,`go2rtc` (prefers a working system binary, else pinned downloads from `sources.py`; caches same-URL archives).
- `tapo/` — `client.py` (`TapoClientCache` sync→async bridge via `run_blocking`), `info.py` (connection test + SD parsing + `friendly_error`), `recordings.py` (list days/segments), `downloader.py` (`DownloadManager`: background jobs).
- `streaming/` — `go2rtc.py` (subprocess + `go2rtc.yaml` generation), `source.py` (`tapo_source_url` + the SHA-256 hash).
- `api/` — routers under `/api`: `cameras`, `recordings`, `downloads`, `stream`.
- `db/` — raw `sqlite3` (no ORM), WAL, migrate-on-start from `schema.sql`.
- `web/` — Jinja `index.html` + vanilla JS; `static/vendor/video-{stream,rtc}.js` vendored from go2rtc.

## Conventions

- pytapo is **synchronous** — always call it via `tapo.client.run_blocking(fn, ...)` (thread pool), never directly in async code.
- Cache pytapo clients per camera (`TapoClientCache`); `.invalidate(id)` after edits/deletes.
- Camera mutations must regenerate the go2rtc config: `await app.state.go2rtc.reload()`.
- UI-facing errors go through `tapo.info.friendly_error()`.

## Gotchas (learned the hard way)

- go2rtc's internal **`rtsp` module must stay enabled** (bind `127.0.0.1:<port>`, never `listen: ""`) — the `ffmpeg:<stream>#audio=...` transcode source pulls through it.
- `Downloader` builds its output path as `outputDirectory + filename`, so the directory string **must end with a path separator**.
- Same-origin **reverse proxy** under `/api/go2rtc/*` (HTTP + a WebSocket relay in `api/stream.py`) avoids CORS and keeps the go2rtc port internal. The WS relay was verified to pass binary MSE frames intact.
- With `from __future__ import annotations`, FastAPI resolves handler type hints against **module globals** — import `Request` etc. at module level or a `request: Request` param becomes a query param (HTTP 422).
- **Renames:** after renaming a shared symbol, grep ALL call sites — a `_friendly_error`→`friendly_error` rename left a stale caller that 500'd `test-connection` (only caught during live testing).
- **Tapo login lockout:** several bad-credential attempts trigger `Temporary Suspension: try again in N seconds` (~tens of minutes). Attempts during the lockout do NOT reset the timer, but failures *after* it clears re-arm it. **Rebooting the camera clears it instantly.** Don't auto-poll with wrong creds.
- **`getSDCard`** returns unit-suffixed strings (`"0B"`, `"29.7GB"`) and `status:"offline"` when no card — `info._size_to_mb` parses units; `_parse_sdcard` reports `sd_present`.
- **`getRecordingsList` raises `STORAGE_NOT_EXIST (-71114)`** when there's no SD card — mapped to a friendly message.
- **Live view + macOS Local Network permission:** verified working at 2560×1440 on a C520WS (fw 1.3.4) via go2rtc's `tapo://` source. The earlier "nothing renders / `128×96` placeholder / `multipart: NextPart: EOF`" was the **macOS Local Network privacy permission** not yet granted to the helper process — once granted (and given a few seconds to stabilize), MSE plays. go2rtc may still log periodic `multipart: NextPart: EOF` reconnects on this firmware; playback continues through them. `live.js` shows a hint box only if no real frame decodes within ~12s. An explicit RTSP source remains a possible fallback for cameras that won't stream via `tapo://`.

## Testing notes

- Test against a real Tapo camera on your LAN; live view + downloads can't be fully verified without one (downloads also need a working SD card).
- Verify binaries: `bin/ffmpeg -version`, `bin/ffprobe -version`, `bin/go2rtc --version`.
- Quick stream check: point `ffprobe -rtsp_transport tcp rtsp://127.0.0.1:<go2rtc_rtsp_port>/<slug>` at go2rtc's re-exported stream; "unspecified size" means SPS isn't arriving.
- Never put real camera or TP-Link credentials into committed files.
