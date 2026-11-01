"""Layout inference: turns positioned elements into nested flex rows and columns.

Steps per container:
1. Offset decorations: two equal shapes offset by a few px become one shape plus an outline/shadow.
2. Containment: a shape or image that fully contains later-painted elements becomes their box.
3. Background layers: shapes/graphics that overlap later elements without containing them are
   taken out of the flow and positioned absolutely behind it.
4. XY-cut: the remaining elements are split into rows or columns along the axis with the largest
   gap, recursively. Elements that overlap on both axes fall back to absolute positioning.
"""

from __future__ import annotations

import itertools

from .ir import El, Page

OFFSET_MAX = 10.0
SIZE_TOL = 1.0
SPLIT_RATIO = 0.5  # split at gaps >= 50% of the largest gap on the chosen axis
_wrapper_ids = itertools.count()


def detect_offset_decorations(items: list[El]) -> list[El]:
    shapes = [e for e in items if e.kind == "shape"]
    removed: set[int] = set()
    for a, b in itertools.combinations(shapes, 2):
        if id(a) in removed or id(b) in removed:
            continue
        if abs(a.w - b.w) > SIZE_TOL or abs(a.h - b.h) > SIZE_TOL:
            continue
        dx, dy = b.x - a.x, b.y - a.y
        if (dx == 0 and dy == 0) or abs(dx) > OFFSET_MAX or abs(dy) > OFFSET_MAX:
            continue
        front, back = (a, b) if a.z > b.z else (b, a)
        front.deco = {"type": "offset", "dx": back.x - front.x, "dy": back.y - front.y, "node": back.node}
        removed.add(id(back))
    return [e for e in items if id(e) not in removed]


def nest_contained(items: list[El]) -> list[El]:
    """Assigns each element to the smallest earlier-painted shape/image that contains it."""
    ordered = sorted(items, key=lambda e: e.z)
    parent: dict[int, El] = {}
    for i, e in enumerate(ordered):
        best = None
        for cand in ordered[:i]:
            if cand.kind not in ("shape", "image") or cand.area <= e.area or not cand.contains(e):
                continue
            if best is None or cand.area < best.area:
                best = cand
        if best is not None:
            parent[id(e)] = best
    top = []
    for e in ordered:
        p = parent.get(id(e))
        if p is None:
            top.append(e)
        else:
            p.children.append(e)
    for e in ordered:
        if e.children and e.kind in ("shape", "image"):
            e.kind = "box"  # shape becomes a styled container; image becomes a box with a background image
    return top


def split_layers(items: list[El]) -> tuple[list[El], list[El]]:
    """Graphics painted behind other elements and overlapping them become absolute layers."""
    flow, layers = [], []
    for e in items:
        # SVGs are never containers, so any overlap with later elements makes them a layer.
        # Shapes/images that fully contain later elements were already turned into boxes.
        behind = [
            o for o in items if o is not e and o.z > e.z and e.intersects(o) and (e.kind == "svg" or not e.contains(o))
        ]
        if e.kind in ("svg", "shape", "image") and behind:
            layers.append(e)
        else:
            flow.append(e)
    return flow, layers


def _partition(items: list[El], axis: str) -> list[tuple[list[El], float]]:
    """Groups items whose intervals overlap on an axis. Returns (group, gap before group)."""
    start = (lambda e: e.y) if axis == "y" else (lambda e: e.x)
    end = (lambda e: e.bottom) if axis == "y" else (lambda e: e.right)
    groups: list[list] = []  # each entry: [members, gap_before, end]
    for e in sorted(items, key=start):
        if groups and start(e) < groups[-1][2] - 0.5:
            groups[-1][0].append(e)
            groups[-1][2] = max(groups[-1][2], end(e))
        else:
            gap = start(e) - groups[-1][2] if groups else 0.0
            groups.append([[e], gap, end(e)])
    return [(members, gap) for members, gap, _ in groups]


def _wrap(items: list[El]) -> El:
    if len(items) == 1:
        return items[0]
    x, y = min(e.x for e in items), min(e.y for e in items)
    r, b = max(e.right for e in items), max(e.bottom for e in items)
    return El(
        f"w{next(_wrapper_ids)}",
        "group",
        "wrapper",
        x,
        y,
        r - x,
        b - y,
        z=min(e.z for e in items),
        children=items,
    )


def xy_cut(items: list[El]) -> tuple[str, list[El]]:
    """Returns (mode, children) where mode is column, row or absolute."""
    if len(items) <= 1:
        return "column", items
    ys, xs = _partition(items, "y"), _partition(items, "x")
    max_y = max((g for _, g in ys[1:]), default=-1)
    max_x = max((g for _, g in xs[1:]), default=-1)
    if len(ys) == 1 and len(xs) == 1:
        return "absolute", items
    if len(xs) == 1 or (len(ys) > 1 and max_y >= max_x):
        axis, parts, max_gap = "y", ys, max_y
    else:
        axis, parts, max_gap = "x", xs, max_x
    threshold = max_gap * SPLIT_RATIO
    merged: list[list[El]] = []
    for group, gap in parts:
        if merged and gap < threshold:
            merged[-1].extend(group)
        else:
            merged.append(list(group))
    mode = "column" if axis == "y" else "row"
    children = []
    for group in merged:
        w = _wrap(group)
        if w.kind == "wrapper":
            sub_mode, sub_children = xy_cut(w.children)
            if sub_mode == mode:
                children.extend(sub_children)  # same direction: splice instead of nesting
                continue
            w.children, w.layout = sub_children, {"mode": sub_mode}
        children.append(w)
    return mode, children


def layout_container(c: El) -> None:
    if c.auto and c.node is not None:
        mode = "row" if c.node.get("layoutMode") == "HORIZONTAL" else "column"
        c.layout = {"mode": mode, "auto": True}
        for ch in c.children:
            if ch.children:
                layout_container(ch)
        return
    items = detect_offset_decorations(c.children)
    items = nest_contained(items)
    flow, layers = split_layers(items)
    c.layers.extend(layers)
    mode, children = xy_cut(flow)
    c.children = children
    c.layout = {"mode": mode}
    for ch in c.layers + c.children:
        if ch.kind == "wrapper":
            for g in ch.children:
                _recurse(g)
        else:
            _recurse(ch)


def _recurse(e: El) -> None:
    if e.kind == "wrapper":
        for g in e.children:
            _recurse(g)
    elif e.children:
        layout_container(e)


def layout_page(page: Page) -> Page:
    for sec in page.sections:
        if sec.auto:
            layout_container(sec)
            continue
        layout_container(sec)
        # Wrap the section's flow in a container so it can be centred and padded.
        if sec.children:
            inner = _wrap(sec.children) if len(sec.children) > 1 else None
            if inner is None:
                only = sec.children[0]
                inner = El(
                    f"w{next(_wrapper_ids)}",
                    "container",
                    "wrapper",
                    only.x,
                    only.y,
                    only.w,
                    only.h,
                    children=[only],
                )
                inner.layout = {"mode": "column"}
            else:
                inner.layout = dict(sec.layout)
            inner.kind = "container"
            sec.children = [inner]
            sec.layout = {"mode": "column"}
    return page
