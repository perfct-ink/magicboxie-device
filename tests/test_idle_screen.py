from PIL import Image

from player_app.views.idle_screen import render_idle_screen

from fakes import FakeLibrary


class _EmptyLibrary:
    movies = []

    def thumbnail_path_for(self, movie_id):
        return None


def test_render_idle_screen_creates_an_image(tmp_path):
    output_path = tmp_path / "idle.png"

    result_path = render_idle_screen(FakeLibrary(), output_path=output_path)

    assert result_path == output_path
    assert output_path.exists()
    with Image.open(output_path) as image:
        image.verify()


def test_render_idle_screen_with_no_movies_does_not_crash(tmp_path):
    output_path = tmp_path / "idle.png"

    render_idle_screen(_EmptyLibrary(), output_path=output_path)

    assert output_path.exists()


def test_render_idle_screen_draws_sync_badge_only_when_a_title_is_given(tmp_path):
    """Doesn't assert on exact badge geometry (that's an implementation
    detail) - just that passing a syncing_title visibly changes the
    top-left corner of the image (the accent-colored dot) and omitting it
    (the default) doesn't, which is the only contract callers actually
    rely on."""
    library = FakeLibrary()

    def top_left_colors(path):
        with Image.open(path) as image:
            corner = image.convert("RGB").crop((0, 0, 400, 60))
            return corner.getcolors(maxcolors=1_000_000)

    without_badge = tmp_path / "idle-no-sync.png"
    render_idle_screen(library, output_path=without_badge, standard_definition=False)
    colors_without = top_left_colors(without_badge)

    with_badge = tmp_path / "idle-sync.png"
    render_idle_screen(library, output_path=with_badge, syncing_title="Alpha", standard_definition=False)
    colors_with = top_left_colors(with_badge)

    assert colors_without != colors_with
    # The badge's accent dot - confirms something was actually drawn there,
    # not just an incidental difference.
    assert any(r > 200 and g > 150 and b < 100 for _count, (r, g, b) in colors_with)


def test_render_idle_screen_composites_real_thumbnails(tmp_path):
    thumbnail_path = tmp_path / "0.jpg"
    # A solid, distinctly-colored square so its presence in the composited
    # grid is unambiguous - not a coincidental match with the background.
    Image.new("RGB", (100, 100), (255, 0, 0)).save(thumbnail_path)
    library = FakeLibrary(thumbnail_paths={0: thumbnail_path})
    output_path = tmp_path / "idle.png"

    render_idle_screen(library, output_path=output_path)

    # JPEG re-encoding is lossy, so the pasted thumbnail won't survive as an
    # exact (255, 0, 0) - just confirm a clearly red pixel made it in.
    with Image.open(output_path) as image:
        colors = image.convert("RGB").getcolors(maxcolors=1_000_000)
    assert any(r > 200 and g < 60 and b < 60 for _count, (r, g, b) in colors)


def test_render_idle_screen_footer_grows_with_keyboard_and_status_lines(tmp_path):
    def height(**kwargs):
        path = tmp_path / "idle.png"
        render_idle_screen(_EmptyLibrary(), output_path=path, standard_definition=False, **kwargs)
        with Image.open(path) as image:
            return image.height

    base = height()
    with_keyboard = height(keyboard_names=["Test Keyboard"])
    no_keyboard = height(keyboard_names=[])
    with_status = no_keyboard

    assert with_keyboard > base
    assert no_keyboard == with_keyboard
    assert with_status == no_keyboard


def test_render_idle_screen_adds_a_banner_for_activity(tmp_path):
    plain, banner = tmp_path / "a.png", tmp_path / "b.png"
    render_idle_screen(_EmptyLibrary(), output_path=plain, standard_definition=False)
    render_idle_screen(_EmptyLibrary(), output_path=banner, status_message="Updating device software",
                       standard_definition=False)
    with Image.open(plain) as a, Image.open(banner) as b:
        assert b.height > a.height
        assert b.getpixel((5, 5)) != (0, 0, 0)


def test_hd_grid_is_as_wide_as_an_hd_screen(tmp_path):
    output_path = tmp_path / "idle.png"
    render_idle_screen(FakeLibrary(), output_path=output_path, standard_definition=False)
    with Image.open(output_path) as image:
        assert image.width == 1920


def _slide(tmp_path, library, **kwargs):
    path = tmp_path / "slide.png"
    render_idle_screen(library, output_path=path, standard_definition=True, **kwargs)
    with Image.open(path) as image:
        return image.convert("RGB")


def test_sd_slide_is_one_full_screen_image(tmp_path):
    from player_app.views.idle_screen import SLIDE_SIZE

    for library in (FakeLibrary(), _EmptyLibrary()):
        for kwargs in ({}, {"status_message": "Updating"}, {"keyboard_names": ["Kbd"]}, {"syncing_title": "Alpha"}):
            assert _slide(tmp_path, library, **kwargs).size == SLIDE_SIZE == (640, 480)


def test_sd_slide_fills_the_poster_area_with_one_movie(tmp_path):
    red, blue = tmp_path / "0.jpg", tmp_path / "1.jpg"
    Image.new("RGB", (200, 300), (255, 0, 0)).save(red)
    Image.new("RGB", (200, 300), (0, 0, 255)).save(blue)
    library = FakeLibrary(thumbnail_paths={0: red, 1: blue})

    first = _slide(tmp_path, library, slide_index=0)
    second = _slide(tmp_path, library, slide_index=1)
    wrapped = _slide(tmp_path, library, slide_index=2)

    r, g, b = first.getpixel((320, 180))
    assert r > 200 and g < 60 and b < 60
    r, g, b = second.getpixel((320, 180))
    assert b > 200 and r < 60 and g < 60
    assert list(wrapped.getdata()) == list(first.getdata())
    # The poster is big: at least two thirds of the screen's height.
    column = [first.getpixel((320, y)) for y in range(480)]
    assert sum(1 for r, g, b in column if r > 200 and g < 60) >= 320


def test_sd_slide_keeps_the_bottom_for_text(tmp_path):
    poster = tmp_path / "0.jpg"
    Image.new("RGB", (400, 300), (255, 255, 255)).save(poster)
    image = _slide(tmp_path, FakeLibrary(thumbnail_paths={0: poster}))
    # The poster never reaches into the text area...
    assert image.getpixel((5, 470)) == (12, 12, 12)
    # ...which carries the title in white.
    text_area = image.crop((0, 368, 640, 480)).getcolors(maxcolors=1_000_000)
    assert any(min(color) > 230 for _count, color in text_area)


def test_sd_slide_shows_activity_in_red(tmp_path):
    image = _slide(tmp_path, FakeLibrary(), status_message="Updating device software")
    colors = image.crop((0, 368, 640, 480)).getcolors(maxcolors=1_000_000)
    assert any(r > 150 and g < 40 and b < 40 for _count, (r, g, b) in colors)


def test_sd_slide_marks_a_movie_stopped_partway(tmp_path):
    poster = tmp_path / "0.jpg"
    Image.new("RGB", (400, 300), (255, 255, 255)).save(poster)
    library = FakeLibrary(thumbnail_paths={0: poster})

    def red_pixels(image):
        return sum(1 for r, g, b in image.getdata() if r > 200 and g < 40 and b < 40)

    plain = _slide(tmp_path, library)
    marked = _slide(tmp_path, library, positions={0: 50})  # half of 100s
    finished = _slide(tmp_path, library, positions={0: 100})
    assert red_pixels(plain) == 0
    assert red_pixels(finished) == 0
    assert red_pixels(marked) > 0
    # The bar sits on the poster's bottom edge, above the text area.
    rows = [y for y in range(480) if any(
        (lambda p: p[0] > 200 and p[1] < 40 and p[2] < 40)(marked.getpixel((x, y))) for x in range(640))]
    assert max(rows) < 368


def test_hd_grid_marks_a_movie_stopped_partway(tmp_path):
    def red_pixels(**kwargs):
        path = tmp_path / "grid.png"
        render_idle_screen(FakeLibrary(), output_path=path, standard_definition=False, **kwargs)
        with Image.open(path) as image:
            return sum(1 for r, g, b in image.convert("RGB").getdata() if r > 200 and g < 40 and b < 40)

    assert red_pixels() == 0
    assert red_pixels(positions={1: 100}) > 0


def test_sd_activity_screen_replaces_the_slide(tmp_path):
    from player_app.views.idle_screen import IdleActivity

    poster = tmp_path / "0.jpg"
    Image.new("RGB", (400, 300), (255, 255, 255)).save(poster)
    library = FakeLibrary(thumbnail_paths={0: poster})
    activity = IdleActivity("Downloading", "Alpha", 50, "2 more to download", color=(245, 197, 66))

    image = _slide(tmp_path, library, activity=activity)
    assert image.size == (640, 480)
    # No bright poster: just its dark backdrop behind the card.
    assert image.getpixel((320, 30))[0] < 100
    # The progress bar is half filled with the accent color.
    bar_row = [image.getpixel((x, 249)) for x in range(640)]
    accent = [x for x, (r, g, b) in enumerate(bar_row) if r > 200 and 150 < g < 230 and b < 110]
    assert accent and 300 < max(accent) < 340

    no_percent = _slide(tmp_path, library, activity=IdleActivity("Preparing", "Zeta"))
    assert no_percent.size == (640, 480)


def test_sd_slide_backdrop_keeps_the_poster_aspect_ratio(tmp_path):
    from unittest.mock import patch
    from PIL import ImageOps
    from player_app.views import idle_screen

    poster = tmp_path / "0.jpg"
    Image.new("RGB", (200, 300), (255, 255, 255)).save(poster)
    sizes = []
    real_fit = ImageOps.fit

    def fit(image, size, *args, **kwargs):
        sizes.append(size)
        return real_fit(image, size, *args, **kwargs)

    with patch.object(idle_screen.ImageOps, "fit", side_effect=fit):
        _slide(tmp_path, FakeLibrary(thumbnail_paths={0: poster}))
    (width, height), = sizes
    # Same shape as the 640x368 poster area it is scaled up to.
    assert abs(width / height - 640 / 368) < 0.1
