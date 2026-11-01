"""Design IR (with inferred layout) → index.html + css/styles.css + images/ + fonts/."""

from __future__ import annotations

import base64
import html
import json
import math
import re
import shutil
import textwrap
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from . import fonts as fontfiles
from .ir import El, Page
from .styles import background, border, border_widths, effects, line_height_px, px, radius, text_style

RESETS = {
    "letter-spacing": "normal",
    "text-transform": "none",
    "text-decoration": "none",
    "font-style": "normal",
}

ALIGN_TOL = 2.0
GAP_TOL = 0.5
BUTTON_MAX_WORDS = 4
BUTTON_MAX_H = 80
ICON_MAX = 64
WEBP_QUALITY = 82
MAX_IMAGE_EDGE = 2560
PAINT_KINDS = {"text", "image", "svg", "raster", "shape", "box"}
EDGE_MAX_VIEWPORT = 3840  # edge panels reach the screen edge up to this width
MOBILE_MAX = 767  # inferred mobile layout below 768px
FIT_GUTTER = 24.0  # breathing room left and right of the content when the layout starts scaling
FIT_STEP = 32  # zoom steps (px of viewport) between the mobile breakpoint and the content width


def slug(text: str, fallback: str = "section") -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:32].strip("-") or fallback


def _font_size(e: El) -> float:
    n = e.node or {}
    s = n.get("fontSize")
    if isinstance(s, (int, float)):
        return s
    segs = n.get("textSegments") or []
    return max((sg.get("fontSize", 0) for sg in segs), default=0)


def _font_weight(e: El) -> int:
    n = e.node or {}
    w = n.get("fontWeight")
    return w if isinstance(w, int) else 400


def _texts(e: El) -> list[El]:
    return [x for x in e.walk() if x.kind == "text"]


@dataclass
class Output:
    html: str
    css: str
    images: dict[str, Path] = field(default_factory=dict)
    fonts: list[dict] = field(default_factory=list)
    expectations: list[dict] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    pages: list[dict] = field(default_factory=list)
    components: list[str] = field(default_factory=list)


class Generator:
    def __init__(self, page: Page | None, bundle: dict, asset_dir: Path, font_dir: Path | None = None):
        self.page = page
        self.bundle = bundle
        self.asset_dir = asset_dir
        self.font_dir = font_dir
        self.asset_meta = {a["id"]: a for a in bundle["assets"]}
        self.rules: dict[str, dict[str, dict[str, str]]] = {}  # class -> {suffix: decls}
        self._class_by_sig: dict[tuple, str] = {}
        self._class_count: Counter = Counter()
        self.image_uses: dict[str, dict] = {}  # output stem -> {asset, crop, w, h}
        self.fit_width = 0.0  # narrowest viewport that still shows all content at 100%
        self._stack: set[int] = set()  # elements that need an explicit z-index (paint order)
        self.expect: list[dict] = []
        self.assumptions: list[str] = []
        self.warnings: list[str] = list(page.warnings) if page else []
        self.section_slug = "page"
        self._anc: list[str] = []  # figma ids of measurable ancestors while rendering
        self.asset_hashes: dict[str, str] = {}
        self._by_hash: dict[str, str] = {}
        self.components: dict[str, str] = {}  # component name -> rendered HTML (first occurrence)

    def _expect(self, item: dict) -> None:
        item["parent"] = self._anc[-1] if self._anc else None
        self.expect.append(item)

    # ------------------------------------------------------------------ roles
    def assign_roles(self) -> None:
        texts = [t for s in self.page.sections for t in _texts(s)]
        if texts:
            h1 = max(texts, key=lambda t: (_font_size(t), -t.y))
            h1.tag = "h1"
        for i, sec in enumerate(self.page.sections):
            sec_texts = [t for t in _texts(sec) if t.tag != "h1"]
            body = sorted(_font_size(t) for t in sec_texts) or [16]
            median = body[len(body) // 2]
            heading = max(sec_texts, key=lambda t: (_font_size(t), -t.y), default=None)
            if heading and _font_size(heading) >= max(24, median * 1.25):
                heading.tag = "h2"
            for t in sec_texts:
                if t.tag:
                    continue
                if _font_weight(t) >= 600 and _font_size(t) >= 18 and _font_size(t) > median:
                    t.tag = "h3"
            has_h1 = any(t.tag == "h1" for t in _texts(sec))
            heading_text = (heading.node.get("characters", "") if heading and heading.tag == "h2" else "").split("\n")[
                0
            ]
            if has_h1:
                sec.role = "hero"
            elif heading_text:
                sec.role = slug(heading_text)
            elif i == len(self.page.sections) - 1:
                sec.role = "footer"
            else:
                sec.role = f"section-{i + 1}"
            if sec.component:
                sec.role = sec.component
            sec.tag = sec.tag or ("footer" if sec.role in ("footer", "site-footer") else "section")
            for e in sec.walk():
                if e.kind == "box" and self._is_button(e):
                    e.role, e.tag = "button", "a"
                    for t in _texts(e):
                        t.tag = "span"

    @staticmethod
    def _draw_box(e: El) -> tuple[float, float, float, float]:
        """Box an element is drawn at: its layer box plus any SVG bleed (shadows, outside strokes)."""
        if not e.bleed:
            return e.x, e.y, e.w, e.h
        t, r, b, left = e.bleed
        return e.x - left, e.y - t, e.w + left + r, e.h + t + b

    @staticmethod
    def _apply_bleed(e: El, d: dict[str, str]) -> None:
        """Negative margins so a larger-than-box SVG still takes exactly its box in the layout."""
        t, r, b, left = e.bleed

        def val(key: str) -> float:
            v = d.get(key, "0")
            return float(v[:-2]) if v.endswith("px") else float(v or 0)

        if d.get("position") == "absolute":
            d["left"], d["top"] = px(val("left") - left), px(val("top") - t)
            return
        d["margin-left"] = px(val("margin-left") - left)
        d["margin-top"] = px(val("margin-top") - t)
        d["margin-right"] = px(-r)
        d["margin-bottom"] = px(-b)

    def _svg_data_uri(self, asset_id: str, limit: int = 24_000) -> str | None:
        """Small SVGs used as CSS masks are inlined: browsers refuse file:// masks."""
        src = self.asset_dir / f"{asset_id}.svg"
        if not src.exists() or src.stat().st_size > limit:
            return None
        svg = src.read_text(encoding="utf-8")
        enc = base64.b64encode(svg.encode("utf-8")).decode("ascii")
        return f'url("data:image/svg+xml;base64,{enc}")'

    @staticmethod
    def _backdrop_blur(e: El) -> float:
        for fx in (e.node or {}).get("effects") or []:
            if isinstance(fx, dict) and fx.get("type") == "BACKGROUND_BLUR" and fx.get("visible", True):
                return float(fx.get("radius", 0))
        return 0.0

    @staticmethod
    def _is_button(e: El) -> bool:
        texts = _texts(e)
        if len(texts) != 1 or e.h > BUTTON_MAX_H or e.w > 400:
            return False
        words = texts[0].node.get("characters", "").split()
        others = [x for x in e.walk() if x is not e and x.kind not in ("text", "svg", "wrapper")]
        return 0 < len(words) <= BUTTON_MAX_WORDS and not others

    # ------------------------------------------------------------------ classes
    def class_for(self, role: str, css: dict[str, dict[str, str]]) -> str:
        sig = (self.section_slug, role, tuple(sorted((k, tuple(sorted(v.items()))) for k, v in css.items())))
        if sig in self._class_by_sig:
            return self._class_by_sig[sig]
        base = f"{self.section_slug}__{role}" if role != self.section_slug else role
        self._class_count[base] += 1
        name = base if self._class_count[base] == 1 else f"{base}--{self._class_count[base]}"
        self._class_by_sig[sig] = name
        self.rules[name] = css
        return name

    # ------------------------------------------------------------------ styles
    def visual(self, e: El) -> dict[str, str]:
        n = e.node or {}
        d: dict[str, str] = {}
        if e.kind in ("box", "shape", "section"):
            d.update(background(n.get("fills"), (e.w, e.h)))
            d.update(border(n))
        if e.kind in ("box", "shape", "image", "section"):
            d.update(radius(n))
            d.update(effects(n))
        if e.kind == "text":
            d.update(effects(n, is_text=True))
        op = n.get("opacity", 1)
        # Exported SVG/PNG layers already carry the layer's own opacity (Figma bakes it in).
        if isinstance(op, (int, float)) and op < 0.999 and e.kind not in ("svg", "raster"):
            d["opacity"] = f"{op:.3g}"
        if e.kind == "box" and e.asset:
            crop = self.image_crop(e)
            d["background-image"] = f"url('../images/{self.image_file(e.asset, e.w, e.h, crop)}')"
            d["background-size"] = "100% 100%" if crop else "cover"
            d["background-position"] = "center"
        if (e.kind in ("box", "section")) and n.get("clipsContent"):
            d["overflow"] = "hidden"
        return d

    def deco_rules(self, e: El) -> dict[str, dict[str, str]]:
        if not e.deco:
            return {}
        back = e.deco["node"] or {}
        d = {"content": "''", "position": "absolute", "inset": "0", "z-index": "-1", "pointer-events": "none"}
        d["transform"] = f"translate({px(e.deco['dx'])}, {px(e.deco['dy'])})"
        d.update(background(back.get("fills"), (e.w, e.h)))
        d.update(border(back))
        d.update(radius(back))
        return {"::before": d}

    def canonical(self, asset_id: str) -> str:
        """Identical files (same bytes) share one asset: the first id seen with that hash."""
        digest = self.asset_hashes.get(asset_id)
        if not digest:
            return asset_id
        return self._by_hash.setdefault(digest, asset_id)

    def image_file(self, asset_id: str, w: float, h: float, crop: tuple | None = None) -> str:
        """File name for an image use; `crop` = (x0, y0, x1, y1) window in 0..1 image coordinates."""
        asset_id = self.canonical(asset_id)
        meta = self.asset_meta.get(asset_id, {})
        if meta.get("format") == "SVG":
            crop = None
        stem = asset_id
        if crop:
            stem = f"{asset_id}-c{abs(hash(tuple(round(c, 4) for c in crop))) % 10**8:08d}"
        use = self.image_uses.setdefault(stem, {"asset": asset_id, "crop": crop, "w": 0.0, "h": 0.0})
        use["w"], use["h"] = max(use["w"], w), max(use["h"], h)
        if meta.get("format") == "SVG":
            return f"{stem}.svg"
        if meta.get("format") == "GIF" and not crop:
            return f"{stem}.gif"
        return f"{stem}.webp"

    @staticmethod
    def image_crop(e: El) -> tuple[float, float, float, float] | None:
        """Visible window of a CROP image fill (Figma's imageTransform maps the layer onto the image)."""
        for p in (e.node or {}).get("fills") or []:
            if p.get("type") != "IMAGE" or not p.get("visible", True) or p.get("scaleMode") != "CROP":
                continue
            t = p.get("imageTransform")
            try:
                (a, b, c), (d, e_, f) = t
            except (TypeError, ValueError):
                return None
            if abs(b) > 1e-3 or abs(d) > 1e-3 or a <= 0 or e_ <= 0:
                return None  # rotated crops are not supported; fall back to cover
            x0, y0, x1, y1 = c, f, c + a, f + e_
            if x0 <= 1e-3 and y0 <= 1e-3 and x1 >= 0.999 and y1 >= 0.999:
                return None
            return x0, y0, x1, y1
        return None

    # ------------------------------------------------------------------ layout
    def flow_css(
        self, parent: El, children: list[El], pad: tuple[float, float, float, float]
    ) -> tuple[dict, list[dict]]:
        """CSS for a flex/absolute parent and the per-child placement declarations."""
        mode = parent.layout.get("mode", "column")
        pt, pr, pb, pl = pad
        per_child: list[dict] = [{} for _ in children]
        if mode == "absolute":
            d = {"position": "relative", "width": px(parent.w), "height": px(parent.h)}
            for c, cd in zip(children, per_child, strict=True):
                cd.update({"position": "absolute", "left": px(c.x - parent.x), "top": px(c.y - parent.y)})
            return d, per_child

        col = mode == "column"
        d = {"display": "flex", "flex-direction": "column" if col else "row", "align-items": "flex-start"}
        order = sorted(range(len(children)), key=lambda i: children[i].y if col else children[i].x)
        main0 = (parent.y + pt) if col else (parent.x + pl)
        gaps = []
        prev_end = main0
        for i in order:
            c = children[i]
            start, end = (c.y, c.bottom) if col else (c.x, c.right)
            gaps.append(start - prev_end)
            prev_end = end
        inner = gaps[1:]
        uniform = len(inner) >= 1 and max(inner) - min(inner) <= GAP_TOL and inner[0] > GAP_TOL
        if uniform:
            d["gap"] = px(inner[0])
        for k, i in enumerate(order):
            g = gaps[k] if k == 0 or not uniform else 0
            if g > GAP_TOL:
                per_child[i]["margin-top" if col else "margin-left"] = px(g)
        # Cross-axis placement.
        cross0 = (parent.x + pl) if col else (parent.y + pt)
        cross1 = (parent.right - pr) if col else (parent.bottom - pb)
        for c, cd in zip(children, per_child, strict=True):
            a, b = (c.x, c.right) if col else (c.y, c.bottom)
            lead, trail = a - cross0, cross1 - b
            if abs(lead - trail) <= ALIGN_TOL and lead > 1:
                cd["align-self"] = "center"
            elif lead > GAP_TOL:
                cd["margin-left" if col else "margin-top"] = px(lead)
        d["__order__"] = order  # type: ignore[assignment]
        return d, per_child

    def padding(self, e: El, children: list[El]) -> tuple[float, float, float, float]:
        if not children or e.kind == "wrapper":
            return 0, 0, 0, 0
        bt, br, bb, bl = border_widths(e.node or {}) if e.kind == "box" else (0, 0, 0, 0)
        # Figma strokes sit inside the shape; CSS borders add to the box, so padding absorbs them.
        top = max(0.0, min(c.y for c in children) - e.y - bt)
        left = max(0.0, min(c.x for c in children) - e.x - bl)
        bottom = max(0.0, e.bottom - max(c.bottom for c in children) - bb)
        right = max(0.0, e.right - max(c.right for c in children) - br)
        return top, right, bottom, left

    # ------------------------------------------------------------------ render
    def render(self, e: El, placement: dict[str, str], depth: int) -> str:
        ind = "  " * depth
        css: dict[str, dict[str, str]] = {"": {}}
        main = css[""]
        attrs: list[str] = []
        inner = ""
        tag = e.tag or "div"
        role = e.role or e.kind

        if e.node is not None and e.kind not in ("wrapper", "container", "section"):
            attrs.append(f'data-figma-id="{html.escape(e.node["id"])}"')

        if e.kind == "text":
            tag = e.tag or "p"
            role = {"h1": "title", "h2": "title", "h3": "heading", "span": "label"}.get(tag, "text")
            main.update(self.text_css(e))
            inner = self.text_html(e, role)
            self.expect_text(e)
        elif e.kind == "svg" and self._backdrop_blur(e):
            # Frosted shape: a solid layer masked by the SVG, blurring what is behind it.
            role = "frost"
            f = self.image_file(e.asset, e.w, e.h)
            blur = self._backdrop_blur(e)
            mask_src = self._svg_data_uri(e.asset) or f"url('../images/{f}')"
            bx, by, bw, bh = self._draw_box(e)
            main.update(
                {
                    "width": px(bw),
                    "height": px(bh),
                    "background-color": "#ffffff",
                    "-webkit-mask": f"{mask_src} center / 100% 100% no-repeat",
                    "mask": f"{mask_src} center / 100% 100% no-repeat",
                    "-webkit-backdrop-filter": f"blur({px(blur / 2)})",
                    "backdrop-filter": f"blur({px(blur / 2)})",
                    "pointer-events": "none",
                }
            )
            fills = [p for p in (e.node or {}).get("fills", []) if p.get("type") == "SOLID" and p.get("visible", True)]
            if fills:
                main["background-color"] = background([{**fills[-1], "opacity": 1}]).get("background-color", "#ffffff")
            attrs.append('aria-hidden="true"')
            self._expect({"id": e.node["id"], "kind": "svg", "box": [bx, by, bw, bh], "asset": f})
        elif e.kind in ("image", "svg", "raster"):
            tag = "img"
            icon = e.kind == "svg" and max(e.w, e.h) <= ICON_MAX
            role = "icon" if icon else {"svg": "graphic", "raster": "illustration"}.get(e.kind, "image")
            crop = self.image_crop(e) if e.kind == "image" else None
            f = self.image_file(e.asset, e.w, e.h, crop)
            bx, by, bw, bh = self._draw_box(e)
            alt = "" if e.kind in ("svg", "raster") else html.escape(e.name)
            attrs += [f'src="images/{f}"', f'alt="{alt}"', f'width="{round(bw)}"', f'height="{round(bh)}"']
            if e.y > 900:
                attrs.append('loading="lazy"')
            main.update({"width": px(bw), "height": px(bh), "flex-shrink": "0"})
            if e.kind == "image":
                mode = next(
                    (p.get("scaleMode") for p in (e.node or {}).get("fills", []) if p.get("type") == "IMAGE"),
                    "FILL",
                )
                main["object-fit"] = "fill" if crop else {"FIT": "contain", "TILE": "none"}.get(mode, "cover")
            main.update(self.visual(e))
            self._expect({"id": e.node["id"], "kind": e.kind, "box": [bx, by, bw, bh], "asset": f})
        elif e.kind == "shape":
            main.update({"width": px(e.w), "height": px(e.h), "flex-shrink": "0"})
            main.update(self.visual(e))
            css.update(self.deco_rules(e))
            if e.deco:
                main.update({"position": "relative", "isolation": "isolate"})
            self.expect_box(e)
        else:  # section, container, box, wrapper
            if e.kind == "section":
                role = e.role or "section"
                tag = e.tag or "section"
                # No clipping: like Figma, layers may extend past the section and later sections paint over them.
                main.update({"position": "relative", "min-height": px(e.h)})
                main.update(self.visual(e))
                if e.overlay:
                    main["z-index"] = "2"
                attrs.append(f'data-section="{html.escape(e.role or "")}"')
                sec_id = f"section:{self.page.frame.get('rootId', 'page')}:{e.id}"
                attrs.append(f'data-figma-id="{html.escape(sec_id)}"')
                self._expect({"id": sec_id, "kind": "section", "box": [e.x, e.y, e.w, e.h]})
                if e.component:
                    attrs.append(f'data-component="{html.escape(e.component)}"')
            elif e.kind == "container":
                role = "container"
                left, right = e.x - 0, self.page.width - e.right
                if abs(left - right) <= 4:
                    main.update({"width": f"min(100% - 32px, {px(e.w)})", "margin": "0 auto"})
                else:
                    main.update({"width": px(e.w), "max-width": "100%", "margin-left": px(left)})
            elif e.kind == "box":
                role = e.role or "card"
                main.update({"width": px(e.w), "max-width": "100%", "flex-shrink": "0"})
                main.update(self.visual(e))
                css.update(self.deco_rules(e))
                if e.deco:
                    main.update({"position": "relative", "isolation": "isolate"})
                if e.role == "button":
                    attrs.append('href="#"')
                    main["text-decoration"] = "none"
                self.expect_box(e)
            else:
                role = "row" if e.layout.get("mode") == "row" else "stack"
                if e.layout.get("mode") == "absolute":
                    role = "layer-group"

            children = e.children
            box_css = main  # where padding and flow go: the element itself, or a section's stage
            stage_css: dict[str, str] | None = None
            if e.kind == "section":
                # The section spans the viewport (backgrounds stretch); its content sits on a stage of the
                # design's width centred in it, so nothing drifts off-centre on wider or narrower screens.
                W = self.page.width
                stage_css = {
                    "position": "relative",
                    "width": px(W),
                    "margin-left": f"calc(50% - {px(W / 2)})",
                    "min-height": px(e.h),
                }
                main["isolation"] = "isolate"
                box_css = stage_css
            if e.kind == "section" and len(children) == 1 and children[0].kind == "container":
                # Band section: one centred container carries the content; the section only pads it.
                pad = (children[0].y - e.y, 0, e.bottom - children[0].bottom, 0)
                pad = tuple(max(0.0, p) for p in pad)
                flow, per_child = {"display": "flex", "flex-direction": "column"}, [{} for _ in children]
                box_css["padding"] = f"{px(pad[0])} 0 {px(pad[2])}"
            else:  # boxes, wrappers and frame sections (Auto Layout or not) lay out their children directly
                pad = self.padding(e, children)
                if any(pad):
                    box_css["padding"] = " ".join(px(p) for p in pad)
                flow, per_child = self.flow_css(e, children, pad)
                flow.pop("__order__", None)
                if e.kind == "section":
                    flow.pop("width", None)
                    flow.pop("height", None)
            box_css.update(flow)
            if e.layout.get("mode") == "row":
                attrs.append('data-layout="row"')

            parts: list[str] = []
            stage_parts: list[str] = []
            cdepth = depth + 2 if stage_css is not None else depth + 1
            measurable = (e.kind == "box" and e.node is not None) or e.kind == "section"
            if measurable:
                self._anc.append(self.expect[-1]["id"] if e.kind == "section" else e.node["id"])
            if e.layers:
                if main.get("position", "static") == "static":
                    main["position"] = "relative"
                for layer in sorted(e.layers, key=lambda x: x.z):
                    full = e.kind == "section" and self._full_bleed(layer)
                    lp = {
                        "position": "absolute",
                        "left": "0" if full else px(layer.x - e.x),
                        "top": px(layer.y - e.y),
                    }
                    if full:
                        lp["width"] = "100%"
                        if layer.kind in ("svg", "raster"):
                            lp["object-fit"] = "cover"
                    if full or stage_css is None:
                        parts.append(self.render(layer, lp, depth + 1))
                    else:
                        stage_parts.append(self.render(layer, lp, cdepth))
            for c, cd in zip(children, per_child, strict=True):
                (stage_parts if stage_css is not None else parts).append(self.render(c, cd, cdepth))
            if measurable:
                self._anc.pop()
            if stage_css is not None:
                stage_cls = self.class_for("stage", {"": stage_css})
                sind = "  " * (depth + 1)
                stage = f'{sind}<div class="{stage_cls} l-stage">\n' + "\n".join(stage_parts) + f"\n{sind}</div>"
                parts = parts + [stage]
            inner = "\n" + "\n".join(parts) + "\n" + ind

        main.update(placement)
        if id(e) in self._stack:
            # Overlaps something outside its own box: paint exactly where Figma paints it.
            main["z-index"] = str(e.z + 1)
            if main.get("position", "static") == "static":
                main["position"] = "relative"
        if e.bleed:
            self._apply_bleed(e, main)
        if e.mask:
            main.update(self.mask_css(e))
        if e.kind in ("box", "shape"):
            self._extend_to_edge(e, main)
        cls = self.class_for(role, css)
        utility = (" l-row" if e.layout.get("mode") == "row" else "") + (" t-lock" if e.kind == "text" else "")
        extra = ' class="' + cls + utility + '"'
        a = (" " + " ".join(attrs)) if attrs else ""
        if tag == "img":
            return f"{ind}<img{extra}{a}>"
        return f"{ind}<{tag}{extra}{a}>{inner}</{tag}>"

    @staticmethod
    def stacking(sec: El) -> set[int]:
        """Elements that overlap another element outside their own ancestry.

        They get z-index = Figma paint order, so the browser stacks them exactly like Figma, wherever
        layout inference put them in the DOM. Wrappers never get a z-index, so they stay transparent
        to stacking; boxes that do get one keep their children on top of them, as in Figma.
        """
        painted: list[tuple[El, frozenset]] = []

        def visit(e: El, chain: frozenset) -> None:
            for c in e.layers + e.children:
                if c.kind in PAINT_KINDS:
                    painted.append((c, chain))
                visit(c, chain | {id(c)})

        visit(sec, frozenset())
        painted.sort(key=lambda t: t[0].x)
        need: set[int] = set()
        for i, (a, chain_a) in enumerate(painted):
            for b, chain_b in painted[i + 1 :]:
                if b.x >= a.right:
                    break
                if id(a) in chain_b or id(b) in chain_a or not a.intersects(b):
                    continue
                need.add(id(a))
                need.add(id(b))
        return need

    def _extend_to_edge(self, e: El, d: dict[str, str]) -> None:
        """A solid panel touching the frame's left or right edge keeps touching the screen edge on wider
        screens. Drawn as box-shadow copies: they never change layout or scroll width, and at the design
        width they sit outside the viewport."""
        W = self.page.width
        bg = d.get("background-color")
        left, right = e.x <= 1, e.right >= W - 1
        if not bg or (left and right) or not (left or right) or e.w < 40 or e.mask or "border-radius" in d:
            return
        reach = (EDGE_MAX_VIEWPORT - W) / 2
        n = max(1, math.ceil(reach / e.w))
        sign = -1 if left else 1
        copies = [f"{px(sign * e.w * (i + 1))} 0 0 0 {bg}" for i in range(n)]
        d["box-shadow"] = ", ".join(([d["box-shadow"]] if d.get("box-shadow") else []) + copies)

    def _full_bleed(self, e: El) -> bool:
        """A section layer spanning the whole frame width stretches with the viewport."""
        return e.x <= 1 and e.right >= self.page.width - 1 and not e.bleed

    def _image_data_uri(self, asset_id: str, w: float, h: float, limit: int = 400_000) -> str | None:
        """Small raster as a data URI (CSS masks refuse file:// images)."""
        from io import BytesIO

        from PIL import Image

        meta = self.asset_meta.get(asset_id, {})
        ext = {"PNG": "png", "JPG": "jpg", "WEBP": "webp", "GIF": "gif"}.get(meta.get("format", "PNG"), "png")
        src = self.asset_dir / f"{asset_id}.{ext}"
        if not src.exists():
            return None
        with Image.open(src) as im:
            tw = max(1, min(im.width, math.ceil(w * 2)))
            if tw < im.width:
                im = im.resize((tw, max(1, round(im.height * tw / im.width))), Image.LANCZOS)
            buf = BytesIO()
            im.convert("RGBA").save(buf, "PNG", optimize=True)
        data = buf.getvalue()
        if len(data) > limit:
            return None
        return "data:image/png;base64," + base64.b64encode(data).decode("ascii")

    def mask_css(self, e: El) -> dict[str, str]:
        """Figma mask → CSS: image masks become mask-image, shapes become clip-path."""
        m = e.mask or {}
        mx, my, mw, mh = m.get("box", (e.x, e.y, e.w, e.h))
        ox, oy, ow, oh = self._draw_box(e)
        dx, dy = mx - ox, my - oy
        if m.get("kind") == "image":
            uri = self._image_data_uri(m["asset"], mw, mh)
            src = f'url("{uri}")' if uri else f"url('../images/{self.image_file(m['asset'], mw, mh)}')"
            d = {
                "-webkit-mask-image": src,
                "mask-image": src,
                "-webkit-mask-size": f"{px(mw)} {px(mh)}",
                "mask-size": f"{px(mw)} {px(mh)}",
                "-webkit-mask-position": f"{px(dx)} {px(dy)}",
                "mask-position": f"{px(dx)} {px(dy)}",
                "-webkit-mask-repeat": "no-repeat",
                "mask-repeat": "no-repeat",
            }
            if m.get("maskType") == "LUMINANCE":
                d["mask-mode"] = "luminance"
            return d
        if m.get("kind") == "ellipse":
            return {"clip-path": f"ellipse({px(mw / 2)} {px(mh / 2)} at {px(dx + mw / 2)} {px(dy + mh / 2)})"}
        right, bottom = (ox + ow) - (mx + mw), (oy + oh) - (my + mh)
        rad = radius(m.get("node") or {}).get("border-radius")
        inset = f"inset({px(dy)} {px(right)} {px(bottom)} {px(dx)}" + (f" round {rad}" if rad else "") + ")"
        return {"clip-path": inset}

    def text_css(self, e: El) -> dict[str, str]:
        n = e.node or {}
        segs = n.get("textSegments") or [{}]
        base_seg = max(segs, key=lambda s: len(s.get("characters", "")))
        d = text_style(base_seg, n)
        align = n.get("textAlignHorizontal")
        if align in ("CENTER", "RIGHT", "JUSTIFIED"):
            d["text-align"] = {"CENTER": "center", "RIGHT": "right", "JUSTIFIED": "justify"}[align]
        d["margin"] = "0"
        resize = n.get("textAutoResize")
        # Blueprint lock: every text box keeps its Figma size on desktop, so a line that renders a
        # pixel taller (or a fallback font) cannot push the rest of the page. Released on mobile.
        d["width"] = px(e.w)
        d["height"] = px(e.h)
        d["max-width"] = "100%"
        d["white-space"] = "pre-wrap" if resize in ("HEIGHT", "NONE", "TRUNCATE") else "pre"
        d["flex-shrink"] = "0"
        d.update(self.visual(e))
        return d

    def text_html(self, e: El, role: str) -> str:
        n = e.node or {}
        segs = n.get("textSegments") or []
        if len(segs) <= 1:
            return html.escape(n.get("characters", ""))
        base_seg = max(segs, key=lambda s: len(s.get("characters", "")))
        base = text_style(base_seg, n)
        out = []
        for s in segs:
            st = text_style(s, n)
            diff = {k: v for k, v in st.items() if base.get(k) != v}
            for k, reset in RESETS.items():  # the base style would otherwise leak into this span
                if k in base and k not in st:
                    diff[k] = reset
            if ("font-weight" in diff or "font-style" in diff) and "font-family" in st:
                diff["font-family"] = st["font-family"]  # keeps font loading explicit per weight
            txt = html.escape(s.get("characters", ""))
            if diff:
                cls = self.class_for("span", {"": diff})
                out.append(f'<span class="{cls}">{txt}</span>')
            else:
                out.append(txt)
        return "".join(out)

    def expect_text(self, e: El) -> None:
        n = e.node or {}
        segs = n.get("textSegments") or [{}]
        base_seg = max(segs, key=lambda s: len(s.get("characters", "")))
        st = text_style(base_seg, n)
        lh = line_height_px({**n, **{k: v for k, v in base_seg.items() if k == "lineHeight"}})
        self._expect(
            {
                "id": n["id"],
                "kind": "text",
                "box": [e.x, e.y, e.w, e.h],
                "lineHeight": lh,
                "lines": round(e.h / lh) if lh else None,
                "style": {k: st[k] for k in ("font-family", "font-size", "font-weight", "color") if k in st},
            }
        )

    def expect_box(self, e: El) -> None:
        bg = background((e.node or {}).get("fills"), (e.w, e.h))
        self._expect(
            {
                "id": e.node["id"],
                "kind": e.kind,
                "box": [e.x, e.y, e.w, e.h],
                "background": bg.get("background-color"),
            }
        )

    # ------------------------------------------------------------------ assets
    def write_images(self, out_dir: Path) -> dict[str, Path]:
        from PIL import Image

        img_dir = out_dir / "images"
        img_dir.mkdir(parents=True, exist_ok=True)
        written = {}
        for stem, use in self.image_uses.items():
            asset_id, crop, w = use["asset"], use["crop"], use["w"]
            meta = self.asset_meta.get(asset_id, {})
            fmt = meta.get("format", "PNG")
            ext = {"PNG": "png", "JPG": "jpg", "SVG": "svg", "GIF": "gif", "WEBP": "webp"}.get(fmt, "bin")
            src = self.asset_dir / f"{asset_id}.{ext}"
            if not src.exists():
                self.warnings.append(f"Asset file missing: {src.name}")
                continue
            if fmt == "SVG" or (fmt == "GIF" and not crop):
                dst = img_dir / f"{stem}.{ext}"
                shutil.copyfile(src, dst)
            else:
                dst = img_dir / f"{stem}.webp"
                with Image.open(src) as im:
                    if crop:
                        x0, y0, x1, y1 = crop
                        box = (
                            max(0, round(x0 * im.width)),
                            max(0, round(y0 * im.height)),
                            min(im.width, round(x1 * im.width)),
                            min(im.height, round(y1 * im.height)),
                        )
                        if box[2] > box[0] and box[3] > box[1]:
                            im = im.crop(box)
                    target_w = min(im.width, MAX_IMAGE_EDGE, max(1, math.ceil(w * 2)))
                    if target_w < im.width:
                        im = im.resize((target_w, max(1, round(im.height * target_w / im.width))), Image.LANCZOS)
                    if im.mode not in ("RGB", "RGBA"):
                        im = im.convert("RGBA")
                    im.save(dst, "WEBP", quality=WEBP_QUALITY, method=4)
            written[stem] = dst
        return written

    def font_usage(self) -> dict[str, set[tuple[int, str]]]:
        """(weight, style) pairs used per family, read from the generated rules."""
        used: dict[str, set[tuple[int, str]]] = {}
        for parts in self.rules.values():
            d = parts.get("", {})
            fam = d.get("font-family", "").strip("'")
            if not fam:
                continue
            weight = int(d.get("font-weight", "400"))
            style = d.get("font-style", "normal")
            used.setdefault(fam, set()).add((weight, style))
        return used

    def write_fonts(self, out_dir: Path) -> tuple[list[dict], str, str | None]:
        """Self-hosts fonts found in font_dir; returns (fonts, @font-face css, google fonts href)."""
        used = self.font_usage()
        fonts, faces, google = [], [], []
        font_out = out_dir / "fonts"
        for family, variants in sorted(used.items()):
            available = fontfiles.find(self.font_dir, family)
            missing = []
            for weight, style in sorted(variants):
                f = fontfiles.pick(available, weight, style)
                if f:
                    fname = f"{slug(family, 'font')}-{f.weight if not f.variable else 'var'}-{f.style}{f.path.suffix}"
                    font_out.mkdir(parents=True, exist_ok=True)
                    if not (font_out / fname).exists():
                        shutil.copyfile(f.path, font_out / fname)
                    face_weight = "100 900" if f.variable else str(weight)
                    faces.append(
                        f"@font-face {{\n  font-family: '{family}';\n  font-style: {style};\n"
                        f"  font-weight: {face_weight};\n  font-display: swap;\n"
                        f"  src: url('../fonts/{fname}') format('{f.fmt}');\n}}"
                    )
                    note = "self-hosted" if (f.variable or f.weight == weight) else f"self-hosted (nearest: {f.weight})"
                    fonts.append({"family": family, "weight": weight, "style": style, "source": note})
                    if not f.variable and f.weight != weight:
                        self.warnings.append(f"{family} {weight}: no exact file, using weight {f.weight}.")
                else:
                    missing.append((weight, style))
                    fonts.append({"family": family, "weight": weight, "style": style, "source": "google-fonts"})
            if missing:
                ital = any(s == "italic" for _, s in missing)
                if ital:
                    axis = ";".join(
                        f"{1 if s == 'italic' else 0},{w}"
                        for w, s in sorted(missing, key=lambda v: (v[1] == "italic", v[0]))
                    )
                    google.append(f"family={family.replace(' ', '+')}:ital,wght@{axis}")
                else:
                    google.append(
                        f"family={family.replace(' ', '+')}:wght@{';'.join(str(w) for w, _ in sorted(missing))}"
                    )
        href = f"https://fonts.googleapis.com/css2?{'&'.join(google)}&display=swap" if google else None
        if href:
            self.assumptions.append("Some fonts load from Google Fonts (no local font file was found).")
        return fonts, "\n\n".join(faces), href

    # ------------------------------------------------------------------ tokens + css text
    def css_text(self) -> tuple[str, str]:
        blocks = []
        for cls, parts in self.rules.items():
            for suffix, decls in parts.items():
                if not decls:
                    continue
                body = "\n".join(f"  {k}: {v};" for k, v in decls.items())
                blocks.append(f".{cls}{suffix} {{\n{body}\n}}")
        text = "\n\n".join(blocks)
        # Colour tokens: colours used 3+ times become custom properties.
        colors = Counter(re.findall(r"#[0-9a-f]{6}\b|rgba\([^)]*\)", text))
        tokens = {}
        for c, n in colors.most_common():
            if n < 3:
                break
            tokens[c] = f"--color-{len(tokens) + 1}"
        for c, var in tokens.items():
            text = text.replace(c, f"var({var})")
        families = sorted(set(re.findall(r"font-family: '([^']+)'", text)))
        for fam in families:
            text = text.replace(f"font-family: '{fam}'", f"font-family: var(--font-{slug(fam, 'font')})")
        root_lines = [f"  {v}: {c};" for c, v in tokens.items()]
        root_lines += [f"  --font-{slug(f, 'font')}: '{f}', system-ui, sans-serif;" for f in families]
        root = ":root {\n" + "\n".join(root_lines) + "\n}" if root_lines else ""
        return root, text

    # ------------------------------------------------------------------ main
    def add_page(self, page: Page, filename: str) -> dict:
        """Renders one page into the shared class registry. Call finish() after the last page."""
        self.page = page
        self.expect = []
        self.warnings.extend(w for w in page.warnings if w not in self.warnings)
        self.assign_roles()
        body_parts = []
        prev_bottom = 0.0
        self._stack = set()
        for sec in page.sections:
            self._stack |= self.stacking(sec)
        for sec in page.sections:
            self.section_slug = sec.role or "section"
            # Sections stack in normal flow: keep any gap (or overlap) the design has between them.
            gap = sec.y - prev_bottom
            placement = {"margin-top": px(gap)} if abs(gap) > 0.5 else {}
            prev_bottom = sec.bottom
            html_part = self.render(sec, placement, 2)
            if sec.component and sec.component not in self.components:
                self.components[sec.component] = html_part
            body_parts.append(html_part)
        self.fit_width = max(self.fit_width, self.content_width(page))
        return {
            "name": page.name,
            "file": filename,
            "frame": page.frame,
            "title": page.root.name,
            "body": "\n".join(body_parts),
            "expectations": self.expect,
            "width": page.width,
            "height": page.height,
            "background": background(page.background).get("background-color", "#ffffff"),
        }

    @staticmethod
    def content_width(page: Page, gutter: float = FIT_GUTTER) -> float:
        """Narrowest centred viewport that shows every piece of content (text, images, icons, buttons).

        Decorative shapes and backgrounds may run to the frame edges; they are allowed to be cut off.
        """
        W = page.width
        left, right = W / 2, W / 2
        for sec in page.sections:
            for e in sec.walk():
                content = e.kind in ("text", "image", "svg") or (e.kind == "box" and e.role == "button")
                if not content or e.w >= W * 0.95:
                    continue
                left, right = min(left, e.x), max(right, e.right)
        half = max(W / 2 - left, right - W / 2)
        return min(W, 2 * half + 2 * gutter)

    def fit_css(self, design_width: float) -> str:
        """Stepped zoom between the mobile breakpoint and the content width: nothing is ever cut off."""
        fit = math.ceil(self.fit_width or design_width)
        if fit <= MOBILE_MAX + 1:
            return ""
        self.assumptions.append(
            "Content stays centred on any screen; between 768px and "
            f"{fit}px the layout scales down so nothing is cut off."
        )
        lines = [f"/* Below {fit}px the layout scales down (like the browser's zoom) so all content stays visible. */"]
        top = fit
        while top > MOBILE_MAX + 1:
            low = max(MOBILE_MAX + 1, top - FIT_STEP)
            query = f"(min-width: {low}px) and (max-width: {top - 0.02:.2f}px)"
            lines.append(f"@media {query} {{ main {{ zoom: {low / fit:.4f}; }} }}")
            top = low
        return "\n".join(lines)

    def finish(self, out_dir: Path, pages: list[dict]) -> Output:
        root_css, rules_css = self.css_text()
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "css").mkdir(exist_ok=True)
        images = self.write_images(out_dir)
        fonts, faces, google_href = self.write_fonts(out_dir)
        page_bg = pages[0]["background"] if pages else "#ffffff"
        base = (
            "*, *::before, *::after { box-sizing: border-box; }\n"
            f"html, body {{ margin: 0; padding: 0; background: {page_bg}; }}\n"
            "body { -webkit-font-smoothing: antialiased; text-rendering: optimizeLegibility; }\n"
            "img { display: block; max-width: 100%; }\n"
            "a { color: inherit; }\n"
            "main { overflow-x: clip; }"
        )
        responsive = (
            "/* Inferred mobile layout: rows stack below 768px (no mobile frame was supplied). */\n"
            "@media (max-width: 767px) {\n"
            "  .l-stage { width: auto; margin-left: 0; }\n"
            "  .l-row { flex-direction: column; flex-wrap: wrap; align-items: center; }\n"
            "  .l-row > * { margin-left: 0; }\n"
            "  .t-lock { height: auto; white-space: pre-wrap; }\n"
            "  img { height: auto; }\n"
            "}"
        )
        if not any(f["role"] == "mobile" for f in self.bundle["frames"]):
            self.assumptions.append("No mobile frame: a simple stacked mobile layout was inferred below 768px.")
        fit = self.fit_css(pages[0]["width"] if pages else 1440)
        css = "\n\n".join(p for p in [faces, root_css, base, rules_css, fit, responsive] if p) + "\n"
        site = html.escape(self.bundle["source"].get("fileName", "Site"))
        font_link = f'  <link rel="stylesheet" href="{html.escape(google_href)}">\n' if google_href else ""
        corrections = out_dir / "css" / "corrections.css"
        if not corrections.exists():
            corrections.write_text("/* Fidelity corrections are written here after measuring the page. */\n")
        docs = []
        for p in pages:
            bg = f' style="background: {p["background"]}"' if p["background"] != page_bg else ""
            doc = (
                '<!doctype html>\n<html lang="en">\n<head>\n  <meta charset="utf-8">\n'
                '  <meta name="viewport" content="width=device-width, initial-scale=1">\n'
                f"  <title>{html.escape(p['name'])} · {site}</title>\n{font_link}"
                '  <link rel="stylesheet" href="css/styles.css">\n'
                '  <link rel="stylesheet" href="css/corrections.css">\n'
                f"</head>\n<body{bg}>\n  <main>\n{p['body']}\n  </main>\n</body>\n</html>\n"
            )
            (out_dir / p["file"]).write_text(doc, encoding="utf-8")
            docs.append(doc)
        if self.components:
            comp_dir = out_dir / "components"
            comp_dir.mkdir(exist_ok=True)
            for name, fragment in self.components.items():
                (comp_dir / f"{name}.html").write_text(textwrap.dedent(fragment).strip() + "\n", encoding="utf-8")
        (out_dir / "css" / "styles.css").write_text(css, encoding="utf-8")
        out = Output(docs[0] if docs else "", css, images, fonts, [], self.assumptions, self.warnings)
        out.pages = pages
        out.components = sorted(self.components)
        return out

    def run(self, out_dir: Path) -> Output:
        """Single page (kept for callers that convert one frame)."""
        result = self.add_page(self.page, "index.html")
        out = self.finish(out_dir, [result])
        out.expectations = result["expectations"]
        return out


def generate(page: Page, bundle: dict, asset_dir: Path, out_dir: Path, font_dir: Path | None = None) -> Output:
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out = Generator(page, bundle, asset_dir, font_dir).run(out_dir)
    meta = {
        "expectations": out.expectations,
        "fonts": out.fonts,
        "assumptions": out.assumptions,
        "warnings": out.warnings,
    }
    (out_dir.parent / "generation.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return out


def generate_site(
    pages: list[tuple[Page, str]],
    bundle: dict,
    asset_dir: Path,
    out_dir: Path,
    font_dir: Path | None = None,
    asset_hashes: dict[str, str] | None = None,
) -> Output:
    """Several pages sharing one stylesheet, images, fonts and component classes."""
    if out_dir.exists():
        shutil.rmtree(out_dir)
    gen = Generator(None, bundle, asset_dir, font_dir)
    gen.asset_hashes = asset_hashes or {}
    results = [gen.add_page(page, filename) for page, filename in pages]
    out = gen.finish(out_dir, results)
    meta = {
        "pages": [{k: v for k, v in r.items() if k not in ("body",)} for r in results],
        "components": out.components,
        "fonts": out.fonts,
        "assumptions": out.assumptions,
        "warnings": out.warnings,
    }
    (out_dir.parent / "generation.json").write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")
    return out
