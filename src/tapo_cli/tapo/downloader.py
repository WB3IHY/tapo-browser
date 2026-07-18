"""Manage recording downloads as background asyncio tasks.

Each job runs pytapo's async ``Downloader`` generator, persisting progress to the
``downloads`` table (durable, survives a page reload) and pushing live updates to
a per-job queue that the SSE endpoint streams to the browser. A semaphore caps
how many downloads run at once.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

from pytapo.media_stream.downloader import Downloader

from .. import paths
from ..db.repo import DownloadRepo
from ..models import DownloadOut
from .client import TapoClientCache, run_blocking
from .info import friendly_error
from .recordings import time_correction

log = logging.getLogger("tapo_cli.download")

_DONE = {"_done": True}


class DownloadManager:
    def __init__(
        self,
        repo: DownloadRepo,
        cache: TapoClientCache,
        downloads_dir: Path,
        max_concurrent: int = 2,
    ) -> None:
        self._repo = repo
        self._cache = cache
        self._dir = downloads_dir
        self._sem = asyncio.Semaphore(max_concurrent)
        self._tasks: dict[int, asyncio.Task] = {}
        self._queues: dict[int, asyncio.Queue] = {}

    # ----- subscription / state ---------------------------------------- #
    def subscribe(self, job_id: int) -> asyncio.Queue:
        q = self._queues.get(job_id)
        if q is None:
            q = asyncio.Queue()
            self._queues[job_id] = q
        return q

    def is_active(self, job_id: int) -> bool:
        t = self._tasks.get(job_id)
        return t is not None and not t.done()

    async def _snapshot(self, job_id: int, cam: dict[str, Any]) -> dict[str, Any]:
        row = self._repo.get(job_id)
        try:
            tc = await time_correction(self._cache, cam)
        except Exception:  # noqa: BLE001 - display-only correction, never worth failing a status update over
            tc = 0
        return DownloadOut.from_row(row, tc).model_dump()

    async def _emit(self, job_id: int, payload: dict[str, Any]) -> None:
        await self.subscribe(job_id).put(payload)

    # ----- lifecycle --------------------------------------------------- #
    async def start(self, cam: dict[str, Any], job: dict[str, Any]) -> None:
        jid = job["id"]
        self.subscribe(jid)  # create the queue before the task can emit
        self._tasks[jid] = asyncio.create_task(self._run(cam, job))

    async def cancel(self, job_id: int) -> bool:
        t = self._tasks.get(job_id)
        if t is not None and not t.done():
            t.cancel()
            return True
        return False

    async def _run(self, cam: dict[str, Any], job: dict[str, Any]) -> None:
        jid = job["id"]
        try:
            async with self._sem:
                self._repo.update(jid, status="running", current_action="Starting", error=None)
                await self._emit(jid, await self._snapshot(jid, cam))

                tapo = await self._cache.get(cam)
                tc = int(await run_blocking(tapo.getTimeCorrection) or 0)
                out_dir = self._dir / cam["slug"] / job["date"]
                out_dir.mkdir(parents=True, exist_ok=True)
                # Downloader concatenates outputDirectory + filename, so it MUST
                # end with a path separator.
                out_prefix = str(out_dir) + os.sep

                downloader = Downloader(
                    tapo,
                    job["start_time"],
                    job["end_time"],
                    tc,
                    out_prefix,
                    overwriteFiles=True,
                )

                final_path: Path | None = None
                last_action: str | None = None
                async for status in downloader.download():
                    last_action = status.get("currentAction")
                    fn = status.get("fileName")
                    if fn:
                        final_path = Path(fn)
                    progress = float(status.get("progress") or 0)
                    total = float(status.get("total") or 0) or float(job["end_time"] - job["start_time"])
                    self._repo.update(
                        jid, current_action=last_action, progress_sec=progress, total_sec=total
                    )
                    await self._emit(jid, await self._snapshot(jid, cam))

                if final_path is not None and final_path.exists():
                    total = self._repo.get(jid)["total_sec"]
                    self._repo.update(
                        jid,
                        status="done",
                        current_action="Finished",
                        file_path=str(final_path),
                        progress_sec=total,
                    )
                else:
                    self._repo.update(jid, status="error", error=self._explain_failure(cam, last_action))
                await self._emit(jid, await self._snapshot(jid, cam))

        except asyncio.CancelledError:
            self._repo.update(jid, status="canceled", current_action="Canceled")
            await self._emit(jid, await self._snapshot(jid, cam))
            raise
        except Exception as exc:  # noqa: BLE001
            log.exception("download %s failed", jid)
            self._repo.update(jid, status="error", error=friendly_error(exc))
            await self._emit(jid, await self._snapshot(jid, cam))
        finally:
            await self._emit(jid, _DONE)
            self._tasks.pop(jid, None)

    @staticmethod
    def _explain_failure(cam: dict[str, Any], last_action: str | None) -> str:
        if last_action == "Recording in progress":
            return "This recording is too recent — wait a minute for the camera to finish writing it."
        return f"Download did not complete (last step: {last_action or 'unknown'})."
