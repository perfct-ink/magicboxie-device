"""Persistent Wi-Fi credentials, deliberately outside the Git checkout."""
from __future__ import annotations

import fcntl
import json
import os
import sys
from pathlib import Path

if __package__:
    from .storage import atomic_write
else:  # Root-owned standalone startup scripts installed on the Pi.
    from storage import atomic_write

WIFI_NETWORKS_PATH = Path(os.environ.get("MAGICBOXIE_WIFI_NETWORKS_FILE", "/var/lib/magicboxie/wifi-networks.json"))


def load_networks(path: Path = WIFI_NETWORKS_PATH) -> list:
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return []
    if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("networks"), list):
        raise ValueError("Invalid Wi-Fi credentials file format")
    networks = data["networks"]
    seen = set()
    for network in networks:
        if not isinstance(network, dict):
            raise ValueError("Invalid Wi-Fi entry")
        ssid, password = network.get("ssid"), network.get("password")
        if not isinstance(ssid, str) or not 1 <= len(ssid.encode("utf-8")) <= 32 or "\x00" in ssid:
            raise ValueError("Invalid Wi-Fi SSID")
        if not isinstance(password, str) or "\x00" in password:
            raise ValueError("Invalid Wi-Fi password")
        if ssid in seen:
            raise ValueError("Duplicate Wi-Fi SSID")
        seen.add(ssid)
    return networks


def save_network(ssid: str, password: str, path: Path = WIFI_NETWORKS_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = path.with_suffix(".lock")
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(descriptor, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        networks = load_networks(path)
        networks = [entry for entry in networks if entry["ssid"] != ssid]
        networks.append({"ssid": ssid, "password": password})
        # Validate before replacing a complete file, including new entries.
        if not isinstance(ssid, str) or not 1 <= len(ssid.encode("utf-8")) <= 32 or "\x00" in ssid:
            raise ValueError("Invalid Wi-Fi SSID")
        if not isinstance(password, str) or "\x00" in password:
            raise ValueError("Invalid Wi-Fi password")
        atomic_write(path, (json.dumps({"version": 1, "networks": networks}, indent=2) + "\n").encode())


def add_missing_networks(seed_path: Path, path: Path = WIFI_NETWORKS_PATH) -> list:
    """Adds seed networks whose SSID the device file lacks, keeping every
    existing entry (and its password) as is. Lets a deploy deliver networks
    added to the tracked seed after the device was first installed."""
    seed = load_networks(seed_path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = path.with_suffix(".lock")
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(descriptor, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        networks = load_networks(path)
        known = {entry["ssid"] for entry in networks}
        added = [entry for entry in seed if entry["ssid"] not in known]
        if added:
            atomic_write(path, (json.dumps({"version": 1, "networks": networks + added}, indent=2) + "\n").encode())
        return [entry["ssid"] for entry in added]


if __name__ == "__main__":
    for name in add_missing_networks(Path(sys.argv[1])):
        print(f"Added saved Wi-Fi network {name!r}")
