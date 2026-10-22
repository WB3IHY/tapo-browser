"""Concurrency limiter where playback requests take priority over queued
thumbnail requests, with a minimum cooldown between consecutive sessions.

Both subsystems compete for the same scarce resource (a camera media
session — opening one while another is active was observed to make one of
the two fail outright, and the camera's own device/session accounting
appears to degrade under rapid open/close churn independent of that, not
recovering cleanly even across a reboot). A plain `asyncio.Semaphore` serves
waiters strictly FIFO with no pacing, which let a batch of queued background
thumbnail generations both starve an actively-waited-on playback start *and*
open/close camera connections back-to-back as fast as the network allowed.
This limiter always serves playback (`high_priority=True`) waiters before
thumbnail waiters (though, same as a semaphore, it can never preempt a slot
already in use), and enforces `cooldown_seconds` of idle time after each
release before the next waiter is granted the slot — so no backlog, however
large, can ever cause rapid-fire session churn. The cooldown covers requests
that find the slot free too (found live 2026-10-22: playback started right
after another session ended got no video from the camera).
"""

from __future__ import annotations

import asyncio
import logging

log = logging.getLogger("tapo_cli.limiter")


class MediaSessionLimiter:
    def __init__(self, capacity: int = 1, cooldown_seconds: float = 2.0) -> None:
        self._capacity = capacity
        self._cooldown_seconds = cooldown_seconds
        self._in_use = 0
        self._ready_at = 0.0  # loop time before which no slot may be granted
        self._high: list[asyncio.Future] = []
        self._normal: list[asyncio.Future] = []

    def _wake_next(self) -> None:
        loop = asyncio.get_event_loop()
        remaining = self._ready_at - loop.time()
        if remaining > 0:
            loop.call_later(remaining, self._wake_next)
            return
        queue = self._high or self._normal
        while queue and self._in_use < self._capacity:
            fut = queue.pop(0)
            if not fut.done():
                self._in_use += 1
                fut.set_result(None)
            queue = self._high or self._normal

    async def acquire(self, high_priority: bool = False) -> None:
        # Always queue, even when the slot is free: the cooldown after the last
        # release applies to every request, not just ones that had to wait.
        fut = asyncio.get_event_loop().create_future()
        (self._high if high_priority else self._normal).append(fut)
        log.debug("acquire requested high=%s in_use=%s waiting=%s", high_priority, self._in_use, len(self._high) + len(self._normal))
        self._wake_next()
        try:
            await fut
            log.debug("slot granted high=%s", high_priority)
        except asyncio.CancelledError:
            for q in (self._high, self._normal):
                if fut in q:
                    q.remove(fut)
            raise

    def release(self, cooldown_seconds: float | None = None) -> None:
        """Free the slot; nothing is granted again until the cooldown has passed."""
        self._in_use -= 1
        log.debug("slot released, cooldown=%s", self._cooldown_seconds if cooldown_seconds is None else cooldown_seconds)
        cooldown = self._cooldown_seconds if cooldown_seconds is None else cooldown_seconds
        loop = asyncio.get_event_loop()
        self._ready_at = max(self._ready_at, loop.time() + cooldown)
        loop.call_later(cooldown, self._wake_next)

    def normal(self) -> "_LimiterContext":
        return _LimiterContext(self, high_priority=False)

    def high_priority(self) -> "_LimiterContext":
        return _LimiterContext(self, high_priority=True)


class _LimiterContext:
    def __init__(self, limiter: MediaSessionLimiter, high_priority: bool = False) -> None:
        self._limiter = limiter
        self._high_priority = high_priority

    async def __aenter__(self) -> None:
        await self._limiter.acquire(high_priority=self._high_priority)

    async def __aexit__(self, *exc) -> None:
        self._limiter.release()
