"""Builds a single composite image of every movie's thumbnail (plus title),
arranged in a grid - the "home screen" shown on the device's own HDMI output
whenever nothing is playing. mpv can only display one image or video at a
time, not a live interactive UI, so this is regenerated and loaded as an
ordinary (very long-lived) "file" whenever the device needs to show it.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .library import MovieLibrary

IDLE_SCREEN_PATH = Path("/tmp/magicboxie-idle-screen.png")

_COLUMNS = 6
# 6 columns * 320px cells = 1920px wide, matching a real HD display's width
# exactly - the canvas used to size itself to content alone (4 columns of
# 300px cells = 1200px), which mpv then had to upscale ~1.6x to fill a real
# screen, blurring the whole grid. Height still grows with row count (no
# reason to pad it out to a fixed 1080/1200 - mpv letterboxes the
# difference instead of stretching, which stays crisp).
_THUMBNAIL_MAX_SIZE = (280, 200)
_CAPTION_HEIGHT = 28
_CELL_PADDING = 20
_CELL_WIDTH = _THUMBNAIL_MAX_SIZE[0] + _CELL_PADDING * 2
_CELL_HEIGHT = _THUMBNAIL_MAX_SIZE[1] + _CAPTION_HEIGHT + _CELL_PADDING * 2
_BACKGROUND = (0, 0, 0)
_TEXT_COLOR = (220, 220, 220)
_CAPTION_FONT_SIZE = 18
# fonts-dejavu-core (installed by both the Dockerfile and `make pi-setup`) is
# the only TrueType font guaranteed to be on the box. Without it, PIL falls
# back to its own tiny unscaled bitmap font, which is illegible on an HDMI
# display - hence the fallback below only kicks in for dev machines that
# happen to be missing the package.
_CAPTION_FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

# Small top-left "syncing" badge, next to what's actually downloading -
# mirrors the iOS app's own floating sync indicator (see
# ContentView.SyncIndicator) so the same "something's downloading from the
# home server right now" state is visible on the TV too, not just the
# phone. Plain drawn shapes + ASCII text rather than a Unicode glyph (e.g.
# a refresh-arrow symbol): DejaVu Sans's exact symbol coverage isn't
# guaranteed, and a missing glyph renders as a "tofu" box - not worth the
# risk on a screen this was already once mistaken for a font bug (see the
# idle-screen resolution fix elsewhere in this file's git history). A
# static PNG can't actually spin either way.
_SYNC_BADGE_MARGIN = 16
_SYNC_BADGE_HEIGHT = 32
_SYNC_BADGE_PADDING_X = 12
_SYNC_BADGE_COLOR = (24, 24, 24)
_SYNC_DOT_COLOR = (245, 197, 66)
_SYNC_LABEL_COLOR = (230, 230, 230)
_SYNC_LABEL_FONT_SIZE = 15
_SYNC_LABEL_MAX_WIDTH = 360
_SYNC_DOT_DIAMETER = 10


def _load_caption_font() -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.truetype(_CAPTION_FONT_PATH, _CAPTION_FONT_SIZE)
    except OSError:
        return ImageFont.load_default()


def _load_sync_badge_font() -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.truetype(_CAPTION_FONT_PATH, _SYNC_LABEL_FONT_SIZE)
    except OSError:
        return ImageFont.load_default()


def render_idle_screen(
    library: MovieLibrary, output_path: Path = IDLE_SCREEN_PATH, syncing_title: str | None = None
) -> Path:
    """(Re)builds the grid from the library's current movies/thumbnails.
    Cheap enough - a handful of small images composited together - to just
    regenerate on demand each time it's shown rather than caching and
    invalidating it as the library changes."""
    movies = library.movies
    columns = _COLUMNS if movies else 1
    rows = max(1, -(-len(movies) // columns))  # ceil division
    canvas = Image.new("RGB", (columns * _CELL_WIDTH, rows * _CELL_HEIGHT), _BACKGROUND)
    draw = ImageDraw.Draw(canvas)
    font = _load_caption_font()

    for index, movie in enumerate(movies):
        col, row = index % columns, index // columns
        cell_x, cell_y = col * _CELL_WIDTH, row * _CELL_HEIGHT

        thumbnail_path = library.thumbnail_path_for(movie.id)
        if thumbnail_path is not None:
            _paste_thumbnail(canvas, thumbnail_path, cell_x, cell_y)

        _draw_caption(draw, movie.title, cell_x, cell_y, font)

    if syncing_title:
        _draw_sync_badge(canvas, draw, syncing_title)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path)
    return output_path


def _draw_sync_badge(canvas: Image.Image, draw: ImageDraw.ImageDraw, title: str) -> None:
    """Small pill in the top-left corner, right next to the title of
    whatever's actually downloading - not a generic "Syncing" label
    divorced from what it's for, and not competing with the movie grid's
    own bottom-right-leaning reading flow."""
    font = _load_sync_badge_font()
    label = _truncate_to_width(title, draw, font, _SYNC_LABEL_MAX_WIDTH)
    text_width = draw.textlength(label, font=font)
    badge_width = int(_SYNC_BADGE_PADDING_X * 2 + _SYNC_DOT_DIAMETER + 8 + text_width)

    x0 = _SYNC_BADGE_MARGIN
    y0 = _SYNC_BADGE_MARGIN
    x1 = x0 + badge_width
    y1 = y0 + _SYNC_BADGE_HEIGHT
    draw.rounded_rectangle((x0, y0, x1, y1), radius=_SYNC_BADGE_HEIGHT // 2, fill=_SYNC_BADGE_COLOR)

    dot_cx = x0 + _SYNC_BADGE_PADDING_X + _SYNC_DOT_DIAMETER // 2
    dot_cy = (y0 + y1) // 2
    dot_r = _SYNC_DOT_DIAMETER // 2
    draw.ellipse((dot_cx - dot_r, dot_cy - dot_r, dot_cx + dot_r, dot_cy + dot_r), fill=_SYNC_DOT_COLOR)

    text_x = dot_cx + dot_r + 8
    text_y = (y0 + y1) // 2 - _SYNC_LABEL_FONT_SIZE // 2 - 1
    draw.text((text_x, text_y), label, fill=_SYNC_LABEL_COLOR, font=font)


def _paste_thumbnail(canvas: Image.Image, thumbnail_path: Path, cell_x: int, cell_y: int) -> None:
    try:
        with Image.open(thumbnail_path) as source:
            thumbnail = source.convert("RGB")
    except OSError:
        return

    thumbnail.thumbnail(_THUMBNAIL_MAX_SIZE)
    paste_x = cell_x + (_CELL_WIDTH - thumbnail.width) // 2
    paste_y = cell_y + _CELL_PADDING + (_THUMBNAIL_MAX_SIZE[1] - thumbnail.height) // 2
    canvas.paste(thumbnail, (paste_x, paste_y))


def _draw_caption(draw: ImageDraw.ImageDraw, title: str, cell_x: int, cell_y: int, font) -> None:
    caption = _truncate_to_width(title, draw, font, _CELL_WIDTH - _CELL_PADDING * 2)
    text_x = cell_x + (_CELL_WIDTH - draw.textlength(caption, font=font)) / 2
    text_y = cell_y + _CELL_PADDING + _THUMBNAIL_MAX_SIZE[1] + 6
    draw.text((text_x, text_y), caption, fill=_TEXT_COLOR, font=font)


def _truncate_to_width(text: str, draw: ImageDraw.ImageDraw, font, max_width: int) -> str:
    if draw.textlength(text, font=font) <= max_width:
        return text
    while text and draw.textlength(text + "…", font=font) > max_width:
        text = text[:-1]
    return text + "…"
