"""Builds the "home screen" image shown on the device's own HDMI output
whenever nothing is playing: a grid of every movie's thumbnail (plus title)
on an HD screen, or one full-screen slide per movie on the default 720x480
TV output, which the controller steps through as a slideshow. While the
device is downloading or transcoding, the TV output shows that instead of
the slideshow (see IdleActivity). mpv can only display one image or video at a
time, not a live interactive UI, so this is regenerated and loaded as an
ordinary (very long-lived) "file" whenever the device needs to show it.
"""
from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Mapping, Optional, Sequence

from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageOps

from ..display import is_standard_definition
from ..storage import atomic_write
from ..models.library import MovieLibrary

IDLE_SCREEN_PATH = Path("/tmp/magicboxie-idle-screen.png")

# Standard definition (the default 720x480 output, see display.py) gets the
# slideshow below instead of the grid: a grid that grows a row per three
# movies gets shrunk to fit 480 lines until neither posters nor captions
# can be read.
_SD = is_standard_definition()
_COLUMNS = 6
# HD grid: 6 columns * 320px cells = 1920px wide, matching a real HD display's width
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
# fonts-dejavu-core (installed by both the Dockerfile and `make setup`) is
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

# Footer lines: update/internet progress and whether a keyboard is attached
# (and what Escape does). Plain ASCII/em-dash only (DejaVu Sans covers it).
_HINT_LINE_HEIGHT = 40
_HINT_PADDING = 16
_HINT_FONT_SIZE = 20
# Big banner across the top for whatever the device is busy doing (internet
# check, updating, downloading...), readable from across the room.
_BANNER_HEIGHT = 150
_BANNER_FONT_SIZE = 64
_BANNER_BACKGROUND = (176, 16, 24)
_BANNER_COLOR = (255, 255, 255)
_HINT_FOUND_COLOR = (120, 200, 120)
_HINT_MISSING_COLOR = (245, 197, 66)
_MIN_CANVAS_WIDTH = _COLUMNS * _CELL_WIDTH

# SD slideshow (see render_idle_screen): one movie per image, so nothing has
# to shrink to fit a grid. 640x480 is 4:3 in square pixels, which mpv's
# --monitorpixelaspect (see main.py) stretches to exactly fill the 720x480
# output of a 4:3 TV; a 720x480 image would be letterboxed instead.
SLIDE_SIZE = (640, 480)
# The poster sits above a reserved text area. Composite TVs overscan, so
# everything stays inside a safe margin from each edge.
_SLIDE_SAFE_X = 36
_SLIDE_SAFE_Y = 22
_SLIDE_TEXT_HEIGHT = 112
_SLIDE_TEXT_BACKGROUND = (12, 12, 12)
_SLIDE_TITLE_FONT_SIZE = 32
_SLIDE_INFO_FONT_SIZE = 20
_SLIDE_INFO_COLOR = (170, 170, 170)
_BOLD_FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

# "Watched partway" marker under a poster, like the web page's progress bar.
_PROGRESS_HEIGHT = 8
_PROGRESS_TRACK = (70, 70, 70)
_PROGRESS_FILL = (229, 9, 20)

# SD activity screen (downloading/transcoding), shown instead of the slideshow.
_ACTIVITY_HEADING_FONT_SIZE = 44
_ACTIVITY_TITLE_FONT_SIZE = 30
_ACTIVITY_BAR_WIDTH = 440
_ACTIVITY_BAR_HEIGHT = 18
_ACTIVITY_PERCENT_FONT_SIZE = 26
_ACTIVITY_TRACK = (60, 60, 60)


@dataclass(frozen=True)
class IdleActivity:
    """What the device is busy with while idle, drawn full-screen on the TV
    output in place of the slideshow: downloading from the media server,
    transcoding here, or waiting on the media server's own transcode."""
    heading: str
    title: str
    percent: Optional[int] = None
    detail: Optional[str] = None
    # The movie whose poster goes behind it, once it's in the library.
    movie_id: Optional[int] = None
    color: tuple = _SYNC_DOT_COLOR


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
    library: MovieLibrary,
    output_path: Path = IDLE_SCREEN_PATH,
    syncing_title: str | None = None,
    keyboard_names: Sequence[str] | None = None,
    status_message: str | None = None,
    slide_index: int = 0,
    standard_definition: bool = _SD,
    activity: Optional[IdleActivity] = None,
    positions: Optional[Mapping[int, int]] = None,
) -> Path:
    """(Re)builds the grid from the library's current movies/thumbnails.
    Cheap enough - a handful of small images composited together - to just
    regenerate on demand each time it's shown rather than caching and
    invalidating it as the library changes. keyboard_names (None = unknown,
    draws nothing) adds a footer saying whether a keyboard is detected.
    In standard definition it instead draws the single slide for movie
    slide_index (wrapping around), or the activity screen while there is
    one. positions (movie id -> saved resume seconds) adds a progress
    marker under every movie that was stopped partway."""
    positions = positions or {}
    if standard_definition:
        if activity is not None:
            canvas = _render_activity_slide(library, activity, status_message)
        else:
            canvas = _render_slide(library, slide_index, syncing_title, keyboard_names,
                                   status_message, positions)
        image_bytes = BytesIO()
        canvas.save(image_bytes, format="PNG", compress_level=1)
        atomic_write(output_path, image_bytes.getvalue())
        return output_path

    if activity is not None and not status_message:
        status_message = f"{activity.heading} {activity.title}"
    movies = library.movies
    columns = _COLUMNS if movies else 1
    rows = max(1, -(-len(movies) // columns))  # ceil division
    grid_height = rows * _CELL_HEIGHT
    banner_height = _BANNER_HEIGHT if status_message else 0
    footer = _footer_lines(keyboard_names)
    footer_height = len(footer) * _HINT_LINE_HEIGHT + _HINT_PADDING if footer else 0
    canvas = Image.new(
        "RGB",
        (max(columns * _CELL_WIDTH, _MIN_CANVAS_WIDTH), banner_height + grid_height + footer_height),
        _BACKGROUND,
    )
    draw = ImageDraw.Draw(canvas)
    font = _load_caption_font()

    for index, movie in enumerate(movies):
        col, row = index % columns, index // columns
        cell_x, cell_y = col * _CELL_WIDTH, banner_height + row * _CELL_HEIGHT

        thumbnail_path = library.thumbnail_path_for(movie.id)
        if thumbnail_path is not None:
            _paste_thumbnail(canvas, thumbnail_path, cell_x, cell_y)
        fraction = _watched_fraction(movie, positions)
        if fraction is not None:
            bar_y = cell_y + _CELL_PADDING + _THUMBNAIL_MAX_SIZE[1] - _PROGRESS_HEIGHT
            _draw_progress(draw, cell_x + _CELL_PADDING, bar_y,
                           _THUMBNAIL_MAX_SIZE[0], fraction)

        _draw_caption(draw, movie.title, cell_x, cell_y, font)

    if syncing_title:
        _draw_sync_badge(canvas, draw, syncing_title, banner_height)

    if status_message:
        _draw_banner(canvas, draw, status_message)
    _draw_footer(canvas, draw, footer, banner_height + grid_height)

    image_bytes = BytesIO()
    canvas.save(image_bytes, format="PNG")
    atomic_write(output_path, image_bytes.getvalue())
    return output_path


def _render_slide(
    library: MovieLibrary,
    slide_index: int,
    syncing_title: str | None,
    keyboard_names: Sequence[str] | None,
    status_message: str | None,
    positions: Mapping[int, int],
) -> Image.Image:
    width, height = SLIDE_SIZE
    poster_bottom = height - _SLIDE_TEXT_HEIGHT
    canvas = Image.new("RGB", SLIDE_SIZE, _BACKGROUND)
    draw = ImageDraw.Draw(canvas)
    title_font = _load_font(_BOLD_FONT_PATH, _SLIDE_TITLE_FONT_SIZE)
    info_font = _load_font(_CAPTION_FONT_PATH, _SLIDE_INFO_FONT_SIZE)
    text_width = width - 2 * _SLIDE_SAFE_X

    movies = library.movies
    if movies:
        index = slide_index % len(movies)
        movie = movies[index]
        title = movie.title
        info = (f"{index + 1} of {len(movies)}", _SLIDE_INFO_COLOR)
        thumbnail_path = library.thumbnail_path_for(movie.id)
        poster_box = None
        if thumbnail_path is not None:
            poster_box = _paste_slide_poster(canvas, thumbnail_path, poster_bottom)
        fraction = _watched_fraction(movie, positions)
        if fraction is not None:
            # Along the poster's bottom edge, or where it would be.
            left, right, bottom = poster_box or (_SLIDE_SAFE_X, width - _SLIDE_SAFE_X, poster_bottom - 10)
            _draw_progress(draw, left, bottom - _PROGRESS_HEIGHT, right - left, fraction)
    else:
        title = "No movies yet"
        info = ("Movies you add will show up here", _SLIDE_INFO_COLOR)

    # What the device is busy doing outranks the slide counter.
    if status_message:
        info = (status_message, _BANNER_COLOR)
    elif syncing_title:
        info = (f"Downloading {syncing_title}", _SYNC_DOT_COLOR)
    elif keyboard_names:
        info = ("Keyboard detected \u2014 press Esc to quit the player", _HINT_FOUND_COLOR)

    draw.rectangle((0, poster_bottom, width, height), fill=_SLIDE_TEXT_BACKGROUND)
    title = _truncate_to_width(title, draw, title_font, text_width)
    title_y = poster_bottom + 12
    draw.text(((width - draw.textlength(title, font=title_font)) / 2, title_y),
              title, fill=_BANNER_COLOR, font=title_font)

    info_text, info_color = info
    info_text = _truncate_to_width(info_text, draw, info_font, text_width - 24)
    info_width = draw.textlength(info_text, font=info_font)
    info_x = (width - info_width) / 2
    info_y = height - _SLIDE_SAFE_Y - _SLIDE_INFO_FONT_SIZE - 6
    if status_message:
        draw.rounded_rectangle(
            (info_x - 12, info_y - 5, info_x + info_width + 12, info_y + _SLIDE_INFO_FONT_SIZE + 7),
            radius=8, fill=_BANNER_BACKGROUND,
        )
    draw.text((info_x, info_y), info_text, fill=info_color, font=info_font)
    return canvas


def _paste_slide_poster(canvas: Image.Image, thumbnail_path: Path, poster_bottom: int):
    """The poster as large as it fits above the text area, over a dark,
    blurred copy of itself so its edges don't float on flat black. Returns
    the poster's (left, right, bottom), or None if it couldn't be read."""
    poster = _paste_backdrop(canvas, thumbnail_path, poster_bottom, 0.35)
    if poster is None:
        return None
    width = canvas.width
    box = (width - 2 * _SLIDE_SAFE_X, poster_bottom - _SLIDE_SAFE_Y - 10)
    scale = min(box[0] / poster.width, box[1] / poster.height)
    size = (max(1, round(poster.width * scale)), max(1, round(poster.height * scale)))
    poster = poster.resize(size, Image.BILINEAR)
    left, top = (width - size[0]) // 2, _SLIDE_SAFE_Y + (box[1] - size[1]) // 2
    canvas.paste(poster, (left, top))
    return left, left + size[0], top + size[1]


def _paste_backdrop(canvas: Image.Image, thumbnail_path: Path, height: int, brightness: float):
    """Fills the top `height` rows with a dark, blurred copy of the poster
    and returns the poster itself (None if it can't be read)."""
    try:
        with Image.open(thumbnail_path) as source:
            poster = source.convert("RGB")
    except OSError:
        return None
    # Blurring a tiny copy and scaling it up is far cheaper on the Pi Zero
    # than a real blur at full size, and looks the same.
    # The tiny copy is cropped to the area's own shape, so scaling it up
    # never stretches the picture.
    small = (32, max(1, round(32 * height / canvas.width)))
    backdrop = ImageOps.fit(poster, small).resize((canvas.width, height), Image.BILINEAR)
    canvas.paste(ImageEnhance.Brightness(backdrop).enhance(brightness), (0, 0))
    return poster


def _render_activity_slide(
    library: MovieLibrary, activity: IdleActivity, status_message: str | None,
) -> Image.Image:
    """Full-screen "Downloading" / "Transcoding" card: what, which movie,
    how far along, and what's left after it."""
    width, height = SLIDE_SIZE
    canvas = Image.new("RGB", SLIDE_SIZE, _BACKGROUND)
    if activity.movie_id is not None:
        thumbnail_path = library.thumbnail_path_for(activity.movie_id)
        if thumbnail_path is not None:
            _paste_backdrop(canvas, thumbnail_path, height, 0.25)
    draw = ImageDraw.Draw(canvas)
    text_width = width - 2 * _SLIDE_SAFE_X

    def centered(text, font, y, color):
        text = _truncate_to_width(text, draw, font, text_width)
        draw.text(((width - draw.textlength(text, font=font)) / 2, y), text, fill=color, font=font)

    centered(activity.heading, _load_font(_BOLD_FONT_PATH, _ACTIVITY_HEADING_FONT_SIZE), 96, activity.color)
    centered(activity.title, _load_font(_BOLD_FONT_PATH, _ACTIVITY_TITLE_FONT_SIZE), 170, _BANNER_COLOR)

    if activity.percent is not None:
        left = (width - _ACTIVITY_BAR_WIDTH) // 2
        top = 240
        bottom = top + _ACTIVITY_BAR_HEIGHT
        radius = _ACTIVITY_BAR_HEIGHT // 2
        draw.rounded_rectangle((left, top, left + _ACTIVITY_BAR_WIDTH, bottom), radius=radius, fill=_ACTIVITY_TRACK)
        filled = round(_ACTIVITY_BAR_WIDTH * max(0, min(100, activity.percent)) / 100)
        if filled > 0:
            draw.rounded_rectangle((left, top, left + max(filled, _ACTIVITY_BAR_HEIGHT), bottom),
                                   radius=radius, fill=activity.color)
        centered(f"{activity.percent}%", _load_font(_CAPTION_FONT_PATH, _ACTIVITY_PERCENT_FONT_SIZE),
                 bottom + 14, _BANNER_COLOR)

    info_font = _load_font(_CAPTION_FONT_PATH, _SLIDE_INFO_FONT_SIZE)
    info_y = height - _SLIDE_SAFE_Y - _SLIDE_INFO_FONT_SIZE - 6
    if status_message:
        info_text = _truncate_to_width(status_message, draw, info_font, text_width - 24)
        info_width = draw.textlength(info_text, font=info_font)
        info_x = (width - info_width) / 2
        draw.rounded_rectangle(
            (info_x - 12, info_y - 5, info_x + info_width + 12, info_y + _SLIDE_INFO_FONT_SIZE + 7),
            radius=8, fill=_BANNER_BACKGROUND,
        )
        draw.text((info_x, info_y), info_text, fill=_BANNER_COLOR, font=info_font)
    elif activity.detail:
        centered(activity.detail, info_font, info_y, _SLIDE_INFO_COLOR)
    return canvas


def _watched_fraction(movie, positions: Mapping[int, int]) -> Optional[float]:
    """How far into the movie its saved resume point is, or None if it was
    never started (or finished, which clears the position)."""
    position = positions.get(movie.id, 0)
    duration = movie.duration_seconds
    if position <= 0 or not duration or position >= duration:
        return None
    return position / duration


def _draw_progress(draw: ImageDraw.ImageDraw, left: int, top: int, width: int, fraction: float) -> None:
    draw.rectangle((left, top, left + width - 1, top + _PROGRESS_HEIGHT - 1), fill=_PROGRESS_TRACK)
    filled = max(_PROGRESS_HEIGHT, round(width * fraction))
    draw.rectangle((left, top, left + filled - 1, top + _PROGRESS_HEIGHT - 1), fill=_PROGRESS_FILL)


def _load_font(path: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


def _footer_lines(keyboard_names: Sequence[str] | None) -> list:
    lines = []
    if keyboard_names:
        lines.append((
            f"Keyboard detected ({keyboard_names[0]}) \u2014 press Esc to quit the player and reach the login prompt",
            _HINT_FOUND_COLOR,
        ))
    elif keyboard_names is not None:
        lines.append((
            "No keyboard detected \u2014 plug in a USB keyboard to use Esc to quit the player",
            _HINT_MISSING_COLOR,
        ))
    return lines


def _draw_footer(canvas: Image.Image, draw: ImageDraw.ImageDraw, lines: list, top: int) -> None:
    if not lines:
        return
    try:
        font = ImageFont.truetype(_CAPTION_FONT_PATH, _HINT_FONT_SIZE)
    except OSError:
        font = ImageFont.load_default()
    for index, (text, color) in enumerate(lines):
        text = _truncate_to_width(text, draw, font, canvas.width - 2 * _CELL_PADDING)
        x = (canvas.width - draw.textlength(text, font=font)) / 2
        y = top + _HINT_PADDING // 2 + index * _HINT_LINE_HEIGHT + (_HINT_LINE_HEIGHT - _HINT_FONT_SIZE) // 2
        draw.text((x, y), text, fill=color, font=font)


def _draw_banner(canvas: Image.Image, draw: ImageDraw.ImageDraw, message: str) -> None:
    draw.rectangle((0, 0, canvas.width, _BANNER_HEIGHT), fill=_BANNER_BACKGROUND)
    try:
        font = ImageFont.truetype(_CAPTION_FONT_PATH, _BANNER_FONT_SIZE)
    except OSError:
        font = ImageFont.load_default()
    message = _truncate_to_width(message, draw, font, canvas.width - 2 * _CELL_PADDING)
    x = (canvas.width - draw.textlength(message, font=font)) / 2
    draw.text((x, (_BANNER_HEIGHT - _BANNER_FONT_SIZE) // 2 - 6), message, fill=_BANNER_COLOR, font=font)


def _draw_sync_badge(
    canvas: Image.Image, draw: ImageDraw.ImageDraw, title: str, top: int = 0
) -> None:
    """Small pill in the top-left corner, right next to the title of
    whatever's actually downloading - not a generic "Syncing" label
    divorced from what it's for, and not competing with the movie grid's
    own bottom-right-leaning reading flow."""
    font = _load_sync_badge_font()
    label = _truncate_to_width(title, draw, font, _SYNC_LABEL_MAX_WIDTH)
    text_width = draw.textlength(label, font=font)
    badge_width = int(_SYNC_BADGE_PADDING_X * 2 + _SYNC_DOT_DIAMETER + 8 + text_width)

    x0 = _SYNC_BADGE_MARGIN
    y0 = top + _SYNC_BADGE_MARGIN
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
