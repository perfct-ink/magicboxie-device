"""Plain HTTP transport for dev/testing without Bluetooth.

Enable with MAGICBOXIE_TRANSPORT=http (see main.py). Mirrors the same
PlaybackController the BLE GATT service uses, just over JSON/REST instead of
GATT characteristics - useful when there's no BlueZ available (e.g. running
in Docker Desktop on macOS) or no BLE-capable client at hand.
"""
from __future__ import annotations

import logging
from pathlib import Path

from aiohttp import web

from ..controllers.playback_controller import PlaybackController
from ..models.library import VIDEO_EXTENSIONS
from ..models.protocol import API_VERSION, Command, Movie, Opcode
from ..util import cpu_temperature_celsius, get_throttle_status, local_ip

_METADATA_FIELDS = ("title", "description", "year", "duration_seconds")

logger = logging.getLogger(__name__)

_OPCODE_BY_NAME = {opcode.name.lower(): opcode for opcode in Opcode}
_CONTROLLER_KEY = web.AppKey("controller", PlaybackController)

# Generous but bounded - movie files are large, but this still guards against
# a truly unbounded upload filling the disk.
_MAX_UPLOAD_BYTES = 1024 ** 3 * 20  # 20GB

_UPLOAD_CHUNK_BYTES = 1024 * 1024  # 1MB


def create_app(controller: PlaybackController) -> web.Application:
    app = web.Application(client_max_size=_MAX_UPLOAD_BYTES)
    app[_CONTROLLER_KEY] = controller

    app.router.add_get("/api/movies", _get_movies)
    app.router.add_post("/api/movies", _post_movie)
    app.router.add_delete("/api/movies/{id}", _delete_movie)
    app.router.add_post("/api/rescan", _post_rescan)
    app.router.add_get("/api/movies/{id}/thumbnail", _get_thumbnail)
    app.router.add_post("/api/movies/{id}/thumbnail", _post_thumbnail)
    app.router.add_post("/api/movies/{id}/metadata", _post_metadata)
    app.router.add_get("/api/status", _get_status)
    app.router.add_post("/api/command", _post_command)
    app.router.add_get("/api/version", _get_version)
    return app


def _movie_payload(controller: PlaybackController, movie: Movie) -> dict:
    """Base fields from the filesystem scan, overlaid with any
    phone-supplied metadata (title/description/year/duration) fetched
    online - see _post_metadata."""
    payload = {
        "id": movie.id,
        "title": movie.title,
        "duration_seconds": movie.duration_seconds,
        "description": None,
        "year": None,
        # Whether TranscodeService still needs to (re-)encode this movie -
        # lets the app show a "not yet optimized for this device" badge
        # distinct from the transcode_status characteristic's "actively
        # transcoding right now" signal.
        "needs_transcoding": not controller.library.transcode_path_for(movie.id).exists(),
    }
    payload.update(controller.library.metadata_for(movie.id))
    return payload


def _movies_payload(controller: PlaybackController) -> list:
    return [_movie_payload(controller, movie) for movie in controller.movies]


async def _get_movies(request: web.Request) -> web.Response:
    controller = request.app[_CONTROLLER_KEY]
    return web.json_response(_movies_payload(controller))


async def _post_rescan(request: web.Request) -> web.Response:
    """Re-scans the movies directory - probing durations and generating any
    thumbnails that don't already exist - so files dropped onto the
    filesystem directly (outside POST /api/movies) show up without
    restarting the daemon."""
    controller = request.app[_CONTROLLER_KEY]
    controller.library.scan()
    return web.json_response(_movies_payload(controller))


async def _delete_movie(request: web.Request) -> web.Response:
    """Permanently removes a movie from the device (see
    MovieLibrary.delete) - stops playback first if it's the one currently
    selected, so its file isn't yanked out from under mpv mid-playback."""
    controller = request.app[_CONTROLLER_KEY]
    try:
        movie_id = int(request.match_info["id"])
    except ValueError:
        return web.json_response({"error": "invalid movie id"}, status=400)

    if not any(movie.id == movie_id for movie in controller.movies):
        return web.json_response({"error": "unknown movie id"}, status=404)

    state = await controller.refresh_status()
    if state.movie_id == movie_id:
        await controller.stop_and_show_idle_screen()

    controller.library.delete(movie_id)
    return web.json_response({"ok": True})


async def _post_movie(request: web.Request) -> web.Response:
    """Accepts a whole movie file (e.g. shared into the iOS app from Photos/
    Dropbox/Files) and adds it to the library. Streams the body straight to
    disk in chunks rather than buffering it all in memory - these are large
    files and this runs on a Pi. The phone is expected to check GET /movies
    first and only call this when the title isn't already present; this
    endpoint itself just rejects an exact filename collision as a safety net.
    """
    controller = request.app[_CONTROLLER_KEY]
    filename = request.headers.get("X-Filename")
    if not filename or "/" in filename or filename.startswith("."):
        return web.json_response({"error": "missing or invalid X-Filename header"}, status=400)

    extension = Path(filename).suffix.lower()
    if extension not in VIDEO_EXTENSIONS:
        return web.json_response({"error": f"unsupported file extension {extension!r}"}, status=400)

    dest_path = controller.library.root / filename
    if dest_path.exists():
        return web.json_response({"error": "a file with this name already exists"}, status=409)

    bytes_written = 0
    try:
        with dest_path.open("wb") as f:
            async for chunk in request.content.iter_chunked(_UPLOAD_CHUNK_BYTES):
                f.write(chunk)
                bytes_written += len(chunk)
    except Exception:
        dest_path.unlink(missing_ok=True)
        raise

    if bytes_written == 0:
        dest_path.unlink(missing_ok=True)
        return web.json_response({"error": "empty body"}, status=400)

    controller.library.scan()
    title = Path(filename).stem
    movie = next((m for m in controller.movies if m.title == title), None)
    if movie is None:
        # Scanned but didn't come back with this exact title - still saved,
        # just report it generically rather than failing the upload outright.
        return web.json_response(
            {"id": None, "title": title, "duration_seconds": 0, "description": None, "year": None},
            status=201,
        )

    return web.json_response(_movie_payload(controller, movie), status=201)


async def _get_thumbnail(request: web.Request) -> web.StreamResponse:
    controller = request.app[_CONTROLLER_KEY]
    try:
        movie_id = int(request.match_info["id"])
    except ValueError:
        return web.json_response({"error": "invalid movie id"}, status=400)

    thumbnail_path = controller.library.thumbnail_path_for(movie_id)
    if thumbnail_path is None:
        return web.json_response({"error": "no thumbnail for this movie"}, status=404)
    return web.FileResponse(thumbnail_path)


async def _post_thumbnail(request: web.Request) -> web.Response:
    """Accepts a phone-supplied "official" thumbnail (e.g. from TMDB) and
    caches it, overwriting the local ffmpeg frame grab, so other devices
    that connect later get it too without needing their own internet access."""
    controller = request.app[_CONTROLLER_KEY]
    try:
        movie_id = int(request.match_info["id"])
    except ValueError:
        return web.json_response({"error": "invalid movie id"}, status=400)

    if not any(movie.id == movie_id for movie in controller.movies):
        return web.json_response({"error": "unknown movie id"}, status=404)

    data = await request.read()
    if not data:
        return web.json_response({"error": "empty body"}, status=400)

    controller.library.save_uploaded_thumbnail(movie_id, data)
    return web.json_response({"ok": True})


async def _post_metadata(request: web.Request) -> web.Response:
    """Accepts phone-supplied metadata (title/description/year/duration -
    e.g. fetched from TMDB) and merges it into what's cached for this movie,
    overriding the filename/ffprobe-derived values in GET /api/movies."""
    controller = request.app[_CONTROLLER_KEY]
    try:
        movie_id = int(request.match_info["id"])
    except ValueError:
        return web.json_response({"error": "invalid movie id"}, status=400)

    movie = next((m for m in controller.movies if m.id == movie_id), None)
    if movie is None:
        return web.json_response({"error": "unknown movie id"}, status=404)

    payload = await request.json()
    fields = {}
    for key in ("title", "description"):
        if key in payload:
            fields[key] = str(payload[key])
    for key in ("year", "duration_seconds"):
        if key in payload:
            try:
                fields[key] = int(payload[key])
            except (TypeError, ValueError):
                return web.json_response({"error": f"{key} must be an integer"}, status=400)

    if not fields:
        return web.json_response(
            {"error": f"body must include at least one of: {', '.join(_METADATA_FIELDS)}"}, status=400
        )

    controller.library.save_metadata(movie_id, **fields)
    return web.json_response(_movie_payload(controller, movie))


async def _get_status(request: web.Request) -> web.Response:
    controller = request.app[_CONTROLLER_KEY]
    state = await controller.refresh_status()
    throttle = await get_throttle_status()
    return web.json_response({
        "status": state.status.name.lower(),
        "movie_id": state.movie_id,
        "position_seconds": state.position_seconds,
        "syncing_movie_title": controller.currently_syncing_movie_title,
        "cpu_temperature_celsius": cpu_temperature_celsius(),
        "under_voltage": throttle.under_voltage if throttle else None,
        "throttled": throttle.throttled if throttle else None,
    })


async def _get_version(request: web.Request) -> web.Response:
    return web.json_response({"api_version": API_VERSION, "ip_address": local_ip()})


async def _post_command(request: web.Request) -> web.Response:
    controller = request.app[_CONTROLLER_KEY]
    payload = await request.json()
    opcode_name = str(payload.get("opcode", "")).lower()
    opcode = _OPCODE_BY_NAME.get(opcode_name)
    if opcode is None:
        return web.json_response({"error": f"unknown opcode {opcode_name!r}"}, status=400)

    argument = payload.get("argument")
    cmd = Command(opcode=opcode, argument=None if argument is None else int(argument))
    await controller.handle_command(cmd)
    return web.json_response({"ok": True})
