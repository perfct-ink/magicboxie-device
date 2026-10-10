"""Boot loader: the red MB logo and a small spinner on black instead of the
console login.

Installed as a root-owned standalone script (standard library only) and run
by magicboxie-splash.service before getty. It switches tty1 to graphics mode
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
FRAME_SECONDS = 0.25
DOTS = 8
DOT_RADIUS, RING_RADIUS = 6, 28
BOX = 2 * (RING_RADIUS + DOT_RADIUS) + 4
# Gap between the bottom of the logo and the top of the spinner.
LOGO_GAP = 36
LOGO_COLOR = (230, 3, 10)

# The MB brand mark (web/static/apple-touch-icon.png, cropped): one alpha
# byte per pixel, zlib-compressed, so this script needs no image library.
# The daemon shows the same logo in mpv until its first picture (main.py).
LOGO_SIZE = (160, 136)
LOGO_MASK = (
    "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"
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


def logo_alpha() -> bytes:
    return zlib.decompress(b64decode("".join(LOGO_MASK)))


def logo_rows(bits_per_pixel: int) -> list:
    """The logo as rows of framebuffer bytes, red blended onto black."""
    width, height = LOGO_SIZE
    palette = [color_pixel(bits_per_pixel, *(c * a // 255 for c in LOGO_COLOR)) for a in range(256)]
    alpha = logo_alpha()
    return [b"".join(palette[a] for a in alpha[y * width:(y + 1) * width]) for y in range(height)]


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

    def _top(self) -> int:
        """Top of the logo, with the logo and spinner centred together."""
        return max(0, (self.height - (LOGO_SIZE[1] + LOGO_GAP + BOX)) // 2)

    def draw_logo(self) -> None:
        left = max(0, (self.width - LOGO_SIZE[0]) // 2)
        top = self._top()
        for index, row in enumerate(logo_rows(self.bpp)):
            if top + index >= self.height:
                break
            row = row[:(self.width - left) * self.bytes_per_pixel]
            start = (top + index) * self.stride + left * self.bytes_per_pixel
            self.buffer[start:start + len(row)] = row

    def draw_spinner(self, tick: int) -> None:
        left = (self.width - BOX) // 2
        top = min(self._top() + LOGO_SIZE[1] + LOGO_GAP, self.height - BOX)
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
    screen.draw_logo()
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
