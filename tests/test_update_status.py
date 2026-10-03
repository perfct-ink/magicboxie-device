import os
import time

from player_app import update_status
from player_app.storage import write_json


def _point_at(tmp_path, monkeypatch):
    monkeypatch.setattr(update_status, "STATUS_PATH", tmp_path / "user.json")
    monkeypatch.setattr(update_status, "BOOT_STATUS_PATH", tmp_path / "boot.json")


def test_no_message_without_status_files(tmp_path, monkeypatch):
    _point_at(tmp_path, monkeypatch)
    assert update_status.read_message() is None


def test_running_phase_needs_a_live_pid(tmp_path, monkeypatch):
    _point_at(tmp_path, monkeypatch)
    write_json(update_status.BOOT_STATUS_PATH, {"phase": "internet", "pid": os.getpid(), "at": time.time()})
    assert update_status.read_message() == "Checking for internet…"
    write_json(update_status.BOOT_STATUS_PATH, {"phase": "internet", "pid": 2 ** 22 + 1, "at": time.time()})
    assert update_status.read_message() is None


def test_transient_results_show_briefly_without_a_pid(tmp_path, monkeypatch):
    _point_at(tmp_path, monkeypatch)
    write_json(update_status.BOOT_STATUS_PATH, {"phase": "no_internet", "pid": 1, "at": time.time()})
    assert "No internet" in update_status.read_message()
    write_json(update_status.BOOT_STATUS_PATH, {"phase": "no_internet", "pid": 1, "at": time.time() - 1000})
    assert update_status.read_message() is None


def test_read_status_still_only_reports_blocking_phases(tmp_path, monkeypatch):
    _point_at(tmp_path, monkeypatch)
    write_json(update_status.STATUS_PATH, {"phase": "checking", "pid": os.getpid(), "at": time.time()})
    assert update_status.read_status() is None
    assert update_status.read_message() == "Checking for updates…"
