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
