import asyncio
import time

from fakes import FakeLibrary, FakeMpv

from player_app.controllers.playback_controller import PlaybackController
from player_app.services.idle_dim_service import IDLE_DIM_PERCENT, IDLE_DIM_TIMEOUT_SECONDS, IdleDimService


def test_dims_when_idle_past_the_timeout():
    async def scenario():
        mpv = FakeMpv()
        controller = PlaybackController(FakeLibrary(), mpv)
        controller.last_input_at = time.monotonic() - IDLE_DIM_TIMEOUT_SECONDS - 1
        service = IdleDimService(controller)
        await service._tick()
        return mpv

    mpv = asyncio.run(scenario())
    assert mpv.dim_percent == IDLE_DIM_PERCENT


def test_does_not_dim_before_the_timeout_elapses():
    async def scenario():
        mpv = FakeMpv()
        controller = PlaybackController(FakeLibrary(), mpv)
        controller.last_input_at = time.monotonic()  # just happened
        service = IdleDimService(controller)
        await service._tick()
        return mpv

    mpv = asyncio.run(scenario())
    assert mpv.dim_percent == 0


def test_does_not_dim_while_something_is_selected_even_past_the_timeout():
    """A long-paused movie shouldn't trigger the idle dim on top of (or
    instead of) PlaybackController's own much lighter pause dim."""
    async def scenario():
        mpv = FakeMpv()
        controller = PlaybackController(FakeLibrary(), mpv)
        controller._current_movie_id = 0  # something is selected
        controller.last_input_at = time.monotonic() - IDLE_DIM_TIMEOUT_SECONDS - 1
        service = IdleDimService(controller)
        await service._tick()
        return mpv

    mpv = asyncio.run(scenario())
    assert mpv.dim_percent == 0


def test_undims_once_no_longer_idle_past_the_timeout():
    async def scenario():
        mpv = FakeMpv()
        controller = PlaybackController(FakeLibrary(), mpv)
        controller.last_input_at = time.monotonic() - IDLE_DIM_TIMEOUT_SECONDS - 1
        service = IdleDimService(controller)
        await service._tick()
        assert mpv.dim_percent == IDLE_DIM_PERCENT  # sanity check it dimmed first

        controller.last_input_at = time.monotonic()  # new input arrives
        await service._tick()
        return mpv

    mpv = asyncio.run(scenario())
    assert mpv.dim_percent == 0
