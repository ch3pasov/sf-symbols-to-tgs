#!/usr/bin/env python3
import gzip
import json
import math
import sys
from pathlib import Path

from PIL import Image

from .vector_to_tgs import compound_fill_groups, make_lottie as make_grouped_lottie, normalize_winding


CANVAS = 512
MARGIN = 44
THRESHOLD = 32


def rdp(points, epsilon):
    if len(points) <= 2:
        return points

    start = points[0]
    end = points[-1]
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    denom = math.hypot(dx, dy) or 1.0

    best_index = 0
    best_distance = -1
    for index, point in enumerate(points[1:-1], start=1):
        distance = abs(dy * point[0] - dx * point[1] + end[0] * start[1] - end[1] * start[0]) / denom
        if distance > best_distance:
            best_distance = distance
            best_index = index

    if best_distance <= epsilon:
        return [start, end]
    return rdp(points[: best_index + 1], epsilon)[:-1] + rdp(points[best_index:], epsilon)


def simplify_closed(points, epsilon=0.5):
    if len(points) < 4:
        return points
    points = points[:-1] if points[0] == points[-1] else points
    corners = []
    for index, point in enumerate(points):
        prev_point = points[index - 1]
        next_point = points[(index + 1) % len(points)]
        if (point[0] - prev_point[0]) * (next_point[1] - point[1]) == (
            point[1] - prev_point[1]
        ) * (next_point[0] - point[0]):
            continue
        corners.append(point)

    if len(corners) < 4:
        return corners

    start_index = min(range(len(corners)), key=lambda index: (corners[index][0], corners[index][1]))
    start = corners[start_index]
    far_index = max(
        range(len(corners)),
        key=lambda index: (corners[index][0] - start[0]) ** 2 + (corners[index][1] - start[1]) ** 2,
    )
    if start_index < far_index:
        arc_a = corners[start_index : far_index + 1]
        arc_b = corners[far_index:] + corners[: start_index + 1]
    else:
        arc_a = corners[start_index:] + corners[: far_index + 1]
        arc_b = corners[far_index : start_index + 1]

    return rdp(arc_a, epsilon)[:-1] + rdp(arc_b, epsilon)[:-1]


def trace_edges(mask):
    height = len(mask)
    width = len(mask[0])
    grid = [[False] * (width + 2)]
    grid += [[False] + row + [False] for row in mask]
    grid += [[False] * (width + 2)]

    segments_by_point = {}

    def add(a, b):
        segments_by_point.setdefault(a, []).append(b)
        segments_by_point.setdefault(b, []).append(a)

    def point(x, y, edge):
        if edge == "top":
            return (2 * x + 1, 2 * y)
        if edge == "right":
            return (2 * x + 2, 2 * y + 1)
        if edge == "bottom":
            return (2 * x + 1, 2 * y + 2)
        return (2 * x, 2 * y + 1)

    table = {
        1: [("left", "top")],
        2: [("top", "right")],
        3: [("left", "right")],
        4: [("right", "bottom")],
        5: [("left", "bottom"), ("top", "right")],
        6: [("top", "bottom")],
        7: [("left", "bottom")],
        8: [("bottom", "left")],
        9: [("top", "bottom")],
        10: [("top", "left"), ("right", "bottom")],
        11: [("right", "bottom")],
        12: [("left", "right")],
        13: [("top", "right")],
        14: [("left", "top")],
    }

    for y in range(height + 1):
        for x in range(width + 1):
            code = (
                (1 if grid[y][x] else 0)
                | (2 if grid[y][x + 1] else 0)
                | (4 if grid[y + 1][x + 1] else 0)
                | (8 if grid[y + 1][x] else 0)
            )
            for edge_a, edge_b in table.get(code, []):
                add(point(x, y, edge_a), point(x, y, edge_b))

    contours = []
    while segments_by_point:
        start = next(iter(segments_by_point))
        contour = [start]
        prev = None
        current = start
        while True:
            neighbors = segments_by_point.get(current, [])
            if not neighbors:
                break
            candidates = [neighbor for neighbor in neighbors if neighbor != prev] or neighbors
            nxt = candidates[0]

            segments_by_point[current].remove(nxt)
            if not segments_by_point[current]:
                del segments_by_point[current]
            segments_by_point[nxt].remove(current)
            if not segments_by_point[nxt]:
                del segments_by_point[nxt]

            prev, current = current, nxt
            contour.append(current)
            if current == start:
                break

        if len(contour) > 3 and contour[-1] == contour[0]:
            contours.append([((x / 2) - 1, (y / 2) - 1) for x, y in contour])
    return contours


def polygon_area(points):
    return sum(
        points[index][0] * points[(index + 1) % len(points)][1]
        - points[(index + 1) % len(points)][0] * points[index][1]
        for index in range(len(points))
    ) / 2


def transform_contours(contours):
    all_points = [point for contour in contours for point in contour]
    min_x = min(x for x, _ in all_points)
    min_y = min(y for _, y in all_points)
    max_x = max(x for x, _ in all_points)
    max_y = max(y for _, y in all_points)
    width = max_x - min_x
    height = max_y - min_y
    scale = (CANVAS - MARGIN * 2) / max(width, height)
    x_offset = (CANVAS - width * scale) / 2
    y_offset = (CANVAS - height * scale) / 2

    return [
        [
            (
                (x - min_x) * scale + x_offset,
                (y - min_y) * scale + y_offset,
            )
            for x, y in contour
        ]
        for contour in contours
    ]


def make_shape(points):
    vertices = [[round(x, 3), round(y, 3)] for x, y in points]
    return {
        "ty": "sh",
        "ks": {
            "a": 0,
            "k": {
                "i": [[0, 0] for _ in vertices],
                "o": [[0, 0] for _ in vertices],
                "v": vertices,
                "c": True,
            },
        },
    }


def convert(png_path, out_path, name):
    image = Image.open(png_path).convert("RGBA")
    alpha = image.getchannel("A")
    width, height = alpha.size
    mask = [[alpha.getpixel((x, y)) >= THRESHOLD for x in range(width)] for y in range(height)]
    contours = trace_edges(mask)
    contours = [
        simplify_closed(contour)
        for contour in contours
        if abs(polygon_area(contour)) >= 16
    ]
    contours = [contour for contour in contours if len(contour) >= 3]
    contours = transform_contours(contours) if contours else []

    shapes = []
    for contour in contours:
        shapes.append(make_shape(contour))

    normalize_winding(shapes)
    shape_groups = compound_fill_groups(shapes)
    lottie = make_grouped_lottie(shape_groups, name)
    payload = json.dumps(lottie, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    with gzip.GzipFile(filename="", mode="wb", fileobj=out_path.open("wb"), mtime=0) as gz:
        gz.write(payload)
    result = {
        "symbol": name,
        "contours": len(shapes),
        "groups": len(shape_groups),
        "vertices": sum(len(s["ks"]["k"]["v"]) for s in shapes),
        "bytes": out_path.stat().st_size,
    }
    print(f"{name}: {result['contours']} contours, {result['vertices']} vertices, {result['bytes']} bytes")
    return result


def main():
    if len(sys.argv) != 4:
        print("Usage: trace_png_to_tgs.py <png> <out.tgs> <name>", file=sys.stderr)
        raise SystemExit(2)
    convert(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3])


if __name__ == "__main__":
    main()
