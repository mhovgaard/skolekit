import base64
import copy
import io
import re

import pytest
from PIL import Image

from skoleklar import dashboard_renderer as renderer
from skoleklar.weekend_art import (
    ART_ORIGIN, ART_SIZE, PRESETS, artwork_bytes, panel_artwork, select_artwork,
)
from tests_python.test_weather import weather_state


@pytest.mark.parametrize("day,icon,expected", [
    ("2026-03-07", "sun", "spring-dry"),
    ("2026-05-02", "rain", "spring-rain"),
    ("2026-06-06", "partly", "summer-dry"),
    ("2026-08-01", "storm", "summer-rain"),
    ("2026-09-05", "cloud", "autumn-dry"),
    ("2026-10-24", "rain", "autumn-rain"),
    ("2026-12-05", "fog", "winter-dry"),
    ("2027-01-02", "rain", "winter-rain"),
    ("2027-02-06", "snow", "winter-snow"),
    ("2027-03-06", "snow", "winter-snow"),
])
def test_selects_season_and_cover_weather(day, icon, expected):
    assert select_artwork({"date": day, "icon": icon}) == expected


@pytest.mark.parametrize("preset", PRESETS)
def test_all_packaged_presets_have_valid_rgb_and_six_pigment_versions(preset):
    with Image.open(io.BytesIO(artwork_bytes(preset))) as original:
        assert original.size == (680, 400) and original.mode == "RGB"
    art = panel_artwork(preset)
    assert art.size == ART_SIZE and art.mode == "P"
    assert set(art.tobytes()) <= set(renderer.PANEL_NOMINAL_RGB)
    assert art.getpalette() == renderer.panel_palette()


def test_both_views_select_first_displayed_day_and_keep_the_forecast_intact():
    state = weather_state("Museum")
    state["weather"]["days"][1]["icon"] = "sun"
    untouched = copy.deepcopy(state)
    wet = renderer.render_png(state)
    html = renderer.render_html(state)
    assert 'data-artwork="autumn-rain"' in html
    encoded = re.search(r'<img src="data:image/webp;base64,([^"]+)"', html)[1]
    assert base64.b64decode(encoded) == artwork_bytes("autumn-rain")
    x, y = ART_ORIGIN
    assert wet.crop((x, y, x + ART_SIZE[0], y + ART_SIZE[1])).tobytes() == panel_artwork("autumn-rain").tobytes()
    renderer.validate_panel_image(wet)
    assert len(renderer.pack_panel_frame(wet)) == 192000
    # The illustration introduces red/green only inside its own rectangle.
    assert set(wet.crop((0, y + ART_SIZE[1], 340, 480)).tobytes()) <= set(renderer.PANEL_DESIGN_CODES)
    state["weather"]["days"][0]["icon"] = "sun"
    dry = renderer.render_png(state)
    assert 'data-artwork="autumn-dry"' in renderer.render_html(state)
    assert dry.crop((0, 82, 340, 282)).tobytes() != wet.crop((0, 82, 340, 282)).tobytes()
    # The only changed input affects the left cover; the right-hand forecast is unchanged.
    assert dry.crop((341, 0, 800, 480)).tobytes() == wet.crop((341, 0, 800, 480)).tobytes()
    untouched["weather"]["days"][0]["icon"] = "sun"
    assert state == untouched


def test_weekday_view_does_not_include_weekend_artwork():
    state = weather_state()
    del state["weather"]
    assert "data-artwork=" not in renderer.render_html(state)


def test_artwork_paths_are_allowlisted():
    with pytest.raises(ValueError, match="Unknown weekend artwork"):
        artwork_bytes("../../private")


def own_picture(folder, preset, *, size=(680, 400), kind="WEBP"):
    folder.mkdir(exist_ok=True)
    Image.new("RGB", size, (12, 34, 56)).save(folder / f"{preset}.webp", kind)


def test_a_households_own_picture_replaces_the_packaged_house_for_that_preset(tmp_path):
    from skolekit.state import using_state_dir

    packaged = artwork_bytes("summer-dry")
    with using_state_dir(tmp_path):
        own_picture(tmp_path / "artwork", "summer-dry")
        own = artwork_bytes("summer-dry")
        assert own == (tmp_path / "artwork/summer-dry.webp").read_bytes() != packaged
        assert panel_artwork("summer-dry").size == ART_SIZE
        assert artwork_bytes("winter-snow") != own          # the other presets stay packaged
    assert artwork_bytes("summer-dry") == packaged          # and so does everyone else's board


def test_a_wrong_own_picture_gives_the_packaged_house(tmp_path):
    from skolekit.state import using_state_dir

    packaged = artwork_bytes("autumn-dry")
    with using_state_dir(tmp_path):
        own_picture(tmp_path / "artwork", "autumn-dry", size=(640, 480))
        assert artwork_bytes("autumn-dry") == packaged
        own_picture(tmp_path / "artwork", "autumn-dry", kind="PNG")
        assert artwork_bytes("autumn-dry") == packaged
        (tmp_path / "artwork/autumn-dry.webp").write_bytes(b"not a picture")
        assert artwork_bytes("autumn-dry") == packaged


def test_the_cloud_scan_fetches_only_the_named_pictures(tmp_path):
    from skoleklar.runtime.gcp import GcpRuntime
    from tests_python.test_cloud_scan_pinned import FakeGCS

    gcs = FakeGCS()
    gcs.put("private", "state/artwork/summer-dry.webp", b"picture")
    gcs.put("private", "state/artwork/notes.txt", b"no")
    gcs.put("private", "state/artwork/unknown.webp", b"no")
    runtime = GcpRuntime()
    runtime.private_bucket = gcs.bucket("private")
    runtime.checkout(tmp_path)
    assert [path.name for path in (tmp_path / "artwork").iterdir()] == ["summer-dry.webp"]
    assert not any(" upload " in entry or " write " in entry for entry in gcs.trail)
