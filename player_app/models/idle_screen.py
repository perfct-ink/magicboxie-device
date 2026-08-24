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


def _load_caption_font() -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.truetype(_CAPTION_FONT_PATH, _CAPTION_FONT_SIZE)
    except OSError:
        return ImageFont.load_default()


def render_idle_screen(library: MovieLibrary, output_path: Path = IDLE_SCREEN_PATH) -> Path:
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

    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path)
    return output_path


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
    caption = _truncate(title, draw, font)
    text_x = cell_x + (_CELL_WIDTH - draw.textlength(caption, font=font)) / 2
    text_y = cell_y + _CELL_PADDING + _THUMBNAIL_MAX_SIZE[1] + 6
    draw.text((text_x, text_y), caption, fill=_TEXT_COLOR, font=font)


def _truncate(title: str, draw: ImageDraw.ImageDraw, font) -> str:
    max_width = _CELL_WIDTH - _CELL_PADDING * 2
    if draw.textlength(title, font=font) <= max_width:
        return title
    while title and draw.textlength(title + "…", font=font) > max_width:
        title = title[:-1]
    return title + "…"
