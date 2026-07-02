#!/usr/bin/env python3
import gzip
import json
import sys
from pathlib import Path


CANVAS = 512
MARGIN = 64


def pt(obj):
    return float(obj["x"]), float(obj["y"])


def transform_factory(bounds, scale_bounds=None):
    min_x = float(bounds["x"])
    min_y = float(bounds["y"])
    width = float(bounds["width"])
    height = float(bounds["height"])
    scale_source = scale_bounds or bounds
    scale_width = float(scale_source["width"])
    scale_height = float(scale_source["height"])
    scale = (CANVAS - MARGIN * 2) / max(scale_width, scale_height)
    x_offset = (CANVAS - width * scale) / 2
    y_offset = (CANVAS - height * scale) / 2

    def transform(point):
        x, y = point
        return (
            (x - min_x) * scale + x_offset,
            (y - min_y) * scale + y_offset,
        )

    return transform


def transform_factory_for_source(source):
    scale_bounds = source.get("content_bounds")
    return transform_factory(source["bounds"], scale_bounds=scale_bounds)


def add_vertex(vertices, in_tangents, out_tangents, point, in_tangent=(0, 0)):
    vertices.append(point)
    in_tangents.append(in_tangent)
    out_tangents.append((0, 0))


def path_to_shape(subpath):
    vertices = []
    in_tangents = []
    out_tangents = []
    current = None
    closed = False

    for element in subpath:
        kind = element["type"]
        points = [pt(p) for p in element.get("points", [])]

        if kind == "move":
            current = points[0]
            add_vertex(vertices, in_tangents, out_tangents, current)
        elif kind == "line":
            end = points[0]
            add_vertex(vertices, in_tangents, out_tangents, end)
            current = end
        elif kind == "quad":
            control, end = points
            c1 = (
                current[0] + (2.0 / 3.0) * (control[0] - current[0]),
                current[1] + (2.0 / 3.0) * (control[1] - current[1]),
            )
            c2 = (
                end[0] + (2.0 / 3.0) * (control[0] - end[0]),
                end[1] + (2.0 / 3.0) * (control[1] - end[1]),
            )
            out_tangents[-1] = (c1[0] - current[0], c1[1] - current[1])
            add_vertex(vertices, in_tangents, out_tangents, end, (c2[0] - end[0], c2[1] - end[1]))
            current = end
        elif kind == "curve":
            c1, c2, end = points
            out_tangents[-1] = (c1[0] - current[0], c1[1] - current[1])
            add_vertex(vertices, in_tangents, out_tangents, end, (c2[0] - end[0], c2[1] - end[1]))
            current = end
        elif kind == "close":
            closed = True

    if len(vertices) > 1 and closed:
        first = vertices[0]
        last = vertices[-1]
        if abs(first[0] - last[0]) < 1e-6 and abs(first[1] - last[1]) < 1e-6:
            closing_in = in_tangents[-1]
            vertices.pop()
            in_tangents.pop()
            out_tangents.pop()
            in_tangents[0] = closing_in

    return {
        "ty": "sh",
        "ks": {
            "a": 0,
            "k": {
                "i": [[round(x, 3), round(y, 3)] for x, y in in_tangents],
                "o": [[round(x, 3), round(y, 3)] for x, y in out_tangents],
                "v": [[round(x, 3), round(y, 3)] for x, y in vertices],
                "c": closed,
            },
        },
    }


def bezier(p0, out_tangent, p1, in_tangent, t):
    c1 = (p0[0] + out_tangent[0], p0[1] + out_tangent[1])
    c2 = (p1[0] + in_tangent[0], p1[1] + in_tangent[1])
    u = 1 - t
    return (
        u**3 * p0[0] + 3 * u * u * t * c1[0] + 3 * u * t * t * c2[0] + t**3 * p1[0],
        u**3 * p0[1] + 3 * u * u * t * c1[1] + 3 * u * t * t * c2[1] + t**3 * p1[1],
    )


def sample_shape(shape):
    data = shape["ks"]["k"]
    vertices = data["v"]
    in_tangents = data["i"]
    out_tangents = data["o"]
    closed = data.get("c", False)
    count = len(vertices)
    if count < 2:
        return []

    points = []
    segments = count if closed else count - 1
    for index in range(segments):
        next_index = (index + 1) % count
        p0 = vertices[index]
        p1 = vertices[next_index]
        distance = abs(p1[0] - p0[0]) + abs(p1[1] - p0[1])
        steps = max(8, min(32, int(distance / 8)))
        for step in range(steps):
            points.append(bezier(p0, out_tangents[index], p1, in_tangents[next_index], step / steps))
    return points


def signed_area(points):
    if len(points) < 3:
        return 0
    return sum(
        points[index][0] * points[(index + 1) % len(points)][1]
        - points[(index + 1) % len(points)][0] * points[index][1]
        for index in range(len(points))
    ) / 2


def point_in_polygon(point, polygon):
    x, y = point
    inside = False
    j = len(polygon) - 1
    for i in range(len(polygon)):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if (yi > y) != (yj > y):
            x_cross = (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi
            if x < x_cross:
                inside = not inside
        j = i
    return inside


def reverse_shape(shape):
    data = shape["ks"]["k"]
    data["v"] = list(reversed(data["v"]))
    data["i"], data["o"] = list(reversed(data["o"])), list(reversed(data["i"]))


def normalize_winding(shapes):
    samples = [sample_shape(shape) for shape in shapes]
    areas = [signed_area(points) for points in samples]
    centers = [
        (
            sum(point[0] for point in points) / len(points),
            sum(point[1] for point in points) / len(points),
        )
        if points
        else (0, 0)
        for points in samples
    ]

    depths = []
    for index, center in enumerate(centers):
        containers = [
            abs(areas[other])
            for other, polygon in enumerate(samples)
            if other != index and abs(areas[other]) > abs(areas[index]) and point_in_polygon(center, polygon)
        ]
        depths.append(len(containers))

    for shape, area, depth in zip(shapes, areas, depths):
        target_negative = depth % 2 == 0
        if (area < 0) != target_negative:
            reverse_shape(shape)


def remove_redundant_same_winding_contours(shapes):
    samples = [sample_shape(shape) for shape in shapes]
    areas = [signed_area(points) for points in samples]
    centers = [
        (
            sum(point[0] for point in points) / len(points),
            sum(point[1] for point in points) / len(points),
        )
        if points
        else (0, 0)
        for points in samples
    ]

    parents = []
    for index, center in enumerate(centers):
        containing = [
            other
            for other, polygon in enumerate(samples)
            if other != index and abs(areas[other]) > abs(areas[index]) and point_in_polygon(center, polygon)
        ]
        parents.append(min(containing, key=lambda other: abs(areas[other]), default=None))

    filtered = []
    for index, shape in enumerate(shapes):
        parent = parents[index]
        if parent is None or (areas[index] < 0) != (areas[parent] < 0):
            filtered.append(shape)
    return filtered


def shape_containment(shapes):
    samples = [sample_shape(shape) for shape in shapes]
    areas = [signed_area(points) for points in samples]

    def contains_most(container, child, threshold=0.65):
        if not samples[child]:
            return False
        inside = sum(1 for point in samples[child] if point_in_polygon(point, samples[container]))
        return inside / len(samples[child]) >= threshold

    parents = []
    for index in range(len(shapes)):
        containing = [
            other
            for other in range(len(shapes))
            if other != index and abs(areas[other]) > abs(areas[index]) and contains_most(other, index)
        ]
        parents.append(min(containing, key=lambda other: abs(areas[other]), default=None))
    return samples, areas, parents


def nested_shape_levels(shapes):
    _, _, parents = shape_containment(shapes)
    depths = []
    for index in range(len(shapes)):
        depth = 0
        parent = parents[index]
        while parent is not None:
            depth += 1
            parent = parents[parent]
        depths.append(depth)
    return parents, depths


def split_eraser_shapes(shapes):
    parents, depths = nested_shape_levels(shapes)
    top_masks = [shape for shape, depth in zip(shapes, depths) if depth == 0]
    addbacks = []
    for index, shape in enumerate(shapes):
        if depths[index] % 2 == 1:
            masks = [
                shapes[child]
                for child, parent in enumerate(parents)
                if parent == index and depths[child] == depths[index] + 1
            ]
            addbacks.append({"shapes": [shape], "masks": masks})
    return top_masks, addbacks


def remove_outer_same_winding_duplicates(shapes):
    samples = [sample_shape(shape) for shape in shapes]
    areas = [signed_area(points) for points in samples]

    def contains_most(container, child):
        if not samples[child]:
            return False
        inside = sum(1 for point in samples[child] if point_in_polygon(point, samples[container]))
        return inside / len(samples[child]) >= 0.8

    filtered = []
    for index, shape in enumerate(shapes):
        same_winding_children = [
            other
            for other in range(len(shapes))
            if other != index
            and abs(areas[other]) < abs(areas[index])
            and (areas[other] < 0) == (areas[index] < 0)
            and contains_most(index, other)
        ]
        opposite_winding_children = [
            other
            for other in range(len(shapes))
            if other != index
            and abs(areas[other]) < abs(areas[index])
            and (areas[other] < 0) != (areas[index] < 0)
            and contains_most(index, other)
        ]
        if same_winding_children and not opposite_winding_children:
            continue
        filtered.append(shape)
    return filtered


def compound_fill_groups(shapes):
    samples = [sample_shape(shape) for shape in shapes]
    areas = [signed_area(points) for points in samples]
    centers = [
        (
            sum(point[0] for point in points) / len(points),
            sum(point[1] for point in points) / len(points),
        )
        if points
        else (0, 0)
        for points in samples
    ]

    parents = []
    for index, center in enumerate(centers):
        containing = [
            other
            for other, polygon in enumerate(samples)
            if other != index and abs(areas[other]) > abs(areas[index]) and point_in_polygon(center, polygon)
        ]
        parents.append(min(containing, key=lambda other: abs(areas[other]), default=None))

    children = {index: [] for index in range(len(shapes))}
    roots = []
    for index, parent in enumerate(parents):
        if parent is None:
            roots.append(index)
        else:
            children[parent].append(index)

    groups = []
    for root in roots:
        group = [root]
        stack = list(children[root])
        while stack:
            index = stack.pop(0)
            if (areas[index] < 0) != (areas[root] < 0):
                group.append(index)
            stack.extend(children[index])
        groups.append([shapes[index] for index in group])
    return groups


def split_subpaths(elements, transform):
    subpaths = []
    current = []
    for element in elements:
        converted = {"type": element["type"], "points": []}
        for point in element.get("points", []):
            x, y = transform(pt(point))
            converted["points"].append({"x": x, "y": y})

        if converted["type"] == "move" and current:
            subpaths.append(current)
            current = []

        current.append(converted)
        if converted["type"] == "close":
            subpaths.append(current)
            current = []

    if current:
        subpaths.append(current)
    return subpaths


def make_fill_group(shapes, name, index, color=(0, 0, 0, 1), fill_rule=1):
    return {
        "ty": "gr",
        "nm": f"{name}.{index}",
        "it": shapes
        + [
            {"ty": "fl", "c": {"a": 0, "k": list(color)}, "o": {"a": 0, "k": 100}, "r": fill_rule},
            {
                "ty": "tr",
                "p": {"a": 0, "k": [0, 0]},
                "a": {"a": 0, "k": [0, 0]},
                "s": {"a": 0, "k": [100, 100]},
                "r": {"a": 0, "k": 0},
                "o": {"a": 0, "k": 100},
            },
        ],
    }


def make_mask(shape):
    return {
        "inv": False,
        "mode": "s",
        "pt": shape["ks"],
        "o": {"a": 0, "k": 100},
    }


def make_shape_layer(name, index, shape_groups, masks=None, fill_rule=1):
    groups = [make_fill_group(group, name, group_index, fill_rule=fill_rule) for group_index, group in enumerate(shape_groups, start=1)]
    layer = {
        "ddd": 0,
        "ind": index,
        "ty": 4,
        "nm": name,
        "sr": 1,
        "ks": {
            "o": {"a": 0, "k": 100},
            "r": {"a": 0, "k": 0},
            "p": {"a": 0, "k": [CANVAS / 2, CANVAS / 2, 0]},
            "a": {"a": 0, "k": [CANVAS / 2, CANVAS / 2, 0]},
            "s": {"a": 0, "k": [100, 100, 100]},
        },
        "ao": 0,
        "shapes": groups,
        "ip": 0,
        "op": 1,
        "st": 0,
        "bm": 0,
    }
    if masks:
        layer["hasMask"] = True
        layer["masksProperties"] = [make_mask(shape) for shape in masks]
    return layer


def make_colored_shape_layer(name, index, shapes, color, fill_rule=1):
    layer = make_shape_layer(name, index, [shapes], fill_rule=fill_rule)
    layer["shapes"] = [make_fill_group(shapes, name, index, color=color, fill_rule=fill_rule)]
    return layer


def make_black_masked_shape_layer(name, index, shapes, masks=None):
    return make_shape_layer(name, index, [shapes], masks=masks or [], fill_rule=1)


def make_lottie(shape_groups, name):
    return {
        "v": "5.7.4",
        "fr": 60,
        "ip": 0,
        "op": 1,
        "w": CANVAS,
        "h": CANVAS,
        "nm": name,
        "ddd": 0,
        "assets": [],
        "layers": [make_shape_layer(name, 1, shape_groups)],
    }


def shapes_from_elements(elements, transform):
    subpaths = split_subpaths(elements, transform)
    return [path_to_shape(subpath) for subpath in subpaths if len(subpath) > 1]


def path_group_shapes(layer, transform):
    shapes = []
    for group_elements in layer.get("path_groups", []):
        shapes.extend(shapes_from_elements(group_elements, transform))
    return shapes


def make_shape_group_paint_lottie(source):
    transform = transform_factory_for_source(source)
    shapes = path_group_shapes(source["layers"][0], transform)
    if not shapes:
        shapes = shapes_from_elements(source["layers"][0]["elements"], transform)
    if not shapes:
        return make_lottie([], source["symbol"]), 0, 0, 0

    samples, areas, parents = shape_containment(shapes)
    root_shapes = [shape for shape, parent in zip(shapes, parents) if parent is None]
    children = {index: [] for index in range(len(shapes))}
    for child, parent in enumerate(parents):
        if parent is not None:
            children[parent].append(child)

    def contains_most(container, child):
        if not samples[child]:
            return False
        inside = sum(1 for point in samples[child] if point_in_polygon(point, samples[container]))
        return inside / len(samples[child]) >= 0.8

    root_indexes = {index for index, parent in enumerate(parents) if parent is None}
    first_level_indexes = {index for index, parent in enumerate(parents) if parent in root_indexes}
    separator_indexes = {
        index
        for index in first_level_indexes
        if any(
            other != index
            and abs(areas[other]) < abs(areas[index])
            and (areas[other] < 0) == (areas[index] < 0)
            and contains_most(index, other)
            for other in range(len(shapes))
        )
    }
    overlay_indexes = {
        child
        for child, parent in enumerate(parents)
        if parent in separator_indexes
    }
    structural_indexes = first_level_indexes - separator_indexes
    addback_indexes = {
        index
        for index, parent in enumerate(parents)
        if parent in structural_indexes and index not in overlay_indexes
    }

    separator_shapes = []
    structural_cutout_shapes = []
    overlay_cutout_shapes = []
    addback_shapes = []
    for index, shape in enumerate(shapes):
        if index in separator_indexes:
            separator_shapes.append(shape)
        elif index in overlay_indexes:
            overlay_cutout_shapes.append(shape)
        elif index in structural_indexes:
            structural_cutout_shapes.append(shape)
        elif index in addback_indexes:
            addback_shapes.append(shape)

    separator_masks = list(overlay_cutout_shapes)
    for separator_index in separator_indexes:
        for structural_index in structural_indexes:
            if abs(areas[structural_index]) >= abs(areas[separator_index]):
                continue
            if not samples[separator_index]:
                continue
            overlap = sum(
                1
                for point in samples[separator_index]
                if point_in_polygon(point, samples[structural_index])
            ) / len(samples[separator_index])
            if overlap >= 0.1:
                separator_masks.append(shapes[structural_index])

    layers = []
    index = 1
    if root_shapes:
        root_masks = structural_cutout_shapes + overlay_cutout_shapes
        layers.append(make_black_masked_shape_layer(source["symbol"], index, root_shapes, masks=root_masks))
        index += 1
    if structural_cutout_shapes:
        pass
    if addback_shapes:
        layers.append(make_black_masked_shape_layer(source["symbol"], index, addback_shapes, masks=overlay_cutout_shapes))
        index += 1
    if separator_shapes:
        layers.append(make_black_masked_shape_layer(source["symbol"], index, separator_shapes, masks=separator_masks))
        index += 1
    if overlay_cutout_shapes:
        pass

    lottie = make_lottie([], source["symbol"])
    lottie["layers"] = list(reversed(layers))
    vertex_count = sum(len(shape["ks"]["k"]["v"]) for shape in shapes)
    return lottie, len(shapes), len(layers), vertex_count


def make_shape_group_compound_lottie(source):
    transform = transform_factory_for_source(source)
    shapes = path_group_shapes(source["layers"][0], transform)
    if not shapes:
        shapes = shapes_from_elements(source["layers"][0]["elements"], transform)
    if not shapes:
        return make_lottie([], source["symbol"]), 0, 0, 0

    lottie = make_lottie([], source["symbol"])
    lottie["layers"] = [make_shape_layer(source["symbol"], 1, [shapes], fill_rule=1)]
    vertex_count = sum(len(shape["ks"]["k"]["v"]) for shape in shapes)
    return lottie, len(shapes), 1, vertex_count


def make_layered_lottie(source):
    transform = transform_factory_for_source(source)
    draw_layers = []
    subpath_count = 0
    vertex_count = 0
    boolean_resolved = any(layer.get("boolean_resolved") for layer in source.get("layers", []))
    fill_rule = 1 if boolean_resolved else 2 if source.get("source") == "CUINamedVectorGlyph._createShapeGroupSubpaths" else 1

    source_layers = source["layers"]
    for source_index, layer in enumerate(source_layers):
        shapes = shapes_from_elements(layer["elements"], transform)
        is_subtract_mask = layer.get("is_eraser") and float(layer.get("opacity", 1)) == 0
        if is_subtract_mask and draw_layers:
            top_masks, addbacks = split_eraser_shapes(shapes)
            has_later_visible_layer = any(
                not (later.get("is_eraser") and float(later.get("opacity", 1)) == 0)
                for later in source_layers[source_index + 1 :]
            )
            targets = draw_layers if has_later_visible_layer else [draw_layers[-1]]
            for draw_layer in targets:
                draw_layer["masks"].extend(top_masks)
            draw_layers.extend(addbacks)
        elif is_subtract_mask:
            draw_layers.append({"shapes": shapes, "masks": []})
        else:
            draw_layers.append({"shapes": shapes, "masks": []})
        subpath_count += len(shapes)
        vertex_count += sum(len(shape["ks"]["k"]["v"]) for shape in shapes)

    lottie = make_lottie([], source["symbol"])
    lottie["layers"] = [
        make_shape_layer(source["symbol"], index, [layer["shapes"]], masks=layer["masks"], fill_rule=fill_rule)
        for index, layer in enumerate(reversed(draw_layers), start=1)
    ]
    return lottie, subpath_count, len(draw_layers), vertex_count


def convert(json_path, out_path):
    source = json.loads(json_path.read_text(encoding="utf-8"))
    if (
        source.get("source") == "CUINamedVectorGlyph._createShapeGroupSubpaths"
        and source.get("layers")
        and source["layers"][0].get("path_groups")
        and not source["layers"][0].get("boolean_resolved")
    ):
        lottie, subpath_count, group_count, vertex_count = make_shape_group_compound_lottie(source)
    elif "layers" in source:
        lottie, subpath_count, group_count, vertex_count = make_layered_lottie(source)
    else:
        transform = transform_factory_for_source(source)
        shapes = shapes_from_elements(source["elements"], transform)
        # Preserve CGPath subpath order and winding. The extracted glyph path already
        # encodes holes and inner marks; regrouping can drop valid nested contours.
        shape_groups = [shapes]
        lottie = make_lottie(shape_groups, source["symbol"])
        subpath_count = len(shapes)
        group_count = len(shape_groups)
        vertex_count = sum(len(shape["ks"]["k"]["v"]) for shape in shapes)
    payload = json.dumps(lottie, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    with gzip.GzipFile(filename="", mode="wb", fileobj=out_path.open("wb"), mtime=0) as gz:
        gz.write(payload)
    return {
        "symbol": source["symbol"],
        "source": source["source"],
        "subpaths": subpath_count,
        "groups": group_count,
        "vertices": vertex_count,
        "bytes": out_path.stat().st_size,
    }


def convert_directory(input_dir, output_dir):
    input_dir = Path(input_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    manifest = []
    for json_path in sorted(input_dir.glob("*.json")):
        if json_path.name == "manifest.json":
            continue
        out_path = output_dir / f"{json_path.stem}.tgs"
        manifest.append(convert(json_path, out_path))

    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def main():
    if len(sys.argv) != 3:
        print("Usage: true_vector_to_tgs.py <true-vector-json-dir> <tgs-dir>", file=sys.stderr)
        raise SystemExit(2)

    input_dir = Path(sys.argv[1])
    output_dir = Path(sys.argv[2])
    output_dir.mkdir(parents=True, exist_ok=True)

    manifest = convert_directory(input_dir, output_dir)
    for item in manifest:
        print(f"{item['symbol']}: {item['bytes']} bytes, {item['subpaths']} subpaths, {item['vertices']} vertices")


if __name__ == "__main__":
    main()
