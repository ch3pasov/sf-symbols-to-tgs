#!/usr/bin/env python3
import argparse
import json
import mimetypes
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

try:
    from sf_symbols_to_tgs.validate import validate_tgs
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from sf_symbols_to_tgs.validate import validate_tgs


API_ROOT = "https://api.telegram.org/bot{token}/{method}"
MAX_STICKERS = 200
MAX_INITIAL_STICKERS = 50


def load_env(path):
    values = {}
    path = Path(path)
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def config_value(config, key, required=True):
    value = os.environ.get(key) or config.get(key)
    if required and not value:
        raise SystemExit(f"Missing {key}. Put it in .env or export it.")
    return value


def mask_token(token):
    if ":" not in token:
        return "<hidden>"
    prefix, suffix = token.split(":", 1)
    return f"{prefix}:...{suffix[-4:]}"


def normalize_bot_username(username):
    return username.strip().lstrip("@")


def validate_set_name(name, bot_username):
    if not 1 <= len(name) <= 64:
        raise ValueError("TELEGRAM_SET_NAME must be 1-64 characters")
    if not re.match(r"^[A-Za-z][A-Za-z0-9_]*$", name):
        raise ValueError("TELEGRAM_SET_NAME can contain only English letters, digits, and underscores, and must start with a letter")
    if "__" in name:
        raise ValueError("TELEGRAM_SET_NAME cannot contain consecutive underscores")
    suffix = f"_by_{bot_username}".lower()
    if not name.lower().endswith(suffix):
        raise ValueError(f"TELEGRAM_SET_NAME must end with {suffix}")


def read_response(request):
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            data = response.read()
    except urllib.error.HTTPError as error:
        data = error.read()
        try:
            parsed = json.loads(data.decode("utf-8"))
        except Exception:
            raise SystemExit(f"Telegram HTTP error {error.code}: {data[:300]!r}")
        raise SystemExit(f"Telegram error {error.code}: {parsed.get('description', parsed)}")
    except (urllib.error.URLError, TimeoutError, socket.timeout) as error:
        raise SystemExit(f"Network error: {error}")

    parsed = json.loads(data.decode("utf-8"))
    if not parsed.get("ok"):
        raise SystemExit(f"Telegram API error: {parsed.get('description', parsed)}")
    return parsed["result"]


def api_json(token, method, payload=None):
    request = urllib.request.Request(
        API_ROOT.format(token=token, method=method),
        data=json.dumps(payload or {}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    return read_response(request)


def api_multipart(token, method, fields, files):
    boundary = f"sf2tgs-{int(time.time() * 1000)}"
    chunks = []

    for key, value in fields.items():
        chunks.append(f"--{boundary}\r\n".encode())
        chunks.append(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode())
        chunks.append(str(value).encode("utf-8"))
        chunks.append(b"\r\n")

    for key, file_path in files.items():
        path = Path(file_path)
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        chunks.append(f"--{boundary}\r\n".encode())
        chunks.append(f'Content-Disposition: form-data; name="{key}"; filename="{path.name}"\r\n'.encode())
        chunks.append(f"Content-Type: {content_type}\r\n\r\n".encode())
        chunks.append(path.read_bytes())
        chunks.append(b"\r\n")

    chunks.append(f"--{boundary}--\r\n".encode())
    request = urllib.request.Request(
        API_ROOT.format(token=token, method=method),
        data=b"".join(chunks),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    return read_response(request)


def input_sticker(file_id, item):
    payload = {
        "sticker": file_id,
        "format": "animated",
        "emoji_list": item["emoji"][:20],
    }
    if item.get("keywords"):
        payload["keywords"] = item["keywords"][:20]
    return payload


def load_manifest(path):
    manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(manifest, list):
        raise ValueError("Manifest must be a JSON array")
    if not 1 <= len(manifest) <= MAX_STICKERS:
        raise ValueError(f"Manifest must contain 1-{MAX_STICKERS} stickers")

    validated = []
    for index, item in enumerate(manifest, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"Manifest item {index} must be an object")
        if not item.get("file"):
            raise ValueError(f"Manifest item {index} is missing file")
        if not item.get("emoji"):
            raise ValueError(f"Manifest item {index} is missing emoji")
        file_path = Path(item["file"])
        if not file_path.exists():
            raise ValueError(f"Missing file: {file_path}")
        validate_tgs(file_path)
        validated.append({**item, "file": str(file_path)})
    return validated


def upload_pack(token, user_id, set_name, title, manifest):
    uploaded = []
    for item in manifest:
        result = api_multipart(
            token,
            "uploadStickerFile",
            {"user_id": user_id, "sticker_format": "animated"},
            {"sticker": item["file"]},
        )
        uploaded.append({**item, "file_id": result["file_id"]})

    first = uploaded[:MAX_INITIAL_STICKERS]
    api_json(
        token,
        "createNewStickerSet",
        {
            "user_id": user_id,
            "name": set_name,
            "title": title,
            "sticker_type": "custom_emoji",
            "needs_repainting": True,
            "stickers": [input_sticker(item["file_id"], item) for item in first],
        },
    )

    for item in uploaded[MAX_INITIAL_STICKERS:]:
        api_json(
            token,
            "addStickerToSet",
            {
                "user_id": user_id,
                "name": set_name,
                "sticker": input_sticker(item["file_id"], item),
            },
        )
    return uploaded


def build_parser():
    parser = argparse.ArgumentParser(description="Upload one prepared .tgs custom emoji pack through the Telegram Bot API.")
    parser.add_argument("--manifest", required=True, help="JSON array of {file, emoji, keywords?} items.")
    parser.add_argument("--env", default=".env", help="Optional .env file with Telegram credentials.")
    parser.add_argument("--result", help="Optional path for upload result JSON.")
    parser.add_argument("--execute", action="store_true", help="Actually call Telegram. Without this flag the script only validates.")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    config = load_env(args.env)
    token = config_value(config, "TELEGRAM_BOT_TOKEN")
    user_id = int(config_value(config, "TELEGRAM_USER_ID"))
    bot_username = normalize_bot_username(config_value(config, "TELEGRAM_BOT_USERNAME"))
    set_name = config_value(config, "TELEGRAM_SET_NAME")
    title = config_value(config, "TELEGRAM_SET_TITLE")
    validate_set_name(set_name, bot_username)
    manifest = load_manifest(args.manifest)

    print(f"Bot token: {mask_token(token)}")
    print(f"Owner user_id: {user_id}")
    print(f"Set name: {set_name}")
    print(f"Set title: {title}")
    print(f"Stickers: {len(manifest)}")

    if not args.execute:
        print("Dry-run only. Add --execute to upload.")
        for item in manifest:
            print(f"  {Path(item['file']).name}: {' '.join(item['emoji'])}")
        return

    uploaded = upload_pack(token, user_id, set_name, title, manifest)
    result = {
        "set_name": set_name,
        "title": title,
        "url": f"https://t.me/addstickers/{set_name}",
        "stickers": uploaded,
    }
    if args.result:
        Path(args.result).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(result["url"])


if __name__ == "__main__":
    main()
