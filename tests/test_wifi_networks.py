import json
import stat
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest

from player_app.wifi_networks import load_networks, save_network
from player_app import wifi_startup


def test_credentials_round_trip_update_and_private_permissions(tmp_path):
    path = tmp_path / "credentials" / "wifi-networks.json"
    save_network("Mitera", "first-password", path)
    save_network("AV-iPhone17Pro", "phone-password", path)
    save_network("Mitera", "changed-password", path)
    assert load_networks(path) == [
        {"ssid": "AV-iPhone17Pro", "password": "phone-password"},
        {"ssid": "Mitera", "password": "changed-password"},
    ]
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_concurrent_saves_preserve_every_network(tmp_path):
    path = tmp_path / "wifi-networks.json"
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda n: save_network(f"WiFi-{n}", "password", path), range(10)))
    assert len(load_networks(path)) == 10


def test_invalid_file_is_preserved_on_provisioning(tmp_path):
    path = tmp_path / "wifi-networks.json"
    path.write_text('{"version": 1, "networks": "invalid"}')
    before = path.read_bytes()
    with pytest.raises(ValueError):
        save_network("Mitera", "password", path)
    assert path.read_bytes() == before


def test_invalid_entry_does_not_replace_saved_credentials(tmp_path):
    path = tmp_path / "wifi-networks.json"
    save_network("Mitera", "password", path)
    before = path.read_bytes()
    with pytest.raises(ValueError):
        save_network("x" * 33, "password", path)
    assert path.read_bytes() == before


def test_startup_restores_file_without_duplicate_profiles():
    networks = [{"ssid": "Mitera", "password": "example-password"}]
    with patch.object(wifi_startup, "load_networks", return_value=networks), \
            patch.object(wifi_startup, "nmcli", side_effect=["", "created", "uuid", "updated"]) as cli:
        wifi_startup.restore_saved_networks()
        wifi_startup.restore_saved_networks()
    assert cli.call_args_list[1].args[:3] == ("connection", "add", "type")
    assert cli.call_args_list[3].args[:3] == ("connection", "modify", "id")
    assert cli.call_args_list[1].args[-1] == "example-password"


def test_bad_credentials_file_keeps_startup_functional_and_logs_no_secrets(caplog):
    with patch.object(wifi_startup, "load_networks", side_effect=ValueError("secret-password")), \
            patch.object(wifi_startup, "nmcli") as cli:
        wifi_startup.restore_saved_networks()
    cli.assert_not_called()
    assert "secret-password" not in caplog.text
