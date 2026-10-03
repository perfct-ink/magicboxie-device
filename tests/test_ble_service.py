import asyncio
from unittest.mock import AsyncMock, patch

from player_app.models.protocol import Movie
from player_app.services.ble_service import MagicBoxieService

# @characteristic(...) replaces the decorated method with a `characteristic`
# descriptor object (see bluez_peripheral.gatt.characteristic) rather than
# leaving it directly callable - the original function it wraps is reachable
# at .getter_func (for a READ characteristic) or .setter_func (for a WRITE
# one, like wifi_provision), each taking the service instance explicitly as
# its first arg.
_library_getter = MagicBoxieService.library.getter_func
_wifi_provision_setter = MagicBoxieService.wifi_provision.setter_func


class _FakeController:
    def __init__(self, movies):
        self.movies = movies


class _FakeOptions:
    def __init__(self, offset):
        self.offset = offset


def _make_service(movies):
    return MagicBoxieService(_FakeController(movies), http_port=8000)


def test_library_read_returns_all_movies_within_one_fragment():
    service = _make_service([Movie(id=0, title="A", duration_seconds=100)])
    data = _library_getter(service, _FakeOptions(offset=0))
    assert data == b"0|A|100"


def test_library_read_stays_consistent_across_fragments_despite_mutation():
    """A payload longer than one ATT MTU chunk is reassembled by the client
    across multiple offset-based fragment reads of this same characteristic
    (see the offset-handling comment on library() itself). If the library
    mutates between two fragments of what's supposed to be one contiguous
    read - e.g. a home-sync download landing mid-reassembly - the second
    fragment must still come from the SAME snapshot as the first, not a
    freshly recomputed (and now differently-sized/ordered) one."""
    service = _make_service([Movie(id=0, title="Alpha", duration_seconds=100)])

    first_fragment = _library_getter(service, _FakeOptions(offset=0))
    assert first_fragment == b"0|Alpha|100"

    # Mutate the library mid-reassembly, before the next fragment is read -
    # simulates a download/rescan completing between two ATT Read Blob
    # Requests for the same logical read.
    service._controller.movies = [
        Movie(id=0, title="Alpha", duration_seconds=100),
        Movie(id=1, title="Beta", duration_seconds=200),
    ]

    second_fragment = _library_getter(service, _FakeOptions(offset=len(first_fragment)))
    assert first_fragment + second_fragment == b"0|Alpha|100"


def test_library_read_offset_zero_starts_a_fresh_snapshot():
    """A brand new logical read (offset back to 0) should pick up whatever
    the library looks like now, not stay pinned to the very first snapshot
    forever - only reads within the same in-progress reassembly need to
    stay pinned."""
    service = _make_service([Movie(id=0, title="Alpha", duration_seconds=100)])
    _library_getter(service, _FakeOptions(offset=0))

    service._controller.movies = [Movie(id=1, title="Beta", duration_seconds=200)]

    fresh_read = _library_getter(service, _FakeOptions(offset=0))
    assert fresh_read == b"1|Beta|200"


def test_wifi_provision_write_schedules_apply_with_decoded_credentials():
    service = _make_service([])
    ssid = b"MyHotspot"
    payload = bytes([len(ssid)]) + ssid + b"hunter2"

    async def scenario():
        with patch("player_app.services.ble_service.apply_wifi_credentials", new=AsyncMock()) as mock_apply:
            _wifi_provision_setter(service, payload, _FakeOptions(offset=0))
            # The setter itself only schedules a task (create_task) so it can
            # return immediately and let BlueZ send the ATT write response -
            # yield once to actually let that scheduled task run.
            await asyncio.sleep(0)
            mock_apply.assert_awaited_once_with("MyHotspot", "hunter2")

    asyncio.run(scenario())


def test_wifi_provision_write_drops_malformed_payload_without_scheduling_anything():
    service = _make_service([])
    with patch("player_app.services.ble_service.apply_wifi_credentials", new=AsyncMock()) as mock_apply:
        _wifi_provision_setter(service, b"", _FakeOptions(offset=0))
    mock_apply.assert_not_called()


def test_update_status_reports_and_clears_message():
    service = _make_service([])
    getter = MagicBoxieService.update_status.getter_func
    service._controller.update_status = "Updating device software"
    assert getter(service, _FakeOptions(0)) == b"Updating device software"
    service._controller.update_status = None
    assert getter(service, _FakeOptions(0)) == b""
