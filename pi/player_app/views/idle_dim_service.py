"""Dims the idle screen after extended inactivity - lighter-touch than
turning the display off entirely (which the Pi's HDMI/DRM output has no
clean software-triggered way to do), but enough to signal "nothing's
happening" during long stretches with nothing selected and no input."""
from __future__ import annotations

import asyncio
import logging
import time

from ..controllers.playback_controller import PlaybackController
from ..util import sleep_unless_stopped

logger = logging.getLogger(__name__)

IDLE_DIM_TIMEOUT_SECONDS = 600  # 10 minutes
IDLE_DIM_PERCENT = 50
CHECK_INTERVAL_SECONDS = 15


class IdleDimService:
    def __init__(self, controller: PlaybackController):
        self._controller = controller
        self._dimmed = False

    async def run(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            await self._tick()
            await sleep_unless_stopped(stop_event, CHECK_INTERVAL_SECONDS)

    async def _tick(self) -> None:
        idle_for = time.monotonic() - self._controller.last_input_at
        should_be_dimmed = self._controller.is_idle and idle_for >= IDLE_DIM_TIMEOUT_SECONDS
        if should_be_dimmed and not self._dimmed:
            await self._controller.player.set_dim(IDLE_DIM_PERCENT)
            self._dimmed = True
        elif not should_be_dimmed and self._dimmed:
            await self._controller.player.set_dim(0)
            self._dimmed = False
