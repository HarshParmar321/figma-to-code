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
    kind: str  # page | section | container | box | wrapper | text | image | svg | shape
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
        if not node.get("visible", True) or node.get("opacity", 1) == 0 or node.get("insideSvg"):
            return []
        t = node["type"]
        if node.get("exportAs") == "svg":
            asset = f"svg-{_sanitize(node['id'])}"
            if asset in self.asset_ids:
                e = self.el(node, root, "svg", asset=asset)
                return [e] if e else []
            self.warnings.append(f"SVG asset missing for {node['name']} ({node['id']})")
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
            return [e for c in node.get("children", []) if c in self.nodes for e in self.flatten(self.nodes[c], root)]
        if t in FRAME_TYPES:
            kids = [self.nodes[c] for c in node.get("children", []) if c in self.nodes]
            if node.get("layoutMode") in AUTO_LAYOUT:
                e = self.el(node, root, "box", auto=True, asset=self._image_asset(node))
                if not e:
                    return []
                for k in kids:
                    e.children.extend(self.flatten(k, root))
                return [e]
            if self._draws(node) or (node.get("clipsContent") and kids and self._draws(node)):
                e = self.el(node, root, "box", asset=self._image_asset(node))
                if not e:
                    return []
                for k in kids:
                    e.children.extend(self.flatten(k, root))
                return [e]
            return [e for k in kids for e in self.flatten(k, root)]
        self.warnings.append(f"Unsupported node type {t} ({node['name']})")
        return []

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
        return Page(frame["role"], page, sections, W, H, root.get("fills") or [], self.warnings)

    @staticmethod
    def _pull_backgrounds(sec: El, page_width: float) -> None:
        """Full-width shapes and images at the top of a band become section background layers."""
        keep = []
        for e in sec.children:
            is_bg = (
                e.kind in ("shape", "image", "svg")
                and e.w >= page_width * FULL_WIDTH_RATIO
                and e.y <= sec.y + 2
                and e.h >= sec.h * 0.5
            )
            (sec.layers if is_bg else keep).append(e)
        sec.children = keep


def build_ir(bundle: dict) -> dict[str, Page]:
    builder = IRBuilder(bundle)
    return {f["role"]: builder.build_page(f) for f in bundle["frames"]}
