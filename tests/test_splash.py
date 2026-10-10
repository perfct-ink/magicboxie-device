import struct

from player_app import splash


def _event(etype, code, value):
    return struct.pack(splash.EVENT_FORMAT, 0, 0, etype, code, value)


def test_escape_press_is_detected_but_release_and_other_keys_are_not():
    assert splash.is_escape(_event(1, 1, 1))
    assert not splash.is_escape(_event(1, 1, 0))
    assert not splash.is_escape(_event(1, 30, 1))
    assert not splash.is_escape(_event(0, 1, 1))
    assert splash.is_escape(_event(1, 30, 1) + _event(1, 1, 1))


def test_pixels_match_framebuffer_depth():
    assert splash.pixel(32, 255) == bytes((255, 255, 255, 0))
    assert len(splash.pixel(16, 128)) == 2


def test_spinner_rows_are_square_and_move_each_tick():
    rows = splash.spinner_rows(32, 0)
    assert len(rows) == splash.BOX and all(len(row) == splash.BOX * 4 for row in rows)
    assert rows != splash.spinner_rows(32, 1)


def test_player_gone_only_after_consecutive_stopped_polls():
    states = iter(["active", "inactive", "activating", "inactive", "inactive", "failed"])
    from unittest.mock import patch
    with patch.object(splash, "player_state", side_effect=lambda: next(states)), \
            patch.object(splash.time, "sleep"):
        splash.wait_for_player_to_go()
    assert next(states, None) is None


def test_logo_rows_are_red_on_black():
    rows = splash.logo_rows(32)
    width, height = splash.LOGO_SIZE
    assert len(rows) == height and all(len(row) == width * 4 for row in rows)
    pixels = {bytes(row[i:i + 4]) for row in rows for i in range(0, len(row), 4)}
    assert bytes(4) in pixels  # black around the letters
    assert splash.color_pixel(32, *splash.LOGO_COLOR) in pixels  # solid red inside them
    assert len(splash.logo_rows(16)[0]) == width * 2


def test_logo_comes_back_while_the_player_restarts():
    from unittest.mock import patch
    states = iter(["active", "deactivating", "activating", "active", "inactive", "inactive", "inactive"])
    with patch.object(splash, "player_state", side_effect=lambda: next(states)), \
            patch.object(splash.time, "sleep"), patch.object(splash, "redraw_logo") as redraw:
        splash.wait_for_player_to_go()
    assert redraw.call_count == 2
