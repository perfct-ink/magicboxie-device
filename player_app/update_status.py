"""Live status shared by the updater process and the device daemon."""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

from .storage import read_dict, write_json

STATUS_PATH = Path(tempfile.gettempdir()) / f"magicboxie-update-{os.getuid()}.json"


def read_status():
    data = read_dict(STATUS_PATH)
    try:
        if data.get("phase") not in ("waiting", "installing"):
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
    elif phase in ("waiting", "installing"):
        write_json(STATUS_PATH, {"phase": phase, "pid": int(pid), "at": time.time()})
    else:
        raise SystemExit("Invalid update phase")
