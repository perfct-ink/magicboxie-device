from unittest.mock import patch

from player_app import wifi_startup


class Clock:
    def __init__(self):
        self.now = 0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def test_connected_saved_wifi_requests_update_and_keeps_hotspot_off():
    with patch.object(wifi_startup, "load_networks", return_value=[]), \
            patch.object(wifi_startup, "nmcli"), \
            patch.object(wifi_startup, "connection_mode", return_value="infrastructure"), \
            patch.object(wifi_startup.subprocess, "run") as run:
        wifi_startup.main()
    run.assert_called_once_with(
        ["systemctl", "--no-block", "start", "magicboxie-self-update.service"],
        check=True, capture_output=True, text=True, timeout=5,
    )


def test_no_saved_wifi_starts_hotspot_after_thirty_seconds():
    clock = Clock()
    with patch.object(wifi_startup, "load_networks", return_value=[]), \
            patch.object(wifi_startup, "nmcli"), \
            patch.object(wifi_startup, "connection_mode", return_value=""), \
            patch.object(wifi_startup.time, "monotonic", clock.monotonic), \
            patch.object(wifi_startup.time, "sleep", clock.sleep), \
            patch.object(wifi_startup.subprocess, "run") as run:
        wifi_startup.main()
    assert clock.now == 30
    run.assert_called_once_with(["systemctl", "start", "magicboxie-hotspot.service"], check=True)


def test_saved_wifi_connecting_during_startup_prevents_fallback():
    clock = Clock()
    with patch.object(wifi_startup.time, "monotonic", clock.monotonic), \
            patch.object(wifi_startup.time, "sleep", clock.sleep), \
            patch.object(wifi_startup, "connection_mode", side_effect=["", "", "infrastructure"]):
        assert wifi_startup.wait_for_saved_wifi()
    assert clock.now == 2


def test_connection_at_end_of_window_is_accepted():
    clock = Clock()
    with patch.object(wifi_startup.time, "monotonic", clock.monotonic), \
            patch.object(wifi_startup.time, "sleep", clock.sleep), \
            patch.object(wifi_startup, "connection_mode", side_effect=[""] * 29 + ["infrastructure"]):
        assert wifi_startup.wait_for_saved_wifi()
    assert clock.now == 29


def test_existing_hotspot_is_preserved_without_waiting():
    clock = Clock()
    with patch.object(wifi_startup.time, "monotonic", clock.monotonic), \
            patch.object(wifi_startup.time, "sleep", clock.sleep), \
            patch.object(wifi_startup, "connection_mode", return_value="ap"):
        assert not wifi_startup.wait_for_saved_wifi()
    assert clock.now == 0


def test_client_connection_requires_activated_wlan_and_checks_profile_mode():
    with patch.object(wifi_startup, "nmcli", side_effect=["100 (connected)\nprofile-uuid", "infrastructure"]) as nmcli:
        assert wifi_startup.connection_mode() == "infrastructure"
    assert nmcli.call_args_list[0].args[-1] == "wlan0"
    assert nmcli.call_args_list[1].args[-2:] == ("uuid", "profile-uuid")
    for disconnected in ("", "30 (disconnected)\n", "70 (connecting)\nprofile-uuid"):
        with patch.object(wifi_startup, "nmcli", return_value=disconnected):
            assert wifi_startup.connection_mode() == ""


def test_nmcli_failure_does_not_count_as_a_connection():
    with patch.object(wifi_startup.subprocess, "run", side_effect=wifi_startup.subprocess.TimeoutExpired("nmcli", 2)):
        assert wifi_startup.connection_mode() == ""


def test_failed_update_request_keeps_saved_wifi_connected(caplog):
    with patch.object(wifi_startup, "restore_saved_networks"), \
            patch.object(wifi_startup, "nmcli"), \
            patch.object(wifi_startup, "connection_mode", return_value="infrastructure"), \
            patch.object(wifi_startup.subprocess, "run", side_effect=OSError("systemctl unavailable")) as run:
        wifi_startup.main()
    assert run.call_count == 1
    assert "keeping saved Wi-Fi connected" in caplog.text


def test_existing_hotspot_does_not_request_update():
    with patch.object(wifi_startup, "restore_saved_networks"), \
            patch.object(wifi_startup, "nmcli"), \
            patch.object(wifi_startup, "connection_mode", return_value="ap"), \
            patch.object(wifi_startup.subprocess, "run") as run:
        wifi_startup.main()
    run.assert_called_once_with(["systemctl", "start", "magicboxie-hotspot.service"], check=True)
