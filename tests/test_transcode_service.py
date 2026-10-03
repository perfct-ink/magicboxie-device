import asyncio
from unittest.mock import AsyncMock, patch

from fakes import FakeLibrary, FakeMpv

from player_app.controllers.playback_controller import PlaybackController
from player_app.services.transcode_service import TranscodeService

# Real ffmpeg subprocess creation is patched out so these tests don't depend
# on a real ffmpeg binary, real video files, or real encode time - matching
# how test_mdns_service.py patches out AsyncZeroconf rather than opening
# real sockets.
_PATCH_TARGET = "player_app.services.transcode_service.asyncio.create_subprocess_exec"


class FakeProcess:
    """Stands in for asyncio.subprocess.Process. By default has already
    exited by the time it's returned (mirrors ffmpeg finishing quickly, the
    common case in these tests) - pass already_exited=False to simulate one
    still running, whose .wait() only resolves once terminate()/kill() is
    called, for testing the interrupt-mid-transcode path."""

    def __init__(self, returncode=0, stderr=b"", already_exited=True):
        self._final_returncode = returncode
        self.returncode = returncode if already_exited else None
        self.stderr = AsyncMock()
        self.stderr.read = AsyncMock(return_value=stderr)
        self.terminated = False
        self.killed = False
        self._exited = asyncio.Event()
        if already_exited:
            self._exited.set()

    async def wait(self):
        await self._exited.wait()
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = self._final_returncode
        self._exited.set()

    def kill(self):
        self.killed = True
        self.returncode = self._final_returncode
        self._exited.set()


def test_next_movie_needing_transcode_skips_ones_that_already_have_a_copy(tmp_path):
    (tmp_path / "0.mp4").write_bytes(b"already transcoded")
    library = FakeLibrary(transcode_dir=tmp_path)
    controller = PlaybackController(library, FakeMpv())
    service = TranscodeService(controller)

    movie = service._next_movie_needing_transcode()

    assert movie.id == 1


def test_next_movie_needing_transcode_returns_none_when_all_done(tmp_path):
    (tmp_path / "0.mp4").write_bytes(b"x")
    (tmp_path / "1.mp4").write_bytes(b"x")
    library = FakeLibrary(transcode_dir=tmp_path)
    controller = PlaybackController(library, FakeMpv())
    service = TranscodeService(controller)

    assert service._next_movie_needing_transcode() is None


def test_successful_transcode_moves_partial_file_into_place(tmp_path):
    async def scenario():
        library = FakeLibrary(transcode_dir=tmp_path)
        controller = PlaybackController(library, FakeMpv())
        service = TranscodeService(controller)

        async def fake_exec(*args, **kwargs):
            # Simulate ffmpeg actually having written the output file - the
            # real subprocess would do this itself before exiting 0.
            (tmp_path / "0.partial.mp4").write_bytes(b"encoded")
            return FakeProcess(returncode=0)

        with patch(_PATCH_TARGET, side_effect=fake_exec):
            await service._transcode(movie_id=0)

        return tmp_path / "0.mp4"

    dest = asyncio.run(scenario())
    assert dest.read_bytes() == b"encoded"
    assert not (dest.parent / "0.partial.mp4").exists()


def test_currently_transcoding_movie_id_set_during_and_cleared_after(tmp_path):
    """controller.currently_transcoding_movie_id is what ble_service.py's
    status poll reads to notify the app - must be accurate both while a
    transcode is in flight and once it's done, regardless of outcome."""
    async def scenario():
        library = FakeLibrary(transcode_dir=tmp_path)
        controller = PlaybackController(library, FakeMpv())
        service = TranscodeService(controller)
        seen_during: list = []

        async def fake_exec(*args, **kwargs):
            seen_during.append(controller.currently_transcoding_movie_id)
            (tmp_path / "0.partial.mp4").write_bytes(b"encoded")
            return FakeProcess(returncode=0)

        with patch(_PATCH_TARGET, side_effect=fake_exec):
            await service._transcode(movie_id=0)

        return seen_during, controller.currently_transcoding_movie_id

    seen_during, after = asyncio.run(scenario())
    assert seen_during == [0]
    assert after is None


def test_failed_transcode_leaves_no_partial_file_behind(tmp_path):
    async def scenario():
        library = FakeLibrary(transcode_dir=tmp_path)
        controller = PlaybackController(library, FakeMpv())
        service = TranscodeService(controller)

        async def fake_exec(*args, **kwargs):
            (tmp_path / "0.partial.mp4").write_bytes(b"truncated garbage")
            return FakeProcess(returncode=1, stderr=b"ffmpeg exploded")

        with patch(_PATCH_TARGET, side_effect=fake_exec):
            await service._transcode(movie_id=0)

        return tmp_path

    transcode_dir = asyncio.run(scenario())
    assert not (transcode_dir / "0.mp4").exists()
    assert not (transcode_dir / "0.partial.mp4").exists()


def test_next_movie_needing_transcode_skips_one_that_already_failed(tmp_path):
    """A movie ffmpeg can't encode (corrupt file, unsupported codec
    quirk, ...) must not get retried every single loop iteration forever -
    see TranscodeService._failed_movie_ids."""
    async def scenario():
        library = FakeLibrary(transcode_dir=tmp_path)
        controller = PlaybackController(library, FakeMpv())
        service = TranscodeService(controller)

        async def fake_exec(*args, **kwargs):
            return FakeProcess(returncode=1, stderr=b"moov atom not found")

        with patch(_PATCH_TARGET, side_effect=fake_exec):
            await service._transcode(movie_id=0)

        return service

    service = asyncio.run(scenario())
    next_movie = service._next_movie_needing_transcode()
    assert next_movie is not None
    assert next_movie.id == 1


def test_transcode_stops_and_cleans_up_once_playback_starts(tmp_path):
    """The core guarantee this whole feature exists for: transcoding must
    never keep competing with playback decode on this device's one core."""
    async def scenario():
        library = FakeLibrary(transcode_dir=tmp_path)
        controller = PlaybackController(library, FakeMpv())
        service = TranscodeService(controller)
        process = FakeProcess(already_exited=False)  # stays "running" until killed

        async def fake_exec(*args, **kwargs):
            (tmp_path / "0.partial.mp4").write_bytes(b"still encoding")
            return process

        with patch(_PATCH_TARGET, side_effect=fake_exec), \
                patch("player_app.services.transcode_service.PLAYBACK_CHECK_INTERVAL_SECONDS", 0.01):
            transcode_task = asyncio.create_task(service._transcode(movie_id=0))
            await asyncio.sleep(0)  # let it start and enter the wait loop
            controller._current_movie_id = 5  # playback "starts"
            await asyncio.wait_for(transcode_task, timeout=5)

        return process

    process = asyncio.run(scenario())
    assert process.terminated
    assert not (tmp_path / "0.mp4").exists()
    assert not (tmp_path / "0.partial.mp4").exists()


def test_run_does_nothing_while_playback_is_active(tmp_path):
    library = FakeLibrary(transcode_dir=tmp_path)
    controller = PlaybackController(library, FakeMpv())
    controller._current_movie_id = 5
    service = TranscodeService(controller)

    with patch(_PATCH_TARGET) as mock_exec:
        assert service._next_movie_needing_transcode() is not None  # something to do...
        assert not controller.is_idle  # ...but playback is active
        mock_exec.assert_not_called()


def test_run_skips_a_movie_whose_ffmpeg_raises_and_continues(tmp_path):
    async def scenario():
        library = FakeLibrary(transcode_dir=tmp_path)
        controller = PlaybackController(library, FakeMpv())
        service = TranscodeService(controller)
        stop_event = asyncio.Event()

        call_count = 0

        async def fake_exec(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise OSError("ffmpeg not found")
            (tmp_path / "1.partial.mp4").write_bytes(b"encoded")
            stop_event.set()  # stop the loop right after the second attempt
            return FakeProcess(returncode=0)

        with patch(_PATCH_TARGET, side_effect=fake_exec):
            await asyncio.wait_for(service.run(stop_event), timeout=5)

        return call_count

    call_count = asyncio.run(scenario())
    # One failed attempt (movie 0, raised) didn't crash the loop or wedge
    # movie 1 behind it forever.
    assert call_count == 2
