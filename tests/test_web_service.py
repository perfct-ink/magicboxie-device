import asyncio

from aiohttp.test_utils import TestClient, TestServer
from fakes import FakeLibrary, FakeMpv

from player_app.controllers.playback_controller import PlaybackController
from player_app.models import protocol
from player_app.models.library import MovieLibrary
from player_app.views.web_service import create_app


async def _make_client(library=None):
    controller = PlaybackController(library or FakeLibrary(), FakeMpv())
    app = create_app(controller)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client, controller


def test_get_movies():
    async def scenario():
        client, _ = await _make_client()
        try:
            resp = await client.get("/api/movies")
            assert resp.status == 200
            return await resp.json()
        finally:
            await client.close()

    data = asyncio.run(scenario())
    assert data == [
        {"id": 0, "title": "A", "duration_seconds": 100, "description": None, "year": None, "needs_transcoding": True},
        {"id": 1, "title": "B", "duration_seconds": 200, "description": None, "year": None, "needs_transcoding": True},
    ]


def test_get_movies_reports_needs_transcoding_false_once_a_transcoded_file_exists(tmp_path):
    async def scenario():
        library = FakeLibrary(transcode_dir=tmp_path)
        (tmp_path / "0.mp4").write_bytes(b"fake transcoded data")
        client, _ = await _make_client(library)
        try:
            resp = await client.get("/api/movies")
            return await resp.json()
        finally:
            await client.close()

    data = asyncio.run(scenario())
    by_id = {movie["id"]: movie for movie in data}
    assert by_id[0]["needs_transcoding"] is False
    assert by_id[1]["needs_transcoding"] is True


def test_get_version():
    async def scenario():
        client, _ = await _make_client()
        try:
            resp = await client.get("/api/version")
            assert resp.status == 200
            return await resp.json()
        finally:
            await client.close()

    data = asyncio.run(scenario())
    assert data["api_version"] == protocol.API_VERSION
    # Best-effort LAN IP - not asserting an exact value (depends on the
    # test host's own network config), just that it's a plausible one.
    assert data["ip_address"].count(".") == 3


def test_post_command_select_and_play_reflected_in_status():
    async def scenario():
        client, _ = await _make_client()
        try:
            resp = await client.post("/api/command", json={"opcode": "select_movie", "argument": 1})
            assert resp.status == 200
            resp = await client.get("/api/status")
            return await resp.json()
        finally:
            await client.close()

    data = asyncio.run(scenario())
    assert data["status"] == "playing"
    assert data["movie_id"] == 1


def test_status_reports_currently_syncing_movie_title():
    async def scenario():
        client, controller = await _make_client()
        try:
            controller.currently_syncing_movie_title = "Some Movie"
            resp = await client.get("/api/status")
            return await resp.json()
        finally:
            await client.close()

    data = asyncio.run(scenario())
    assert data["syncing_movie_title"] == "Some Movie"


def test_post_command_unknown_opcode_returns_400():
    async def scenario():
        client, _ = await _make_client()
        try:
            resp = await client.post("/api/command", json={"opcode": "not_a_real_command"})
            return resp.status
        finally:
            await client.close()

    assert asyncio.run(scenario()) == 400


def test_get_thumbnail_returns_file_bytes(tmp_path):
    thumbnail_path = tmp_path / "0.jpg"
    thumbnail_path.write_bytes(b"fake-jpeg-bytes")

    async def scenario():
        client, _ = await _make_client(FakeLibrary(thumbnail_paths={0: thumbnail_path}))
        try:
            resp = await client.get("/api/movies/0/thumbnail")
            assert resp.status == 200
            return await resp.read()
        finally:
            await client.close()

    assert asyncio.run(scenario()) == b"fake-jpeg-bytes"


def test_get_thumbnail_missing_returns_404():
    async def scenario():
        client, _ = await _make_client()
        try:
            resp = await client.get("/api/movies/0/thumbnail")
            return resp.status
        finally:
            await client.close()

    assert asyncio.run(scenario()) == 404


def test_get_thumbnail_invalid_id_returns_400():
    async def scenario():
        client, _ = await _make_client()
        try:
            resp = await client.get("/api/movies/not-a-number/thumbnail")
            return resp.status
        finally:
            await client.close()

    assert asyncio.run(scenario()) == 400


def test_post_thumbnail_upload_then_get_returns_uploaded_bytes(tmp_path):
    async def scenario():
        client, _ = await _make_client(FakeLibrary(thumbnail_dir=tmp_path))
        try:
            resp = await client.post("/api/movies/0/thumbnail", data=b"official-poster-bytes")
            assert resp.status == 200
            resp = await client.get("/api/movies/0/thumbnail")
            assert resp.status == 200
            return await resp.read()
        finally:
            await client.close()

    assert asyncio.run(scenario()) == b"official-poster-bytes"


def test_post_thumbnail_unknown_movie_returns_404():
    async def scenario():
        client, _ = await _make_client()
        try:
            resp = await client.post("/api/movies/99/thumbnail", data=b"bytes")
            return resp.status
        finally:
            await client.close()

    assert asyncio.run(scenario()) == 404


def test_post_thumbnail_invalid_id_returns_400():
    async def scenario():
        client, _ = await _make_client()
        try:
            resp = await client.post("/api/movies/not-a-number/thumbnail", data=b"bytes")
            return resp.status
        finally:
            await client.close()

    assert asyncio.run(scenario()) == 400


def test_post_thumbnail_empty_body_returns_400(tmp_path):
    async def scenario():
        client, _ = await _make_client(FakeLibrary(thumbnail_dir=tmp_path))
        try:
            resp = await client.post("/api/movies/0/thumbnail", data=b"")
            return resp.status
        finally:
            await client.close()

    assert asyncio.run(scenario()) == 400


def test_post_metadata_overrides_fields_in_movie_list():
    async def scenario():
        client, _ = await _make_client()
        try:
            resp = await client.post(
                "/api/movies/0/metadata",
                json={"title": "Alpha", "description": "A movie.", "year": 1999},
            )
            assert resp.status == 200
            body = await resp.json()

            resp = await client.get("/api/movies")
            return body, await resp.json()
        finally:
            await client.close()

    post_body, movies = asyncio.run(scenario())
    expected = {
        "id": 0,
        "title": "Alpha",
        "duration_seconds": 100,
        "description": "A movie.",
        "year": 1999,
        "needs_transcoding": True,
    }
    assert post_body == expected
    assert movies[0] == expected
    assert movies[1] == {
        "id": 1, "title": "B", "duration_seconds": 200, "description": None, "year": None, "needs_transcoding": True,
    }


def test_post_metadata_unknown_movie_returns_404():
    async def scenario():
        client, _ = await _make_client()
        try:
            resp = await client.post("/api/movies/99/metadata", json={"title": "X"})
            return resp.status
        finally:
            await client.close()

    assert asyncio.run(scenario()) == 404


def test_post_metadata_invalid_id_returns_400():
    async def scenario():
        client, _ = await _make_client()
        try:
            resp = await client.post("/api/movies/not-a-number/metadata", json={"title": "X"})
            return resp.status
        finally:
            await client.close()

    assert asyncio.run(scenario()) == 400


def test_post_metadata_non_integer_year_returns_400():
    async def scenario():
        client, _ = await _make_client()
        try:
            resp = await client.post("/api/movies/0/metadata", json={"year": "not-a-year"})
            return resp.status
        finally:
            await client.close()

    assert asyncio.run(scenario()) == 400


def test_post_metadata_empty_body_returns_400():
    async def scenario():
        client, _ = await _make_client()
        try:
            resp = await client.post("/api/movies/0/metadata", json={})
            return resp.status
        finally:
            await client.close()

    assert asyncio.run(scenario()) == 400


def _real_library(tmp_path):
    movies_dir = tmp_path / "movies"
    movies_dir.mkdir()
    library = MovieLibrary(movies_dir, thumbnail_dir=tmp_path / "thumbnails")
    library.scan()
    return library


def test_post_movie_uploads_and_appears_in_library(tmp_path):
    library = _real_library(tmp_path)

    async def scenario():
        client, _ = await _make_client(library)
        try:
            resp = await client.post(
                "/api/movies",
                data=b"fake-video-bytes",
                headers={"X-Filename": "New Movie.mp4"},
            )
            assert resp.status == 201
            body = await resp.json()
            assert body["title"] == "New Movie"

            resp = await client.get("/api/movies")
            return await resp.json()
        finally:
            await client.close()

    movies = asyncio.run(scenario())
    assert [m["title"] for m in movies] == ["New Movie"]
    assert (tmp_path / "movies" / "New Movie.mp4").read_bytes() == b"fake-video-bytes"


def test_post_rescan_picks_up_files_added_directly_to_the_filesystem(tmp_path):
    library = _real_library(tmp_path)

    async def scenario():
        client, _ = await _make_client(library)
        try:
            (tmp_path / "movies" / "Added Directly.mp4").write_bytes(b"fake-video-bytes")

            resp = await client.post("/api/rescan")
            assert resp.status == 200
            return await resp.json()
        finally:
            await client.close()

    movies = asyncio.run(scenario())
    assert [m["title"] for m in movies] == ["Added Directly"]


def test_post_movie_missing_filename_header_returns_400(tmp_path):
    library = _real_library(tmp_path)

    async def scenario():
        client, _ = await _make_client(library)
        try:
            resp = await client.post("/api/movies", data=b"bytes")
            return resp.status
        finally:
            await client.close()

    assert asyncio.run(scenario()) == 400


def test_post_movie_unsupported_extension_returns_400(tmp_path):
    library = _real_library(tmp_path)

    async def scenario():
        client, _ = await _make_client(library)
        try:
            resp = await client.post(
                "/api/movies", data=b"bytes", headers={"X-Filename": "not-a-video.txt"}
            )
            return resp.status
        finally:
            await client.close()

    assert asyncio.run(scenario()) == 400


def test_post_movie_duplicate_filename_returns_409(tmp_path):
    library = _real_library(tmp_path)
    (tmp_path / "movies" / "Existing.mp4").write_bytes(b"already-here")

    async def scenario():
        client, _ = await _make_client(library)
        try:
            resp = await client.post(
                "/api/movies", data=b"new-bytes", headers={"X-Filename": "Existing.mp4"}
            )
            return resp.status
        finally:
            await client.close()

    assert asyncio.run(scenario()) == 409


def test_post_movie_empty_body_returns_400(tmp_path):
    library = _real_library(tmp_path)

    async def scenario():
        client, _ = await _make_client(library)
        try:
            resp = await client.post(
                "/api/movies", data=b"", headers={"X-Filename": "Empty.mp4"}
            )
            return resp.status
        finally:
            await client.close()

    assert asyncio.run(scenario()) == 400
    assert not (tmp_path / "movies" / "Empty.mp4").exists()


def test_delete_movie_removes_it_from_disk_and_the_list(tmp_path):
    library = _real_library(tmp_path)
    (tmp_path / "movies" / "Doomed.mp4").write_bytes(b"fake-video-bytes")
    library.scan()
    movie_id = next(m.id for m in library.movies if m.title == "Doomed")

    async def scenario():
        client, _ = await _make_client(library)
        try:
            resp = await client.delete(f"/api/movies/{movie_id}")
            assert resp.status == 200
            resp = await client.get("/api/movies")
            return await resp.json()
        finally:
            await client.close()

    movies = asyncio.run(scenario())
    assert not any(m["title"] == "Doomed" for m in movies)
    assert not (tmp_path / "movies" / "Doomed.mp4").exists()


def test_delete_movie_unknown_id_returns_404():
    async def scenario():
        client, _ = await _make_client()
        try:
            resp = await client.delete("/api/movies/99999")
            return resp.status
        finally:
            await client.close()

    assert asyncio.run(scenario()) == 404


def test_delete_movie_invalid_id_returns_400():
    async def scenario():
        client, _ = await _make_client()
        try:
            resp = await client.delete("/api/movies/not-a-number")
            return resp.status
        finally:
            await client.close()

    assert asyncio.run(scenario()) == 400


def test_delete_movie_stops_playback_first_if_currently_selected():
    async def scenario():
        client, controller = await _make_client()
        try:
            await client.post("/api/command", json={"opcode": "select_movie", "argument": 0})
            resp = await client.delete("/api/movies/0")
            assert resp.status == 200
            return controller
        finally:
            await client.close()

    controller = asyncio.run(scenario())
    # Mirrors test_stop_shows_idle_screen's own assertion: confirms
    # stop_and_show_idle_screen ran (rather than the file being deleted out
    # from under active playback) without depending on FakeMpv.idle, which
    # goes back to False once the idle-screen image itself gets "loaded".
    assert controller.player.shown_image_path is not None
