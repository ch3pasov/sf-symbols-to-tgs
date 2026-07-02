import argparse
import json
import sys
from pathlib import Path

from . import native
from .trace_png import convert as convert_png
from .validate import validate_path
from .vector_to_tgs import convert_directory


def cmd_trace_png(args):
    name = args.name or Path(args.png).stem
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    result = convert_png(Path(args.png), out_path, name)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_export_symbols(args):
    out_dir = native.export_symbols(
        assets_car=args.assets_car,
        symbols_file=args.symbols,
        out_dir=args.out,
        weight=args.weight,
        exporter=args.exporter,
        build_dir=args.native_build_dir,
        no_shape_group_subpaths=args.no_shape_group_subpaths,
    )
    print(f"Exported vector JSON to {out_dir}")


def cmd_convert_vector_json(args):
    manifest = convert_directory(Path(args.input), Path(args.out))
    print(f"Converted {len(manifest)} vector JSON files to {args.out}")


def cmd_validate(args):
    results = validate_path(args.path, require_shapes=not args.allow_empty)
    for item in results:
        print(f"{item['file']}: ok ({item['layers']} layers, {item['bytes']} bytes)")


def build_parser():
    parser = argparse.ArgumentParser(
        prog="sf2tgs",
        description="Convert SF Symbols vector exports and PNG alpha masks into Telegram-compatible .tgs files.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    trace_parser = subparsers.add_parser("trace-png", help="Trace a PNG alpha mask into a .tgs file.")
    trace_parser.add_argument("png", help="Input PNG path.")
    trace_parser.add_argument("-o", "--out", required=True, help="Output .tgs path.")
    trace_parser.add_argument("--name", help="Lottie composition name. Defaults to the input file stem.")
    trace_parser.set_defaults(func=cmd_trace_png)

    export_parser = subparsers.add_parser("export-symbols", help="Export native SF Symbol vector JSON from Assets.car.")
    export_parser.add_argument("--assets-car", required=True, help="Path to SF Symbols Assets.car.")
    export_parser.add_argument("--symbols", required=True, help="Text file with one SF Symbol name per line.")
    export_parser.add_argument("--out", required=True, help="Output directory for vector JSON files.")
    export_parser.add_argument("--weight", default="Regular", help="SF Symbols weight name or numeric continuous weight.")
    export_parser.add_argument("--exporter", help="Path to a precompiled layered_vector_export binary.")
    export_parser.add_argument("--native-build-dir", help="Directory for the compiled native helper.")
    export_parser.add_argument(
        "--no-shape-group-subpaths",
        action="store_true",
        help="Disable shape-group fallback extraction in the native helper.",
    )
    export_parser.set_defaults(func=cmd_export_symbols)

    convert_parser = subparsers.add_parser("convert-vector-json", help="Convert native vector JSON exports into .tgs files.")
    convert_parser.add_argument("input", help="Input directory containing vector JSON files.")
    convert_parser.add_argument("-o", "--out", required=True, help="Output directory for .tgs files.")
    convert_parser.set_defaults(func=cmd_convert_vector_json)

    validate_parser = subparsers.add_parser("validate", help="Validate one .tgs file or every .tgs file in a directory.")
    validate_parser.add_argument("path", help="Input .tgs path or directory.")
    validate_parser.add_argument(
        "--allow-empty",
        action="store_true",
        help="Allow valid Lottie files that contain no shape paths, useful for empty-mask tests.",
    )
    validate_parser.set_defaults(func=cmd_validate)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except Exception as error:
        print(f"sf2tgs: error: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
