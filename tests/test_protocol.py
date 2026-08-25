import pytest

from player_app.models import protocol
from player_app.models.protocol import (
    Command,
    Movie,
    Opcode,
    PlaybackState,
    PlaybackStatus,
)


def test_decode_command_no_argument():
    cmd = protocol.decode_command(bytes([Opcode.PLAY]))
    assert cmd == Command(opcode=Opcode.PLAY, argument=None)


def test_decode_command_with_argument():
    payload = bytes([Opcode.SEEK]) + (90).to_bytes(4, "little")
    cmd = protocol.decode_command(payload)
    assert cmd == Command(opcode=Opcode.SEEK, argument=90)


def test_decode_select_movie_argument_round_trips_large_ids():
    payload = bytes([Opcode.SELECT_MOVIE]) + (70000).to_bytes(4, "little")
    cmd = protocol.decode_command(payload)
    assert cmd.argument == 70000


def test_status_round_trip_idle():
    state = PlaybackState.idle()
    encoded = protocol.encode_status(state)
    assert len(encoded) == 7
    assert protocol.decode_status(encoded) == state


def test_status_round_trip_playing():
    state = PlaybackState(status=PlaybackStatus.PLAYING, movie_id=3, position_seconds=125)
    encoded = protocol.encode_status(state)
    assert protocol.decode_status(encoded) == state


def test_transcode_status_round_trip_none():
    encoded = protocol.encode_transcode_status(None)
    assert len(encoded) == 2
    assert protocol.decode_transcode_status(encoded) is None


def test_transcode_status_round_trip_movie_id():
    encoded = protocol.encode_transcode_status(7)
    assert protocol.decode_transcode_status(encoded) == 7


def test_api_version_is_a_single_byte_matching_the_constant():
    encoded = protocol.encode_api_version()
    assert encoded == bytes([protocol.API_VERSION])


def test_library_round_trip():
    movies = [
        Movie(id=0, title="Star Wars", duration_seconds=7620),
        Movie(id=1, title="The Room", duration_seconds=5460),
    ]
    encoded = protocol.encode_library(movies)
    assert protocol.decode_library(encoded) == movies


def test_library_truncates_to_fit_max_bytes():
    movies = [Movie(id=i, title=f"Movie {i}" * 5, duration_seconds=100) for i in range(50)]
    encoded = protocol.encode_library(movies, max_bytes=200)
    assert len(encoded) <= 200
    decoded = protocol.decode_library(encoded)
    assert decoded == movies[: len(decoded)]


def test_library_decode_ignores_malformed_lines():
    decoded = protocol.decode_library(b"0|Ok|100\nnot-a-valid-line\n1|Also Ok|200")
    assert decoded == [
        Movie(id=0, title="Ok", duration_seconds=100),
        Movie(id=1, title="Also Ok", duration_seconds=200),
    ]


def test_decode_wifi_credentials():
    ssid = "MyHotspot".encode("utf-8")
    payload = bytes([len(ssid)]) + ssid + "hunter2".encode("utf-8")
    assert protocol.decode_wifi_credentials(payload) == ("MyHotspot", "hunter2")


def test_decode_wifi_credentials_password_can_contain_pipe():
    """The library characteristic's "|" delimiter would corrupt a payload
    like this - decode_wifi_credentials is length-prefixed specifically to
    avoid that, since a WiFi password can contain any printable character."""
    ssid = "MyHotspot".encode("utf-8")
    payload = bytes([len(ssid)]) + ssid + "pa|ss|word".encode("utf-8")
    assert protocol.decode_wifi_credentials(payload) == ("MyHotspot", "pa|ss|word")


def test_decode_wifi_credentials_empty_password_is_valid():
    """Open networks (no password) are a real case, not malformed input."""
    ssid = "OpenNetwork".encode("utf-8")
    payload = bytes([len(ssid)]) + ssid
    assert protocol.decode_wifi_credentials(payload) == ("OpenNetwork", "")


def test_decode_wifi_credentials_rejects_empty_payload():
    with pytest.raises(ValueError):
        protocol.decode_wifi_credentials(b"")


def test_decode_wifi_credentials_rejects_empty_ssid():
    with pytest.raises(ValueError):
        protocol.decode_wifi_credentials(bytes([0]) + "password".encode("utf-8"))


def test_decode_wifi_credentials_rejects_payload_shorter_than_declared_ssid():
    with pytest.raises(ValueError):
        protocol.decode_wifi_credentials(bytes([10]) + "short".encode("utf-8"))
