"""Transport-agnostic playback control: wraps the movie library and mpv,
independent of whether commands arrive over BLE or plain HTTP."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import List, Optional

from ..models.idle_screen import render_idle_screen
from ..models.library import MovieLibrary
from ..models.player import MpvController
from ..models.protocol import Command, Movie, Opcode, PlaybackState, PlaybackStatus

logger = logging.getLogger(__name__)

# How much to dim the picture (via MpvController.set_dim) while paused -
# separate from, and much lighter than, IdleDimService's dim for extended
# inactivity with nothing selected at all.
PAUSE_DIM_PERCENT = 10


class PlaybackController:
    def __init__(self, library: MovieLibrary, player: MpvController, state_path: Optional[Path] = None):
        self.library = library
        self.player = player
        self._current_movie_id: Optional[int] = None
        # Where the currently-selected movie/position/paused-ness gets
        # persisted (see _save_playback_state/restore_last_playback) so a
        # power loss or reboot can pick back up instead of dropping back to
        # the idle screen. None (the default, and what every existing test
        # here gets) disables persistence entirely rather than writing
        # somewhere real - main.py is the only caller that passes one.
        self._state_path = state_path
        # Set/cleared by TranscodeService, read by ble_service.py's status
        # poll loop to notify the app - a shared hub between the two rather
        # than a direct dependency between them, mirroring how is_idle
        # already works in the other direction (TranscodeService reads it).
        self.currently_transcoding_movie_id: Optional[int] = None
        # Set/cleared by HomeServerSync, read by web_service.py's /api/status
        # - same hub pattern as currently_transcoding_movie_id above. A
        # title, not an id: the movie doesn't have a local id yet while
        # it's still downloading (library._stable_id only ever runs against
        # files that already exist on disk).
        self.currently_syncing_movie_title: Optional[str] = None
        # Updated on every command (remote or local) - IdleDimService reads
        # this to know how long it's been since anything happened, so it
        # knows when to dim the idle screen. monotonic(), not wall-clock
        # time, since it only needs to measure elapsed duration and can't
        # be upset by clock adjustments.
        self.last_input_at: float = time.monotonic()

    @property
    def movies(self) -> List[Movie]:
        return self.library.movies

    @property
    def is_idle(self) -> bool:
        """Whether anything is currently selected to play - checked by
        TranscodeService before starting (or continuing) a background
        transcode, since that's CPU-intensive enough to compete directly
        with playback decode on this device's single core."""
        return self._current_movie_id is None

    async def handle_command(self, cmd: Command) -> None:
        self.last_input_at = time.monotonic()
        if cmd.opcode == Opcode.SELECT_MOVIE and cmd.argument is not None:
            if not any(movie.id == cmd.argument for movie in self.movies):
                # A client's cached movie list can be stale relative to what
                # the library currently has (e.g. mid-rescan, or a movie
                # removed since) - not selecting anything is a much better
                # failure mode than an unhandled KeyError deep in
                # library.playable_path_for taking the whole request down.
                logger.warning("Ignoring select_movie for unknown movie id %d", cmd.argument)
                return
            self._current_movie_id = cmd.argument
            await self.player.load(self.library.playable_path_for(cmd.argument))
            # Immediate feedback rather than waiting for IdleDimService's
            # next poll to notice is_idle flipped and undo an idle dim.
            await self.player.set_dim(0)
        elif cmd.opcode == Opcode.PLAY:
            await self.player.play()
            await self.player.hide_pause_icon()
            await self.player.set_dim(0)
        elif cmd.opcode == Opcode.PAUSE:
            await self.player.pause()
            await self.player.show_pause_icon()
            await self.player.set_dim(PAUSE_DIM_PERCENT)
        elif cmd.opcode == Opcode.STOP:
            await self.stop_and_show_idle_screen()
        elif cmd.opcode == Opcode.SEEK and cmd.argument is not None:
            await self.player.seek(cmd.argument)
        elif cmd.opcode == Opcode.SHUTDOWN:
            await self._shutdown()

    @staticmethod
    async def _shutdown() -> None:
        """Powers off the whole device. Not really "playback control", but
        routed through the same command channel as everything else since
        there's no separate system-command pathway and this is the only
        such action that exists. Needs sudo since the service itself runs
        unprivileged (see deploy/magicboxie-device.service.in) - relies on
        the Pi's default passwordless sudo for the setup user rather than
        provisioning a narrower rule, since that's already how this
        specific device is configured."""
        await asyncio.create_subprocess_exec("sudo", "systemctl", "poweroff")

    async def show_idle_screen(self) -> None:
        """Displays the thumbnail-grid home screen - the device's resting
        state whenever nothing is selected to play (at startup, and after
        stop_and_show_idle_screen()). Also the live "syncing" badge's only
        home: it's drawn into this same image (see idle_screen.py) since
        mpv can only ever show one static file at a time, not a separate
        overlay layer on top of it."""
        image_path = render_idle_screen(self.library, syncing_title=self.currently_syncing_movie_title)
        await self.player.show_image(image_path)

    async def stop_and_show_idle_screen(self) -> None:
        """What both an explicit stop command and the local keyboard's
        Escape key do - stop whatever's playing and return to the thumbnail
        grid, so the screen never just goes blank or freezes on the last
        frame."""
        await self.player.stop()
        self._current_movie_id = None
        self._clear_playback_state()
        # The pause icon/dim are a separate overlay layer from whatever's
        # loaded, so stopping while paused would otherwise leave them
        # visible over the idle screen.
        await self.player.hide_pause_icon()
        await self.player.set_dim(0)
        await self.show_idle_screen()

    async def refresh_status(self) -> PlaybackState:
        # Nothing selected - already known to be idle without asking mpv,
        # which would otherwise report "not idle" while the idle-screen
        # image itself is loaded (see MpvController.show_image). Persisted
        # state was already cleared at the point _current_movie_id became
        # None (below, or in stop_and_show_idle_screen), so there's nothing
        # left to do here.
        if self._current_movie_id is None:
            return PlaybackState.idle()

        idle = await self.player.get_idle()
        if idle:
            self._current_movie_id = None
            self._clear_playback_state()
            return PlaybackState.idle()

        paused = await self.player.get_paused()
        position = await self.player.get_position()
        # Called on every refresh - roughly once a second via ble_service's
        # status poll loop - so a power loss or ungraceful kill (the case
        # that actually matters here: this device has no graceful-shutdown
        # signal on real power loss) never loses more than ~1s of position.
        self._save_playback_state(self._current_movie_id, position, paused)
        return PlaybackState(
            status=PlaybackStatus.PAUSED if paused else PlaybackStatus.PLAYING,
            movie_id=self._current_movie_id,
            position_seconds=position,
        )

    def _save_playback_state(self, movie_id: int, position_seconds: int, paused: bool) -> None:
        if self._state_path is None:
            return
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            self._state_path.write_text(json.dumps({
                "movie_id": movie_id,
                "position_seconds": position_seconds,
                "paused": paused,
            }))
        except OSError:
            logger.warning("Failed to persist playback state to %s", self._state_path)

    def _clear_playback_state(self) -> None:
        if self._state_path is None:
            return
        try:
            self._state_path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Failed to clear persisted playback state at %s", self._state_path)

    async def restore_last_playback(self) -> bool:
        """Resumes whatever was selected the last time the device ran - see
        _save_playback_state above for where this gets written, continuously,
        while something's selected. Meant to be called once at startup,
        after the library has been scanned (a persisted movie id only means
        anything once it can be checked against a freshly-scanned library -
        see main.py's _run_library_scan). Returns whether it actually
        resumed something, so the caller knows whether to fall back to
        show_idle_screen() itself."""
        if self._state_path is None or not self._state_path.is_file():
            return False

        try:
            data = json.loads(self._state_path.read_text())
            movie_id = int(data["movie_id"])
            position_seconds = int(data["position_seconds"])
            paused = bool(data["paused"])
        except (OSError, ValueError, KeyError, TypeError):
            logger.warning("Failed to read persisted playback state from %s - ignoring", self._state_path)
            self._clear_playback_state()
            return False

        if not any(movie.id == movie_id for movie in self.movies):
            # The movie was deleted, or the library changed, since this was
            # written - same "stale reference" handling as an unknown
            # SELECT_MOVIE id in handle_command.
            logger.info("Persisted playback state names movie %d, no longer in the library - ignoring", movie_id)
            self._clear_playback_state()
            return False

        self._current_movie_id = movie_id
        await self.player.load(
            self.library.playable_path_for(movie_id),
            start_seconds=position_seconds,
            paused=paused,
        )
        if paused:
            await self.player.show_pause_icon()
            await self.player.set_dim(PAUSE_DIM_PERCENT)
        else:
            await self.player.set_dim(0)
        return True
