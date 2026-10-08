"""Live status shared by the updater process and the device daemon."""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

from .storage import read_dict, write_json

STATUS_PATH = Path(tempfile.gettempdir()) / f"magicboxie-update-{os.getuid()}.json"
# Written by the root-run boot-update unit (internet check); a separate file
# because a root-owned file in the sticky /tmp could not be replaced by the
# updater's user.
BOOT_STATUS_PATH = Path("/run/magicboxie-boot-update.json")

# Phases that mean "something is running": need a live pid. TRANSIENT ones
# are results that stay on screen briefly.
BOOT_PHASES = ("internet", "no_internet")
MESSAGES = {
    "internet": "Checking for internet\u2026",
    "no_internet": "No internet \u2014 continuing without updating",
    "checking": "Checking for updates\u2026",
    "current": "Software is up to date",
    "installing": "Updating device software",
}
TRANSIENT = ("no_internet", "current")
TRANSIENT_SECONDS = 90


def _alive(pid) -> bool:
    if type(pid) is not int or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def read_message():
    """Startup/update progress text for the idle screen, or None."""
    for path in (STATUS_PATH, BOOT_STATUS_PATH):
        data = read_dict(path)
        phase = data.get("phase")
        if phase not in MESSAGES:
            continue
        try:
            age = time.time() - data["at"]
        except (KeyError, TypeError):
            continue
        if phase in TRANSIENT:
            ok = 0 <= age < TRANSIENT_SECONDS
        else:
            ok = 0 <= age < 86400 and _alive(data.get("pid"))
        if ok:
            return MESSAGES[phase]
    return None


def read_status():
    data = read_dict(STATUS_PATH)
    try:
        if data.get("phase") not in ("installing",):
            return None
        if not 0 <= time.time() - data["at"] < 86400:
            return None
        if type(data["pid"]) is not int or data["pid"] <= 0:
            return None
        os.kill(data["pid"], 0)
    except (OSError, KeyError, TypeError, ValueError):
        return None
    return data["phase"]


if __name__ == "__main__":
    phase, pid = sys.argv[1:]
    if phase == "clear":
        STATUS_PATH.unlink(missing_ok=True)
    elif phase == "clear-boot":
        BOOT_STATUS_PATH.unlink(missing_ok=True)
    elif phase in MESSAGES:
        path = BOOT_STATUS_PATH if phase in BOOT_PHASES else STATUS_PATH
        write_json(path, {"phase": phase, "pid": int(pid), "at": time.time()})
        os.chmod(path, 0o644)
    else:
        raise SystemExit("Invalid update phase")
