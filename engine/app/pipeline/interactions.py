"""Prototype interactions → web behaviour.

The plugin sends, for every component whose instances react to the user, its default variant and its
state variants (hover, press, focus, click, after-delay), plus the overlays that open and the
destination of every prototype connection. This module:

* prepares the bundle: layers that only exist (or are only visible) in a state are added to each
  instance, hidden, so the page can reveal them (an accordion's answer, a "View" label on hover);
* turns each state into CSS changes, the way Smart Animate works: layers are matched by name path
  between the default and the state variant, and only what differs is written (colours, borders,
  radii, shadows, opacity, text style, and movement / scale / rotation as transforms);
* converts Figma transitions (duration, easing curves and springs) into CSS timing;
* resolves where clicks go: another page, a section, a URL, back, or an overlay.
"""

from __future__ import annotations

import copy
import math
from collections import Counter

from .styles import background, border, effects, radius, text_style

Path_ = tuple[tuple[str, int], ...]

TEXT_STATE_PROPS = ("color", "font-weight", "font-style", "text-decoration", "letter-spacing", "text-transform")
RESET = {
    "box-shadow": "none",
    "filter": "none",
    "backdrop-filter": "none",
    "background": "transparent",
    "background-color": "transparent",
    "background-image": "none",
    "border": "0 solid transparent",
    "border-color": "transparent",
    "border-radius": "0",
    "opacity": "1",
    "text-decoration": "none",
    "letter-spacing": "normal",
    "text-transform": "none",
    "font-style": "normal",
}
STATE_TRIGGERS = ("hover", "press", "focus", "click", "timeout")
DEFAULT_TRANSITION = {"type": "DISSOLVE", "duration": 0.2, "easing": {"type": "EASE_OUT"}}

# Figma's named curves (https://help.figma.com/hc/en-us/articles/360051748654).
BEZIER = {
    "LINEAR": (0, 0, 1, 1),
    "EASE_IN": (0.42, 0, 1, 1),
    "EASE_OUT": (0, 0, 0.58, 1),
    "EASE_IN_AND_OUT": (0.42, 0, 0.58, 1),
    "EASE_IN_BACK": (0.3, -0.05, 0.7, -0.5),
    "EASE_OUT_BACK": (0.45, 1.45, 0.8, 1),
    "EASE_IN_AND_OUT_BACK": (0.7, -0.4, 0.4, 1.4),
}
SPRINGS = {  # mass, stiffness, damping
    "GENTLE": (1, 100, 15),
    "QUICK": (1, 300, 20),
    "BOUNCY": (1, 600, 15),
    "SLOW": (1, 80, 20),
}


# ----------------------------------------------------------------------------- timing
def _num(v: float) -> str:
    s = f"{v:.3f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


def spring_curve(mass: float, stiffness: float, damping: float, velocity: float = 0.0) -> tuple[str, float]:
    """A damped spring as a CSS linear() easing and the time it takes to settle (seconds)."""
    w0 = math.sqrt(stiffness / mass)
    zeta = damping / (2 * math.sqrt(stiffness * mass))

    def x(t: float) -> float:
        if zeta < 1:
            wd = w0 * math.sqrt(1 - zeta * zeta)
            b = (zeta * w0 - velocity) / wd
            return 1 - math.exp(-zeta * w0 * t) * (math.cos(wd * t) + b * math.sin(wd * t))
        b = w0 - velocity
        return 1 - math.exp(-w0 * t) * (1 + b * t)

    # Settled when it stays within 0.1% of the target.
    settle, t, step = 0.0, 0.0, 1 / 240
    while t < 10:
        if abs(1 - x(t)) > 0.001:
            settle = t
        t += step
    settle = max(0.1, min(settle + step, 5.0))
    points = [x(settle * i / 32) for i in range(33)]
    points[-1] = 1.0
    return "linear(" + ", ".join(_num(p) for p in points) + ")", settle


def easing_css(easing: dict | None) -> tuple[str, float | None]:
    """CSS timing function for a Figma easing; springs also return their own duration."""
    easing = easing or {}
    t = easing.get("type", "EASE_OUT")
    if t == "CUSTOM_CUBIC_BEZIER" and easing.get("easingFunctionCubicBezier"):
        b = easing["easingFunctionCubicBezier"]
        return f"cubic-bezier({_num(b['x1'])}, {_num(b['y1'])}, {_num(b['x2'])}, {_num(b['y2'])})", None
    if t == "CUSTOM_SPRING" and easing.get("easingFunctionSpring"):
        s = easing["easingFunctionSpring"]
        return spring_curve(
            s.get("mass", 1), s.get("stiffness", 100), s.get("damping", 15), s.get("initialVelocity", 0)
        )
    if t in SPRINGS:
        return spring_curve(*SPRINGS[t])
    if t == "LINEAR":
        return "linear", None
    b = BEZIER.get(t, BEZIER["EASE_OUT"])
    named = {(0.42, 0, 1, 1): "ease-in", (0, 0, 0.58, 1): "ease-out", (0.42, 0, 0.58, 1): "ease-in-out"}
    return named.get(b) or f"cubic-bezier({', '.join(_num(v) for v in b)})", None


def timing(transition: dict | None) -> tuple[float, str]:
    """(duration in seconds, timing function) of a Figma transition."""
    tr = transition or DEFAULT_TRANSITION
    fn, spring_duration = easing_css(tr.get("easing"))
    duration = spring_duration if spring_duration is not None else float(tr.get("duration") or 0.3)
    return duration, fn


def transition_value(props: list[str], transition: dict | None) -> str:
    duration, fn = timing(transition)
    return ", ".join(f"{p} {_num(duration)}s {fn}" for p in props)


# ----------------------------------------------------------------------------- layer matching
def subtree_paths(nodes: dict[str, dict], root_id: str) -> dict[Path_, str]:
    """Name path (with an index for repeated names) → node id, like Smart Animate matches layers."""
    out: dict[Path_, str] = {(): root_id}
    stack: list[tuple[str, Path_]] = [(root_id, ())]
    while stack:
        nid, path = stack.pop()
        counts: Counter = Counter()
        for cid in (nodes.get(nid) or {}).get("children", []):
            c = nodes.get(cid)
            if c is None:
                continue
            k = counts[c["name"]]
            counts[c["name"]] += 1
            p = path + ((c["name"], k),)
            out[p] = cid
            stack.append((cid, p))
    return out


def _box(node: dict, root: dict) -> tuple[float, float, float, float]:
    b, r = node.get("absoluteBoundingBox") or {}, root.get("absoluteBoundingBox") or {}
    return (
        b.get("x", 0) - r.get("x", 0),
        b.get("y", 0) - r.get("y", 0),
        b.get("width", 0),
        b.get("height", 0),
    )


def _shown(node: dict) -> bool:
    return node.get("visible", True) and (node.get("opacity", 1) or 0) > 0


LAYOUT_KEYS = ("itemSpacing", "paddingTop", "paddingRight", "paddingBottom", "paddingLeft")


def layout_style(node: dict) -> dict[str, str]:
    """Auto Layout gap and padding as CSS (animatable, and the layout reflows like in Figma)."""
    v = [float(node.get(k) or 0) for k in LAYOUT_KEYS]
    return {"gap": f"{_num(v[0])}px", "padding": " ".join(f"{_num(x)}px" if x else "0" for x in v[1:])}


def node_style(node: dict) -> dict[str, str]:
    """The CSS a layer's look produces (geometry excluded), for comparing two states."""
    t = node.get("type")
    w, h = node.get("width", 0), node.get("height", 0)
    d: dict[str, str] = {}
    if t == "TEXT":
        segs = node.get("textSegments") or [{}]
        seg = max(segs, key=lambda s: len(s.get("characters", "")))
        ts = text_style(seg, node)
        d.update({k: v for k, v in ts.items() if k in TEXT_STATE_PROPS})
        d.update(effects(node, is_text=True))
    elif node.get("exportAs") in ("svg", "png"):
        pass  # drawn as an image
    elif t != "GROUP":
        d.update(background(node.get("fills"), (w, h)))
        d.update(border(node))
        d.update(radius(node))
        d.update(effects(node))
    op = node.get("opacity", 1)
    d["opacity"] = _num(op if isinstance(op, (int, float)) else 1)
    return d


# ----------------------------------------------------------------------------- the index
class Interactions:
    def __init__(self, bundle: dict):
        data = bundle.get("interactions") or {}
        self.nodes: dict[str, dict] = bundle["nodes"]
        self.components: dict[str, dict] = {c["id"]: c for c in data.get("components", [])}
        self.overlays: dict[str, dict] = {o["rootId"]: o for o in data.get("overlays", [])}
        self.targets: dict[str, dict] = data.get("targets", {})
        self.frame_ids = {f["rootId"] for f in bundle.get("frames", [])}
        # Instance node id → [(state, {page node id: change})], filled by prepare().
        self.changes: dict[str, list[dict]] = {}
        # Instance node id → page node ids of the layers that toggle it (a menu icon, a close icon).
        self.triggers: dict[str, list[str]] = {}
        self.notes: list[str] = []

    @property
    def active(self) -> bool:
        return bool(self.components or self.overlays)

    def component_of(self, node: dict | None) -> dict | None:
        if not node or node.get("type") != "INSTANCE":
            return None
        return self.components.get(node.get("mainComponentId"))

    def reactions(self, node: dict | None) -> list[dict]:
        if not node:
            return []
        own = node.get("reactions") or []
        if own:
            return own
        comp = self.component_of(node)
        return (comp or {}).get("reactions") or []

    # ------------------------------------------------------------------ prepare
    def prepare(self) -> None:
        """Adds state-only layers to every stateful instance (hidden) and records each state's changes."""
        instances = [
            n
            for n in list(self.nodes.values())
            if n.get("type") == "INSTANCE" and self._in_page(n) and (self.component_of(n) or {}).get("states")
        ]
        for inst in instances:
            comp = self.component_of(inst)
            base_id = comp["id"]
            if base_id not in self.nodes:
                continue
            base_paths = subtree_paths(self.nodes, base_id)
            page_paths = subtree_paths(self.nodes, inst["id"])
            result = []
            for state in comp["states"]:
                if state.get("trigger") not in STATE_TRIGGERS or state.get("variantId") not in self.nodes:
                    continue
                st_paths = subtree_paths(self.nodes, state["variantId"])
                self._add_state_only(inst, page_paths, base_paths, st_paths, state)
                page_paths = subtree_paths(self.nodes, inst["id"])
                result.append(
                    {"state": state, "changes": self._diff(inst, page_paths, base_paths, st_paths, base_id, state)}
                )
            self._complete(result, page_paths, base_paths)
            if result:
                self.changes[inst["id"]] = result
                trig = self._trigger_layers(comp, page_paths, base_paths)
                if trig:
                    self.triggers[inst["id"]] = trig

    def _trigger_layers(self, comp: dict, page_paths: dict, base_paths: dict) -> list[str]:
        """Layers inside the component that start a click state ("Menu icon → Change to Open") and the
        layers in that state that change it back (a close icon): only these toggle it on the page."""
        variants = {comp["id"]} | {s["variantId"] for s in comp["states"]}
        out: list[str] = []
        for state in comp["states"]:
            layer = state.get("triggerLayer")
            if state.get("trigger") != "click" or not layer:
                continue
            path = next((p for p, nid in base_paths.items() if nid == layer), None)
            if path is not None and page_paths.get(path):
                out.append(page_paths[path])
            for path, nid in subtree_paths(self.nodes, state["variantId"]).items():
                if not path:
                    continue
                for r in self.nodes[nid].get("reactions") or []:
                    acts = r.get("actions") or ([r["action"]] if r.get("action") else [])
                    back = any(a.get("navigation") == "CHANGE_TO" and a.get("destinationId") in variants for a in acts)
                    if back and page_paths.get(path):
                        out.append(page_paths[path])
        return list(dict.fromkeys(out))

    def _complete(self, result: list[dict], page_paths: dict, base_paths: dict) -> None:
        """Hover, press and focus apply together in a browser (pressing also hovers), while Figma shows
        one variant at a time: each of these states sets every property any of them changes."""
        group = [r for r in result if r["state"]["trigger"] in ("hover", "press", "focus")]
        if len(group) < 2:
            return
        path_of = {pid: path for path, pid in page_paths.items()}
        keys: dict[str, set[str]] = {}
        boxed: set[str] = set()
        for r in group:
            for pid, ch in r["changes"].items():
                keys.setdefault(pid, set()).update(ch["css"])
                if ch.get("box"):
                    boxed.add(pid)
        for r in group:
            for pid, props in keys.items():
                ch = r["changes"].setdefault(pid, {"css": {}, "box": None, "show": None, "center": (0.0, 0.0)})
                missing = props - set(ch["css"])
                if missing:
                    bid = base_paths.get(path_of.get(pid))
                    base = dict(node_style(self.nodes[bid])) if bid else {}
                    if bid and self.nodes[bid].get("layoutMode") in ("HORIZONTAL", "VERTICAL"):
                        base.update(layout_style(self.nodes[bid]))
                    for k in missing:
                        ch["css"][k] = base.get(k, RESET.get(k, "initial"))
            for pid in boxed:
                ch = r["changes"].setdefault(pid, {"css": {}, "box": None, "show": None, "center": (0.0, 0.0)})
                if not ch.get("box"):
                    ch["css"]["transform"] = "none"

    def _in_page(self, node: dict) -> bool:
        n, seen = node, 0
        while n and n.get("parentId") and seen < 200:
            n = self.nodes.get(n["parentId"])
            seen += 1
        return bool(n) and n["id"] in self.frame_ids

    def _add_state_only(self, inst, page_paths, base_paths, st_paths, state) -> None:
        """Layers present in the state but not in the default variant are copied into the instance,
        hidden, at the same place in their parent (so Auto Layout order is kept)."""
        st_root = self.nodes[state["variantId"]]
        inst_box = inst.get("absoluteBoundingBox") or {}
        for path, sid in sorted(st_paths.items(), key=lambda kv: len(kv[0])):
            if not path or path in base_paths or path in page_paths:
                continue
            parent_path = path[:-1]
            parent_id = page_paths.get(parent_path)
            if parent_id is None:
                continue  # its parent is also new: copied with it
            src = self.nodes[sid]
            new_id = self._copy_subtree(sid, parent_id, st_root, inst_box, state)
            parent = self.nodes[parent_id]
            siblings_in_state = self.nodes[st_paths[parent_path]].get("children", [])
            index = siblings_in_state.index(sid) if sid in siblings_in_state else len(parent["children"])
            parent["children"].insert(min(index, len(parent["children"])), new_id)
            page_paths[path] = new_id
            for sub_path, sub_id in subtree_paths(self.nodes, new_id).items():
                page_paths[path + sub_path] = sub_id
            self.nodes[new_id]["_stateOnly"] = src.get("visible", True)

    def _copy_subtree(self, sid: str, parent_id: str, st_root: dict, inst_box: dict, state: dict) -> str:
        new_id = f"S{state['variantId']}:{sid}"
        stack = [(sid, parent_id, new_id)]
        while stack:
            src_id, pid, nid = stack.pop()
            src = copy.deepcopy(self.nodes[src_id])
            src["id"], src["parentId"] = nid, pid
            x, y, w, h = _box(src, st_root)
            src["absoluteBoundingBox"] = {"x": inst_box.get("x", 0) + x, "y": inst_box.get("y", 0) + y, "width": w,
                                          "height": h}  # fmt: skip
            src.pop("absoluteRenderBounds", None)
            kids = src.get("children", [])
            src["children"] = [f"{nid}/{i}" for i in range(len(kids))]
            if nid == new_id:
                src["visible"], src["_reveal"] = False, True
            self.nodes[nid] = src
            for i, k in enumerate(kids):
                stack.append((k, nid, f"{nid}/{i}"))
        return new_id

    def _overridden(self, inst: dict) -> dict[str, set[str]]:
        """Page node id → Figma fields the designer overrode on this instance (kept in every state)."""
        out: dict[str, set[str]] = {}
        for o in inst.get("overrides") or []:
            if isinstance(o, dict) and o.get("id"):
                out[o["id"]] = set(o.get("overriddenFields") or [])
        return out

    def _diff(self, inst, page_paths, base_paths, st_paths, base_id, state) -> dict[str, dict]:
        """Page node id → {"css": {...}, "box": (dx, dy, sx, sy, rot, type), "show": bool | None, "center"}.

        Inside Auto Layout, positions come from the layout: when a frame's gap or padding changes, or a
        child appears, disappears or changes size, the frame reflows (written as real CSS: gap, padding,
        display), so the movement it causes is not written again as transforms."""
        base_root, st_root = self.nodes[base_id], self.nodes[state["variantId"]]
        overridden = self._overridden(inst)
        raw: dict[Path_, dict] = {}
        for path, pid in page_paths.items():
            sid, bid = st_paths.get(path), base_paths.get(path)
            b_node = self.nodes[bid] if bid is not None else None
            s_node = self.nodes[sid] if sid is not None else None
            raw[path] = {"pid": pid, "b": b_node, "s": s_node}
            if b_node is None or s_node is None:
                continue
            bx, by, bw, bh = _box(b_node, base_root)
            sx_, sy_, sw, sh = _box(s_node, st_root)
            raw[path]["geo"] = (bx, by, bw, bh, sx_, sy_, sw, sh)

        def is_auto(n: dict | None) -> bool:
            return bool(n) and n.get("layoutMode") in ("HORIZONTAL", "VERTICAL")

        def resized(r: dict) -> bool:
            g = r.get("geo")
            return bool(g) and (abs(g[2] - g[6]) > 0.5 or abs(g[3] - g[7]) > 0.5)

        def toggled(r: dict) -> bool:
            b, s_ = r["b"], r["s"]
            return (b is None) != (s_ is None) or (b is not None and s_ is not None and _shown(b) != _shown(s_))

        # The whole variant scaled by the same factor (a "pressed" shrink, a "hover" grow): one scale.
        root_geo = raw.get((), {}).get("geo")
        if root_geo and root_geo[2] > 0.5 and root_geo[3] > 0.5:
            f = root_geo[6] / root_geo[2]
            uniform = abs(f - 1) > 0.01 and abs(root_geo[7] / root_geo[3] - f) < 0.02
            for r in raw.values():
                g = r.get("geo")
                if uniform and g and g[2] > 0.5 and g[3] > 0.5:
                    uniform = abs(g[6] / g[2] - f) < 0.03 and abs(g[7] / g[3] - f) < 0.03
            if uniform:
                return self._scaled(raw, f)

        reflow: set[Path_] = set()  # Auto Layout frames whose layout changes in this state
        for path, r in raw.items():
            b, s_ = r["b"], r["s"]
            if not is_auto(b) or s_ is None:
                continue
            own = any(float(b.get(k) or 0) != float(s_.get(k) or 0) for k in LAYOUT_KEYS)
            kids = [q for q in raw if len(q) == len(path) + 1 and q[:-1] == path]
            if own or any(resized(raw[q]) or toggled(raw[q]) for q in kids):
                reflow.add(path)

        out: dict[str, dict] = {}
        for path, r in raw.items():
            pid, b_node, s_node = r["pid"], r["b"], r["s"]
            page_node = self.nodes[pid]
            if s_node is None:
                if b_node is not None and _shown(b_node):
                    out[pid] = {"css": {}, "box": None, "show": False}  # gone in this state
                continue
            change: dict = {"css": {}, "box": None, "show": None}
            if b_node is None:  # a state-only layer copied in: appears
                change["show"] = True if _shown(s_node) else None
                out[pid] = change
                continue
            bx, by, bw, bh, sx_, sy_, sw, sh = r["geo"]
            change["center"] = (bx + bw / 2, by + bh / 2)
            if _shown(s_node) != _shown(b_node):
                change["show"] = _shown(s_node)
                if change["show"] and not _shown(page_node):
                    page_node["_reveal"] = True  # hidden by default: the page must still contain it
            sb, ss = node_style(b_node), node_style(s_node)
            if is_auto(b_node):
                sb.update(layout_style(b_node))
                ss.update(layout_style(s_node))
            skip = self._skipped_props(overridden.get(pid, set()))
            for k in sorted(set(sb) | set(ss)):
                if k in skip or sb.get(k) == ss.get(k):
                    continue
                change["css"][k] = ss.get(k, RESET.get(k, "initial"))
            if "opacity" in change["css"] and change["show"] is not None:
                change["css"].pop("opacity")
            dcx, dcy = (sx_ + sw / 2) - (bx + bw / 2), (sy_ + sh / 2) - (by + bh / 2)
            scx = sw / bw if bw > 0.5 else 1.0
            scy = sh / bh if bh > 0.5 else 1.0
            rot = float(s_node.get("rotation") or 0) - float(b_node.get("rotation") or 0)
            if abs(rot) > 0.1:
                scx = scy = 1.0  # rotation changes the bounding box, not the layer's size
            in_reflow = path[:-1] in reflow if path else False
            if path in reflow or in_reflow or (path == () and is_auto(b_node)):
                # Its size (and, inside a reflowing frame, its place) comes from the layout.
                if path in reflow or (path == () and is_auto(b_node)):
                    scx = scy = 1.0
                if in_reflow or path == ():
                    dcx = dcy = 0.0
            has_text = any(
                self.nodes[q["pid"]].get("type") == "TEXT" for p2, q in raw.items() if p2[: len(path)] == path
            )
            if (abs(scx - scy) > 0.02) and has_text:
                scx = scy = 1.0  # stretching text is never what the design means
                if path == ():
                    dcx = dcy = 0.0
            if abs(scx - 1) > 0.01 and abs(scx - scy) <= 0.02 and "border-radius" in change["css"]:
                change["css"].pop("border-radius")  # the scale already scales the corners
            if (path in reflow or path == ()) and is_auto(b_node) and b_node.get("clipsContent") and abs(bh - sh) > 0.5:
                # A clipped frame that grows in this state (a dropdown opening): its height changes.
                change["css"]["height"] = "auto" if s_node.get("layoutSizingVertical") == "HUG" else f"{_num(sh)}px"
            if max(abs(dcx), abs(dcy)) > 0.25 or abs(scx - 1) > 0.01 or abs(scy - 1) > 0.01 or abs(rot) > 0.1:
                change["box"] = (dcx, dcy, scx, scy, rot, page_node.get("type"))
            if change["css"] or change["box"] or change["show"] is not None:
                out[pid] = change
        return out

    def _scaled(self, raw: dict, f: float) -> dict[str, dict]:
        """The state is the default scaled by f: the root scales about its centre; colours etc. still change."""
        out: dict[str, dict] = {}
        for path, r in raw.items():
            b, s_ = r["b"], r["s"]
            if b is None or s_ is None:
                continue
            sb, ss = node_style(b), node_style(s_)
            css = {k: ss.get(k, RESET.get(k, "initial")) for k in sorted(set(sb) | set(ss))
                   if sb.get(k) != ss.get(k) and k not in ("border-radius", "letter-spacing")}  # fmt: skip
            change = {"css": css, "box": None, "show": None, "center": (0.0, 0.0)}
            if path == ():
                change["box"] = (0.0, 0.0, f, f, 0.0, b.get("type"))
            if change["css"] or change["box"]:
                out[r["pid"]] = change
        return out

    @staticmethod
    def _skipped_props(fields: set[str]) -> set[str]:
        skip: set[str] = set()
        if "fills" in fields:
            skip |= {"background", "background-color", "background-image", "color"}
        if "strokes" in fields or "strokeWeight" in fields:
            skip |= {"border", "border-color"}
        if "effects" in fields:
            skip |= {"box-shadow", "filter", "backdrop-filter"}
        if "opacity" in fields:
            skip.add("opacity")
        return skip

    # ------------------------------------------------------------------ clicks
    def click_action(self, node: dict | None) -> dict | None:
        """What a click on this layer does: page / scroll / url / back / close / overlay / toggle."""
        for r in self.reactions(node):
            trig = (r.get("trigger") or {}).get("type")
            if trig not in ("ON_CLICK", "MOUSE_UP", "ON_PRESS", "MOUSE_DOWN"):
                continue
            actions = r.get("actions") or ([r["action"]] if r.get("action") else [])
            for a in actions:
                kind = a.get("type")
                if kind == "URL" and a.get("url"):
                    return {"kind": "url", "url": a["url"], "newTab": bool(a.get("openInNewTab"))}
                if kind == "BACK":
                    return {"kind": "back"}
                if kind == "CLOSE":
                    return {"kind": "close"}
                if kind != "NODE" or not a.get("destinationId"):
                    continue
                nav, dest = a.get("navigation"), a["destinationId"]
                if nav == "NAVIGATE":
                    return {"kind": "page", "frame": dest, "transition": a.get("transition")}
                if nav == "SCROLL_TO":
                    return {"kind": "scroll", "node": dest}
                if nav in ("OVERLAY", "SWAP"):
                    return {"kind": "overlay", "overlay": dest, "transition": a.get("transition"),
                            "position": a.get("overlayRelativePosition")}  # fmt: skip
                if nav == "CHANGE_TO":
                    return {"kind": "toggle"}
        return None

    def target_name(self, node_id: str) -> str:
        return (self.targets.get(node_id) or {}).get("name") or node_id

    def navigate_transitions(self) -> list[dict]:
        """Transitions of every "Navigate to" connection (for page-to-page animations)."""
        out = []
        for n in self.nodes.values():
            for r in n.get("reactions") or []:
                for a in r.get("actions") or ([r["action"]] if r.get("action") else []):
                    if a.get("type") == "NODE" and a.get("navigation") == "NAVIGATE" and a.get("transition"):
                        out.append(a["transition"])
        for c in self.components.values():
            for r in c.get("reactions") or []:
                for a in r.get("actions") or ([r["action"]] if r.get("action") else []):
                    if a.get("type") == "NODE" and a.get("navigation") == "NAVIGATE" and a.get("transition"):
                        out.append(a["transition"])
        return out


def transform_css(dx: float, dy: float, sx: float, sy: float, rot: float) -> str:
    parts = []
    if max(abs(dx), abs(dy)) > 0.25:
        parts.append(f"translate({_num(dx)}px, {_num(dy)}px)")
    if abs(rot) > 0.1:
        parts.append(f"rotate({_num(-rot)}deg)")  # Figma rotates counter-clockwise
    if abs(sx - 1) > 0.01 or abs(sy - 1) > 0.01:
        parts.append(f"scale({_num(sx)})" if abs(sx - sy) < 0.01 else f"scale({_num(sx)}, {_num(sy)})")
    return " ".join(parts) or "none"


# ----------------------------------------------------------------------------- behaviour (js/site.js)
SITE_JS = """/* Behaviour from the Figma prototype: toggles, auto-play, overlays, the phone menu and scroll-in
   animations. No dependencies; everything respects "reduce motion". */
(() => {
  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const all = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  // Click toggles (accordions, switches): aria-expanded / aria-pressed flips on click, Enter or Space.
  // With data-toggle-by, only the layers marked data-toggle-trigger (a menu icon, a close icon) toggle.
  all('[data-toggle]').forEach((el) => {
    const attr = el.hasAttribute('aria-expanded') ? 'aria-expanded' : 'aria-pressed';
    const by = el.hasAttribute('data-toggle-by');
    const triggers = by ? all('[data-toggle-trigger]', el) : [el];
    const flip = () => {
      const next = el.getAttribute(attr) === 'true' ? 'false' : 'true';
      el.setAttribute(attr, next);
      triggers.forEach((t) => t !== el && t.setAttribute(attr, next));
    };
    triggers.forEach((t) => {
      t.addEventListener('click', (ev) => {
        const inner = ev.target.closest('a[href], button');
        if (inner && inner !== t && t.contains(inner)) return; // a link inside keeps working
        flip();
      });
      t.addEventListener('keydown', (ev) => {
        if ((ev.key === 'Enter' || ev.key === ' ') && t.tagName !== 'A' && t.tagName !== 'BUTTON') {
          ev.preventDefault();
          flip();
        }
      });
    });
  });

  // Auto-advancing states ("After delay" in Figma): carousels, tickers. Paused on hover and focus.
  all('[data-autoplay]').forEach((el) => {
    if (reduce) return;
    const steps = Number(el.dataset.steps || 0);
    const delays = (el.dataset.delays || '').split(',').map(Number);
    let step = 0;
    let paused = false;
    const tick = () => {
      const wait = delays[Math.min(step, delays.length - 1)] || 3000;
      setTimeout(() => {
        if (!paused && !document.hidden) {
          step = (step + 1) % (steps + 1);
          el.dataset.step = String(step);
        }
        tick();
      }, wait);
    };
    el.addEventListener('mouseenter', () => { paused = true; });
    el.addEventListener('mouseleave', () => { paused = false; });
    el.addEventListener('focusin', () => { paused = true; });
    el.addEventListener('focusout', () => { paused = false; });
    tick();
  });

  // Whole cards that link somewhere but contain other links.
  all('[data-href]').forEach((el) => {
    el.style.cursor = 'pointer';
    el.addEventListener('click', (ev) => {
      if (ev.target.closest('a[href], button')) return;
      window.location.href = el.dataset.href;
    });
  });

  all('[data-action="back"]').forEach((el) => el.addEventListener('click', (ev) => {
    ev.preventDefault();
    window.history.back();
  }));

  // Overlays ("Open overlay" in Figma) are <dialog> elements.
  all('[data-open]').forEach((el) => el.addEventListener('click', (ev) => {
    const dialog = document.getElementById(el.dataset.open);
    if (!dialog || typeof dialog.showModal !== 'function') return;
    ev.preventDefault();
    dialog.showModal();
  }));
  all('dialog').forEach((dialog) => {
    dialog.addEventListener('click', (ev) => {
      if (ev.target.closest('[data-close]')) {
        ev.preventDefault();
        dialog.close();
      } else if (ev.target === dialog && dialog.hasAttribute('data-close-outside')) {
        dialog.close();
      }
    });
  });

  // Phone menu.
  all('[data-nav-toggle]').forEach((button) => {
    const header = button.closest('header, section, div');
    button.addEventListener('click', () => {
      const open = button.getAttribute('aria-expanded') !== 'true';
      button.setAttribute('aria-expanded', String(open));
      header?.classList.toggle('is-nav-open', open);
    });
  });

  // Scroll-in animations.
  const animated = all('[data-animate]');
  if (animated.length && !reduce && 'IntersectionObserver' in window) {
    const io = new IntersectionObserver((entries) => entries.forEach((entry) => {
      if (entry.isIntersecting) {
        entry.target.classList.add('is-in');
        io.unobserve(entry.target);
      }
    }), { threshold: 0.15, rootMargin: '0px 0px -5% 0px' });
    animated.forEach((el) => io.observe(el));
  } else {
    animated.forEach((el) => el.classList.add('is-in'));
  }
})();
"""
