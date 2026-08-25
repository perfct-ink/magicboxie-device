"""Opportunistic sync with the home server (MagicBoxie-web): when the device
has internet, downloads any "ready" movies it doesn't have locally yet,
along with their metadata (title/description/year/duration). The device
spends most of its life offline, so a failed check-in (server unreachable,
DNS failure, etc.) is the normal case, not an error - see _run_home_sync in
main.py, which retries on an interval and just logs and moves on.

MagicBoxie-web speaks the real Jellyfin REST API (see its
internal/controllers/items_controller.go) plus a handful of additive
"MagicBoxie*"-prefixed fields real Jellyfin clients don't have -- status,
progress, and original filename -- which this sync relies on to decide
what's actually downloadable and how to name the local file.

Thumbnails aren't fetched here - once a movie's file lands locally, the next
MovieLibrary.scan() grabs a frame from it with ffmpeg like any other movie.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Callable, List, Optional

import aiohttp

from ..models.library import VIDEO_EXTENSIONS, MovieLibrary

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT_SECONDS = 10
_DOWNLOAD_CHUNK_BYTES = 1024 * 1024
_READY_STATUS = "ready"
_USER_ID = "1"  # MagicBoxie-web has one shared login, not per-user accounts
_TICKS_PER_SECOND = 10_000_000  # Jellyfin's RunTimeTicks unit is 100ns


class HomeServerSync:
    def __init__(
        self,
        library: MovieLibrary,
        base_url: str,
        password: str,
        on_progress: Optional[Callable[[Optional[str]], None]] = None,
        is_idle: Callable[[], bool] = lambda: True,
    ):
        self._library = library
        self._base_url = base_url.rstrip("/")
        self._password = password
        self._device_id = self._read_device_identifier()
        # Called with a movie's title when its download starts and None
        # when it ends (success, failure, or exception alike) - lets
        # main.py mirror "what's downloading right now" onto
        # PlaybackController for web_service.py's /api/status to report,
        # without this class needing to know PlaybackController exists.
        self._on_progress = on_progress or (lambda _title: None)
        # Same hub pattern as on_progress above - checked before starting a
        # check-in at all (see check_in()) so this never competes with
        # playback decode for network/disk I/O or (via the library.scan()
        # at the end) CPU, on a device with only one core to share between
        # them. Movies already downloaded are never abandoned mid-file for
        # this - see the _pending_metadata handling below for what happens
        # if playback starts partway through a cycle instead of before one.
        self._is_idle = is_idle
        # Movies downloaded in an earlier check-in whose library.scan() (and
        # metadata save) got deferred because playback started before this
        # cycle reached that point - retried on a later, idle cycle rather
        # than dropped, so a movie that finished downloading right as
        # someone hit play doesn't end up stuck unscanned (visible in
        # `movies` with no duration/artwork) or missing its title/year/
        # description from the home server indefinitely.
        self._pending_metadata: List[dict] = []

    async def check_in(self) -> None:
        if not self._is_idle():
            logger.info("Home server check-in: skipped while playback is active")
            return

        async with aiohttp.ClientSession() as session:
            try:
                remote_movies = await self._register_and_list_movies(session)
            except (aiohttp.ClientError, TimeoutError) as exc:
                logger.info("Home server check-in: couldn't list movies (%s)", exc)
                return

            existing_titles = {movie.title for movie in self._library.movies}
            to_download = [m for m in remote_movies if m["Name"] not in existing_titles]
            if to_download:
                # /devices/register (the common path above) lists movies
                # without authenticating, but the video bytes themselves are
                # served from an authenticated route - unlike the metadata-
                # only registration listing, anonymous access to stream
                # arbitrary video would be a real information-disclosure
                # concern. A token is needed here regardless of which path
                # found the movies to download.
                token = await self._authenticate(session)
                if token is None:
                    logger.info(
                        "Home server check-in: found %d new movie(s) but couldn't authenticate to download them",
                        len(to_download),
                    )
                else:
                    headers = {"Authorization": f"Bearer {token}"}
                    logger.info("Home server check-in: downloading %d new movie(s)", len(to_download))
                    # One at a time, deliberately: these are large files over
                    # a slow WiFi/USB-tethered link, and _on_progress only
                    # ever tracks a single in-flight title - downloading
                    # several concurrently would also just contend with each
                    # other for the same bandwidth with nothing gained.
                    for movie in to_download:
                        if not self._is_idle():
                            # Playback started partway through this cycle -
                            # let whatever's already mid-download finish
                            # (an aborted large download over a slow link is
                            # pure waste) but don't start any more.
                            logger.info("Home server check-in: pausing further downloads - playback started")
                            break
                        self._on_progress(movie["Name"])
                        try:
                            if await self._download_movie(session, movie, headers):
                                self._pending_metadata.append(movie)
                        finally:
                            self._on_progress(None)

            if not self._pending_metadata:
                if not to_download:
                    logger.info("Home server check-in: nothing new")
                return

            if not self._is_idle():
                logger.info("Home server check-in: deferring library scan until playback stops")
                return

            self._library.scan()
            for remote_movie in self._pending_metadata:
                self._save_metadata(remote_movie)
            self._pending_metadata = []

    async def _register_and_list_movies(self, session: aiohttp.ClientSession) -> List[dict]:
        try:
            async with session.post(
                f"{self._base_url}/devices/register",
                json={"device_id": self._device_id},
                timeout=aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT_SECONDS),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    items = data.get("items") or data.get("Items") or []
                    return [self._normalize_registered_movie(item) for item in items]
                logger.info("Home server check-in: registration endpoint responded HTTP %d", resp.status)
        except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
            logger.info("Home server check-in: registration endpoint unavailable (%s)", exc)

        token = await self._authenticate(session)
        if token is None:
            return []
        headers = {"Authorization": f"Bearer {token}"}
        return await self._list_ready_movies(session, headers)

    async def _authenticate(self, session: aiohttp.ClientSession):
        try:
            async with session.post(
                f"{self._base_url}/Users/AuthenticateByName",
                json={"Username": "magicboxie-device", "Pw": self._password},
                timeout=aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT_SECONDS),
            ) as resp:
                if resp.status != 200:
                    logger.info("Home server check-in: login failed (HTTP %d)", resp.status)
                    return None
                data = await resp.json()
                return data.get("AccessToken")
        except (aiohttp.ClientError, TimeoutError) as exc:
            logger.info("Home server check-in: unreachable (%s)", exc)
            return None

    async def _list_ready_movies(self, session: aiohttp.ClientSession, headers: dict) -> List[dict]:
        async with session.get(
            f"{self._base_url}/Users/{_USER_ID}/Items",
            params={"IncludeItemTypes": "Movie", "Recursive": "true"},
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT_SECONDS),
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
        items = data.get("Items", [])
        return [item for item in items if item.get("MagicBoxieStatus") == _READY_STATUS]

    async def _download_movie(self, session: aiohttp.ClientSession, movie: dict, headers: dict) -> bool:
        dest_path = self._library.root / self._local_filename(movie)
        if dest_path.exists():
            return False

        logger.info("Home server check-in: downloading %r", movie["Name"])
        try:
            async with session.get(
                f"{self._base_url}/Videos/{movie['Id']}/stream",
                params={"static": "true"},
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=None),
            ) as resp:
                if resp.status != 200:
                    logger.warning("Home server check-in: download of %r failed (HTTP %d)", movie["Name"], resp.status)
                    return False
                with dest_path.open("wb") as f:
                    async for chunk in resp.content.iter_chunked(_DOWNLOAD_CHUNK_BYTES):
                        f.write(chunk)
        except (aiohttp.ClientError, TimeoutError, OSError) as exc:
            logger.warning("Home server check-in: download of %r failed (%s)", movie["Name"], exc)
            dest_path.unlink(missing_ok=True)
            return False
        return True

    def _save_metadata(self, remote_movie: dict) -> None:
        local_movie = next(
            (m for m in self._library.movies if m.title == remote_movie["Name"]), None
        )
        if local_movie is None:
            return
        self._library.save_metadata(
            local_movie.id,
            title=remote_movie["Name"],
            description=remote_movie.get("Overview") or "",
            year=int(remote_movie.get("ProductionYear") or 0),
            duration_seconds=int((remote_movie.get("RunTimeTicks") or 0) / _TICKS_PER_SECOND),
        )

    @staticmethod
    def _normalize_registered_movie(movie: dict) -> dict:
        return {
            "Id": movie.get("Id") or movie.get("id"),
            "Name": movie.get("Name") or movie.get("name"),
            "Overview": movie.get("Overview") or movie.get("description") or "",
            "ProductionYear": movie.get("ProductionYear") or movie.get("year") or 0,
            "RunTimeTicks": movie.get("RunTimeTicks") or movie.get("duration_seconds", 0) * _TICKS_PER_SECOND,
            "MagicBoxieOriginalFilename": movie.get("MagicBoxieOriginalFilename") or movie.get("filename") or "",
            "MagicBoxieStatus": movie.get("MagicBoxieStatus") or movie.get("status") or _READY_STATUS,
        }

    @staticmethod
    def _read_device_identifier() -> str:
        override = os.environ.get("MAGICBOXIE_DEVICE_ID")
        if override:
            return override

        serial_path = Path("/sys/firmware/devicetree/base/serial-number")
        if serial_path.exists():
            try:
                return serial_path.read_text(encoding="utf-8").strip()
            except OSError:
                pass

        cpuinfo_path = Path("/proc/cpuinfo")
        if cpuinfo_path.exists():
            try:
                for line in cpuinfo_path.read_text(encoding="utf-8").splitlines():
                    if line.startswith("Serial"):
                        return line.split(":", 1)[1].strip()
            except OSError:
                pass

        machine_id_path = Path("/etc/machine-id")
        if machine_id_path.exists():
            try:
                return machine_id_path.read_text(encoding="utf-8").strip()
            except OSError:
                pass

        return "unknown-device"

    @staticmethod
    def _local_filename(movie: dict) -> str:
        original = Path(movie.get("MagicBoxieOriginalFilename") or "")
        extension = original.suffix.lower() if original.suffix.lower() in VIDEO_EXTENSIONS else ".mp4"
        # Titles come from the home server's own TMDB matching/filename
        # parsing and may contain "/" (e.g. "Fast/Furious") - not valid in a
        # single path component, so it gets swapped for a dash.
        safe_title = movie["Name"].replace("/", "-").lstrip(".")
        return f"{safe_title}{extension}"
