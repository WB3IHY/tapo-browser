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
from .tapo.client import TapoClientCache
from .tapo.downloader import DownloadManager
from .tapo.media_session_limiter import MediaSessionLimiter
from .tapo.playback_manager import PlaybackManager
from .tapo.thumbnail_cache import ThumbnailCache

log = logging.getLogger("tapo_cli")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings: Settings = app.state.settings
    paths.ensure_dirs()
    init_db()
    DownloadRepo().reset_stale()

    log.info("Ensuring helper binaries (ffmpeg, ffprobe)...")
    app.state.binaries = await asyncio.to_thread(ensure_binaries)
    # pytapo's recording converter calls bare `ffmpeg`/`ffprobe`, so make sure our
    # bundled bin/ is on PATH no matter how the app was launched.
    bin_dir = str(paths.BIN_DIR)
    if bin_dir not in os.environ.get("PATH", "").split(os.pathsep):
        os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")

    try:
        yield
    finally:
        await app.state.playback.stop_all()
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
    # Shared across thumbnails + playback: opening a camera media session
    # while another is already open was observed to make one fail outright
    # (502), so both subsystems are capped by the same conservative limit —
    # with playback given priority so background thumbnail generation can
    # never make an actively-waited-on playback start queue behind it.
    media_limiter = MediaSessionLimiter(capacity=1)
    app.state.thumbnails = ThumbnailCache(app.state.clients, media_limiter)
    app.state.playback = PlaybackManager(app.state.clients, media_limiter)

    _mount_routers(app)
    _mount_static(app)
    return app


def _mount_routers(app: FastAPI) -> None:
    from .api import cameras, downloads, playback, recordings

    app.include_router(cameras.router, prefix="/api")
    app.include_router(recordings.router, prefix="/api")
    app.include_router(downloads.router, prefix="/api")
    app.include_router(playback.router, prefix="/api")

    @app.get("/api/health")
    async def health() -> JSONResponse:  # noqa: ANN202
        return JSONResponse(
            {
                "ok": True,
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
