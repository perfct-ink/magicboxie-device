import zlib

from player_app.models.library import MovieLibrary


def test_save_uploaded_thumbnail_overwrites_cache(tmp_path):
    movies_dir = tmp_path / "movies"
    movies_dir.mkdir()
    thumbnail_dir = tmp_path / "thumbnails"
    library = MovieLibrary(movies_dir, thumbnail_dir=thumbnail_dir)
    library.scan()

    library.save_uploaded_thumbnail(0, b"official-poster-bytes")

    path = library.thumbnail_path_for(0)
    assert path is not None
    assert path.read_bytes() == b"official-poster-bytes"


def test_save_uploaded_thumbnail_replaces_existing_file(tmp_path):
    movies_dir = tmp_path / "movies"
    movies_dir.mkdir()
    thumbnail_dir = tmp_path / "thumbnails"
    library = MovieLibrary(movies_dir, thumbnail_dir=thumbnail_dir)
    library.scan()

    library.save_uploaded_thumbnail(0, b"first-version")
    library.save_uploaded_thumbnail(0, b"second-version")

    assert library.thumbnail_path_for(0).read_bytes() == b"second-version"


def test_save_metadata_merges_with_existing_fields(tmp_path):
    movies_dir = tmp_path / "movies"
    movies_dir.mkdir()
    library = MovieLibrary(movies_dir, thumbnail_dir=tmp_path / "thumbnails")
    library.scan()

    library.save_metadata(0, title="Alpha", year=1999)
    library.save_metadata(0, description="A movie.")

    assert library.metadata_for(0) == {"title": "Alpha", "year": 1999, "description": "A movie."}


def test_save_metadata_persists_across_rescan(tmp_path):
    movies_dir = tmp_path / "movies"
    movies_dir.mkdir()
    (movies_dir / "a.mp4").write_bytes(b"fake-video-bytes")
    thumbnail_dir = tmp_path / "thumbnails"
    library = MovieLibrary(movies_dir, thumbnail_dir=thumbnail_dir)
    library.scan()
    movie_id = library.movies[0].id
    library.save_metadata(movie_id, title="Alpha")

    reloaded = MovieLibrary(movies_dir, thumbnail_dir=thumbnail_dir)
    reloaded.scan()

    assert reloaded.metadata_for(movie_id) == {"title": "Alpha"}


def test_movie_id_is_stable_across_rescans_when_files_are_added(tmp_path):
    """The id scheme must be filename-derived, not positional - otherwise
    adding a file that sorts earlier shifts every later movie's id,
    silently reattaching its thumbnail/metadata/selection to whatever
    movie now lands on that id instead."""
    movies_dir = tmp_path / "movies"
    movies_dir.mkdir()
    (movies_dir / "z_movie.mp4").write_bytes(b"fake-video-bytes")
    library = MovieLibrary(movies_dir, thumbnail_dir=tmp_path / "thumbnails")
    library.scan()
    original_id = library.movies[0].id

    (movies_dir / "a_movie.mp4").write_bytes(b"fake-video-bytes")
    library.scan()

    rescanned = next(m for m in library.movies if m.title == "z_movie")
    assert rescanned.id == original_id


def test_movie_id_survives_a_collision_being_freed_up_by_another_file_going_away(tmp_path, monkeypatch):
    """The bug this guards against: computing a fresh hash every scan is
    only actually stable in the no-collision case. Once two files collide,
    whichever one the scan reaches first (alphabetically) claims the
    natural slot and the other gets bumped to +1 - and if the first file is
    later removed, recomputing from scratch would let the survivor's
    natural-slot computation succeed outright, silently changing its id
    even though the survivor itself was never touched or renamed. Movie ids
    must be assigned once and persisted (see MovieLibrary._id_by_filename),
    not just recomputed the same way every time."""
    import player_app.models.library as library_module

    real_crc32 = zlib.crc32

    def colliding_crc32(data: bytes) -> int:
        if data in (b"first.mp4", b"second.mp4"):
            return 100
        return real_crc32(data)

    monkeypatch.setattr(library_module.zlib, "crc32", colliding_crc32)

    movies_dir = tmp_path / "movies"
    movies_dir.mkdir()
    (movies_dir / "first.mp4").write_bytes(b"fake-video-bytes")
    (movies_dir / "second.mp4").write_bytes(b"fake-video-bytes")
    library = MovieLibrary(movies_dir, thumbnail_dir=tmp_path / "thumbnails")
    library.scan()

    second_id_before = next(m for m in library.movies if m.title == "second").id
    assert second_id_before == 101  # bumped past "first"'s natural slot (100)

    (movies_dir / "first.mp4").unlink()
    library.scan()

    second_id_after = next(m for m in library.movies if m.title == "second").id
    assert second_id_after == second_id_before

