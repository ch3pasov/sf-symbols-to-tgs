import gzip
import json
from pathlib import Path


def read_tgs(path):
    path = Path(path)
    with gzip.open(path, "rb") as handle:
        return json.loads(handle.read().decode("utf-8"))


def has_shape_path(items):
    for item in items:
        if item.get("ty") == "sh":
            return True
        if item.get("ty") == "gr" and has_shape_path(item.get("it", [])):
            return True
    return False


def layer_has_shape(layer):
    return has_shape_path(layer.get("shapes", []))


def validate_tgs(path, require_shapes=True):
    path = Path(path)
    payload = read_tgs(path)
    errors = []

    if payload.get("w") != 512 or payload.get("h") != 512:
        errors.append(f"expected 512x512 canvas, got {payload.get('w')}x{payload.get('h')}")
    if payload.get("fr") != 60:
        errors.append(f"expected 60 fps, got {payload.get('fr')}")
    if payload.get("ip") != 0 or payload.get("op") != 1:
        errors.append(f"expected one-frame animation ip=0/op=1, got ip={payload.get('ip')} op={payload.get('op')}")

    layers = payload.get("layers")
    if not isinstance(layers, list) or not layers:
        errors.append("expected at least one Lottie layer")
    elif require_shapes and not any(layer_has_shape(layer) for layer in layers):
        errors.append("expected at least one shape path")

    if errors:
        raise ValueError(f"{path}: " + "; ".join(errors))

    return {
        "file": str(path),
        "name": payload.get("nm"),
        "layers": len(layers or []),
        "bytes": path.stat().st_size,
    }


def iter_tgs_paths(path):
    path = Path(path)
    if path.is_dir():
        yield from sorted(path.glob("*.tgs"))
    else:
        yield path


def validate_path(path, require_shapes=True):
    return [validate_tgs(item, require_shapes=require_shapes) for item in iter_tgs_paths(path)]
