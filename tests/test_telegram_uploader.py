import importlib.util
from pathlib import Path

from PIL import Image, ImageDraw

from sf_symbols_to_tgs.trace_png import convert


def load_uploader():
    path = Path(__file__).resolve().parents[1] / "examples" / "upload_telegram_pack.py"
    spec = importlib.util.spec_from_file_location("upload_telegram_pack", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_tgs(tmp_path):
    png = tmp_path / "mask.png"
    out = tmp_path / "mask.tgs"
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    ImageDraw.Draw(image).rectangle((8, 8, 56, 56), fill=(0, 0, 0, 255))
    image.save(png)
    convert(png, out, "mask")
    return out


def test_load_manifest_validates_tgs(tmp_path):
    uploader = load_uploader()
    tgs = make_tgs(tmp_path)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(f'[{{"file": "{tgs}", "emoji": ["✅"], "keywords": ["ok"]}}]', encoding="utf-8")

    manifest = uploader.load_manifest(manifest_path)

    assert manifest[0]["file"] == str(tgs)
    assert manifest[0]["emoji"] == ["✅"]


def test_upload_pack_uses_create_then_add(monkeypatch, tmp_path):
    uploader = load_uploader()
    tgs = make_tgs(tmp_path)
    manifest = [{"file": str(tgs), "emoji": ["✅"]} for _ in range(51)]
    json_calls = []
    upload_count = 0

    def fake_multipart(token, method, fields, files):
        nonlocal upload_count
        upload_count += 1
        assert method == "uploadStickerFile"
        return {"file_id": f"file-{upload_count}"}

    def fake_json(token, method, payload=None):
        json_calls.append((method, payload))
        return True

    monkeypatch.setattr(uploader, "api_multipart", fake_multipart)
    monkeypatch.setattr(uploader, "api_json", fake_json)

    uploaded = uploader.upload_pack("token", 123, "sample_by_bot", "Sample", manifest)

    assert len(uploaded) == 51
    assert json_calls[0][0] == "createNewStickerSet"
    assert len(json_calls[0][1]["stickers"]) == 50
    assert json_calls[1][0] == "addStickerToSet"
