from pathlib import Path

from PIL import Image, ImageDraw

from sf_symbols_to_tgs.trace_png import convert
from sf_symbols_to_tgs.validate import read_tgs


def write_mask(path, draw_fn, size=96):
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw_fn(ImageDraw.Draw(image))
    image.save(path)


def test_trace_rectangle(tmp_path):
    png = tmp_path / "rectangle.png"
    out = tmp_path / "rectangle.tgs"
    write_mask(png, lambda draw: draw.rectangle((24, 24, 72, 72), fill=(0, 0, 0, 255)))

    result = convert(png, out, "rectangle")
    payload = read_tgs(out)

    assert result["contours"] == 1
    assert result["vertices"] >= 4
    assert payload["w"] == 512
    assert payload["h"] == 512


def test_trace_donut_hole(tmp_path):
    png = tmp_path / "donut.png"
    out = tmp_path / "donut.tgs"

    def draw_donut(draw):
        draw.rectangle((12, 12, 84, 84), fill=(0, 0, 0, 255))
        draw.rectangle((36, 36, 60, 60), fill=(0, 0, 0, 0))

    write_mask(png, draw_donut)
    result = convert(png, out, "donut")
    payload = read_tgs(out)

    assert result["contours"] == 2
    assert len(payload["layers"][0]["shapes"]) == 1


def test_trace_nested_contours(tmp_path):
    png = tmp_path / "nested.png"
    out = tmp_path / "nested.tgs"

    def draw_nested(draw):
        draw.rectangle((8, 8, 88, 88), fill=(0, 0, 0, 255))
        draw.rectangle((24, 24, 72, 72), fill=(0, 0, 0, 0))
        draw.rectangle((40, 40, 56, 56), fill=(0, 0, 0, 255))

    write_mask(png, draw_nested)
    result = convert(png, out, "nested")

    assert result["contours"] == 3
    assert result["groups"] >= 1


def test_trace_transparent_empty_input(tmp_path):
    png = tmp_path / "empty.png"
    out = tmp_path / "empty.tgs"
    Image.new("RGBA", (32, 32), (0, 0, 0, 0)).save(png)

    result = convert(png, out, "empty")
    payload = read_tgs(out)

    assert result["contours"] == 0
    assert payload["layers"]


def test_trace_output_is_deterministic(tmp_path):
    png = tmp_path / "rectangle.png"
    out_a = tmp_path / "a.tgs"
    out_b = tmp_path / "b.tgs"
    write_mask(png, lambda draw: draw.rectangle((24, 24, 72, 72), fill=(0, 0, 0, 255)))

    convert(png, out_a, "rectangle")
    convert(png, out_b, "rectangle")

    assert out_a.read_bytes() == out_b.read_bytes()
