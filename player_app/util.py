"""Small helpers shared across main.py's orchestration loops and the
background views/*_service.py workers."""
from __future__ import annotations

import asyncio
import socket


async def sleep_unless_stopped(stop_event: asyncio.Event, seconds: float) -> None:
    """Sleeps for `seconds`, waking early if `stop_event` is set. Shared by
    every "retry/refresh on an interval, but stop immediately if asked to"
    loop, so each one doesn't need its own try/except TimeoutError around
    asyncio.wait_for."""
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=seconds)
    except asyncio.TimeoutError:
        pass


def local_ip() -> str:
    """Best-effort LAN IP: opens a UDP "connection" (no packets sent) to a
    public address just to see which local interface routing would use.
    Lives here (not main.py) so web_service.py can report it too (see
    GET /api/version) without an import cycle back into main.py."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()
