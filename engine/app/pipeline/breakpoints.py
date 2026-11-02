"""Mobile / tablet frames → media queries on the desktop page.

The desktop frame is generated as the page; a mobile (or tablet) frame of the same page describes how
that page should look on a phone. Elements are matched between the frames (same text, same image
bytes, same layer name, in reading order), and only the differences are written, per element, in a
media query: font sizes, line lengths, visibility, the direction and gaps of rows, grid columns,
paddings and spacing. The same matches give the expected boxes for checking the phone layout against
the mobile frame.
"""

from __future__ import annotations

import re
import statistics
from collections import defaultdict
from dataclasses import dataclass, field

from .ir import El, Page
from .styles import line_height_px, px, text_style

LEAVES = ("text", "image", "svg", "raster", "shape")
FLUID_TEXT_MIN = 32.0  # keep in sync with generate.FLUID_TEXT_MIN
AXIS = {"MIN": "flex-start", "CENTER": "center", "MAX": "flex-end", "BASELINE": "baseline"}
CONTAINERS = ("box", "wrapper", "container", "section")


def _norm_text(e: El) -> str:
    return re.sub(r"\s+", " ", (e.node or {}).get("characters", "")).strip().lower()


def _order(e: El) -> tuple[float, float]:
    return (round(e.y / 8), e.x)


def _walk(page: Page) -> list[El]:
    out: list[El] = []
    for sec in page.sections:
        for e in sec.walk():
            if e is not sec:
                out.append(e)
    return out


@dataclass
class Match:
    pairs: dict[int, El] = field(default_factory=dict)  # id(desktop el) -> mobile el
    unmatched: list[El] = field(default_factory=list)  # desktop leaves with no counterpart
    mobile_only: list[El] = field(default_factory=list)
    sections: dict[int, El] = field(default_factory=dict)  # id(desktop section) -> mobile section


def match(desk: Page, mob: Page, hashes: dict[str, str] | None = None) -> Match:
    hashes = hashes or {}

    def key(e: El) -> tuple | None:
        if e.kind == "text":
            t = _norm_text(e)
            return ("text", t) if t else None
        if e.kind in ("image", "svg", "raster") and e.asset:
            return ("asset", hashes.get(e.asset) or re.sub(r"^(svg|png|img)-", "", e.asset))
        if e.kind == "shape" and e.name:
            return ("shape", e.name.lower())  # sizes change between frames; the layer name does not
        return None

    m = Match()
    d_leaves = [e for e in _walk(desk) if e.kind in LEAVES]
    m_leaves = [e for e in _walk(mob) if e.kind in LEAVES]
    by_key: dict[tuple, list[El]] = defaultdict(list)
    for e in sorted(m_leaves, key=_order):
        k = key(e)
        if k:
            by_key[k].append(e)
    used: set[int] = set()
    groups: dict[tuple, list[El]] = defaultdict(list)
    for e in sorted(d_leaves, key=_order):
        k = key(e)
        if k:
            groups[k].append(e)
    # Texts first (i-th occurrence ↔ i-th occurrence), then repeated icons and shapes by context: an
    # icon goes with the one next to the text its own neighbour matched (the arrow of the "Get started"
    # button pairs with the arrow of the mobile "Get started" button, even if another button with the
    # same arrow comes first on desktop and is missing on mobile).
    parent: dict[int, El] = {}
    for e in _walk(desk):
        for c in e.layers + e.children:
            parent[id(c)] = e
    deferred: list[tuple[tuple, list[El]]] = []
    for k, desk_items in groups.items():
        cands = by_key.get(k, [])
        if k[0] != "text" and (len(desk_items) > 1 or len(cands) > 1):
            deferred.append((k, desk_items))
            continue
        for d_el, m_el in zip(desk_items, cands, strict=False):  # i-th occurrence ↔ i-th occurrence
            m.pairs[id(d_el)] = m_el
            used.add(id(m_el))
        m.unmatched += desk_items[len(cands) :]

    def context(d_el: El) -> El | None:
        """The mobile match of a text next to d_el (in its button, card or row; never the whole section)."""
        node, depth = parent.get(id(d_el)), 0
        while node is not None and node.kind not in ("section", "container", "page") and depth < 2:
            for x in node.walk():
                if x is not d_el and x.kind == "text" and id(x) in m.pairs:
                    return m.pairs[id(x)]
            node, depth = parent.get(id(node)), depth + 1
        return None

    for k, desk_items in deferred:
        free = [c for c in by_key.get(k, []) if id(c) not in used]
        leftover = []
        for d_el in desk_items:
            ctx = context(d_el)
            if ctx is None or not free:
                leftover.append(d_el)
                continue
            best = min(free, key=lambda c: abs(c.x - ctx.x) + abs(c.y - ctx.y))
            if abs(best.y - ctx.y) + abs(best.x - ctx.x) > 240:
                leftover.append(d_el)  # nothing of this kind near its neighbour on mobile
                continue
            m.pairs[id(d_el)] = best
            used.add(id(best))
            free.remove(best)
        for d_el in leftover:
            if free and context(d_el) is None:
                m.pairs[id(d_el)] = free.pop(0)
                used.add(id(m.pairs[id(d_el)]))
            else:
                m.unmatched.append(d_el)
    m.unmatched += [e for e in d_leaves if key(e) is None and id(e) not in m.pairs]
    m.mobile_only = [e for e in m_leaves if id(e) not in used and key(e) is not None]

    # Containers: the mobile box that holds what the desktop box holds.
    m_boxes = [e for e in _walk(mob) if e.kind in ("box",)]
    for d_box in (e for e in _walk(desk) if e.kind == "box"):
        inside = [m.pairs[id(x)] for x in d_box.walk() if x is not d_box and id(x) in m.pairs]
        if not inside:
            continue
        best = None
        for mb in m_boxes:
            if all(mb.contains(x, tol=2) for x in inside) and (best is None or mb.area < best.area):
                best = mb
        if best is not None and (best.name.lower() == d_box.name.lower() or len(inside) >= 2):
            m.pairs[id(d_box)] = best

    # Sections: the mobile section holding most of the desktop section's matched content.
    for d_sec in desk.sections:
        votes: dict[int, int] = defaultdict(int)
        lookup: dict[int, El] = {}
        for x in d_sec.walk():
            mx = m.pairs.get(id(x))
            if mx is None:
                continue
            for s in mob.sections:
                if s.contains(mx, tol=2) or (s.y <= mx.y < s.bottom):
                    votes[id(s)] += 1
                    lookup[id(s)] = s
                    break
        if votes:
            m.sections[id(d_sec)] = lookup[max(votes, key=votes.get)]
    return m


def _union(els: list[El]) -> tuple[float, float, float, float] | None:
    if not els:
        return None
    x0, y0 = min(e.x for e in els), min(e.y for e in els)
    return x0, y0, max(e.right for e in els) - x0, max(e.bottom for e in els) - y0


def mobile_box(e: El, m: Match) -> tuple[float, float, float, float] | None:
    """Where e sits in the mobile frame: its own match, or the union of its matched content."""
    if id(e) in m.pairs:
        x = m.pairs[id(e)]
        return x.x, x.y, x.w, x.h
    return _union([m.pairs[id(x)] for x in e.walk() if x is not e and id(x) in m.pairs])


def derive_css(desk: Page, mob: Page, m: Match) -> dict[int, dict[str, str]]:
    """{id(desktop el): declarations} for the breakpoint the mobile frame describes."""
    out: dict[int, dict[str, str]] = defaultdict(dict)
    MW = mob.width

    # Hidden on this breakpoint: content the frame does not contain at all. A button, card, menu or
    # row whose content is all missing is hidden as a whole (not left behind as an empty box).
    missing = {id(e) for e in m.unmatched if e.kind in LEAVES}

    def hide(e: El) -> None:
        if e.kind in LEAVES:
            if id(e) in missing:
                out[id(e)]["display"] = "none"
            return
        leaves = [x for x in e.walk() if x.kind in LEAVES]
        if e.kind in ("box", "wrapper") and leaves and all(id(x) in missing for x in leaves):
            out[id(e)]["display"] = "none"
            return
        for c in e.layers + e.children:
            hide(c)

    for sec in desk.sections:
        hide(sec)

    prev_bottom = 0.0
    lefts: list[float] = []
    for sec in desk.sections:
        msec = m.sections.get(id(sec))
        if msec is None:
            continue
        gap = msec.y - prev_bottom
        prev_bottom = msec.bottom
        if abs(gap) > 0.5 or sec is not desk.sections[0]:
            out[id(sec)]["margin-top"] = px(gap) if abs(gap) > 0.5 else "0"
        content = [m.pairs[id(x)] for x in sec.walk() if id(x) in m.pairs and m.pairs[id(x)].kind in LEAVES]
        flow = [c for c in content if not (c.x <= 1 and c.right >= MW - 1 and c.h >= msec.h * 0.8)]
        pad = _auto_padding(msec)
        box = _union(flow)
        if pad is None and box:
            pad = (box[1] - msec.y, msec.right - (box[0] + box[2]), msec.bottom - (box[1] + box[3]), box[0] - msec.x)
        in_container = len(sec.children) == 1 and sec.children[0].layout.get("page_container")
        if pad and all(v >= 0 for v in pad):
            lefts.append(pad[3])
            # Sections whose content sits in the page container only pad top and bottom: the
            # container's gutter (set from this frame below) does the sides.
            out[id(sec)]["padding"] = _pad((pad[0], 0, pad[2], 0)) if in_container else _pad(pad)
        out[id(sec)]["min-height"] = px(msec.h)
        _containers(sec, m, out, MW)

    for e in _all(desk):
        mx = m.pairs.get(id(e))
        if mx is None:
            continue
        if e.kind == "text":
            out[id(e)].update(_text_diff(e, mx, MW))
        elif e.kind in ("image", "raster") or (e.kind == "svg" and max(e.w, e.h) > 64):
            if abs(e.w / max(e.h, 1) - mx.w / max(mx.h, 1)) > 0.02:
                out[id(e)]["aspect-ratio"] = f"{round(mx.w)} / {round(mx.h)}"
            out[id(e)]["max-width"] = "none" if mx.w >= MW * 0.85 else px(mx.w)
        elif e.kind == "box":
            kids = [m.pairs[id(c)] for c in e.children if id(c) in m.pairs]
            pad = _auto_padding(mx)
            if pad is None and kids:
                bx = _union(kids)
                pad = (bx[1] - mx.y, mx.right - (bx[0] + bx[2]), mx.bottom - (bx[1] + bx[3]), bx[0] - mx.x)
            if pad and all(p >= 0 for p in pad) and e.role != "button":
                out[id(e)]["padding"] = _pad(pad)
            if e.children:
                out[id(e)]["min-height"] = px(mx.h) if e.role != "button" else "0"
            if mx.w >= MW * 0.85:
                out[id(e)].update({"max-width": "none", "width": "100%"})
    if lefts:
        out[ROOT_KEY]["--gutter"] = px(statistics.median(lefts))
    return {k: v for k, v in out.items() if v}


ROOT_KEY = 0  # pseudo element id: declarations for :root on this breakpoint


def _auto_padding(e: El) -> tuple[float, float, float, float] | None:
    """The frame's own Auto Layout padding, when it has one (exact, unlike measured gaps)."""
    n = e.node or {}
    if n.get("layoutMode") not in ("HORIZONTAL", "VERTICAL"):
        return None
    return tuple(float(n.get(k) or 0) for k in ("paddingTop", "paddingRight", "paddingBottom", "paddingLeft"))


def _pad(p: tuple[float, float, float, float]) -> str:
    t, r, b, left = (round(v, 2) for v in p)
    if t == b and r == left:
        return px(t) if t == r else f"{px(t)} {px(r)}"
    return " ".join(px(v) for v in (t, r, b, left))


def _all(page: Page):
    for sec in page.sections:
        yield from sec.walk()


def _text_diff(e: El, mx: El, MW: float) -> dict[str, str]:
    n, mn = e.node or {}, mx.node or {}
    segs, msegs = n.get("textSegments") or [{}], mn.get("textSegments") or [{}]
    a = text_style(max(segs, key=lambda s: len(s.get("characters", ""))), n)
    b = text_style(max(msegs, key=lambda s: len(s.get("characters", ""))), mn)
    d = {k: b[k] for k in ("font-size", "letter-spacing", "font-weight") if b.get(k) and b.get(k) != a.get(k)}
    if b.get("font-size", "").endswith("px") and float(a.get("font-size", "0px")[:-2] or 0) >= FLUID_TEXT_MIN:
        d["font-size"] = b["font-size"]  # the frame's exact size wins over the fluid default
    lh = line_height_px({**mn, **{k: v for k, v in msegs[0].items() if k == "lineHeight"}})
    if lh and (b.get("line-height") != a.get("line-height") or "font-size" in d):
        d["line-height"] = px(lh) if b.get("line-height", "").endswith("px") or b.get("line-height") == "normal" else b[
            "line-height"]  # fmt: skip
    align = mn.get("textAlignHorizontal") or "LEFT"
    if align != (n.get("textAlignHorizontal") or "LEFT"):
        d["text-align"] = {"CENTER": "center", "RIGHT": "right", "JUSTIFIED": "justify"}.get(align, "left")
    if mx.w >= MW * 0.8 or (mn.get("textAutoResize") != "WIDTH_AND_HEIGHT" and mx.w > e.w):
        d["max-width"] = "none" if mx.w >= MW * 0.8 else px(mx.w)
    elif mn.get("textAutoResize") != "WIDTH_AND_HEIGHT" and mx.w < e.w - 2:
        d["max-width"] = px(mx.w)
    if "\n" not in mn.get("characters", "") and round(mx.h / (lh or mx.h)) > 1:
        d["white-space"] = "normal"  # wraps on this screen
    return d


def _containers(sec: El, m: Match, out: dict, MW: float) -> None:
    """Rows, grids and columns: direction, gaps, order and alignment from the mobile frame."""
    for e in sec.walk():
        kids = [c for c in e.children]
        if len(kids) < 2 or e.kind not in CONTAINERS:
            continue
        boxes = [(c, mobile_box(c, m)) for c in kids]
        boxes = [(c, b) for c, b in boxes if b is not None]
        if len(boxes) < 2:
            continue
        mode = e.layout.get("mode", "column")
        by_y = sorted(boxes, key=lambda t: (t[1][1], t[1][0]))
        stacked = all(b2[1] >= b1[1] + b1[3] - 2 for (_, b1), (_, b2) in zip(by_y, by_y[1:], strict=False))
        xs = sorted({round(b[0] / 4) for _, b in boxes})
        decls = out[id(e)]
        if mode == "row" and stacked:
            decls.update({"flex-direction": "column", "display": "flex"})
            if e.layout.get("grid") or _is_grid_like(e):
                decls.update({"display": "grid", "grid-template-columns": "1fr"})
            gaps = [b2[1] - (b1[1] + b1[3]) for (_, b1), (_, b2) in zip(by_y, by_y[1:], strict=False)]
            _set_gaps(decls, gaps, out, [c for c, _ in by_y[1:]], vertical=True)
            lefts = [b[0] for _, b in by_y]
            widths = [b[2] for _, b in by_y]
            if max(widths) >= MW * 0.8 and min(widths) >= MW * 0.8:
                decls["align-items"] = "stretch"
            elif max(lefts) - min(lefts) <= 2:
                decls["align-items"] = "flex-start"
            else:
                centres = [b[0] + b[2] / 2 for _, b in by_y]
                if max(centres) - min(centres) <= 4:
                    decls["align-items"] = "center"
            for k, (c, _) in enumerate(by_y):
                if kids.index(c) != k:
                    out[id(c)]["order"] = str(k)
                out[id(c)].update({"margin-left": "0", "flex": "none"})
        elif mode == "row" and len(xs) >= 2 and _is_grid_like(e):
            rows = {round(b[1] / 4) for _, b in boxes}
            cols = max(1, round(len(boxes) / max(1, len(rows))))
            decls.update({"grid-template-columns": f"repeat({cols}, minmax(0, 1fr))"})
            ordered = sorted(boxes, key=lambda t: t[1][0])
            hg = [
                b2[0] - (b1[0] + b1[2]) for (_, b1), (_, b2) in zip(ordered, ordered[1:], strict=False) if b2[0] > b1[0]
            ]
            if hg and min(hg) > 0:
                decls["column-gap"] = px(statistics.median(hg))
            vg = sorted({b[1] for _, b in boxes})
            if len(vg) > 1:
                hs = [b[3] for _, b in boxes]
                decls["row-gap"] = px(max(0, (vg[1] - vg[0]) - max(hs)))
        elif mode == "column" and stacked:
            gaps = [b2[1] - (b1[1] + b1[3]) for (_, b1), (_, b2) in zip(by_y, by_y[1:], strict=False)]
            _set_gaps(decls, gaps, out, [c for c, _ in by_y[1:]], vertical=True)
        elif mode == "row":
            # Still side by side on this screen: undo the generic "rows stack on phones" rule.
            counter = (e.node or {}).get("counterAxisAlignItems") if e.layout.get("auto") else None
            decls.update({"flex-direction": "row", "align-items": AXIS.get(counter or "MIN", "flex-start")})
            for c, _ in boxes:
                out[id(c)].setdefault("flex", "0 1 auto")
        if mode == "row" and not stacked and (e.node or {}).get("primaryAxisAlignItems") != "SPACE_BETWEEN" and len(
            boxes
        ) > 2:  # fmt: skip
            ordered = sorted(boxes, key=lambda t: t[1][0])
            hg = [b2[0] - (b1[0] + b1[2]) for (_, b1), (_, b2) in zip(ordered, ordered[1:], strict=False)]
            if hg and min(hg) >= 0 and max(hg) - min(hg) <= 2:
                decls["gap"] = px(statistics.median(hg))


def _is_grid_like(e: El) -> bool:
    ws = [c.w for c in e.children]
    return len(ws) >= 2 and max(ws) - min(ws) <= 2 and not all(c.kind == "text" for c in e.children)


def _set_gaps(decls: dict, gaps: list[float], out: dict, after: list[El], vertical: bool) -> None:
    if not gaps:
        return
    if max(gaps) - min(gaps) <= 2 and min(gaps) >= 0:
        decls["gap"] = px(statistics.median(gaps))
        for c in after:
            out[id(c)]["margin-top" if vertical else "margin-left"] = "0"
    else:
        decls["gap"] = "0"
        for c, g in zip(after, gaps, strict=False):
            out[id(c)]["margin-top" if vertical else "margin-left"] = px(max(0.0, g))


def expectations(desk_expect: list[dict], desk: Page, mob: Page, m: Match) -> list[dict]:
    """Desktop expectations re-targeted at the mobile frame: same element ids, mobile boxes."""
    by_node: dict[str, El] = {}
    for e in _all(desk):
        if e.node is not None:
            by_node.setdefault(e.node["id"], e)
    root = desk.frame.get("rootId", "page")
    sec_by_id = {f"section:{root}:{s.id}": s for s in desk.sections}
    out = []
    for exp in desk_expect:
        d_el = sec_by_id.get(exp["id"]) or by_node.get(exp["id"])
        if d_el is None:
            continue
        if exp["kind"] == "section":
            ms = m.sections.get(id(d_el))
            if ms is None:
                continue
            out.append({**exp, "box": [ms.x, ms.y, ms.w, ms.h]})
            continue
        mx = m.pairs.get(id(d_el))
        if mx is None:
            continue
        item = {**exp, "box": [mx.x, mx.y, mx.w, mx.h]}
        if exp["kind"] == "text":
            mn = mx.node or {}
            segs = mn.get("textSegments") or [{}]
            st = text_style(max(segs, key=lambda s: len(s.get("characters", ""))), mn)
            lh = line_height_px({**mn, **{k: v for k, v in segs[0].items() if k == "lineHeight"}})
            item.update({
                "lineHeight": lh, "lines": round(mx.h / lh) if lh else None,
                "hug": mn.get("textAutoResize") == "WIDTH_AND_HEIGHT",
                "align": mn.get("textAlignHorizontal") or "LEFT",
                "style": {k: st[k] for k in ("font-family", "font-size", "font-weight", "color") if k in st},
            })  # fmt: skip
        out.append(item)
    return out
