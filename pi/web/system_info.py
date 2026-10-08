"""Device details and power actions for the web settings panel.

Every reading degrades to None when unavailable (e.g. on a dev machine
without /proc or NetworkManager), so the panel shows what it can.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import socket
import subprocess
from pathlib import Path
from typing import Optional

REPO_DIR = Path(__file__).resolve().parents[2]


def _run(*command: str, timeout: float = 3) -> Optional[str]:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=True)
    except (subprocess.SubprocessError, OSError):
        return None
    return result.stdout.strip()


def _read(path: str) -> Optional[str]:
    try:
        return Path(path).read_text().strip("\x00\n ")
    except OSError:
        return None


def hostname() -> str:
    return socket.gethostname()


def addresses() -> list:
    """Non-loopback IPv4 addresses as [{"interface", "address"}]."""
    output = _run("ip", "-4", "-o", "addr", "show") or ""
    found = []
    for line in output.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[1] != "lo":
            found.append({"interface": parts[1], "address": parts[3].split("/")[0]})
    return found


def active_connections() -> list:
    """NetworkManager's active connections as [{"name", "type", "device"}]."""
    output = _run("nmcli", "--terse", "--escape", "no", "-f", "NAME,TYPE,DEVICE", "connection", "show", "--active")
    kinds = {"802-11-wireless": "wifi", "802-3-ethernet": "ethernet"}
    connections = []
    for line in (output or "").splitlines():
        fields = line.rsplit(":", 2)  # the name itself may contain ":"
        if len(fields) == 3 and fields[2] and fields[2] != "lo":
            connections.append({"name": fields[0], "type": kinds.get(fields[1], fields[1]), "device": fields[2]})
    return connections


def uptime_seconds() -> Optional[int]:
    text = _read("/proc/uptime")
    try:
        return int(float(text.split()[0])) if text else None
    except (ValueError, IndexError):
        return None


def memory_mb() -> Optional[dict]:
    text = _read("/proc/meminfo")
    if not text:
        return None
    values = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        try:
            values[key] = int(rest.split()[0]) // 1024
        except (ValueError, IndexError):
            continue
    if "MemTotal" not in values or "MemAvailable" not in values:
        return None
    return {"total": values["MemTotal"], "available": values["MemAvailable"]}


def disk_gb(path) -> Optional[dict]:
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return None
    gb = 1024 ** 3
    return {"total": round(usage.total / gb, 1), "free": round(usage.free / gb, 1)}


def software_revision() -> Optional[dict]:
    output = _run("git", "-C", str(REPO_DIR), "log", "-1", "--format=%h%x09%s%x09%cs")
    if not output:
        return None
    commit, _, rest = output.partition("\t")
    subject, _, date = rest.partition("\t")
    return {"commit": commit, "subject": subject, "date": date}


def snapshot(movies_root) -> dict:
    """Blocking collection of everything that doesn't need the event loop."""
    name = hostname()
    try:
        load = os.getloadavg()
    except (OSError, AttributeError):
        load = None
    return {
        "hostname": name,
        "mdns_name": f"{name.split('.')[0]}.local",
        "model": _read("/proc/device-tree/model"),
        "addresses": addresses(),
        "connections": active_connections(),
        "uptime_seconds": uptime_seconds(),
        "load_average": [round(value, 2) for value in load] if load else None,
        "memory_mb": memory_mb(),
        "disk_movies_gb": disk_gb(movies_root) if movies_root else None,
        "disk_system_gb": disk_gb("/"),
        "software": software_revision(),
    }


# Units whose logs explain Wi-Fi, startup and update behavior, in that order.
LOG_UNITS = (
    "magicboxie-wifi-startup", "magicboxie-wifi-retry", "magicboxie-hotspot", "NetworkManager",
    "magicboxie-player", "magicboxie-boot-update", "magicboxie-self-update",
)
LOG_LINES = 300


def saved_wifi_names() -> Optional[list]:
    """SSIDs from the device's saved-network file (never the passwords)."""
    from player_app.wifi_networks import load_networks

    try:
        return [network["ssid"] for network in load_networks()]
    except (OSError, ValueError):
        return None


def wifi_profiles() -> list:
    """NetworkManager's saved Wi-Fi profiles as [{"name", "autoconnect"}]."""
    output = _run("nmcli", "--terse", "--escape", "no", "-f", "NAME,TYPE,AUTOCONNECT", "connection", "show")
    profiles = []
    for line in (output or "").splitlines():
        fields = line.rsplit(":", 2)
        if len(fields) == 3 and fields[1] == "802-11-wireless":
            profiles.append({"name": fields[0], "autoconnect": fields[2] == "yes"})
    return profiles


def logs(lines: int = LOG_LINES) -> dict:
    """This boot's journal for LOG_UNITS plus what Wi-Fi is configured, for
    the web page's Logs view. Reading other units' logs needs the
    systemd-journal group (see magicboxie-player.service.in)."""
    units = [arg for unit in LOG_UNITS for arg in ("-u", unit)]
    text = _run("journalctl", "-b", "--no-pager", "-o", "short", "-n", str(lines), *units, timeout=10)
    return {
        "journal": text if text is not None else "Could not read the system journal.",
        "saved_networks": saved_wifi_names(),
        "wifi_profiles": wifi_profiles(),
    }


async def _systemctl_power(action: str) -> Optional[str]:
    """Runs `systemctl <action>` (reboot or poweroff); returns an error
    message if it could not start.

    `sudo -n` never prompts. The installer's sudoers rule (see `make setup`)
    allows exactly these commands for the service user.
    """
    try:
        process = await asyncio.create_subprocess_exec(
            "sudo", "-n", "/usr/bin/systemctl", action,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError as exc:
        return f"could not run {action}: {exc}"
    try:
        code = await asyncio.wait_for(process.wait(), timeout=2)
    except asyncio.TimeoutError:
        return None  # still running: the system is going down
    return None if code == 0 else f"the device is not permitted to {action} itself"


async def reboot() -> Optional[str]:
    return await _systemctl_power("reboot")


async def shutdown() -> Optional[str]:
    return await _systemctl_power("poweroff")
