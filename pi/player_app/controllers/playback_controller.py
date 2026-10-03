"""Transport-agnostic playback control: wraps the movie library and mpv,
independent of whether commands arrive over BLE or plain HTTP."""
from __future__ import annotations

import asyncio
import logging
import random
import time
from pathlib import Path
from typing import List, Optional

from ..update_status import read_status
from ..storage import read_dict, run_io, write_json
from ..models.idle_screen import render_idle_screen
from ..models.library import MovieLibrary
from ..models.player import MpvController
from ..models.protocol import Command, Movie, Opcode, PlaybackState, PlaybackStatus

logger = logging.getLogger(__name__)

# How much to dim the picture (via MpvController.set_dim) while paused -
# separate from, and much lighter than, IdleDimService's dim for extended
# inactivity with nothing selected at all.
PAUSE_DIM_PERCENT = 10
PLAYBACK_SAVE_INTERVAL_SECONDS = 5


class PlaybackController:
    def __init__(self, library: MovieLibrary, player: MpvController, state_path: Optional[Path] = None):
        self.library = library
        self.player = player
        self._current_movie_id: Optional[int] = None
        self._playback_lock: Optional[asyncio.Lock] = None
        self._render_lock: Optional[asyncio.Lock] = None
        self._startup_input_at = time.monotonic()
        # Where the currently-selected movie/position/paused-ness gets
        # persisted (see _save_playback_state/restore_last_playback) so a
        # power loss or reboot can pick back up instead of dropping back to
        # the idle screen. None (the default, and what every existing test
        # here gets) disables persistence entirely rather than writing
        # somewhere real - main.py is the only caller that passes one.
        self._last_saved_state = None
        self._last_checkpoint_at = None
        self._last_checkpoint_movie_id = None
        self._state_path = state_path
        self._positions_path = state_path.with_name("movie_positions.json") if state_path else None
        raw_positions = read_dict(self._positions_path) if self._positions_path else {}
        self._positions = {key: value for key, value in raw_positions.items()
                           if key.isdecimal() and type(value) is int and 0 <= value <= 0xFFFFFFFF}
        self._failed_movie_ids = set()
        self._software_update_phase = None

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
        # Names of attached keyboards, maintained by KeyboardService and
        # shown in the idle screen's footer hint.
        self.keyboard_names: list[str] = []
        # Startup/update progress text (see update_status.read_message),
        # kept fresh by main._run_status_message and drawn on the idle screen.
        self.status_message: Optional[str] = None
        # Updated on every command (remote or local) - IdleDimService reads
        # this to know how long it's been since anything happened, so it
        # knows when to dim the idle screen. monotonic(), not wall-clock
        # time, since it only needs to measure elapsed duration and can't
        # be upset by clock adjustments.
        self.last_input_at: float = self._startup_input_at

    @property
    def activity_message(self) -> Optional[str]:
        """What the device is busy doing, for the big banner on the idle
        screen: update/internet progress first, then downloads and
        transcodes."""
        if self.status_message:
            return self.status_message
        if self.currently_syncing_movie_title:
            return f"Downloading {self.currently_syncing_movie_title}"
        movie_id = self.currently_transcoding_movie_id
        if movie_id is not None:
            movie = next((m for m in self.movies if m.id == movie_id), None)
            return f"Optimizing {movie.title}" if movie else "Optimizing a movie"
        return None

    @property
    def update_status(self) -> Optional[str]:
        phase = self._software_update_phase
        if phase == "waiting":
            return "Update ready — waiting for movie to finish"
        if phase == "installing":
            return "Updating device software"
        if self.currently_syncing_movie_title:
            return "Updating movies"
        return None

    @property
    def _lock(self) -> asyncio.Lock:
        # Python 3.9 binds locks to the loop at construction time.
        if self._playback_lock is None:
            self._playback_lock = asyncio.Lock()
        return self._playback_lock

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
        async with self._lock:
            await self._handle_command(cmd)

    async def _handle_command(self, cmd: Command) -> None:
        if cmd.opcode == Opcode.SELECT_MOVIE and cmd.argument is not None:
            if not any(movie.id == cmd.argument for movie in self.movies):
                # A client's cached movie list can be stale relative to what
                # the library currently has (e.g. mid-rescan, or a movie
                # removed since) - not selecting anything is a much better
                # failure mode than an unhandled KeyError deep in
                # library.playable_path_for taking the whole request down.
                logger.warning("Ignoring select_movie for unknown movie id %d", cmd.argument)
                return
            await self._remember_position()
            self._current_movie_id = cmd.argument
            self._failed_movie_ids.discard(cmd.argument)
            await self.player.load(self.library.playable_path_for(cmd.argument),
                                   start_seconds=self._resume_position(cmd.argument))
            await self.player.hide_pause_icon()
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
            await self._stop_and_show_idle_screen()
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
        await asyncio.create_subprocess_exec("sudo", "-n", "/usr/bin/systemctl", "poweroff")

    async def show_idle_screen(self) -> None:
        """Displays the thumbnail-grid home screen - the device's resting
        state whenever nothing is selected to play (at startup, and after
        stop_and_show_idle_screen()). Also the live "syncing" badge's only
        home: it's drawn into this same image (see idle_screen.py) since
        mpv can only ever show one static file at a time, not a separate
        overlay layer on top of it."""
        if not self.is_idle:
            return
        image_path = await self._render_idle_screen()
        async with self._lock:
            if self.is_idle:
                await self.player.show_image(image_path)

    async def _render_idle_screen(self) -> Path:
        if self._render_lock is None:
            self._render_lock = asyncio.Lock()
        async with self._render_lock:
            return await run_io(render_idle_screen, self.library,
                                syncing_title=self.currently_syncing_movie_title,
                                keyboard_names=list(self.keyboard_names),
                                status_message=self.activity_message)

    async def _show_idle_screen_locked(self) -> None:
        image_path = await self._render_idle_screen()
        await self.player.show_image(image_path)

    async def stop_and_show_idle_screen(self) -> None:
        self.last_input_at = time.monotonic()
        async with self._lock:
            await self._stop_and_show_idle_screen()

    async def _stop_and_show_idle_screen(self) -> None:
        """What both an explicit stop command and the local keyboard's
        Escape key do - stop whatever's playing and return to the thumbnail
        grid, so the screen never just goes blank or freezes on the last
        frame."""
        await self._remember_position()
        await self.player.stop()
        self._current_movie_id = None
        await run_io(self._clear_playback_state)
        # The pause icon/dim are a separate overlay layer from whatever's
        # loaded, so stopping while paused would otherwise leave them
        # visible over the idle screen.
        await self.player.hide_pause_icon()
        await self.player.set_dim(0)
        await self._show_idle_screen_locked()

    async def start_random_playback(self) -> None:
        """Start once the library is ready, unless startup input took priority."""
        async with self._lock:
            if self.last_input_at != self._startup_input_at or not self.is_idle:
                return
            await run_io(self._clear_playback_state)
            if not await self._play_random_movie():
                await self._show_idle_screen_locked()

    async def _play_random_movie(self, exclude_id: Optional[int] = None) -> bool:
        self._software_update_phase = await run_io(read_status)
        if self._software_update_phase is not None:
            return False
        candidates = [movie for movie in self.movies
                      if movie.id != exclude_id and movie.id not in self._failed_movie_ids]
        if not candidates:
            return False
        movie = random.choice(candidates)
        self._current_movie_id = movie.id
        position = self._resume_position(movie.id)
        await self.player.load(self.library.playable_path_for(movie.id), start_seconds=position)
        await self.player.hide_pause_icon()
        await self.player.set_dim(0)
        await run_io(self._save_playback_state, movie.id, position, False)
        return True

    async def refresh_status(self) -> PlaybackState:
        async with self._lock:
            return await self._refresh_status()

    async def _refresh_status(self) -> PlaybackState:
        self._software_update_phase = await run_io(read_status)
        # Nothing selected - already known to be idle without asking mpv,
        # which would otherwise report "not idle" while the idle-screen
        # image itself is loaded (see MpvController.show_image). Persisted
        # state was already cleared at the point _current_movie_id became
        # None (below, or in stop_and_show_idle_screen), so there's nothing
        # left to do here.
        if self._current_movie_id is None:
            return PlaybackState.idle()

        if self.player.failed:
            failed_id = self._current_movie_id
            retry_original = False
            if self.player.media_failed:
                retry_original = await run_io(self.library.quarantine_failed_playback, failed_id)
            if retry_original:
                await self.player.load(self.library.playable_path_for(failed_id),
                                       start_seconds=self._resume_position(failed_id))
            else:
                self._failed_movie_ids.add(failed_id)
                logger.warning("Skipping movie %s after playback failure", failed_id)
                if not await self._play_random_movie(exclude_id=failed_id):
                    self._current_movie_id = None
                    await self._stop_and_show_idle_screen()
                    return PlaybackState.idle()

        if self.player.finished:
            previous_movie_id = self._current_movie_id
            await run_io(self._set_position, previous_movie_id, 0)
            # Do not save the last frame again if there is no next movie.
            self._current_movie_id = None
            if not await self._play_random_movie(exclude_id=previous_movie_id):
                await self._stop_and_show_idle_screen()
                return PlaybackState.idle()

        idle = await self.player.get_idle()
        if idle:
            self._current_movie_id = None
            await run_io(self._clear_playback_state)
            return PlaybackState.idle()

        if self.player.loading:
            return PlaybackState(status=PlaybackStatus.PLAYING, movie_id=self._current_movie_id,
                                 position_seconds=self._resume_position(self._current_movie_id))

        paused = await self.player.get_paused()
        position = await self.player.get_position()
        # Status stays live; disk checkpoints are limited to every five seconds.
        await run_io(self._save_playback_state, self._current_movie_id, position, paused)
        return PlaybackState(
            status=PlaybackStatus.PAUSED if paused else PlaybackStatus.PLAYING,
            movie_id=self._current_movie_id,
            position_seconds=position,
        )

    def _resume_position(self, movie_id: int) -> int:
        position = self._positions.get(str(movie_id), 0)
        movie = next((movie for movie in self.movies if movie.id == movie_id), None)
        return 0 if movie and movie.duration_seconds and position >= movie.duration_seconds else position

    def _set_position(self, movie_id: int, position: int) -> None:
        position = max(0, min(position, 0xFFFFFFFF))
        key = str(movie_id)
        if self._positions.get(key) == position:
            return
        self._positions[key] = position
        if self._positions_path:
            try:
                write_json(self._positions_path, self._positions)
            except OSError:
                logger.warning("Could not save movie positions")

    async def _remember_position(self) -> None:
        if self._current_movie_id is not None and not self.player.finished and not self.player.failed and not self.player.loading:
            position = await self.player.get_position()
            await run_io(self._set_position, self._current_movie_id, position)

    def _save_playback_state(self, movie_id: int, position_seconds: int, paused: bool) -> None:
        now = time.monotonic()
        if (self._last_checkpoint_movie_id == movie_id
                and self._last_checkpoint_at is not None
                and now - self._last_checkpoint_at < PLAYBACK_SAVE_INTERVAL_SECONDS):
            return
        self._last_checkpoint_at = now
        self._last_checkpoint_movie_id = movie_id
        self._set_position(movie_id, position_seconds)
        state = (movie_id, position_seconds, paused)
        if self._state_path is None or state == self._last_saved_state:
            return
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            write_json(self._state_path, {
                "movie_id": movie_id,
                "position_seconds": position_seconds,
                "paused": paused,
            })
            self._last_saved_state = state
        except OSError:
            logger.warning("Failed to persist playback state to %s", self._state_path)

    def _clear_playback_state(self) -> None:
        self._last_saved_state = None
        self._last_checkpoint_at = None
        self._last_checkpoint_movie_id = None
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
            data = await run_io(read_dict, self._state_path)
            movie_id = int(data["movie_id"])
            position_seconds = int(data["position_seconds"])
            paused = bool(data["paused"])
        except (OSError, ValueError, KeyError, TypeError):
            logger.warning("Failed to read persisted playback state from %s - ignoring", self._state_path)
            await run_io(self._clear_playback_state)
            return False

        if not any(movie.id == movie_id for movie in self.movies):
            # The movie was deleted, or the library changed, since this was
            # written - same "stale reference" handling as an unknown
            # SELECT_MOVIE id in handle_command.
            logger.info("Persisted playback state names movie %d, no longer in the library - ignoring", movie_id)
            await run_io(self._clear_playback_state)
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
