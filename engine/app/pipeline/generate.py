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
CTA_WORDS = re.compile(r"\b(contact|help|touch|book|call|quote|talk|hire|start|enquire|inquire|message|demo)\b")
# Where an element sits (set by its parent), as opposed to how it looks.
PLACEMENT_KEYS = {"margin-top", "margin-left", "margin-inline", "align-self", "z-index", "position", "top", "left"}
EDGE_MAX_VIEWPORT = 3840  # edge panels reach the screen edge up to this width
MOBILE_MAX = 767  # inferred mobile layout below 768px
FIT_GUTTER = 24.0  # breathing room left and right of the content when composition sections start scaling
GUTTER = 24.0  # side padding of the content column on small screens
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


def _text(chars: str) -> str:
    """Escaped text; Figma line breaks become <br> (no white-space: pre needed)."""
    return "<br>".join(html.escape(part) for part in chars.split("\n"))


def _sort_key(v: str) -> float:
    m = re.match(r"([\d.]+)px", v)
    return float(m.group(1)) if m else 1e9


def _norm(text: str) -> str:
    """Label comparison key: 'About Us', 'ABOUT US' and 'About us →' all become 'about us'."""
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


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
        self._bg: set[int] = set()  # background layers: z-index -1 inside their isolated parent
        self._levels: dict[int, int] = {}  # element id -> smallest z-index keeping Figma's order
        self._fit_sections = False  # some sections keep a design-width stage (scaled on narrow screens)
        self._anchors: dict[str, str] = {}  # normalised label -> section id, for nav links and buttons
        self._alt_context: dict[int, str] = {}  # image id -> heading text of its card/section
        self._component: list[str] = []  # global component being rendered (its parts get btn__* classes)
        self.grid_columns: set[int] = set()
        self.page_links: dict[str, str] = {}  # normalised page name -> file (multi-page navigation)
        self.body_style: dict[str, str] | None = None  # base text style, written once on <body>
        self.container_width = 0.0
        self.design_width = 0.0
        self.expect: list[dict] = []
        self.assumptions: list[str] = []
        self.warnings: list[str] = list(page.warnings) if page else []
        self.section_slug = "page"
        self._anc: list[str] = []  # figma ids of measurable ancestors while rendering
        self._ctx: list[str] = ["absolute"]  # how the current parent lays out its children
        self._parent_span: list[tuple[float, float] | None] = []  # parent content box (x0, x1) per level
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
    def class_for(self, role: str, css: dict[str, dict[str, str]], component: str | None = None) -> str:
        """Class for a set of declarations. Identical declarations share a class.

        Section parts are named block__element (hero__title). Components (buttons) are global: .btn,
        .btn__label, shared by every section and page.
        """
        comp = component or (self._component[-1] if self._component else None)
        scope = f"@{comp}" if comp else self.section_slug
        sig = (scope, role, tuple(sorted((k, tuple(sorted(v.items()))) for k, v in css.items())))
        if sig in self._class_by_sig:
            return self._class_by_sig[sig]
        if comp:
            base = comp if role == comp else f"{comp}__{role}"
        else:
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
        """CSS for a flex/grid/absolute parent and the per-child placement declarations.

        The returned dict carries "__ctx__" (column, row, grid or absolute): how children are sized.
        """
        mode = parent.layout.get("mode", "column")
        pt, pr, pb, pl = pad
        per_child: list[dict] = [{} for _ in children]
        if mode == "absolute":
            d = {"position": "relative", "width": px(parent.w), "height": px(parent.h), "__ctx__": "absolute"}
            if parent.kind == "wrapper" and self._ctx[-1] != "absolute":
                d["max-width"] = "100%"
            for c, cd in zip(children, per_child, strict=True):
                cd.update({"position": "absolute", "left": px(c.x - parent.x), "top": px(c.y - parent.y)})
            return d, per_child

        col = mode == "column"
        grid = not col and self.is_grid(parent)
        d: dict = {"display": "flex", "flex-direction": "column", "align-items": "flex-start"} if col else {
            "display": "flex"}  # fmt: skip
        if not col and not grid:
            d["align-items"] = "flex-start"
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
        cross0 = (parent.x + pl) if col else (parent.y + pt)
        cross1 = (parent.right - pr) if col else (parent.bottom - pb)
        if grid:
            first, last = children[order[0]], children[order[-1]]
            # Columns never grow past the designed card width, and shrink together on smaller screens.
            col_w = max(c.w for c in children)
            d = {
                "display": "grid",
                "grid-template-columns": f"repeat({len(children)}, minmax(0, {px(col_w)}))",
                "align-items": "start",
            }
            if inner and inner[0] > GAP_TOL:
                d["column-gap"] = px(inner[0])
            self.grid_columns.add(len(children))
            for c, cd in zip(children, per_child, strict=True):
                if c.y - cross0 > GAP_TOL:
                    cd["margin-top"] = px(c.y - cross0)
            d["__order__"] = order  # type: ignore[assignment]
            d["__ctx__"] = "grid"
            if gaps[0] > GAP_TOL:
                per_child[order[0]]["margin-left"] = px(gaps[0])
            del first, last
            return d, per_child
        spread = (
            not col
            and len(children) == 2
            and gaps[0] <= GAP_TOL
            and abs((parent.right - pr) - children[order[-1]].right) <= GAP_TOL
            and inner[0] > (parent.w - pl - pr) * 0.1
        )
        if spread:
            # Two items pinned to both ends (logo + menu, text + image): keep them there at any width.
            d["justify-content"] = "space-between"
            d["column-gap"] = px(min(inner[0], 32))
        elif uniform:
            d["gap"] = px(inner[0])
        end_pinned = (
            not col and not spread and len(order) >= 2 and abs((parent.right - pr) - children[order[-1]].right) <= 2
        )
        for k, i in enumerate(order):
            if spread:
                break
            g = gaps[k] if k == 0 or not uniform else 0
            if end_pinned and k == len(order) - 1 and gaps[k] > 48:
                per_child[i]["margin-left"] = "auto"  # stays on the right edge at any width
                if uniform:
                    per_child[i]["margin-left"] = "auto"
                continue
            if g > GAP_TOL:
                per_child[i]["margin-top" if col else "margin-left"] = px(g)
        # Cross-axis placement.
        for c, cd in zip(children, per_child, strict=True):
            a, b = (c.x, c.right) if col else (c.y, c.bottom)
            lead, trail = a - cross0, cross1 - b
            left_text = c.kind == "text" and not self._hug(c) and (c.node or {}).get("textAlignHorizontal", "LEFT") in (
                "LEFT", "JUSTIFIED")  # fmt: skip
            if col and left_text and lead > GAP_TOL:
                cd["margin-left"] = px(lead)  # a left-aligned text box starts where its box starts
            elif abs(lead - trail) <= ALIGN_TOL and lead > 1:
                cd["align-self"] = "center"
                if col and c.kind in ("image", "box", "shape") or (col and self.is_grid(c)):
                    cd["margin-inline"] = "auto"  # fluid width (max-width) still centred
                    cd.pop("align-self")
            elif col and trail <= GAP_TOL and lead > GAP_TOL:
                cd["align-self"] = "flex-end"
            elif lead > GAP_TOL:
                cd["margin-left" if col else "margin-top"] = px(lead)
        d["__order__"] = order  # type: ignore[assignment]
        d["__ctx__"] = "column" if col else "row"
        return d, per_child

    def padding(self, e: El, children: list[El]) -> tuple[float, float, float, float]:
        if not children or e.kind == "wrapper" or (e.kind == "container" and e.layout.get("page_container")):
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
            role = {"h1": "title", "h2": "title", "h3": "heading", "span": "label", "a": "link"}.get(tag, "text")
            if tag == "a":
                attrs.append(f'href="{html.escape(self.link_for(e))}"')
            if e.html_id:
                attrs.insert(0, f'id="{html.escape(e.html_id)}"')
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
                    "--shape": mask_src,
                    "-webkit-mask": "var(--shape) center / 100% 100% no-repeat",
                    "mask": "var(--shape) center / 100% 100% no-repeat",
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
            alt = "" if e.kind in ("svg", "raster") else html.escape(self.alt_text(e))
            attrs += [f'src="images/{f}"', f'alt="{alt}"', f'width="{round(bw)}"', f'height="{round(bh)}"']
            if e.y > 900:
                attrs.append('loading="lazy"')
            main.update(self.size_css(e, icon=icon))
            if e.kind == "image":
                mode = next(
                    (p.get("scaleMode") for p in (e.node or {}).get("fills", []) if p.get("type") == "IMAGE"),
                    "FILL",
                )
                main["object-fit"] = "fill" if crop else {"FIT": "contain", "TILE": "none"}.get(mode, "cover")
            main.update(self.visual(e))
            self._expect({"id": e.node["id"], "kind": e.kind, "box": [bx, by, bw, bh], "asset": f})
        elif e.kind == "shape":
            main.update(self.size_css(e))
            main.update(self.visual(e))
            css.update(self.deco_rules(e))
            if e.deco:
                main.update({"position": "relative", "isolation": "isolate"})
            self.expect_box(e)
        else:  # section, container, box, wrapper
            stage = e.kind == "section" and e.layout.get("stage")
            if e.kind == "section":
                role = e.role or "section"
                tag = e.tag or "section"
                # No clipping: like Figma, layers may extend past the section and later sections paint over them.
                main.update({"position": "relative", "isolation": "isolate"})
                main.update(self.visual(e))
                if e.overlay:
                    main["z-index"] = "2"
                if e.html_id:
                    attrs.append(f'id="{html.escape(e.html_id)}"')
                attrs.append(f'data-section="{html.escape(e.role or "")}"')
                sec_id = f"section:{self.page.frame.get('rootId', 'page')}:{e.id}"
                attrs.append(f'data-figma-id="{html.escape(sec_id)}"')
                self._expect({"id": sec_id, "kind": "section", "box": [e.x, e.y, e.w, e.h]})
                if e.component:
                    attrs.append(f'data-component="{html.escape(e.component)}"')
            elif e.kind == "container":
                role = "container"
                if not e.layout.get("page_container"):
                    left, right = e.x - 0, self.page.width - e.right
                    if abs(left - right) <= 4:
                        main.update({"width": f"min(100% - 2 * var(--gutter), {px(e.w)})", "margin-inline": "auto"})
                    else:
                        main.update({"width": px(e.w), "max-width": "100%", "margin-left": px(left)})
            elif e.kind == "box":
                role = e.role or "card"
                main.update(self.button_css(e) if e.role == "button" else self.size_css(e))
                main.update(self.visual(e))
                css.update(self.deco_rules(e))
                if e.deco:
                    main.update({"position": "relative", "isolation": "isolate"})
                if e.role == "button":
                    attrs.append(f'href="{html.escape(self.link_for(e))}"')
                    main["text-decoration"] = "none"
                self.expect_box(e)
            else:
                role = "row" if e.layout.get("mode") == "row" else "stack"
                if e.layout.get("mode") == "absolute":
                    role = "layer-group"
                main.update(self.size_css(e))
                if e.tag == "header":
                    role = "header"

            children = e.children
            box_css = main  # where padding and flow go: the element itself, or a section's stage
            stage_css: dict[str, str] | None = None
            if stage:
                # Overlapping full-width composition: its content sits on a stage of the design's width,
                # centred in the section (scaled down on narrow screens, see fit_css).
                W = self.page.width
                stage_css = {
                    "position": "relative",
                    "width": px(W),
                    "margin-left": f"calc(50% - {px(W / 2)})",
                    "min-height": px(e.h),
                }
                main["min-height"] = px(e.h)
                box_css = stage_css
                self._fit_sections = True
            if e.kind == "section" and len(children) == 1 and children[0].kind == "container":
                # Band section: one container carries the content; the section only pads it.
                pad = (children[0].y - e.y, 0, e.bottom - children[0].bottom, 0)
                pad = tuple(max(0.0, p) for p in pad)
                flow, per_child = {}, [{} for _ in children]
                if any(pad):
                    box_css["padding-block"] = f"{px(pad[0])} {px(pad[2])}" if pad[0] != pad[2] else px(pad[0])
                child_ctx = "column"
            elif e.role == "button" and len(children) == 1:
                pad = (0, 0, 0, 0)
                flow, per_child = {}, [{}]  # button_css centres the label
                child_ctx = "row"
            else:  # boxes, wrappers and frame sections (Auto Layout or not) lay out their children directly
                pad = self.padding(e, children)
                if any(pad):
                    box_css["padding"] = self.padding_value(e, pad)
                flow, per_child = self.flow_css(e, children, pad)
                flow.pop("__order__", None)
                if e.kind == "section":
                    flow.pop("width", None)
                    flow.pop("height", None)
                    if not stage:
                        main["min-height"] = px(e.h)
                child_ctx = flow.pop("__ctx__", "column")
            box_css.update(flow)
            if e.layout.get("mode") == "row" and child_ctx == "row":
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
                if any(id(x) in self._bg for x in e.layers):
                    main["isolation"] = "isolate"  # background layers (z-index -1) stay inside this box
                self._ctx.append("layer")
                for layer in sorted(e.layers, key=lambda x: x.z):
                    if e.kind == "section" and not stage:
                        lp = self.anchored_css(layer, e)
                        parts.append(self.render(layer, lp, depth + 1))
                        continue
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
                self._ctx.pop()
            self._ctx.append(child_ctx)
            if e.role == "button":
                self._component.append("btn")
            if e.kind == "section" and len(children) == 1 and children[0].kind == "container":
                self._parent_span.append((children[0].x, children[0].right))
            else:
                self._parent_span.append((e.x + pad[3], e.right - pad[1]))
            for c, cd in zip(children, per_child, strict=True):
                (stage_parts if stage_css is not None else parts).append(self.render(c, cd, cdepth))
            self._parent_span.pop()
            self._ctx.pop()
            if e.role == "button":
                self._component.pop()
            if measurable:
                self._anc.pop()
            if e.tag == "nav":
                # Placement (margins from the parent) stays on <nav>; the flex row goes on the list.
                list_cls = self.class_for("menu", css)
                nav_css = {k: v for k, v in placement.items() if not k.startswith("__")}
                nav_cls = self.class_for("nav", {"": nav_css}) if nav_css else ""
                nav_attr = f' class="{nav_cls}"' if nav_cls else ""
                items = "\n".join(f"{ind}    <li>{part.strip()}</li>" for part in parts)
                return (
                    f'{ind}<nav{nav_attr} aria-label="Main">\n{ind}  <ul class="{list_cls} l-row" role="list">\n'
                    f"{items}\n{ind}  </ul>\n{ind}</nav>"
                )
            if stage_css is not None:
                stage_cls = self.class_for("stage", {"": stage_css})
                sind = "  " * (depth + 1)
                stage_html = f'{sind}<div class="{stage_cls} l-stage">\n' + "\n".join(stage_parts) + f"\n{sind}</div>"
                parts = parts + [stage_html]
                attrs.append('data-fit=""')
            inner = "\n" + "\n".join(parts) + "\n" + ind
            if e.kind == "container" and e.layout.get("page_container"):
                cls_extra = self.class_for("content", css) if any(main.values()) else ""
                a = (" " + " ".join(attrs)) if attrs else ""
                klass = "container" + (f" {cls_extra}" if cls_extra else "")
                if e.layout.get("mode") == "row" and main.get("display") != "grid":
                    klass += " l-row"
                return f'{ind}<div class="{klass}"{a}>{inner}</div>'

        bleed_done = placement.pop("__bleed_done__", None) if "__bleed_done__" in placement else None
        main.update(placement)
        if id(e) in self._bg:
            main["z-index"] = "-1"
        elif id(e) in self._stack:
            # Overlaps something outside its own box: paint exactly where Figma paints it.
            main["z-index"] = str(self._levels.get(id(e), 1))
            if main.get("position", "static") == "static":
                main["position"] = "relative"
        if e.bleed and not bleed_done:
            self._apply_bleed(e, main)
        if e.mask:
            main.update(self.mask_css(e))
        if e.kind in ("box", "shape"):
            self._extend_to_edge(e, main)
        if e.role == "button" and e.kind == "box":
            place = {k: main.pop(k) for k in list(main) if k in PLACEMENT_KEYS}
            if e.deco and "position" not in main:
                main["position"] = "relative"
            cls = self.class_for("btn", css, component="btn")
            if place:
                cls += " " + self.class_for("action", {"": place})
        else:
            cls = self.class_for(role, css)
        utility = " l-row" if e.layout.get("mode") == "row" and "grid" not in main.get("display", "") else ""
        if main.get("display") == "grid" and main.get("grid-template-columns", "").startswith("repeat("):
            utility += f" l-grid-{main['grid-template-columns'][7:].split(',')[0]}"
        extra = ' class="' + cls + utility + '"'
        a = (" " + " ".join(attrs)) if attrs else ""
        if tag == "img":
            return f"{ind}<img{extra}{a}>"
        return f"{ind}<{tag}{extra}{a}>{inner}</{tag}>"

    # ------------------------------------------------------------------ semantics
    def semantics(self, page: Page) -> None:
        """Section ids (anchor targets), nav menus, the site header and link targets."""
        used: set[str] = set()
        self._anchors = {}
        for sec in page.sections:
            base = slug(sec.role or "section")
            hid, n = base, 2
            while hid in used:
                hid, n = f"{base}-{n}", n + 1
            used.add(hid)
            sec.html_id = hid
            self._anchors.setdefault(_norm(hid.replace("-", " ")), f"#{hid}")
            heading = next((t for t in _texts(sec) if t.tag in ("h1", "h2")), None)
            if heading is not None:
                self._anchors.setdefault(_norm(heading.node.get("characters", "")), f"#{hid}")
            # Further headings inside a section (a "Contact us" block in the same band) are targets too.
            for t in _texts(sec):
                if t is heading or t.tag not in ("h2", "h3"):
                    continue
                key = _norm(t.node.get("characters", ""))
                if not key or key in self._anchors or len(key.split()) > 4:
                    continue
                tid, n = slug(key), 2
                while tid in used:
                    tid, n = f"{slug(key)}-{n}", n + 1
                used.add(tid)
                t.html_id = tid
                self._anchors[key] = f"#{tid}"
        if page.sections:
            self._anchors.setdefault("home", f"#{page.sections[0].html_id}")
            self._anchors.setdefault("top", f"#{page.sections[0].html_id}")

        for sec in page.sections:
            for box in sec.walk():
                imgs = [c for c in box.children if c.kind == "image"]
                texts = [t for t in _texts(box) if t.node.get("characters", "").strip()]
                if len(imgs) == 1 and texts and box.kind in ("box", "wrapper"):
                    best = max(texts, key=lambda t: (_font_size(t), -t.y))
                    self._alt_context.setdefault(id(imgs[0]), best.node.get("characters", "").split("\n")[0][:80])

        def visit(e: El, parent: El | None, sec: El) -> None:
            if self.is_nav(e):
                e.tag = "nav"
                for t in e.children:
                    t.tag, t.role = "a", "link"
                if sec is page.sections[0] and sec.h <= 160 and sec.tag in (None, "section"):
                    sec.tag = "header"  # a thin top band holding the menu is the site header
                elif parent is not None and parent.kind == "wrapper" and parent.y < 240 and parent.tag is None:
                    parent.tag = "header"
            for c in e.children:
                visit(c, e, sec)

        for sec in page.sections[:2]:
            visit(sec, None, sec)
        self.heading_order(page)

    @staticmethod
    def heading_order(page: Page) -> None:
        """Headings in document order never skip a level, and nothing before the h1 is a heading
        (a logo or tagline above the main title is a paragraph)."""

        def order(e: El):
            for c in e.layers + e.children:
                if c.kind == "text":
                    yield c
                yield from order(c)

        texts = [t for sec in page.sections for t in order(sec)]
        heads = [t for t in texts if t.tag in ("h1", "h2", "h3")]
        first_h1 = next((i for i, t in enumerate(heads) if t.tag == "h1"), None)
        prev = 0
        for i, t in enumerate(heads):
            if first_h1 is not None and i < first_h1:
                t.tag = None  # rendered as <p>
                continue
            level = int(t.tag[1])
            if level > prev + 1:
                level = prev + 1
                t.tag = f"h{level}"
            prev = level

    @staticmethod
    def is_nav(e: El) -> bool:
        """A row of 2–8 short one-line labels in the same style: a navigation menu."""
        if e.kind != "wrapper" or e.layout.get("mode") != "row" or not 2 <= len(e.children) <= 8:
            return False
        if not all(c.kind == "text" for c in e.children):
            return False
        sizes = set()
        for c in e.children:
            chars = c.node.get("characters", "").strip()
            if not chars or "\n" in chars or len(chars.split()) > 3 or len(chars) > 24:
                return False
            sizes.add(round(_font_size(c)))
        return len(sizes) == 1 and max(sizes) <= 24

    def base_text_style(self, page: Page) -> dict[str, str]:
        """The most common paragraph style becomes the body style; text only declares what differs."""
        counts: Counter = Counter()
        for sec in page.sections:
            for t in _texts(sec):
                if t.tag in ("h1", "h2", "h3"):
                    continue
                n = t.node or {}
                segs = n.get("textSegments") or [{}]
                st = text_style(max(segs, key=lambda s: len(s.get("characters", ""))), n)
                key = tuple((k, st[k]) for k in ("font-family", "font-size", "line-height", "color") if k in st)
                counts[key] += len(n.get("characters", ""))
        if not counts:
            return {}
        return dict(counts.most_common(1)[0][0])

    # ------------------------------------------------------------------ fluid sizing
    def size_css(self, e: El, icon: bool = False) -> dict[str, str]:
        """Width/height for an element, written the way a front-end developer would.

        Flow content is fluid (fills its column up to the design size and keeps its aspect ratio);
        only icons, decorations and absolutely positioned pieces keep fixed pixel sizes.
        """
        ctx = self._ctx[-1]
        bx, by, bw, bh = self._draw_box(e)
        fixed = ctx in ("absolute", "layer") or e.bleed or icon
        if e.kind in ("image", "svg", "raster"):
            if fixed:
                return {"width": px(bw), "height": px(bh), "flex-shrink": "0"} if ctx == "row" or icon else {
                    "width": px(bw), "height": px(bh)}  # fmt: skip
            ratio = f"{round(bw)} / {round(bh)}"
            if ctx == "row":
                return {"flex": f"0 1 {px(bw)}", "width": "100%", "max-width": px(bw), "min-width": "0",
                        "height": "auto", "aspect-ratio": ratio}  # fmt: skip
            if ctx == "grid":
                return {"width": "100%", "height": "auto", "aspect-ratio": ratio}
            return {"width": "100%", "max-width": px(bw), "height": "auto", "aspect-ratio": ratio}
        if e.kind == "shape":
            if fixed or ctx == "row":
                return {"width": px(e.w), "height": px(e.h), "flex-shrink": "0"}
            if ctx == "grid":
                return {"height": px(e.h)}
            return {"width": "100%", "max-width": px(e.w), "height": px(e.h)}
        if e.kind == "box":
            if e.layout.get("mode") == "absolute":
                d = {"width": px(e.w), "max-width": "100%"}
                return d if not fixed else {"width": px(e.w)}
            if fixed:
                return {"width": px(e.w)}
            if ctx == "row":
                return {"flex": f"0 1 {px(e.w)}", "width": "100%", "max-width": px(e.w), "min-width": "0"}
            if ctx == "grid":
                return {}
            return {} if self._fills_parent(e) else {"width": "100%", "max-width": px(e.w)}
        if e.kind == "wrapper":
            if fixed:
                return {"width": px(e.w)}
            if ctx == "row":
                return {"min-width": "0"}
            if self.is_grid(e) and ctx == "column" and not self._fills_parent(e):
                return {"width": "100%", "max-width": px(e.w)}
        return {}

    @staticmethod
    def _hug(e: El) -> bool:
        return (e.node or {}).get("textAutoResize") == "WIDTH_AND_HEIGHT"

    def button_css(self, e: El) -> dict[str, str]:
        """Buttons hug their label: inline-flex, centred, padding from the design (min-width keeps the
        designed width when the label box was wider than its text)."""
        d = {"display": "inline-flex", "align-items": "center", "justify-content": "center"}
        texts = _texts(e)
        if len(texts) != 1:
            d["min-width"] = px(e.w)
            return d
        t = texts[0]
        bt, br, bb, bl = border_widths(e.node or {})
        top, bottom = max(0.0, t.y - e.y - bt), max(0.0, e.bottom - t.bottom - bb)
        if self._hug(t):
            left, right = max(0.0, t.x - e.x - bl), max(0.0, e.right - t.right - br)
            side = (left + right) / 2
        else:
            side = 0.0
            d["min-width"] = px(e.w)
        d["padding"] = px(top) if top == bottom and top == side else (
            f"{px(top)} {px(side)}" if top == bottom else f"{px(top)} {px(side)} {px(bottom)}")  # fmt: skip
        if self._ctx[-1] == "row":
            d["flex-shrink"] = "0"
        return d

    def _fills_parent(self, e: El) -> bool:
        """Whether e spans its parent's content box (set by flow_css before children render)."""
        span = self._parent_span[-1] if self._parent_span else None
        return bool(span) and e.x <= span[0] + 2 and e.right >= span[1] - 2

    def is_grid(self, e: El) -> bool:
        """A row of equally wide, evenly spaced items (cards, photos, features) becomes a CSS grid."""
        kids = e.children
        if e.layout.get("mode") != "row" or len(kids) < 2 or e.layout.get("auto"):
            return False
        if all(k.kind == "text" for k in kids):
            return False
        ws = [k.w for k in kids]
        if max(ws) - min(ws) > 2 or min(ws) < 40:
            return False
        order = sorted(kids, key=lambda k: k.x)
        gaps = [b.x - a.right for a, b in zip(order, order[1:], strict=False)]
        return max(gaps) - min(gaps) <= 2 and min(gaps) >= 0

    def padding_value(self, e: El, pad: tuple[float, float, float, float]) -> str:
        """Padding; on full-width elements, large side padding becomes a centred content column."""
        t, r, b, left = pad
        W = self.page.width
        if e.w >= W * 0.95 and left > W * 0.08 and r > W * 0.08:
            lv = f"max(var(--gutter), calc(50% - {px(W / 2 - left)}))"
            rv = f"max(var(--gutter), calc(50% - {px(W / 2 - r)}))"
            return f"{px(t)} {rv} {px(b)} {lv}"
        if t == b and left == r:
            return px(t) if t == left else f"{px(t)} {px(left)}"
        return " ".join(px(v) for v in pad)

    @staticmethod
    def _center(offset: float) -> str:
        if abs(offset) < 0.5:
            return "50%"
        return f"calc(50% {'+' if offset > 0 else '-'} {px(abs(offset))})"

    def anchored_css(self, layer: El, sec: El) -> dict[str, str]:
        """A section background layer positioned like Figma constraints would: full-bleed layers stretch,
        layers touching a frame edge stay on that edge, everything else stays put relative to the centre."""
        W = self.page.width
        x, y, w, _h = self._draw_box(layer)
        d = {"position": "absolute", "top": px(y - sec.y), "__bleed_done__": "1"}
        left_edge, right_edge = layer.x <= 1, layer.right >= W - 1
        if left_edge and right_edge and not layer.bleed:
            d.update({"left": "0", "width": "100%"})
        elif left_edge and not layer.bleed:
            d.update({"left": "0", "width": self._center(layer.right - W / 2)})
        elif right_edge and not layer.bleed:
            d.update({"right": "0", "width": f"calc(50% + {px(W / 2 - layer.x)})"})
        else:
            d["left"] = self._center(x - W / 2)
        if "width" in d and layer.kind in ("svg", "raster", "image"):
            d["object-fit"] = "cover"
        return d

    GENERIC_NAME = re.compile(
        r"^(rectangle|image|img|photo|picture|frame|group|mask group|ellipse|vector|place your|bg|background)"
        r"|\d+$|\(double click", re.I)  # fmt: skip

    def alt_text(self, e: El) -> str:
        """Layer names like "Rectangle 250" are not descriptions: use the nearest heading instead."""
        name = (e.name or "").strip()
        if name and not self.GENERIC_NAME.search(name) and len(name.split()) <= 12:
            return name
        heading = self._alt_context.get(id(e))
        return heading or ""

    def link_for(self, e: El) -> str:
        """Buttons and nav items whose label names a section link to it (single-page navigation)."""
        label = " ".join(t.node.get("characters", "") for t in _texts(e)) if e.kind != "text" else e.node.get(
            "characters", "")  # fmt: skip
        key = _norm(label)
        found = self._anchors.get(key) or self.page_links.get(key)
        if found:
            return found
        contact = next((v for k, v in self._anchors.items() if "contact" in k), None)
        if contact and e.kind != "text" and CTA_WORDS.search(key):
            return contact  # "Get in touch", "I need help", "Book a call"… go to the contact section
        return "#"

    @staticmethod
    def stacking(sec: El) -> set[int]:
        return Generator.stacking_info(sec)[0]

    @staticmethod
    def stacking_info(sec: El) -> tuple[set[int], set[int], dict[int, int]]:
        """(need, background, levels): how each element must stack to paint exactly in Figma's order.

        Background layers (positioned layers painted below everything they overlap) get z-index -1
        inside their isolated parent, so the content above them needs nothing. Any other element that
        overlaps something outside its own ancestry gets z-index = its Figma paint order, wherever
        layout inference put it in the DOM. Wrappers never get a z-index, so they stay transparent.
        """
        painted: list[tuple[El, frozenset]] = []
        layers: set[int] = set()

        def visit(e: El, chain: frozenset) -> None:
            for c in e.layers + e.children:
                if c.kind in PAINT_KINDS:
                    painted.append((c, chain))
                    if c in e.layers:
                        layers.add(id(c))
                visit(c, chain | {id(c)})

        visit(sec, frozenset())
        painted.sort(key=lambda t: t[0].x)

        def overlaps(i: int):
            a, chain_a = painted[i]
            for j, (b, chain_b) in enumerate(painted):
                if j == i or id(a) in chain_b or id(b) in chain_a or not a.intersects(b):
                    continue
                yield b

        # Bottom-up: a layer is background if everything below it that it overlaps is background too.
        background: set[int] = set()
        for i in sorted(range(len(painted)), key=lambda k: painted[k][0].z):
            a = painted[i][0]
            if id(a) in layers and all(b.z > a.z or id(b) in background for b in overlaps(i)):
                background.add(id(a))
        need: set[int] = set()
        pairs: dict[int, list[El]] = {}
        for i, (a, chain_a) in enumerate(painted):
            if id(a) in background:
                continue
            for b, chain_b in painted[i + 1 :]:
                if b.x >= a.right:
                    break
                if id(b) in background or id(a) in chain_b or id(b) in chain_a or not a.intersects(b):
                    continue
                need.add(id(a))
                need.add(id(b))
                pairs.setdefault(id(a), []).append(b)
                pairs.setdefault(id(b), []).append(a)
        # Smallest z-index that keeps every overlapping pair in Figma's order: identical elements end up
        # with the same value (and share a class) instead of one unique number each.
        levels: dict[int, int] = {}
        for e, _chain in sorted((t for t in painted if id(t[0]) in need), key=lambda t: t[0].z):
            below = [levels[id(o)] for o in pairs.get(id(e), []) if o.z < e.z and id(o) in levels]
            levels[id(e)] = 1 + max(below, default=0)
        return need, background, levels

    def _extend_to_edge(self, e: El, d: dict[str, str]) -> None:
        """A solid panel touching the frame's left or right edge keeps touching the screen edge on wider
        screens. Drawn as box-shadow copies: they never change layout or scroll width, and at the design
        width they sit outside the viewport."""
        W = self.page.width
        bg = d.get("background-color")
        if "calc(" in d.get("width", "") or d.get("width") == "100%":
            return  # already anchored to the screen edge
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
        resize = n.get("textAutoResize")
        chars = n.get("characters", "")
        lh = line_height_px({**n, **{k: v for k, v in base_seg.items() if k == "lineHeight"}}) or 0
        lines = round(e.h / lh) if lh else 1
        ctx = self._ctx[-1]
        if d.get("line-height") == "normal" and lines >= 1:
            # Figma "Auto" line height: write the measured value, so a fallback font cannot change heights.
            d["line-height"] = px(e.h / lines)
        if resize == "WIDTH_AND_HEIGHT":
            # Hugging text: its width is its content. Short labels never wrap.
            if "\n" not in chars and len(chars) <= 60:
                d["white-space"] = "nowrap"
        elif ctx in ("absolute", "layer"):
            d["width"] = px(e.w)
        elif ctx == "row":
            # In a row the text box keeps its designed width (it may shrink): neighbours stay in place.
            d["flex"] = f"0 1 {px(e.w)}"
            d["min-width"] = "0"
            if align in ("CENTER", "RIGHT", "JUSTIFIED"):
                d["width"] = "100%"
        elif lines > 1 or align in ("CENTER", "RIGHT", "JUSTIFIED"):
            # Wrapping text keeps Figma's line length as a maximum, so it reflows on small screens.
            d["max-width"] = px(e.w)
            if align in ("CENTER", "RIGHT", "JUSTIFIED"):
                d["width"] = "100%"
        d.update(self.visual(e))
        for k, v in (self.body_style or {}).items():
            if d.get(k) == v:
                del d[k]  # inherited from body
        return d

    def text_html(self, e: El, role: str) -> str:
        n = e.node or {}
        segs = n.get("textSegments") or []
        if len(segs) <= 1:
            return _text(n.get("characters", ""))
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
            txt = _text(s.get("characters", ""))
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
                "hug": n.get("textAutoResize") == "WIDTH_AND_HEIGHT",
                "align": n.get("textAlignHorizontal") or "LEFT",
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
            if not fam and "font-weight" in d:
                fam = (self.body_style or {}).get("font-family", "").strip("'")
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
    def css_text(self, head: list[tuple[str, dict[str, str]]] | None = None) -> tuple[str, str]:
        """Rules as text, with design tokens: colours, fonts, spacing, radii, shadows and font sizes.

        `head` holds element-level rules (body, headings) written before the class rules.
        """
        self.tokenize()
        blocks = []
        for sel, decls in head or []:
            if decls:
                blocks.append(f"{sel} {{\n" + "\n".join(f"  {k}: {v};" for k, v in decls.items()) + "\n}")
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
        # At the design width the container must be exactly the designed column, so the side gutter can
        # never be larger than the space the design leaves around it (it is 16px on phones regardless).
        gutter = max(0, min(GUTTER, math.floor((self.design_width - self.container_width) / 2)))
        root_lines = [f"  --container: {px(self.container_width or 1200)};", f"  --gutter: {px(gutter)};"]
        root_lines += [f"  {v}: {c};" for c, v in tokens.items()]
        root_lines += [f"  --font-{slug(f, 'font')}: '{f}', system-ui, sans-serif;" for f in families]
        root_lines += [f"  {var}: {val};" for var, val in self.tokens]
        root = ":root {\n" + "\n".join(root_lines) + "\n}"
        return root, text

    def tokenize(self) -> None:
        """Values repeated across the stylesheet become named tokens (spacing, radii, shadows, sizes)."""
        groups = {
            "space": ("gap", "column-gap", "row-gap", "padding", "padding-block", "margin-top", "margin-left"),
            "radius": ("border-radius",),
            "shadow": ("box-shadow",),
            "font-size": ("font-size",),
        }
        self.tokens: list[tuple[str, str]] = []
        for name, props in groups.items():
            counts: Counter = Counter()
            for parts in self.rules.values():
                for decls in parts.values():
                    for k, v in decls.items():
                        if k not in props:
                            continue
                        values = v.split() if name in ("space", "radius") else [v]
                        for part in values:
                            if name == "space" and not re.fullmatch(r"\d+(\.\d+)?px", part):
                                continue
                            if name == "space" and float(part[:-2]) < 4:
                                continue
                            counts[part] += 1
            chosen = sorted((v for v, n in counts.items() if n >= (3 if name == "space" else 2)), key=_sort_key)
            if not chosen:
                continue
            names = {v: f"--{name}-{i + 1}" for i, v in enumerate(chosen)}
            self.tokens += [(var, v) for v, var in names.items()]
            for parts in self.rules.values():
                for decls in parts.values():
                    for k, v in list(decls.items()):
                        if k not in props:
                            continue
                        if name in ("space", "radius"):
                            decls[k] = " ".join(f"var({names[part]})" if part in names else part for part in v.split())
                        elif v in names:
                            decls[k] = f"var({names[v]})"

    # ------------------------------------------------------------------ main
    def add_page(self, page: Page, filename: str) -> dict:
        """Renders one page into the shared class registry. Call finish() after the last page."""
        self.page = page
        self.expect = []
        self.warnings.extend(w for w in page.warnings if w not in self.warnings)
        self.assign_roles()
        self.semantics(page)
        if self.body_style is None:
            self.body_style = self.base_text_style(page)
        if page.container:
            self.container_width = max(self.container_width, page.container[1])
            self.design_width = max(self.design_width, page.width)
        body_parts = []
        prev_bottom = 0.0
        self._stack = set()
        self._bg = set()
        self._levels = {}
        for sec in page.sections:
            need, bg, levels = self.stacking_info(sec)
            self._stack |= need
            self._bg |= bg
            self._levels.update(levels)
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
            "Overlapping compositions keep their exact arrangement: between 768px and "
            f"{fit}px those sections scale down so nothing is cut off."
        )
        lines = [f"/* Composition sections (overlapping layers) scale down below {fit}px so nothing is cut off. */"]
        top = fit
        while top > MOBILE_MAX + 1:
            low = max(MOBILE_MAX + 1, top - FIT_STEP)
            query = f"(min-width: {low}px) and (max-width: {top - 0.02:.2f}px)"
            lines.append(f"@media {query} {{ [data-fit] {{ zoom: {low / fit:.4f}; }} }}")
            top = low
        return "\n".join(lines)

    def finish(self, out_dir: Path, pages: list[dict]) -> Output:
        head = [("body", dict(self.body_style or {}))]
        root_css, rules_css = self.css_text(head)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "css").mkdir(exist_ok=True)
        images = self.write_images(out_dir)
        fonts, faces, google_href = self.write_fonts(out_dir)
        page_bg = pages[0]["background"] if pages else "#ffffff"
        base = (
            "/* Base */\n"
            "*, *::before, *::after { box-sizing: border-box; }\n"
            "html { scroll-behavior: smooth; }\n"
            f"html, body {{ margin: 0; padding: 0; background: {page_bg}; }}\n"
            "body { -webkit-font-smoothing: antialiased; text-rendering: optimizeLegibility; }\n"
            "h1, h2, h3, h4, h5, h6 { font-size: inherit; font-weight: inherit; }\n"
            "h1, h2, h3, h4, h5, h6, p, figure { margin: 0; }\n"
            "img { display: block; max-width: 100%; }\n"
            "a { color: inherit; }\n"
            "nav ul { list-style: none; margin: 0; padding: 0; }\n"
            "nav a { text-decoration: none; }\n"
            "main { overflow-x: clip; }\n"
            "[id] { scroll-margin-top: 24px; }\n\n"
            "/* Layout */\n"
            ".container { width: min(100% - 2 * var(--gutter), var(--container)); margin-inline: auto; }"
        )
        grids = sorted(self.grid_columns)
        tablet = ", ".join(f".l-grid-{n}" for n in grids if n > 2)
        mobile = ", ".join(f".l-grid-{n}" for n in grids)
        responsive = "/* Responsive */\n"
        if tablet:
            responsive += (
                "@media (max-width: 1023px) {\n"
                f"  {tablet} {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}\n}}\n"
            )
        responsive += (
            "/* Inferred mobile layout: rows stack below 768px (no mobile frame was supplied). */\n"
            "@media (max-width: 767px) {\n"
            "  :root { --gutter: 16px; }\n"
            "  .l-stage { width: auto; margin-left: 0; }\n"
            "  .l-row { flex-direction: column; align-items: flex-start; row-gap: 16px; }\n"
            "  .l-row > * { flex: none; margin-left: 0; max-width: 100%; }\n"
            + (f"  {mobile} {{ grid-template-columns: 1fr; row-gap: 24px; }}\n" if mobile else "")
            + "}"
        )
        if not any(f["role"] == "mobile" for f in self.bundle["frames"]):
            self.assumptions.append("No mobile frame: a simple stacked mobile layout was inferred below 768px.")
        fit = self.fit_css(pages[0]["width"] if pages else 1440) if self._fit_sections else ""
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
    gen.page_links = {_norm(page.name): filename for page, filename in pages}
    if len(pages) > 1:
        gen.page_links.update({"home": pages[0][1]})
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
