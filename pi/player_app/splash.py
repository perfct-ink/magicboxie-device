"""Boot loader: the MB logo above three animated dots on black instead of
the console login.

A standalone script (standard library only) run as root straight from the
checkout by magicboxie-splash.service before getty, so a self-update's git
pull changes it without a reinstall. It switches tty1 to graphics mode
so no console text shows, draws on the framebuffer, and hands the screen over
once the player's mpv is up, and gives the console back when the player
exits. While the player restarts (a self-update, crash recovery) the logo
comes back until mpv takes the screen again. Escape on any keyboard gives
the screen back to the text console and stops the player, leaving the login
prompt.
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
import zlib
from base64 import b64decode

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
FRAME_SECONDS = 0.4
# Three dots in a row; one at a time lights up, left to right.
DOTS = 3
DOT_RADIUS = 7
DOT_SPACING = 32
DOTS_WIDTH = (DOTS - 1) * DOT_SPACING + 2 * DOT_RADIUS + 2
DOTS_HEIGHT = 2 * DOT_RADIUS + 2
# Gap between the bottom of the logo and the dots.
LOGO_GAP = 40

# The MB brand mark from the iOS and Apple TV apps (MBMark: a white MB on a
# red rounded square), as raw RGB, zlib-compressed, so this script needs no
# image library.
LOGO_SIZE = (160, 160)
LOGO_RGB = (
    "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"
    "jtPdwL1GcC3RXVpQHtn/B31S3CU="
)


def pixel(bits_per_pixel: int, level: int) -> bytes:
    """A grey pixel (level 0-255) in the framebuffer's native layout."""
    if bits_per_pixel == 32:
        return bytes((level, level, level, 0))
    if bits_per_pixel == 16:
        value = ((level >> 3) << 11) | ((level >> 2) << 5) | (level >> 3)
        return struct.pack("<H", value)
    raise ValueError(f"unsupported framebuffer depth: {bits_per_pixel} bpp")


def color_pixel(bits_per_pixel: int, red: int, green: int, blue: int) -> bytes:
    """A colored pixel in the same native layout pixel() assumes (XRGB / RGB565)."""
    if bits_per_pixel == 32:
        return bytes((blue, green, red, 0))
    if bits_per_pixel == 16:
        return struct.pack("<H", ((red >> 3) << 11) | ((green >> 2) << 5) | (blue >> 3))
    raise ValueError(f"unsupported framebuffer depth: {bits_per_pixel} bpp")


def logo_rows(bits_per_pixel: int) -> list:
    """The logo as rows of framebuffer bytes."""
    width, height = LOGO_SIZE
    rgb = zlib.decompress(b64decode("".join(LOGO_RGB)))
    cache = {}
    rows = []
    for y in range(height):
        row = []
        for x in range(width):
            offset = (y * width + x) * 3
            color = rgb[offset:offset + 3]
            if color not in cache:
                cache[color] = color_pixel(bits_per_pixel, *color)
            row.append(cache[color])
        rows.append(b"".join(row))
    return rows


def dots_rows(bits_per_pixel: int, tick: int) -> list:
    """The loader as DOTS_HEIGHT rows of bytes: dot `tick % DOTS` bright,
    the others dim."""
    levels = [[0] * DOTS_WIDTH for _ in range(DOTS_HEIGHT)]
    cy = DOTS_HEIGHT // 2
    for dot in range(DOTS):
        level = 255 if dot == tick % DOTS else 70
        cx = DOT_RADIUS + 1 + dot * DOT_SPACING
        for y in range(DOTS_HEIGHT):
            for x in range(cx - DOT_RADIUS, cx + DOT_RADIUS + 1):
                if (x - cx) ** 2 + (y - cy) ** 2 <= DOT_RADIUS ** 2:
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

    def _top(self) -> int:
        """Top of the logo, with the logo and dots centred together."""
        return max(0, (self.height - (LOGO_SIZE[1] + LOGO_GAP + DOTS_HEIGHT)) // 2)

    def draw_logo(self) -> None:
        left = max(0, (self.width - LOGO_SIZE[0]) // 2)
        top = self._top()
        for index, row in enumerate(logo_rows(self.bpp)):
            if top + index >= self.height:
                break
            row = row[:(self.width - left) * self.bytes_per_pixel]
            start = (top + index) * self.stride + left * self.bytes_per_pixel
            self.buffer[start:start + len(row)] = row

    def draw_dots(self, tick: int) -> None:
        left = (self.width - DOTS_WIDTH) // 2
        top = min(self._top() + LOGO_SIZE[1] + LOGO_GAP, self.height - DOTS_HEIGHT)
        for index, row in enumerate(dots_rows(self.bpp, tick)):
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
    screen.draw_logo()
    keyboards = open_keyboards()
    started = time.monotonic()
    handover_at = None
    tick = 0
    interrupted = False
    try:
        while time.monotonic() - started < GIVE_UP_SECONDS:
            screen.draw_dots(tick)
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
    was_active = True
    while gone < PLAYER_GONE_POLLS:
        time.sleep(1)
        state = player_state()
        gone = 0 if state in ("active", "activating", "deactivating", "reloading") else gone + 1
        if was_active and state != "active":
            # mpv has let go of the screen: the console's framebuffer shows
            # again, so put the logo back on it until the player returns.
            redraw_logo()
        was_active = state == "active"


def redraw_logo() -> None:
    try:
        screen = Screen()
    except (OSError, ValueError, struct.error):
        return
    try:
        screen.clear()
        screen.draw_logo()
    finally:
        screen.close()


if __name__ == "__main__":
    sys.exit(main())
