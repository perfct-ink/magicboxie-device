import asyncio
import threading
from pathlib import Path
from unittest.mock import patch

from fakes import FakeLibrary, FakeMpv
from player_app.controllers.playback_controller import PlaybackController
from player_app.main import _prepare_startup
from player_app.models.protocol import Command, Opcode
from player_app.storage import run_io


def test_library_preparation_overlaps_player_start():
    async def scenario():
        player_started = threading.Event()
        scan_finished = threading.Event()

        class Library:
            def cleanup_partial_files(self):
                assert player_started.wait(1)

            def scan(self, *, fast):
                assert fast
                scan_finished.set()

        class Player:
            async def start(self):
                player_started.set()
                assert await asyncio.to_thread(scan_finished.wait, 1)

        await asyncio.wait_for(_prepare_startup(Library(), Player()), 2)
    asyncio.run(scenario())


def test_background_render_cannot_replace_new_movie():
    async def scenario():
        entered = threading.Event()
        release = threading.Event()
        player = FakeMpv()
        controller = PlaybackController(FakeLibrary(), player)

        def render(*args, **kwargs):
            entered.set()
            assert release.wait(2)
            return Path("/tmp/idle.png")

        with patch("player_app.controllers.playback_controller.render_idle_screen", side_effect=render):
            task = asyncio.create_task(controller.show_idle_screen())
            try:
                assert await asyncio.to_thread(entered.wait, 1)
                # Control remains responsive even while the renderer is blocked.
                await asyncio.wait_for(controller.handle_command(Command(Opcode.SELECT_MOVIE, 0)), 1)
            finally:
                release.set()
                await task
        assert player.loaded_path == FakeLibrary().path_for(0)
        assert player.shown_image_path is None
    asyncio.run(scenario())


def test_cancellation_waits_for_disk_worker_before_cleanup():
    async def scenario():
        entered = threading.Event()
        release = threading.Event()
        completed = threading.Event()

        def write():
            entered.set()
            assert release.wait(2)
            completed.set()

        task = asyncio.create_task(run_io(write))
        assert await asyncio.to_thread(entered.wait, 1)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert completed.is_set()
    asyncio.run(scenario())


def test_playback_persistence_does_not_block_event_loop(tmp_path):
    async def scenario():
        entered = threading.Event()
        release = threading.Event()
        player = FakeMpv()
        controller = PlaybackController(FakeLibrary(), player, tmp_path / "state.json")
        await controller.handle_command(Command(Opcode.SELECT_MOVIE, 0))
        player.position = 25

        def write(*args):
            entered.set()
            assert release.wait(2)

        with patch("player_app.controllers.playback_controller.write_json", side_effect=write):
            task = asyncio.create_task(controller.refresh_status())
            try:
                assert await asyncio.to_thread(entered.wait, 1)
                # An unrelated request can still run while fsync is blocked.
                assert controller.movies
                assert not task.done()
            finally:
                release.set()
                await task
    asyncio.run(scenario())
