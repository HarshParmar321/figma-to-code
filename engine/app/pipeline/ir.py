"""Bundle → Design IR: a tree of elements with boxes relative to the root frame.

Groups are dissolved (they carry no layout); frames with Auto Layout keep their structure; frames
without Auto Layout become boxes only when they draw something (fill, stroke, effect). Sections
come from top-level frames, or from full-width background bands on flat designs.
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field

from ..spike_report import detect_sections
from .styles import visible_paints

SHAPE_TYPES = {"RECTANGLE", "ELLIPSE", "POLYGON", "STAR", "LINE", "VECTOR"}
FRAME_TYPES = {"FRAME", "COMPONENT", "INSTANCE", "COMPONENT_SET", "SECTION"}
AUTO_LAYOUT = {"HORIZONTAL", "VERTICAL"}
FULL_WIDTH_RATIO = 0.95


@dataclass(eq=False)
class El:
    id: str
    name: str
    kind: str  # page | section | container | box | wrapper | text | image | svg | raster | shape
    x: float
    y: float
    w: float
    h: float
    z: int = 0
    node: dict | None = None
    children: list[El] = field(default_factory=list)
    layers: list[El] = field(default_factory=list)  # absolutely positioned background layers
    asset: str | None = None  # asset id (image fill or svg)
    layout: dict = field(default_factory=dict)
    deco: dict | None = None  # e.g. offset outline ("hard shadow")
    tag: str | None = None
    role: str | None = None
    auto: bool = False  # Auto Layout frame: layout comes from Figma, not inference
    bleed: tuple[float, float, float, float] | None = None  # SVG export area beyond the box (t, r, b, l)
    overlay: bool = False  # section paints above its neighbours' background layers (split-out header)
    component: str | None = None  # shared component name when the section repeats across pages
    mask: dict | None = None  # Figma mask applied to this element (shape clip or image alpha)
    html_id: str | None = None  # id attribute (sections: anchor target for nav links)

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def bottom(self) -> float:
        return self.y + self.h

    @property
    def area(self) -> float:
        return self.w * self.h

    def contains(self, o: El, tol: float = 1.0) -> bool:
        return (
            o.x >= self.x - tol
            and o.y >= self.y - tol
            and o.right <= self.right + tol
            and o.bottom <= self.bottom + tol
        )

    def intersects(self, o: El) -> bool:
        return self.x < o.right and o.x < self.right and self.y < o.bottom and o.y < self.bottom

    def walk(self):
        yield self
        for c in self.layers + self.children:
            yield from c.walk()


@dataclass
class Page:
    role: str
    root: El
    sections: list[El]
    width: float
    height: float
    background: list[dict]
    warnings: list[str] = field(default_factory=list)
    frame: dict = field(default_factory=dict)
    name: str = ""  # site page name (e.g. "Home", "About")
    container: tuple[float, float] | None = None  # (left, width) of the centred content column


def _sanitize(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", s)


class IRBuilder:
    def __init__(self, bundle: dict):
        self.bundle = bundle
        self.nodes: dict[str, dict] = bundle["nodes"]
        self.asset_ids = {a["id"] for a in bundle["assets"]}
        self.image_assets = {a["imageHash"]: a["id"] for a in bundle["assets"] if a.get("imageHash")}
        self._z = itertools.count()
        self.warnings: list[str] = []
        # A visible mask layer clips every sibling painted above it (until the end of its parent).
        self.masked_by: dict[str, str] = {}
        for n in self.nodes.values():
            if n.get("insideSvg"):
                continue
            current = None
            for cid in n.get("children", []):
                c = self.nodes.get(cid)
                if c is None:
                    continue
                if c.get("isMask") and c.get("visible", True):
                    current = cid
                elif current:
                    self.masked_by[cid] = current
        self._mask_cache: dict[str, dict | None] = {}

    # --- geometry
    def box(self, node: dict, root: dict) -> tuple[float, float, float, float] | None:
        b, rb = node.get("absoluteBoundingBox"), root.get("absoluteBoundingBox")
        if not b or not rb:
            return None
        return b["x"] - rb["x"], b["y"] - rb["y"], b["width"], b["height"]

    def el(self, node: dict, root: dict, kind: str, **kw) -> El | None:
        bx = self.box(node, root)
        if not bx:
            return None
        return El(node["id"], node["name"], kind, *bx, z=next(self._z), node=node, **kw)

    @staticmethod
    def _bleed(node: dict) -> tuple[float, float, float, float] | None:
        """How far the render bounds (shadows, outside strokes) extend past the layer box.

        Figma exports SVGs at the render bounds, so the image must be drawn that much larger.
        """
        b, r = node.get("absoluteBoundingBox"), node.get("absoluteRenderBounds")
        if not b or not r:
            return None
        t = max(0.0, b["y"] - r["y"])
        left = max(0.0, b["x"] - r["x"])
        right = max(0.0, (r["x"] + r["width"]) - (b["x"] + b["width"]))
        bottom = max(0.0, (r["y"] + r["height"]) - (b["y"] + b["height"]))
        return (t, right, bottom, left) if max(t, right, bottom, left) > 0.5 else None

    # --- classification
    def _image_asset(self, node: dict) -> str | None:
        for p in visible_paints(node.get("fills")):
            if p["type"] == "IMAGE" and p.get("imageHash") in self.image_assets:
                return self.image_assets[p["imageHash"]]
        return None

    @staticmethod
    def _draws(node: dict) -> bool:
        if visible_paints(node.get("fills")) or visible_paints(node.get("strokes")):
            return True
        return any(e.get("visible", True) for e in node.get("effects") or [] if isinstance(e, dict))

    def flatten(self, node: dict, root: dict) -> list[El]:
        """Returns the elements a node contributes to its parent's flow."""
        if node.get("isMask") and node.get("visible", True) and not node.get("exportAs"):
            return []  # a mask is not drawn itself
        items = self._flatten(node, root)
        mid = self.masked_by.get(node["id"])
        if mid and items:
            if mid not in self._mask_cache:
                self._mask_cache[mid] = self._mask(self.nodes[mid], root)
            m = self._mask_cache[mid]
            for e in items:
                e.mask = e.mask or m
        return items

    def _flatten(self, node: dict, root: dict) -> list[El]:
        if not node.get("visible", True) or node.get("opacity", 1) == 0 or node.get("insideSvg"):
            return []
        t = node["type"]
        if node.get("exportAs") in ("svg", "png"):
            fmt = node["exportAs"]
            asset = f"{fmt}-{_sanitize(node['id'])}"
            if asset in self.asset_ids:
                kind = "svg" if fmt == "svg" else "raster"
                e = self.el(node, root, kind, asset=asset, bleed=self._bleed(node))
                return [e] if e else []
            self.warnings.append(f"{fmt.upper()} asset missing for {node['name']} ({node['id']})")
            return []
        if t == "TEXT":
            if not node.get("characters", "").strip():
                return []
            e = self.el(node, root, "text")
            return [e] if e else []
        if t in SHAPE_TYPES:
            image = self._image_asset(node)
            if image:
                e = self.el(node, root, "image", asset=image)
                return [e] if e else []
            if self._draws(node):
                e = self.el(node, root, "shape")
                return [e] if e else []
            return []
        if t == "GROUP" or t == "BOOLEAN_OPERATION":
            return self._flatten_children([self.nodes[c] for c in node.get("children", []) if c in self.nodes], root)
        if t in FRAME_TYPES:
            kids = [self.nodes[c] for c in node.get("children", []) if c in self.nodes]
            if node.get("layoutMode") in AUTO_LAYOUT:
                e = self.el(node, root, "box", auto=True, asset=self._image_asset(node))
                if not e:
                    return []
                e.children.extend(self._flatten_children(kids, root))
                return [e]
            if self._draws(node) or (node.get("clipsContent") and kids and self._draws(node)):
                e = self.el(node, root, "box", asset=self._image_asset(node))
                if not e:
                    return []
                e.children.extend(self._flatten_children(kids, root))
                return [e]
            return self._flatten_children(kids, root)
        self.warnings.append(f"Unsupported node type {t} ({node['name']})")
        return []

    def _flatten_children(self, kids: list[dict], root: dict) -> list[El]:
        return [e for k in kids for e in self.flatten(k, root)]

    def _mask(self, node: dict, root: dict) -> dict | None:
        bx = self.box(node, root)
        if not bx:
            return None
        info = {"box": bx, "type": node["type"], "maskType": node.get("maskType", "ALPHA"), "node": node}
        image = self._image_asset(node)
        if image:
            info["kind"], info["asset"] = "image", image
        elif node["type"] == "ELLIPSE":
            info["kind"] = "ellipse"
        else:
            info["kind"] = "rect"  # rectangles exactly; other vectors by their bounding box
        return info

    # --- pages
    def build_page(self, frame: dict) -> Page:
        root = self.nodes[frame["rootId"]]
        W, H = frame["width"], frame["height"]
        page = El(root["id"], root["name"], "page", 0, 0, W, H, z=next(self._z), node=root)
        sections: list[El] = []
        kids = [self.nodes[c] for c in root.get("children", []) if c in self.nodes]
        if root.get("layoutMode") == "VERTICAL":
            # Structured file: each top-level child is a section.
            for i, k in enumerate(kids):
                items = self.flatten(k, root)
                if not items:
                    continue
                if len(items) == 1 and items[0].kind == "box":
                    sec = items[0]
                    sec.kind = "section"
                else:
                    bx = self.box(k, root)
                    sec = El(f"section-{i}", k["name"], "section", *bx, z=next(self._z), node=k, children=items)
                sections.append(sec)
        else:
            for band in detect_sections(self.nodes, root):
                items = [e for nid in band["nodeIds"] for e in self.flatten(self.nodes[nid], root)]
                sec = El(
                    f"band-{band['index']}",
                    band["heading"] or f"Section {band['index'] + 1}",
                    "section",
                    0,
                    band["top"],
                    W,
                    band["height"],
                    z=next(self._z),
                    children=items,
                )
                sections.append(sec)

        for sec in sections:
            self._pull_backgrounds(sec, W)
        page.children = sections
        return Page(
            frame["role"],
            page,
            sections,
            W,
            H,
            root.get("fills") or [],
            self.warnings,
            frame=frame,
            name=frame.get("page") or page_name(frame["name"]),
        )

    @staticmethod
    def _pull_backgrounds(sec: El, page_width: float) -> None:
        """Full-width shapes, images and graphics become section background layers, wherever they start.

        They keep their exact position (they may start above the section or run past its bottom) and
        paint in their original order behind the section's content.
        """
        keep = []
        for e in sec.children:
            is_bg = e.kind in ("shape", "image", "svg", "raster") and e.w >= page_width * FULL_WIDTH_RATIO and e.h >= 24
            (sec.layers if is_bg else keep).append(e)
        sec.children = keep


BREAKPOINT_WORDS = re.compile(
    r"\b((iphone|ipad|pixel|galaxy|android|macbook)(\s*(\d+\w*|pro|max|mini|plus|air|ultra|se))*|desktop|tablet|mobile|phone|web|pc|\d{3,4}(px)?)\b",
    re.I,
)


def page_name(frame_name: str) -> str:
    """Site page name from a frame name: "Home / Desktop" and "Home - Mobile" are both "Home"."""
    cleaned = BREAKPOINT_WORDS.sub(" ", frame_name)
    cleaned = re.sub(r"[\s/|_\-–—:()\[\]]+", " ", cleaned).strip()
    return cleaned or frame_name.strip() or "Page"


def build_ir(bundle: dict) -> dict[str, Page]:
    """First page per breakpoint role (single-page callers)."""
    out: dict[str, Page] = {}
    for p in build_pages(bundle):
        out.setdefault(p.role, p)
    return out


def build_pages(bundle: dict) -> list[Page]:
    """Every selected frame as a Page, in selection order."""
    builder = IRBuilder(bundle)
    return [builder.build_page(f) for f in bundle["frames"]]


def site_pages(pages: list[Page]) -> list[dict]:
    """Groups frames into site pages: one desktop frame per page, plus its mobile/tablet frames if any."""
    groups: dict[str, dict] = {}
    for p in pages:
        key = p.name.lower()
        g = groups.setdefault(key, {"name": p.name, "desktop": None, "tablet": None, "mobile": None})
        if g[p.role] is None:
            g[p.role] = p
    result = [g for g in groups.values() if g["desktop"] is not None]
    # A single desktop with differently named mobile/tablet frames: pair them anyway.
    if len(result) == 1:
        for g in groups.values():
            for role in ("tablet", "mobile"):
                if g[role] is not None and result[0][role] is None:
                    result[0][role] = g[role]
    return result
