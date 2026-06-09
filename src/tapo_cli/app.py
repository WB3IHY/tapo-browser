"""FastAPI application: lifespan wiring + router mounts + static UI."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import paths
from .bootstrap.binaries import ensure_binaries
from .config import Settings
from .db.connection import close_db, init_db
from .db.repo import DownloadRepo
from .streaming.go2rtc import Go2rtcProcess
from .tapo.client import TapoClientCache
from .tapo.downloader import DownloadManager

log = logging.getLogger("tapo_cli")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings: Settings = app.state.settings
    paths.ensure_dirs()
    init_db()
    DownloadRepo().reset_stale()

    log.info("Ensuring helper binaries (ffmpeg, ffprobe, go2rtc)...")
    app.state.binaries = await asyncio.to_thread(ensure_binaries)
    # pytapo's recording converter calls bare `ffmpeg`/`ffprobe`, so make sure our
    # bundled bin/ is on PATH no matter how the app was launched.
    bin_dir = str(paths.BIN_DIR)
    if bin_dir not in os.environ.get("PATH", "").split(os.pathsep):
        os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")

    go2rtc = Go2rtcProcess(preferred_port=settings.go2rtc_port)
    app.state.go2rtc = go2rtc
    try:
        await go2rtc.start()
    except Exception:  # noqa: BLE001 — a stream failure must not down the whole app
        log.exception("go2rtc failed to start; live view will be unavailable")

    try:
        yield
    finally:
        await go2rtc.stop()
        close_db()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.load()
    app = FastAPI(title="Tapo Camera Manager", lifespan=lifespan)
    app.state.settings = settings
    app.state.clients = TapoClientCache()
    app.state.downloads = DownloadManager(
        DownloadRepo(),
        app.state.clients,
        paths.DOWNLOADS_DIR,
        max_concurrent=settings.max_concurrent_downloads,
    )

    _mount_routers(app)
    _mount_static(app)
    return app


def _mount_routers(app: FastAPI) -> None:
    from .api import cameras, downloads, recordings, stream

    app.include_router(cameras.router, prefix="/api")
    app.include_router(recordings.router, prefix="/api")
    app.include_router(downloads.router, prefix="/api")
    app.include_router(stream.router, prefix="/api")

    @app.get("/api/health")
    async def health() -> JSONResponse:  # noqa: ANN202
        go2rtc: Go2rtcProcess = app.state.go2rtc
        return JSONResponse(
            {
                "ok": True,
                "go2rtc": {"running": go2rtc.running, "port": go2rtc.port},
                "ffmpeg": str(paths.ffmpeg_path()),
                "version": app.version,
            }
        )


def _mount_static(app: FastAPI) -> None:
    web_dir = paths.PROJECT_ROOT / "src" / "tapo_cli" / "web"
    static_dir = web_dir / "static"
    templates = Jinja2Templates(directory=str(web_dir / "templates"))
    app.state.templates = templates

    if static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/")
    async def index(request: Request):  # noqa: ANN202
        return templates.TemplateResponse(request, "index.html", {})


# Importable target for `uvicorn tapo_cli.app:app`
app = create_app()
