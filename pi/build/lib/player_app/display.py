"""The HDMI mode the player drives. Defaults to 720x480 (NTSC/DVD): the TV is
fed through an HDMI-to-composite converter, so a larger mode would only be
scaled down again by the converter. With a keyboard attached at startup (a
desk setup on a regular monitor) the player keeps the screen's own preferred
mode and the HD layout instead. MAGICBOXIE_DISPLAY_MODE (e.g. "1920x1080")
overrides both."""
from __future__ import annotations

import functools
import os
import re
from pathlib import Path
from typing import Optional

DEFAULT_MODE = (720, 480)
INPUT_DEVICES = Path("/proc/bus/input/devices")
# Linux input key codes: Escape (what KeyboardService listens for) and A, so
# remotes and buttons that only report a few keys don't count as keyboards.
_KEY_ESC = 1
_KEY_A = 30


def keyboard_attached(devices: Path = INPUT_DEVICES) -> bool:
    try:
        text = devices.read_text()
    except OSError:
        return False
    for block in text.split("\n\n"):
        match = re.search(r"^B: KEY=([0-9a-f ]+)$", block, re.MULTILINE)
        if not match:
            continue
        # Words are printed most significant first and are 32 or 64 bits
        # wide depending on the OS; Escape and A both sit in the last one.
        bits = int(match.group(1).split()[-1], 16)
        if bits >> _KEY_ESC & 1 and bits >> _KEY_A & 1:
            return True
    return False


@functools.lru_cache(maxsize=None)
def display_mode() -> Optional[tuple[int, int]]:
    """(width, height) to drive, or None for the screen's preferred mode.
    Decided once per run, so mpv and the idle screen always agree."""
    configured = os.environ.get("MAGICBOXIE_DISPLAY_MODE", "").strip()
    match = re.fullmatch(r"(\d+)x(\d+)", configured)
    if match:
        return int(match.group(1)), int(match.group(2))
    return None if keyboard_attached() else DEFAULT_MODE


def is_standard_definition() -> bool:
    mode = display_mode()
    return mode is not None and mode[1] <= 576
