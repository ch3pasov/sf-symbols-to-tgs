import pytest
from PIL import Image, ImageDraw

from sf_symbols_to_tgs.trace_png import convert
from sf_symbols_to_tgs.validate import validate_path, validate_tgs


def test_validate_generated_tgs(tmp_path):
    png = tmp_path / "mask.png"
    out = tmp_path / "mask.tgs"
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    ImageDraw.Draw(image).ellipse((12, 12, 52, 52), fill=(0, 0, 0, 255))
    image.save(png)
    convert(png, out, "mask")

    result = validate_tgs(out)

    assert result["layers"] == 1
    assert result["bytes"] > 0


def test_validate_directory(tmp_path):
    png = tmp_path / "mask.png"
    out = tmp_path / "mask.tgs"
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    ImageDraw.Draw(image).rectangle((8, 8, 56, 56), fill=(0, 0, 0, 255))
    image.save(png)
    convert(png, out, "mask")

    assert len(validate_path(tmp_path)) == 1


def test_validate_rejects_empty_shapes_by_default(tmp_path):
    png = tmp_path / "empty.png"
    out = tmp_path / "empty.tgs"
    Image.new("RGBA", (32, 32), (0, 0, 0, 0)).save(png)
    convert(png, out, "empty")

    with pytest.raises(ValueError):
        validate_tgs(out)
    assert validate_tgs(out, require_shapes=False)["layers"] == 1
