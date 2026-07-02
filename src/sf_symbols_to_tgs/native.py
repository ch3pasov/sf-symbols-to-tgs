import os
import shutil
import subprocess
from pathlib import Path


WEIGHTS = {
    "Ultralight": -0.8,
    "Thin": -0.6,
    "Light": -0.4,
    "Regular": 0.0,
    "Medium": 0.23,
    "Semibold": 0.3,
    "Bold": 0.4,
    "Heavy": 0.56,
    "Black": 0.62,
}


def project_root():
    return Path(__file__).resolve().parents[2]


def default_native_source():
    candidates = []
    env_source = os.environ.get("SF2TGS_NATIVE_EXPORTER_SOURCE")
    if env_source:
        candidates.append(Path(env_source))
    candidates.extend(
        [
            project_root() / "native" / "layered_vector_export.m",
            Path.cwd() / "native" / "layered_vector_export.m",
        ]
    )
    for candidate in candidates:
        if str(candidate) and candidate.exists():
            return candidate
    raise FileNotFoundError("Could not find native/layered_vector_export.m")


def default_build_dir():
    return project_root() / "build-native"


def compile_exporter(source=None, build_dir=None):
    source = Path(source) if source else default_native_source()
    build_dir = Path(build_dir) if build_dir else default_build_dir()
    build_dir.mkdir(parents=True, exist_ok=True)
    binary = build_dir / "layered_vector_export"

    if binary.exists() and binary.stat().st_mtime >= source.stat().st_mtime:
        return binary

    clang = shutil.which("clang")
    if not clang:
        raise RuntimeError("clang is required to build the native SF Symbols exporter")

    subprocess.run(
        [
            clang,
            "-fobjc-arc",
            "-framework",
            "Foundation",
            "-framework",
            "AppKit",
            "-framework",
            "CoreGraphics",
            str(source),
            "-o",
            str(binary),
        ],
        check=True,
    )
    return binary


def weight_value(value):
    if value in WEIGHTS:
        return WEIGHTS[value]
    try:
        return float(value)
    except ValueError as error:
        allowed = ", ".join(WEIGHTS)
        raise ValueError(f"Unknown weight {value!r}. Use one of {allowed}, or pass a numeric continuous weight.") from error


def export_symbols(assets_car, symbols_file, out_dir, weight="Regular", exporter=None, build_dir=None, no_shape_group_subpaths=False):
    binary = Path(exporter) if exporter else compile_exporter(build_dir=build_dir)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    command = [
        str(binary),
        str(Path(assets_car)),
        str(out_dir),
        "--symbols-file",
        str(Path(symbols_file)),
        "--weight",
        str(weight_value(weight)),
    ]
    if no_shape_group_subpaths:
        command.append("--no-shape-group-subpaths")

    subprocess.run(command, check=True)
    return out_dir
