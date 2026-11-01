"""Converts Figma paint, stroke, effect and text properties into CSS declarations."""

from __future__ import annotations

import math
import re

WEIGHTS = {
    "thin": 100, "hairline": 100, "extralight": 200, "ultralight": 200, "light": 300,
    "regular": 400, "normal": 400, "book": 400, "medium": 500, "semibold": 600, "demibold": 600,
    "bold": 700, "extrabold": 800, "ultrabold": 800, "black": 900, "heavy": 900,
}  # fmt: skip


def px(v: float) -> str:
    """Formats a pixel value without trailing zeros."""
    r = round(v, 2)
    return "0" if r == 0 else f"{r:g}px"


def num(v: float) -> str:
    return f"{round(v, 3):g}"


def color(c: dict, opacity: float = 1.0) -> str:
    r, g, b = (round(c.get(k, 0) * 255) for k in ("r", "g", "b"))
    a = c.get("a", 1) * opacity
    if a >= 0.999:
        return f"#{r:02x}{g:02x}{b:02x}"
    return f"rgba({r}, {g}, {b}, {num(a)})"


def visible_paints(paints) -> list[dict]:
    if not isinstance(paints, list):
        return []
    return [p for p in paints if isinstance(p, dict) and p.get("visible", True) and p.get("opacity", 1) > 0]


def font_weight(style: str, fallback: int | None = None) -> int:
    key = re.sub(r"[\s_-]|italic|oblique", "", style.lower())
    return WEIGHTS.get(key, fallback or 400)


def is_italic(style: str) -> bool:
    return "italic" in style.lower() or "oblique" in style.lower()


def _inverse_affine(t) -> tuple[float, float, float, float, float, float] | None:
    """Inverse of Figma's 2x3 gradientTransform (element space → gradient space)."""
    try:
        (a, b, c), (d, e, f) = t
    except (TypeError, ValueError):
        return None
    det = a * e - b * d
    if abs(det) < 1e-9:
        return None
    ia, ib, id_, ie = e / det, -b / det, -d / det, a / det
    return ia, ib, -(ia * c + ib * f), id_, ie, -(id_ * c + ie * f)


def _apply(m, x: float, y: float) -> tuple[float, float]:
    return m[0] * x + m[1] * y + m[2], m[3] * x + m[4] * y + m[5]


def _gradient(p: dict, size: tuple[float, float] | None = None) -> str | None:
    stops = p.get("gradientStops") or []
    if not stops:
        return None
    op = p.get("opacity", 1)
    positions = [float(s["position"]) for s in stops]
    t = p.get("gradientTransform")
    inv = _inverse_affine(t) if t else None
    w, h = size if size else (100.0, 100.0)

    def parts(pos: list[float]) -> str:
        return ", ".join(f"{color(s['color'], op)} {num(q * 100)}%" for s, q in zip(stops, pos, strict=True))

    if p["type"] == "GRADIENT_LINEAR":
        if inv is None:
            return f"linear-gradient(180deg, {parts(positions)})"
        # The gradient runs from (0, .5) to (1, .5) in gradient space; map both ends to pixels.
        x0, y0 = _apply(inv, 0, 0.5)
        x1, y1 = _apply(inv, 1, 0.5)
        x0, y0, x1, y1 = x0 * w, y0 * h, x1 * w, y1 * h
        dx, dy = x1 - x0, y1 - y0
        if abs(dx) < 1e-9 and abs(dy) < 1e-9:
            return f"linear-gradient(180deg, {parts(positions)})"
        theta = math.atan2(dx, -dy)  # CSS: 0deg points up, clockwise
        ux, uy = math.sin(theta), -math.cos(theta)
        length = abs(w * ux) + abs(h * uy)
        sx, sy = w / 2 - ux * length / 2, h / 2 - uy * length / 2
        t0 = ((x0 - sx) * ux + (y0 - sy) * uy) / length
        t1 = ((x1 - sx) * ux + (y1 - sy) * uy) / length
        mapped = [t0 + q * (t1 - t0) for q in positions]
        return f"linear-gradient({num(math.degrees(theta) % 360)}deg, {parts(mapped)})"
    if p["type"] in ("GRADIENT_RADIAL", "GRADIENT_DIAMOND"):
        if inv is None:
            return f"radial-gradient(ellipse 50% 50% at 50% 50%, {parts(positions)})"
        # Gradient space: centre (.5, .5), radius .5 along both axes.
        cx, cy = _apply(inv, 0.5, 0.5)
        ax = (inv[0] * 0.5, inv[3] * 0.5)  # gradient x axis (radius) in element space
        ay = (inv[1] * 0.5, inv[4] * 0.5)
        rx = math.hypot(ax[0] * w, ay[0] * w) / w * 100
        ry = math.hypot(ax[1] * h, ay[1] * h) / h * 100
        return (
            f"radial-gradient(ellipse {num(rx)}% {num(ry)}% at {num(cx * 100)}% {num(cy * 100)}%, {parts(positions)})"
        )
    if p["type"] == "GRADIENT_ANGULAR":
        cx, cy = _apply(inv, 0.5, 0.5) if inv else (0.5, 0.5)
        return f"conic-gradient(from 90deg at {num(cx * 100)}% {num(cy * 100)}%, {parts(positions)})"
    return None


def background(paints, size: tuple[float, float] | None = None) -> dict[str, str]:
    """Solid and gradient fills → background. Image fills are handled as <img> elements.

    `size` (w, h) lets gradients map Figma's handles exactly; without it they are approximated.
    """
    layers = []
    for p in reversed(visible_paints(paints)):  # Figma lists bottom → top; CSS lists top → bottom
        if p["type"] == "SOLID":
            layers.append(color(p["color"], p.get("opacity", 1)))
        elif p["type"].startswith("GRADIENT"):
            g = _gradient(p, size)
            if g:
                layers.append(g)
    if not layers:
        return {}
    if len(layers) == 1 and not layers[0].startswith(("linear", "radial", "conic")):
        return {"background-color": layers[0]}
    # A solid colour can only be the bottom layer in CSS; wrap others as flat gradients.
    css = [lay if lay.startswith(("linear", "radial", "conic")) else f"linear-gradient({lay}, {lay})" for lay in layers]
    return {"background": ", ".join(css)}


def text_color(paints) -> str | None:
    for p in reversed(visible_paints(paints)):
        if p["type"] == "SOLID":
            return color(p["color"], p.get("opacity", 1))
    return None


def border(node: dict) -> dict[str, str]:
    strokes = [p for p in visible_paints(node.get("strokes")) if p["type"] == "SOLID"]
    weight = node.get("strokeWeight")
    if not strokes or not isinstance(weight, (int, float)) or weight <= 0:
        return {}
    c = color(strokes[-1]["color"], strokes[-1].get("opacity", 1))
    style = "dashed" if node.get("dashPattern") else "solid"
    if node.get("strokeAlign") == "OUTSIDE":
        return {"box-shadow": f"0 0 0 {px(weight)} {c}"}
    sides = [node.get(f"stroke{s}Weight") for s in ("Top", "Right", "Bottom", "Left")]
    if all(isinstance(s, (int, float)) for s in sides) and len(set(sides)) > 1:
        out = {}
        for name, w in zip(("top", "right", "bottom", "left"), sides, strict=True):
            if w > 0:
                out[f"border-{name}"] = f"{px(w)} {style} {c}"
        return out
    return {"border": f"{px(weight)} {style} {c}"}


def border_widths(node: dict) -> tuple[float, float, float, float]:
    """Widths (top, right, bottom, left) that `border()` draws inside the box; 0 when none."""
    strokes = [p for p in visible_paints(node.get("strokes")) if p["type"] == "SOLID"]
    weight = node.get("strokeWeight")
    if not strokes or not isinstance(weight, (int, float)) or weight <= 0 or node.get("strokeAlign") == "OUTSIDE":
        return 0.0, 0.0, 0.0, 0.0
    sides = [node.get(f"stroke{s}Weight") for s in ("Top", "Right", "Bottom", "Left")]
    if all(isinstance(s, (int, float)) for s in sides):
        return tuple(float(s) for s in sides)  # type: ignore[return-value]
    return (float(weight),) * 4  # type: ignore[return-value]


def radius(node: dict) -> dict[str, str]:
    if node.get("type") == "ELLIPSE":
        return {"border-radius": "50%"}
    r = node.get("cornerRadius")
    if isinstance(r, (int, float)):
        return {"border-radius": px(r)} if r > 0 else {}
    corners = [
        node.get(k, 0) or 0 for k in ("topLeftRadius", "topRightRadius", "bottomRightRadius", "bottomLeftRadius")
    ]
    if any(corners):
        return {"border-radius": " ".join(px(c) for c in corners)}
    return {}


def effects(node: dict, is_text: bool = False) -> dict[str, str]:
    shadows, out = [], {}
    for e in node.get("effects") or []:
        if not isinstance(e, dict) or not e.get("visible", True):
            continue
        t = e.get("type")
        if t in ("DROP_SHADOW", "INNER_SHADOW"):
            o = e.get("offset", {"x": 0, "y": 0})
            c = color(e.get("color", {"r": 0, "g": 0, "b": 0, "a": 0.25}))
            if is_text:
                shadows.append(f"{px(o['x'])} {px(o['y'])} {px(e.get('radius', 0))} {c}")
            else:
                inset = "inset " if t == "INNER_SHADOW" else ""
                shadows.append(
                    f"{inset}{px(o['x'])} {px(o['y'])} {px(e.get('radius', 0))} {px(e.get('spread', 0) or 0)} {c}"
                )
        elif t == "LAYER_BLUR":
            out["filter"] = f"blur({px(e.get('radius', 0) / 2)})"
        elif t == "BACKGROUND_BLUR":
            out["backdrop-filter"] = f"blur({px(e.get('radius', 0) / 2)})"
    if shadows:
        out["text-shadow" if is_text else "box-shadow"] = ", ".join(shadows)
    return out


def text_style(seg: dict, node: dict) -> dict[str, str]:
    """CSS for a text node or one of its styled segments (segment values win)."""

    def pick(key):
        v = seg.get(key, node.get(key))
        return None if v == "MIXED" else v

    out: dict[str, str] = {}
    font = pick("fontName")
    if isinstance(font, dict):
        out["font-family"] = f"'{font['family']}'"
        out["font-weight"] = str(font_weight(font["style"], pick("fontWeight")))
        if is_italic(font["style"]):
            out["font-style"] = "italic"
    size = pick("fontSize")
    if isinstance(size, (int, float)):
        out["font-size"] = px(size)
    lh = pick("lineHeight")
    if isinstance(lh, dict):
        if lh.get("unit") == "PIXELS":
            out["line-height"] = px(lh["value"])
        elif lh.get("unit") == "PERCENT":
            out["line-height"] = num(lh["value"] / 100)
        else:
            out["line-height"] = "normal"
    ls = pick("letterSpacing")
    if isinstance(ls, dict) and ls.get("value"):
        out["letter-spacing"] = f"{num(ls['value'] / 100)}em" if ls.get("unit") == "PERCENT" else px(ls["value"])
    case = pick("textCase")
    if case in ("UPPER", "LOWER", "TITLE"):
        out["text-transform"] = {"UPPER": "uppercase", "LOWER": "lowercase", "TITLE": "capitalize"}[case]
    deco = pick("textDecoration")
    if deco in ("UNDERLINE", "STRIKETHROUGH"):
        out["text-decoration"] = "underline" if deco == "UNDERLINE" else "line-through"
    c = text_color(seg.get("fills", node.get("fills")))
    if c:
        out["color"] = c
    return out


def line_height_px(node: dict) -> float | None:
    lh, size = node.get("lineHeight"), node.get("fontSize")
    if isinstance(lh, dict) and lh.get("unit") == "PIXELS":
        return lh["value"]
    if isinstance(lh, dict) and lh.get("unit") == "PERCENT" and isinstance(size, (int, float)):
        return size * lh["value"] / 100
    if isinstance(size, (int, float)):
        return size * 1.2
    segs = node.get("textSegments") or []
    if segs and isinstance(segs[0].get("lineHeight"), dict) and segs[0]["lineHeight"].get("unit") == "PIXELS":
        return segs[0]["lineHeight"]["value"]
    return None
