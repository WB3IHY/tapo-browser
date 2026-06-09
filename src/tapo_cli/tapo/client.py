"""Wrap the (synchronous, requests-based) pytapo client for use in async code.

pytapo does blocking network I/O, so every call is dispatched to a small thread
pool to keep the event loop responsive. Authenticated ``Tapo`` objects are cached
per camera (constructing one performs a login handshake) and invalidated when the
camera's credentials change or it's deleted.
"""

from __future__ import annotations

import asyncio
import functools
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, TypeVar

from pytapo import Tapo

T = TypeVar("T")

_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="tapo")


async def run_blocking(fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Run a blocking pytapo call off the event loop."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, functools.partial(fn, *args, **kwargs))


def _cred_signature(cam: dict[str, Any]) -> str:
    return "|".join(str(cam.get(k) or "") for k in ("host", "control_port", "account_password"))


def build_tapo(cam: dict[str, Any]) -> Tapo:
    """Construct (and authenticate) a Tapo client from a camera DB row.

    Modern Tapo firmware: the local control API logs in as "admin" with the
    TP-Link *account* password (the same secret also decrypts recording downloads).
    """
    pw = cam["account_password"]
    return Tapo(
        host=cam["host"],
        user="admin",
        password=pw,
        cloudPassword=pw,
        controlPort=cam.get("control_port") or 443,
    )


class TapoClientCache:
    def __init__(self) -> None:
        self._clients: dict[int, Tapo] = {}
        self._sigs: dict[int, str] = {}
        self._locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def get(self, cam: dict[str, Any]) -> Tapo:
        cid = cam["id"]
        sig = _cred_signature(cam)
        async with self._locks[cid]:
            if cid in self._clients and self._sigs.get(cid) == sig:
                return self._clients[cid]
            client = await run_blocking(build_tapo, cam)
            self._clients[cid] = client
            self._sigs[cid] = sig
            return client

    def invalidate(self, camera_id: int) -> None:
        self._clients.pop(camera_id, None)
        self._sigs.pop(camera_id, None)
