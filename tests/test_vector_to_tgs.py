from pathlib import Path

from sf_symbols_to_tgs.validate import read_tgs, validate_tgs
from sf_symbols_to_tgs.vector_to_tgs import convert, convert_directory


FIXTURES = Path(__file__).parent / "fixtures"


def test_convert_path_fixture_preserves_curves(tmp_path):
    out = tmp_path / "fixture.tgs"
    result = convert(FIXTURES / "vector_path.json", out)
    payload = read_tgs(out)
    shape = payload["layers"][0]["shapes"][0]["it"][0]
    data = shape["ks"]["k"]

    assert result["subpaths"] == 1
    assert result["vertices"] >= 3
    assert any(tangent != [0, 0] for tangent in data["i"])
    assert any(tangent != [0, 0] for tangent in data["o"])
    validate_tgs(out)


def test_convert_layered_eraser_fixture_builds_masks_and_addbacks(tmp_path):
    out = tmp_path / "layered.tgs"
    result = convert(FIXTURES / "layered_eraser.json", out)
    payload = read_tgs(out)

    assert result["subpaths"] == 3
    assert result["groups"] >= 2
    assert any(layer.get("masksProperties") for layer in payload["layers"])
    validate_tgs(out)


def test_convert_directory_writes_manifest(tmp_path):
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    (input_dir / "vector_path.json").write_text((FIXTURES / "vector_path.json").read_text(encoding="utf-8"), encoding="utf-8")

    manifest = convert_directory(input_dir, output_dir)

    assert len(manifest) == 1
    assert (output_dir / "vector_path.tgs").exists()
    assert (output_dir / "manifest.json").exists()
