"""Applies WiFi credentials received over BLE (see ble_service.py's
wifi_provision characteristic) by handing them to NetworkManager - lets the
phone join the device to a new network (e.g. an iPhone's Personal Hotspot
in a car, with no home network in range) without needing SSH access.
"""
from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


async def apply_wifi_credentials(ssid: str, password: str) -> bool:
    """`nmcli dev wifi connect` both creates a NetworkManager connection
    profile for this network (so it auto-reconnects later on its own,
    exactly like any other network saved this way) and switches to it
    immediately. Returns whether it succeeded; never raises - a bad
    password or an out-of-range network is an expected, not exceptional,
    outcome here."""
    logger.info("Applying WiFi credentials for network %r", ssid)
    process = await asyncio.create_subprocess_exec(
        "nmcli", "dev", "wifi", "connect", ssid, "password", password,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()
    if process.returncode == 0:
        logger.info("Connected to WiFi network %r", ssid)
        return True
    logger.warning("Failed to connect to WiFi network %r: %s", ssid, stderr.decode(errors="replace").strip())
    return False
