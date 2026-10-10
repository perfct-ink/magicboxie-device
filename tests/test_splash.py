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


def test_dots_are_three_in_a_row_and_move_each_tick():
    rows = splash.dots_rows(32, 0)
    assert len(rows) == splash.DOTS_HEIGHT
    assert all(len(row) == splash.DOTS_WIDTH * 4 for row in rows)
    assert rows != splash.dots_rows(32, 1)
    assert rows == splash.dots_rows(32, splash.DOTS)

    middle = rows[splash.DOTS_HEIGHT // 2]
    levels = [middle[x * 4] for x in range(splash.DOTS_WIDTH)]
    centres = [splash.DOT_RADIUS + 1 + dot * splash.DOT_SPACING for dot in range(splash.DOTS)]
    assert [levels[x] for x in centres] == [255, 70, 70]


def test_player_gone_only_after_consecutive_stopped_polls():
    states = iter(["active", "inactive", "activating", "inactive", "inactive", "failed"])
    from unittest.mock import patch
    with patch.object(splash, "player_state", side_effect=lambda: next(states)), \
            patch.object(splash.time, "sleep"):
        splash.wait_for_player_to_go()
    assert next(states, None) is None


def test_logo_is_a_white_mb_on_red():
    rows = splash.logo_rows(32)
    width, height = splash.LOGO_SIZE
    assert len(rows) == height and all(len(row) == width * 4 for row in rows)
    centre_row = rows[height // 2]
    pixels = [tuple(centre_row[i:i + 4]) for i in range(0, len(centre_row), 4)]
    assert any(b < 40 and g < 40 and r > 180 for b, g, r, _ in pixels)  # red square
    assert any(min(b, g, r) > 230 for b, g, r, _ in pixels)  # white letters
    assert rows[0][:4] == bytes(4)  # rounded corner on black
    assert len(splash.logo_rows(16)[0]) == width * 2


def test_logo_comes_back_while_the_player_restarts():
    from unittest.mock import patch
    states = iter(["active", "deactivating", "activating", "active", "inactive", "inactive", "inactive"])
    with patch.object(splash, "player_state", side_effect=lambda: next(states)), \
            patch.object(splash.time, "sleep"), patch.object(splash, "redraw_logo") as redraw:
        splash.wait_for_player_to_go()
    assert redraw.call_count == 2
