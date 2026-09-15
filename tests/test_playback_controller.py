import asyncio
import json
from unittest.mock import AsyncMock, patch

from fakes import FakeLibrary, FakeMpv

from player_app.controllers.playback_controller import PAUSE_DIM_PERCENT, PlaybackController
from player_app.models.protocol import Command, Opcode, PlaybackStatus


def test_select_and_play_updates_status():
    async def scenario():
        controller = PlaybackController(FakeLibrary(), FakeMpv())
        await controller.handle_command(Command(opcode=Opcode.SELECT_MOVIE, argument=1))
        return await controller.refresh_status()

    state = asyncio.run(scenario())
    assert state.status == PlaybackStatus.PLAYING
    assert state.movie_id == 1


def test_select_unknown_movie_id_is_ignored_not_a_crash():
    async def scenario():
        mpv = FakeMpv()
        controller = PlaybackController(FakeLibrary(), mpv)
        await controller.handle_command(Command(opcode=Opcode.SELECT_MOVIE, argument=99999))
        return controller, mpv

    controller, mpv = asyncio.run(scenario())
    assert controller.is_idle
    assert mpv.loaded_path is None


def test_stop_clears_movie_id():
    async def scenario():
        controller = PlaybackController(FakeLibrary(), FakeMpv())
        await controller.handle_command(Command(opcode=Opcode.SELECT_MOVIE, argument=0))
        await controller.handle_command(Command(opcode=Opcode.STOP))
        return await controller.refresh_status()

    state = asyncio.run(scenario())
    assert state.status == PlaybackStatus.STOPPED
    assert state.movie_id is None


def test_stop_shows_idle_screen():
    async def scenario():
        mpv = FakeMpv()
        controller = PlaybackController(FakeLibrary(), mpv)
        await controller.handle_command(Command(opcode=Opcode.SELECT_MOVIE, argument=0))
        await controller.handle_command(Command(opcode=Opcode.STOP))
        return mpv

    mpv = asyncio.run(scenario())
    assert mpv.shown_image_path is not None


def test_status_stays_idle_while_idle_screen_is_shown():
    """The idle-screen image is itself loaded into mpv as a "file" (see
    MpvController.show_image), which would otherwise report as "not idle" -
    refresh_status must not mistake it for a movie playing."""
    async def scenario():
        controller = PlaybackController(FakeLibrary(), FakeMpv())
        await controller.show_idle_screen()
        return await controller.refresh_status()

    state = asyncio.run(scenario())
    assert state.status == PlaybackStatus.STOPPED
    assert state.movie_id is None


def test_pause_reflected_in_status():
    async def scenario():
        controller = PlaybackController(FakeLibrary(), FakeMpv())
        await controller.handle_command(Command(opcode=Opcode.SELECT_MOVIE, argument=0))
        await controller.handle_command(Command(opcode=Opcode.PAUSE))
        return await controller.refresh_status()

    state = asyncio.run(scenario())
    assert state.status == PlaybackStatus.PAUSED


def test_seek_updates_position():
    async def scenario():
        controller = PlaybackController(FakeLibrary(), FakeMpv())
        await controller.handle_command(Command(opcode=Opcode.SELECT_MOVIE, argument=0))
        await controller.handle_command(Command(opcode=Opcode.SEEK, argument=42))
        return await controller.refresh_status()

    state = asyncio.run(scenario())
    assert state.position_seconds == 42


def test_pause_dims_and_shows_pause_icon():
    async def scenario():
        mpv = FakeMpv()
        controller = PlaybackController(FakeLibrary(), mpv)
        await controller.handle_command(Command(opcode=Opcode.SELECT_MOVIE, argument=0))
        await controller.handle_command(Command(opcode=Opcode.PAUSE))
        return mpv

    mpv = asyncio.run(scenario())
    assert mpv.dim_percent == PAUSE_DIM_PERCENT
    assert mpv.pause_icon_shown


def test_play_clears_dim_and_pause_icon():
    async def scenario():
        mpv = FakeMpv()
        controller = PlaybackController(FakeLibrary(), mpv)
        await controller.handle_command(Command(opcode=Opcode.SELECT_MOVIE, argument=0))
        await controller.handle_command(Command(opcode=Opcode.PAUSE))
        await controller.handle_command(Command(opcode=Opcode.PLAY))
        return mpv

    mpv = asyncio.run(scenario())
    assert mpv.dim_percent == 0
    assert not mpv.pause_icon_shown


def test_stop_clears_dim_and_pause_icon_left_over_from_a_pause():
    async def scenario():
        mpv = FakeMpv()
        controller = PlaybackController(FakeLibrary(), mpv)
        await controller.handle_command(Command(opcode=Opcode.SELECT_MOVIE, argument=0))
        await controller.handle_command(Command(opcode=Opcode.PAUSE))
        await controller.handle_command(Command(opcode=Opcode.STOP))
        return mpv

    mpv = asyncio.run(scenario())
    assert mpv.dim_percent == 0
    assert not mpv.pause_icon_shown


def test_any_command_updates_last_input_at():
    async def scenario():
        controller = PlaybackController(FakeLibrary(), FakeMpv())
        controller.last_input_at = 0
        await controller.handle_command(Command(opcode=Opcode.SELECT_MOVIE, argument=0))
        return controller.last_input_at

    last_input_at = asyncio.run(scenario())
    assert last_input_at > 0


def test_shutdown_invokes_systemctl_poweroff_via_sudo():
    # Patched out so this test never actually powers off the machine
    # running it.
    async def scenario():
        controller = PlaybackController(FakeLibrary(), FakeMpv())
        with patch(
            "player_app.controllers.playback_controller.asyncio.create_subprocess_exec",
            new=AsyncMock(),
        ) as mock_exec:
            await controller.handle_command(Command(opcode=Opcode.SHUTDOWN))
            return mock_exec

    mock_exec = asyncio.run(scenario())
    mock_exec.assert_awaited_once_with("sudo", "systemctl", "poweroff")


def test_refresh_status_persists_playback_state(tmp_path):
    state_path = tmp_path / "playback_state.json"

    async def scenario():
        mpv = FakeMpv()
        controller = PlaybackController(FakeLibrary(), mpv, state_path=state_path)
        await controller.handle_command(Command(opcode=Opcode.SELECT_MOVIE, argument=1))
        mpv.position = 42
        await controller.refresh_status()

    asyncio.run(scenario())

    assert json.loads(state_path.read_text()) == {
        "movie_id": 1,
        "position_seconds": 42,
        "paused": False,
    }


def test_stopping_clears_persisted_state(tmp_path):
    state_path = tmp_path / "playback_state.json"

    async def scenario():
        controller = PlaybackController(FakeLibrary(), FakeMpv(), state_path=state_path)
        await controller.handle_command(Command(opcode=Opcode.SELECT_MOVIE, argument=0))
        await controller.refresh_status()
        await controller.handle_command(Command(opcode=Opcode.STOP))

    asyncio.run(scenario())

    assert not state_path.exists()


def test_movie_finishing_naturally_clears_persisted_state(tmp_path):
    state_path = tmp_path / "playback_state.json"

    async def scenario():
        mpv = FakeMpv()
        controller = PlaybackController(FakeLibrary(), mpv, state_path=state_path)
        await controller.handle_command(Command(opcode=Opcode.SELECT_MOVIE, argument=0))
        await controller.refresh_status()
        mpv.idle = True  # mpv reports the file played through to the end
        await controller.refresh_status()

    asyncio.run(scenario())

    assert not state_path.exists()


def test_no_persistence_without_a_state_path():
    """The default (no state_path passed) must not try to write anywhere -
    every other test in this file relies on that."""
    async def scenario():
        controller = PlaybackController(FakeLibrary(), FakeMpv())
        await controller.handle_command(Command(opcode=Opcode.SELECT_MOVIE, argument=0))
        await controller.refresh_status()
        return controller

    controller = asyncio.run(scenario())
    assert controller._state_path is None


def test_restore_last_playback_resumes_movie_and_position(tmp_path):
    state_path = tmp_path / "playback_state.json"
    state_path.write_text(json.dumps({"movie_id": 1, "position_seconds": 123, "paused": False}))

    async def scenario():
        mpv = FakeMpv()
        controller = PlaybackController(FakeLibrary(), mpv, state_path=state_path)
        resumed = await controller.restore_last_playback()
        return controller, mpv, resumed

    controller, mpv, resumed = asyncio.run(scenario())
    assert resumed is True
    assert not controller.is_idle
    assert mpv.loaded_path == FakeLibrary()._paths[1]
    assert mpv.position == 123
    assert not mpv.paused


def test_restore_last_playback_resumes_paused_state(tmp_path):
    state_path = tmp_path / "playback_state.json"
    state_path.write_text(json.dumps({"movie_id": 0, "position_seconds": 10, "paused": True}))

    async def scenario():
        mpv = FakeMpv()
        controller = PlaybackController(FakeLibrary(), mpv, state_path=state_path)
        await controller.restore_last_playback()
        return mpv

    mpv = asyncio.run(scenario())
    assert mpv.paused
    assert mpv.pause_icon_shown
    assert mpv.dim_percent == PAUSE_DIM_PERCENT


def test_restore_last_playback_returns_false_with_no_state_file(tmp_path):
    async def scenario():
        controller = PlaybackController(FakeLibrary(), FakeMpv(), state_path=tmp_path / "missing.json")
        return await controller.restore_last_playback()

    assert asyncio.run(scenario()) is False


def test_restore_last_playback_returns_false_without_a_state_path():
    async def scenario():
        controller = PlaybackController(FakeLibrary(), FakeMpv())
        return await controller.restore_last_playback()

    assert asyncio.run(scenario()) is False


def test_restore_last_playback_ignores_movie_no_longer_in_library(tmp_path):
    state_path = tmp_path / "playback_state.json"
    state_path.write_text(json.dumps({"movie_id": 99999, "position_seconds": 5, "paused": False}))

    async def scenario():
        mpv = FakeMpv()
        controller = PlaybackController(FakeLibrary(), mpv, state_path=state_path)
        resumed = await controller.restore_last_playback()
        return controller, mpv, resumed

    controller, mpv, resumed = asyncio.run(scenario())
    assert resumed is False
    assert controller.is_idle
    assert mpv.loaded_path is None
    assert not state_path.exists()


def test_restore_last_playback_ignores_corrupt_state_file(tmp_path):
    state_path = tmp_path / "playback_state.json"
    state_path.write_text("not valid json")

    async def scenario():
        controller = PlaybackController(FakeLibrary(), FakeMpv(), state_path=state_path)
        return await controller.restore_last_playback()

    resumed = asyncio.run(scenario())
    assert resumed is False
    assert not state_path.exists()
