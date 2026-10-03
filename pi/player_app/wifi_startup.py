"""Give NetworkManager 30 seconds to join saved Wi-Fi before starting the AP.

Installed as a root-owned standalone script; only Python's standard library
is needed. NetworkManager scans and authenticates using its saved profiles.
"""
from __future__ import annotations

import hashlib
import logging
import subprocess
import time

if __package__:
    from .wifi_networks import load_networks
else:
    from wifi_networks import load_networks

logger = logging.getLogger(__name__)
INTERFACE = "wlan0"
STARTUP_WAIT_SECONDS = 30


def nmcli(*args: str, timeout: float = 2) -> str:
    try:
        result = subprocess.run(
            ["nmcli", "--terse", "--escape", "no", *args],
            capture_output=True, text=True, timeout=timeout, check=True,
        )
        return result.stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return ""


def connection_mode(timeout: float = 2) -> str:
    """Only an activated Wi-Fi client counts, not Ethernet or internet reachability."""
    fields = nmcli("--get-values", "GENERAL.STATE,GENERAL.CON-UUID",
                   "device", "show", INTERFACE, timeout=timeout).splitlines()
    if len(fields) != 2 or fields[0].split()[0] != "100" or not fields[1]:
        return ""
    mode = nmcli("--get-values", "802-11-wireless.mode", "connection", "show",
                 "uuid", fields[1], timeout=timeout)
    return mode


def wait_for_saved_wifi(wait_seconds: float = STARTUP_WAIT_SECONDS) -> bool:
    deadline = time.monotonic() + wait_seconds
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        # Bound both queries to the remaining startup window.
        mode = connection_mode(timeout=min(2, remaining / 2))
        if mode == "infrastructure":
            logger.info("Connected to saved Wi-Fi; keeping hotspot off")
            return True
        if mode == "ap":
            # Re-running deployment must not disconnect existing AP clients.
            logger.info("Hotspot already active; keeping it running")
            return False
        time.sleep(min(1, max(0, deadline - time.monotonic())))


def restore_saved_networks() -> None:
    try:
        networks = load_networks()
    except (OSError, ValueError):
        logger.error("Cannot read Wi-Fi credentials file; keeping existing NetworkManager profiles")
        return
    for network in networks:
        ssid, password = network["ssid"], network["password"]
        # Stable names avoid creating a new connection on every boot.
        name = "magicboxie-saved-" + hashlib.sha256(ssid.encode()).hexdigest()[:16]
        exists = nmcli("--get-values", "connection.uuid", "connection", "show", "id", name)
        settings = ["connection.autoconnect", "yes", "802-11-wireless.ssid", ssid,
                    "802-11-wireless.mode", "infrastructure", "ipv4.method", "auto"]
        if password:
            settings += ["wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", password]
        elif exists:
            # Clear security if a previously protected SSID is now open.
            settings += ["802-11-wireless-security", ""]
        if exists:
            nmcli("connection", "modify", "id", name, *settings)
        else:
            nmcli("connection", "add", "type", "wifi", "ifname", INTERFACE,
                  "con-name", name, *settings)


def request_self_update() -> None:
    # Queue the existing updater instead of waiting for download/playback idle.
    # Starting an already running unit also coalesces timer/startup requests.
    logger.info("Saved Wi-Fi connected; requesting a self-update")
    try:
        subprocess.run(
            ["systemctl", "--no-block", "start", "magicboxie-self-update.service"],
            check=True, capture_output=True, text=True, timeout=5,
        )
    except (subprocess.SubprocessError, OSError):
        logger.warning("Could not request self-update; keeping saved Wi-Fi connected")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    restore_saved_networks()
    nmcli("radio", "wifi", "on")
    nmcli("device", "set", INTERFACE, "autoconnect", "yes")
    logger.info("Waiting up to %d seconds for saved Wi-Fi on %s", STARTUP_WAIT_SECONDS, INTERFACE)
    if wait_for_saved_wifi():
        request_self_update()
    else:
        logger.info("Starting MagicBoxie Player hotspot")
        subprocess.run(["systemctl", "start", "magicboxie-hotspot.service"], check=True)


if __name__ == "__main__":
    main()
