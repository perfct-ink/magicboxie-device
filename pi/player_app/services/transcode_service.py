"""Background transcoding: this device's CPU (a Pi Zero W - single ARM11
core, no NEON) can't decode the original H.264 High-profile source files in
real time. Re-encodes each movie, one at a time, to a profile/resolution
that CPU can actually keep up with - only while nothing is playing, since
transcoding is itself CPU-intensive enough to compete directly with
playback decode on the same single core."""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from ..storage import publish_file, run_io
from ..controllers.playback_controller import PlaybackController
from ..models.protocol import Movie
from ..util import sleep_unless_stopped

logger = logging.getLogger(__name__)

# Baseline profile drops CABAC entropy coding and B-frames - the most
# CPU-expensive parts of H.264 decode - which matters far more for this
# device's decode cost than resolution alone. Scaled down some too for
# extra margin. ultrafast keeps the (also CPU-bound, on the same hardware)
# encode itself from taking forever - at the cost of encoded file size,
# which isn't the constrained resource here.
FFMPEG_ENCODE_ARGS = [
    "-c:v", "libx264", "-profile:v", "baseline", "-level", "3.0",
    "-preset", "ultrafast", "-vf", "scale=480:-2",
    "-c:a", "aac", "-b:a", "128k",
]

# How often to check whether playback has started while a transcode is
# in-flight - not instant, but keeps the ffmpeg subprocess from running
# more than about a second into a movie actually being selected.
PLAYBACK_CHECK_INTERVAL_SECONDS = 1.0

# How long to wait before checking again when there's nothing to do (e.g.
# everything's already transcoded, or the library hasn't scanned in yet).
IDLE_POLL_INTERVAL_SECONDS = 5.0


class TranscodeService:
    def __init__(self, controller: PlaybackController):
        self._controller = controller
        # A movie ffmpeg can't encode (corrupt/incomplete download, a codec
        # quirk it rejects, etc.) would otherwise get retried every loop
        # iteration forever - _next_movie_needing_transcode has no other way
        # to tell "no transcoded file yet because it hasn't been tried" apart
        # from "no transcoded file yet because every attempt has failed".
        # Cleared on restart, so a fixed/re-downloaded file does get another
        # chance eventually.
        self._failed_movie_ids: set = set()

    async def run(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            movie = self._next_movie_needing_transcode() if self._controller.is_idle else None
            if movie is None:
                await sleep_unless_stopped(stop_event, IDLE_POLL_INTERVAL_SECONDS)
                continue
            try:
                await self._transcode(movie_id=movie.id)
            except Exception:
                # One bad file (missing ffmpeg, disk full, a codec ffmpeg
                # can't handle, whatever) shouldn't crash the whole daemon
                # or wedge every other movie behind it forever - log and
                # move on; the outer loop just tries the next one.
                logger.exception("Transcoding movie %d failed unexpectedly", movie.id)
                self._failed_movie_ids.add(movie.id)

    def _next_movie_needing_transcode(self) -> Optional[Movie]:
        for movie in self._controller.movies:
            if movie.id in self._failed_movie_ids:
                continue
            if not self._controller.library.transcode_path_for(movie.id).exists():
                return movie
        return None

    async def _transcode(self, movie_id: int) -> None:
        library = self._controller.library
        source = library.path_for(movie_id)
        dest = library.transcode_path_for(movie_id)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp_dest = dest.with_suffix(".partial" + dest.suffix)

        self._controller.currently_transcoding_movie_id = movie_id
        logger.info("Transcoding %s", source.name)
        process = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", str(source),
            *FFMPEG_ENCODE_ARGS,
            str(tmp_dest),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            while process.returncode is None:
                if not self._controller.is_idle:
                    logger.info("Playback started - pausing transcode of %s", source.name)
                    process.terminate()
                    await process.wait()
                    return
                try:
                    await asyncio.wait_for(process.wait(), timeout=PLAYBACK_CHECK_INTERVAL_SECONDS)
                except asyncio.TimeoutError:
                    continue

            if process.returncode == 0:
                await run_io(publish_file, tmp_dest, dest)
                logger.info("Finished transcoding %s", source.name)
                return

            stderr = await process.stderr.read()
            logger.warning(
                "Transcoding %s failed (exit %d): %s",
                source.name, process.returncode, stderr.decode(errors="replace"),
            )
            self._failed_movie_ids.add(movie_id)
        finally:
            # Covers every exit path uniformly: interrupted by playback,
            # ffmpeg failed, or something above raised unexpectedly. Never
            # leave a half-written file around for playable_path_for() to
            # trip over, or a zombie ffmpeg process behind.
            if process.returncode is None:
                process.kill()
                await process.wait()
            tmp_dest.unlink(missing_ok=True)
            self._controller.currently_transcoding_movie_id = None
