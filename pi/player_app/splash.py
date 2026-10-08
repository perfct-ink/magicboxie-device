"""Boot loader: a black screen with a small spinner instead of the console login.

Installed as a root-owned standalone script (standard library only) and run
by magicboxie-splash.service before getty. It switches tty1 to graphics mode
so no console text shows, draws on the framebuffer, and hands the screen over
once the player's mpv is up, and gives the console back when the player
exits. Escape on any keyboard gives the screen back to
the text console and stops the player, leaving the login prompt.
"""
from __future__ import annotations

import fcntl
import glob
import mmap
import os
import select
import struct
import subprocess
import sys
import time

KDSETMODE, KD_TEXT, KD_GRAPHICS = 0x4B3A, 0, 1
FBIOGET_VSCREENINFO, FBIOGET_FSCREENINFO = 0x4600, 0x4602
KEY_ESC = 1
EV_KEY = 1
EVENT_FORMAT = "@llHHi"
EVENT_SIZE = struct.calcsize(EVENT_FORMAT)

PLAYER_SOCKET = "/tmp/magicboxie-mpv.sock"
PLAYER_UNIT = "magicboxie-player.service"
# mpv's first picture follows its socket by about a second.
HANDOVER_DELAY_SECONDS = 2.0
GIVE_UP_SECONDS = 120.0
# Consecutive seconds the player must be stopped before the prompt returns.
PLAYER_GONE_POLLS = 3
FRAME_SECONDS = 0.25
DOTS = 8
DOT_RADIUS, RING_RADIUS = 6, 28
BOX = 2 * (RING_RADIUS + DOT_RADIUS) + 4


def pixel(bits_per_pixel: int, level: int) -> bytes:
    """A grey pixel (level 0-255) in the framebuffer's native layout."""
    if bits_per_pixel == 32:
        return bytes((level, level, level, 0))
    if bits_per_pixel == 16:
        value = ((level >> 3) << 11) | ((level >> 2) << 5) | (level >> 3)
        return struct.pack("<H", value)
    raise ValueError(f"unsupported framebuffer depth: {bits_per_pixel} bpp")


def spinner_rows(bits_per_pixel: int, tick: int) -> list:
    """The spinner as BOX rows of bytes: DOTS dots on a ring, brightest at `tick`."""
    import math

    levels = [[0] * BOX for _ in range(BOX)]
    centre = BOX // 2
    for dot in range(DOTS):
        age = (tick - dot) % DOTS
        level = max(40, 255 - age * 30)
        angle = 2 * math.pi * dot / DOTS
        cx = centre + round(RING_RADIUS * math.sin(angle))
        cy = centre - round(RING_RADIUS * math.cos(angle))
        for y in range(cy - DOT_RADIUS, cy + DOT_RADIUS + 1):
            for x in range(cx - DOT_RADIUS, cx + DOT_RADIUS + 1):
                if (x - cx) ** 2 + (y - cy) ** 2 <= DOT_RADIUS ** 2 and 0 <= x < BOX and 0 <= y < BOX:
                    levels[y][x] = level
    return [b"".join(pixel(bits_per_pixel, level) for level in row) for row in levels]


def is_escape(data: bytes) -> bool:
    """Whether a chunk of raw evdev events holds an Escape key press."""
    for offset in range(0, len(data) - EVENT_SIZE + 1, EVENT_SIZE):
        *_, etype, code, value = struct.unpack_from(EVENT_FORMAT, data, offset)
        if etype == EV_KEY and code == KEY_ESC and value == 1:
            return True
    return False


class Screen:
    def __init__(self, path: str = "/dev/fb0"):
        self.fd = os.open(path, os.O_RDWR)
        var = fcntl.ioctl(self.fd, FBIOGET_VSCREENINFO, bytes(160))
        self.width, self.height, _, _, _, _, self.bpp = struct.unpack_from("7I", var)
        fix = fcntl.ioctl(self.fd, FBIOGET_FSCREENINFO, bytes(80))
        # struct fb_fix_screeninfo: id[16], smem_start (ulong), smem_len, type, type_aux, visual,
        # xpanstep, ypanstep, ywrapstep (u16 x3), line_length.
        self.stride = struct.unpack_from("@16sLIIIIHHHI", fix)[-1]
        self.buffer = mmap.mmap(self.fd, self.stride * self.height)
        self.bytes_per_pixel = self.bpp // 8

    def clear(self) -> None:
        self.buffer[:] = bytes(len(self.buffer))

    def draw_spinner(self, tick: int) -> None:
        left = (self.width - BOX) // 2
        top = (self.height - BOX) // 2
        for index, row in enumerate(spinner_rows(self.bpp, tick)):
            start = (top + index) * self.stride + left * self.bytes_per_pixel
            self.buffer[start:start + len(row)] = row

    def close(self) -> None:
        self.buffer.close()
        os.close(self.fd)


def open_screen(wait_seconds: float = 10.0) -> "Screen | None":
    """The framebuffer appears a moment after boot starts; give up quietly."""
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        try:
            return Screen()
        except (OSError, ValueError, struct.error):
            time.sleep(0.2)
    return None


def set_console_mode(mode: int) -> None:
    try:
        with open("/dev/tty1", "wb", buffering=0) as tty:
            fcntl.ioctl(tty, KDSETMODE, mode)
    except OSError:
        pass


def open_keyboards() -> list:
    fds = []
    for path in glob.glob("/dev/input/event*"):
        try:
            fds.append(os.open(path, os.O_RDONLY | os.O_NONBLOCK))
        except OSError:
            pass
    return fds


def escape_pressed(fds: list, timeout: float) -> bool:
    if not fds:
        time.sleep(timeout)
        return False
    ready, _, _ = select.select(fds, [], [], timeout)
    for fd in ready:
        try:
            if is_escape(os.read(fd, EVENT_SIZE * 32)):
                return True
        except OSError:
            pass
    return False


def main() -> int:
    screen = open_screen()
    if screen is None:
        return 0  # no framebuffer: leave the console alone
    set_console_mode(KD_GRAPHICS)
    screen.clear()
    keyboards = open_keyboards()
    started = time.monotonic()
    handover_at = None
    tick = 0
    interrupted = False
    try:
        while time.monotonic() - started < GIVE_UP_SECONDS:
            screen.draw_spinner(tick)
            tick += 1
            if escape_pressed(keyboards, FRAME_SECONDS):
                interrupted = True
                break
            if handover_at is None and os.path.exists(PLAYER_SOCKET):
                handover_at = time.monotonic() + HANDOVER_DELAY_SECONDS
            if handover_at is not None and time.monotonic() >= handover_at:
                break
    finally:
        screen.close()
        for fd in keyboards:
            os.close(fd)
    if interrupted:
        subprocess.run(["systemctl", "stop", PLAYER_UNIT], check=False)
    elif handover_at is not None:
        # mpv owns the display now; stay in graphics mode (so no console text
        # shows through) until the player has really gone, then show the prompt.
        wait_for_player_to_go()
    set_console_mode(KD_TEXT)
    return 0


def player_state() -> str:
    result = subprocess.run(["systemctl", "is-active", PLAYER_UNIT], capture_output=True, text=True)
    return result.stdout.strip()


def wait_for_player_to_go() -> None:
    """Returns once the player has been stopped for a few seconds in a row. A
    restart (self-update, crash recovery) is only a brief gap, so it is ignored."""
    gone = 0
    while gone < PLAYER_GONE_POLLS:
        time.sleep(1)
        gone = 0 if player_state() in ("active", "activating", "deactivating", "reloading") else gone + 1


if __name__ == "__main__":
    sys.exit(main())
