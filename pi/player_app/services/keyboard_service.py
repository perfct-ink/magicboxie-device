"""Local input transport: a USB keyboard is the only way to control the
device without the iOS app, so pressing Escape quits the player app, freeing
the screen for a console login. systemd only restarts the unit on failure, so
it stays off until the device reboots or the service is started by hand.
Entirely optional - if no keyboard is
attached, this just periodically finds nothing and does nothing.

Devices are (re)scanned on an interval rather than once at startup so a
keyboard plugged in after boot still gets picked up.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Dict, Set

from ..controllers.playback_controller import PlaybackController

logger = logging.getLogger(__name__)

RESCAN_INTERVAL_SECONDS = 5.0
# While a movie plays, check for hot-plugged keyboards less often.
PLAYING_RESCAN_INTERVAL_SECONDS = 15.0


class KeyboardService:
    def __init__(self, controller: PlaybackController):
        self._controller = controller
        self._watched_paths: Set[str] = set()
        self._watch_tasks: Set[asyncio.Task] = set()
        self._names: Dict[str, str] = {}
        self._stop_event = None

    async def run(self, stop_event: asyncio.Event) -> None:
        self._stop_event = stop_event
        try:
            while not stop_event.is_set():
                before = list(self._controller.keyboard_names)
                self._attach_new_keyboards()
                if self._controller.keyboard_names != before and self._controller.is_idle:
                    # Plugged in/unplugged while idle: refresh the footer hint.
                    await self._controller.show_idle_screen()
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=RESCAN_INTERVAL_SECONDS if self._controller.is_idle else PLAYING_RESCAN_INTERVAL_SECONDS)
                except asyncio.TimeoutError:
                    continue
        finally:
            for task in self._watch_tasks:
                task.cancel()

    def _attach_new_keyboards(self) -> None:
        try:
            import evdev
        except ImportError:
            logger.warning("evdev not installed - keyboard Escape-to-quit is disabled")
            return

        try:
            device_paths = evdev.list_devices()
        except OSError as exc:
            logger.debug("Could not list input devices: %s", exc)
            return

        for path in device_paths:
            if path in self._watched_paths:
                continue
            try:
                device = evdev.InputDevice(path)
                is_keyboard = evdev.ecodes.KEY_ESC in device.capabilities().get(evdev.ecodes.EV_KEY, [])
            except OSError as exc:
                logger.debug("Could not inspect input device %s: %s", path, exc)
                continue
            if not is_keyboard:
                continue

            self._watched_paths.add(path)
            self._names[path] = device.name
            self._publish_names()
            task = asyncio.create_task(self._watch(device))
            self._watch_tasks.add(task)
            task.add_done_callback(self._watch_tasks.discard)
            logger.info("Watching for Escape on keyboard %s (%s)", path, device.name)

    def _publish_names(self) -> None:
        self._controller.keyboard_names = list(self._names.values())

    async def _watch(self, device) -> None:
        import evdev

        try:
            async for event in device.async_read_loop():
                if event.type != evdev.ecodes.EV_KEY or event.value != 1:
                    continue
                # Any keypress counts as "input" for IdleDimService, even
                # though only Escape actually does something.
                self._controller.last_input_at = time.monotonic()
                if event.code == evdev.ecodes.KEY_ESC:
                    logger.info("Escape pressed - quitting the player app")
                    self._stop_event.set()
        except OSError:
            # Most likely unplugged - drop it so a later rescan can pick it
            # back up if it's reconnected (possibly at a different path).
            self._watched_paths.discard(device.path)
            self._names.pop(device.path, None)
            self._publish_names()
