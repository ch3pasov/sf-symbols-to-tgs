# sf-symbols-to-tgs

Convert SF Symbols and PNG alpha masks into deterministic Telegram-compatible `.tgs` files.

This project packages two conversion paths:

- Native SF Symbols vector export on macOS, preserving symbol weights, layered monochrome geometry, cutout/eraser behavior, and vector curves.
- PNG alpha-mask tracing for fallback, debugging, and custom monochrome icons.

It does not redistribute Apple SF Symbols assets, generated symbol catalogs, or full `.tgs` output sets. Users must provide their own local SF Symbols installation and comply with Apple's SF Symbols license.

Published pack examples: <https://t.me/ch_an/2413>

Telegram atlas: [Browse SF Symbols emoji packs](https://t.me/dot_ch_bot?start=sf7_emojis).

## Requirements

- macOS.
- Python 3.10+.
- `clang` from Xcode or Command Line Tools.
- SF Symbols app from Apple: <https://developer.apple.com/sf-symbols/>

Install for local development:

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Finding SF Symbols Assets

Download and install SF Symbols from Apple's developer site:

<https://developer.apple.com/sf-symbols/>

The installed app exposes useful metadata here:

```sh
/Applications/SF Symbols.app/Contents/Resources/Metadata/
```

For this converter, the native exporter needs the full SF Symbols glyph `Assets.car`. A compatible catalog is usually large, often 100 MB or more. Some small `Assets.car` files inside the `.app` bundle are only app UI resources and will produce `Missing glyph` messages.

Start by locating candidate catalogs:

```sh
find /Applications -path '*SF Symbols*.app*Assets.car' -print
```

Then run a tiny export to verify the catalog:

```sh
printf "car\n" > symbols.txt
sf2tgs export-symbols --assets-car /path/to/Assets.car --symbols symbols.txt --out out/vector-json
```

Do not commit `Assets.car` or the extracted Apple plist catalogs into a public repo.

## Quickstart

Create a symbol list:

```sh
printf "car\nbus\ntram\n" > symbols.txt
```

Export native vector JSON:

```sh
sf2tgs export-symbols \
  --assets-car "/Applications/SF Symbols.app/Contents/Resources/Assets.car" \
  --symbols symbols.txt \
  --out out/vector-json \
  --weight Regular
```

Convert vector JSON to `.tgs`:

```sh
sf2tgs convert-vector-json out/vector-json -o out/tgs
```

Validate output:

```sh
sf2tgs validate out/tgs
```

Trace a standalone PNG alpha mask:

```sh
sf2tgs trace-png icon-mask.png -o out/icon.tgs --name icon
```

## CLI

```sh
sf2tgs trace-png <png> -o <out.tgs> --name <name>
sf2tgs export-symbols --assets-car <Assets.car> --symbols <symbols.txt> --out <json-dir> --weight Regular
sf2tgs convert-vector-json <json-dir> -o <tgs-dir>
sf2tgs validate <tgs-or-dir>
```

Weights can be one of `Ultralight`, `Thin`, `Light`, `Regular`, `Medium`, `Semibold`, `Bold`, `Heavy`, `Black`, or a numeric continuous weight.

## Telegram Upload Example

The converter is the main product. A small upload helper is included only for one prepared custom emoji pack.

Prepare `.env` from `.env.example`, then create a manifest:

```json
[
  {
    "file": "out/tgs/car.tgs",
    "emoji": ["🚗"],
    "keywords": ["car"]
  }
]
```

Dry-run:

```sh
python examples/upload_telegram_pack.py --manifest pack.json --env .env
```

Upload:

```sh
python examples/upload_telegram_pack.py --manifest pack.json --env .env --execute --result upload-result.json
```

The helper uses Telegram Bot API sticker methods documented at:

<https://core.telegram.org/bots/api#createnewstickerset>

It creates a `custom_emoji` set with `needs_repainting=true`, uploads up to 200 `.tgs` stickers, creates the first 1-50 stickers with `createNewStickerSet`, and appends the remainder with `addStickerToSet`.

## Legal Notes

This repository contains conversion code only. SF Symbols are Apple assets distributed under Apple's terms. Download SF Symbols from Apple and review the license before converting or publishing any symbols.

The generated Telegram packs are your responsibility: make sure your use of SF Symbols and any resulting custom emoji pack is permitted for your context.
