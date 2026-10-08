"""The HDMI mode the player drives. Defaults to 720x480 (NTSC/DVD): the TV is
fed through an HDMI-to-composite converter, so a larger mode would only be
scaled down again by the converter. Set MAGICBOXIE_DISPLAY_MODE (e.g.
"1920x1080") for a TV connected directly over HDMI."""
from __future__ import annotations

import os
import re

DEFAULT_MODE = "720x480"


def display_mode() -> tuple[int, int]:
    match = re.fullmatch(r"(\d+)x(\d+)", os.environ.get("MAGICBOXIE_DISPLAY_MODE", DEFAULT_MODE).strip())
    if not match:
        return 720, 480
    return int(match.group(1)), int(match.group(2))


def is_standard_definition() -> bool:
    return display_mode()[1] <= 576
