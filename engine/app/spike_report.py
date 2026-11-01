"""Spike 1 extraction report: how complete and convertible is a plugin bundle?

Usage:  python -m app.spike_report path/to/bundle.json [--md]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import UTC, datetime

KNOWN_TYPES = {
    "FRAME",
    "GROUP",
    "SECTION",
    "COMPONENT",
    "COMPONENT_SET",
    "INSTANCE",
    "TEXT",
    "RECTANGLE",
    "ELLIPSE",
    "LINE",
    "VECTOR",
    "STAR",
    "POLYGON",
    "BOOLEAN_OPERATION",
    "SLICE",
}
CONTAINER_TYPES = {"FRAME", "COMPONENT", "INSTANCE", "COMPONENT_SET"}
AUTO_LAYOUT_MODES = {"HORIZONTAL", "VERTICAL", "GRID"}
NORMAL_BLENDS = {None, "PASS_THROUGH", "NORMAL"}
GRADIENTS = {"GRADIENT_LINEAR", "GRADIENT_RADIAL", "GRADIENT_ANGULAR", "GRADIENT_DIAMOND"}
DEFAULT_NAME_RE = re.compile(r"^(Frame|Group|Rectangle|Ellipse|Vector|Text|Line|Polygon|Star|Union)\s*\d*$")
HOVER_TRIGGERS = {"ON_HOVER", "WHILE_HOVERING"}
SAMPLE = 50  # max node ids attached to a finding

BASE_FIELDS = [
    "id",
    "name",
    "type",
    "parentId",
    "children",
    "visible",
    "x",
    "y",
    "width",
    "height",
    "absoluteBoundingBox",
]
CONTAINER_FIELDS = ["layoutMode", "fills", "effects", "clipsContent"]
AUTO_LAYOUT_FIELDS = [
    "itemSpacing",
    "paddingTop",
    "paddingRight",
    "paddingBottom",
    "paddingLeft",
    "primaryAxisAlignItems",
    "counterAxisAlignItems",
    "primaryAxisSizingMode",
    "counterAxisSizingMode",
]
TEXT_FIELDS = [
    "characters",
    "fontName",
    "fontSize",
    "lineHeight",
    "letterSpacing",
    "textAlignHorizontal",
    "textAutoResize",
    "textSegments",
]
SHAPE_FIELDS = ["fills", "strokes"]
AUTO_LAYOUT_CHILD_FIELDS = ["layoutSizingHorizontal", "layoutSizingVertical", "layoutPositioning"]


def _required_fields(node: dict, parent: dict | None) -> list[str]:
    fields = list(BASE_FIELDS)
    t = node["type"]
    if t in CONTAINER_TYPES:
        fields += CONTAINER_FIELDS
        if node.get("layoutMode") in ("HORIZONTAL", "VERTICAL"):
            fields += AUTO_LAYOUT_FIELDS
    if t == "TEXT":
        fields += TEXT_FIELDS
    if t in ("RECTANGLE", "ELLIPSE"):
        fields += SHAPE_FIELDS
    if t == "INSTANCE":
        fields.append("mainComponentId")
    if parent and parent.get("layoutMode") in AUTO_LAYOUT_MODES:
        fields += AUTO_LAYOUT_CHILD_FIELDS
    return fields


def _reaction_parts(reaction: dict) -> tuple[str | None, list[dict]]:
    trigger = (reaction.get("trigger") or {}).get("type")
    actions = reaction.get("actions")
    if actions is None and reaction.get("action"):
        actions = [reaction["action"]]
    return trigger, [a for a in (actions or []) if isinstance(a, dict)]


def _alias_ids(value: object, out: set[str] | None = None) -> set[str]:
    """Variable ids referenced anywhere in a node (node-level, paint-level or text-segment bindings)."""
    out = set() if out is None else out
    if isinstance(value, dict):
        if value.get("type") == "VARIABLE_ALIAS" and isinstance(value.get("id"), str):
            out.add(value["id"])
        for v in value.values():
            _alias_ids(v, out)
    elif isinstance(value, list):
        for v in value:
            _alias_ids(v, out)
    return out


def _visible_children(nodes: dict, node: dict) -> list[dict]:
    return [nodes[c] for c in node.get("children", []) if c in nodes and nodes[c].get("visible", True)]


def _rel_box(node: dict, root: dict) -> tuple[float, float, float, float] | None:
    """Box relative to the root frame, from absoluteBoundingBox.

    Plain x/y are relative to the nearest FRAME (groups do not create a coordinate space),
    so absolute boxes are the only reliable geometry.
    """
    b, rb = node.get("absoluteBoundingBox"), root.get("absoluteBoundingBox")
    if not b or not rb:
        return None
    return b["x"] - rb["x"], b["y"] - rb["y"], b["width"], b["height"]


BAND_MIN_WIDTH_RATIO = 0.95
BAND_MIN_HEIGHT = 48


def detect_sections(nodes: dict, root: dict) -> list[dict]:
    """Splits a frame into vertical sections.

    Uses the root's direct children when they are already full-width frames; otherwise uses
    full-width background shapes (rectangles, frames, groups) as section boundaries. Every
    other top-level child is assigned to the band containing its vertical centre.
    """
    rbox = _rel_box(root, root)
    if not rbox:
        return []
    width, height = rbox[2], rbox[3]
    children = [c for c in _visible_children(nodes, root) if _rel_box(c, root)]
    starts = {0.0}
    for c in children:
        _, y, w, h = _rel_box(c, root)
        if w >= width * BAND_MIN_WIDTH_RATIO and h >= BAND_MIN_HEIGHT and c["type"] != "TEXT":
            starts.add(max(0.0, round(y, 1)))
    edges = sorted(s for s in starts if s < height) + [height]
    bands = [{"top": edges[i], "bottom": edges[i + 1], "nodeIds": []} for i in range(len(edges) - 1)]
    for c in children:
        _, y, _, h = _rel_box(c, root)
        cy = y + h / 2
        band = next((b for b in bands if b["top"] <= cy < b["bottom"]), bands[-1])
        band["nodeIds"].append(c["id"])

    sections = []
    for i, b in enumerate(bands):
        ids = set(b["nodeIds"])
        stack = list(ids)
        while stack:  # collect descendants to find the band's heading
            nid = stack.pop()
            for k in nodes[nid].get("children", []):
                if k in nodes and k not in ids:
                    ids.add(k)
                    stack.append(k)
        texts = [nodes[t] for t in ids if nodes[t]["type"] == "TEXT" and nodes[t].get("characters")]
        heading = max(
            texts,
            key=lambda t: t.get("fontSize") if isinstance(t.get("fontSize"), (int, float)) else 0,
            default=None,
        )
        sections.append(
            {
                "index": i,
                "top": b["top"],
                "height": round(b["bottom"] - b["top"], 1),
                "topLevelNodes": len(b["nodeIds"]),
                "heading": heading["characters"][:60] if heading else None,
                "nodeIds": b["nodeIds"],
            }
        )
    return sections


def build_report(bundle: dict, uploaded_assets: set[str] | None = None, bundle_id: str | None = None) -> dict:
    nodes: dict[str, dict] = bundle["nodes"]
    frames = bundle["frames"]
    roles = [f["role"] for f in frames]
    findings: list[dict] = []

    def finding(severity: str, code: str, message: str, node_ids: list[str] | None = None):
        item = {"severity": severity, "code": code, "message": message}
        if node_ids:
            item["nodeIds"] = node_ids[:SAMPLE]
            item["count"] = len(node_ids)
        findings.append(item)

    # --- node types
    types = Counter(n["type"] for n in nodes.values())
    unknown = sorted(t for t in types if t not in KNOWN_TYPES)

    # --- layout
    containers, auto_layout, non_auto = 0, 0, []
    groups_with_children, absolute_children, grid_layouts = [], 0, 0
    for n in nodes.values():
        if n.get("insideSvg") or n.get("exportAs") == "svg" or not n.get("visible", True):
            continue
        kids = _visible_children(nodes, n)
        # Groups count as containers too: they hold children but carry no layout data.
        if (n["type"] in CONTAINER_TYPES or n["type"] == "GROUP") and len(kids) >= 2:
            containers += 1
            if n.get("layoutMode") in AUTO_LAYOUT_MODES:
                auto_layout += 1
            else:
                non_auto.append(n["id"])
        if n.get("layoutMode") == "GRID":
            grid_layouts += 1
        if n["type"] == "GROUP" and len(kids) >= 2:
            groups_with_children.append(n["id"])
        if n.get("layoutPositioning") == "ABSOLUTE":
            absolute_children += 1
    coverage = auto_layout / containers if containers else 1.0

    # --- text
    text_nodes = [n for n in nodes.values() if n["type"] == "TEXT"]
    mixed_style = [n["id"] for n in text_nodes if len(n.get("textSegments") or []) > 1]
    fixed_size = [n["id"] for n in text_nodes if n.get("textAutoResize") == "NONE" and n.get("visible", True)]
    auto_resize = Counter(n.get("textAutoResize", "UNKNOWN") for n in text_nodes)
    missing_font_nodes = [d["nodeId"] for d in bundle.get("diagnostics", []) if d["code"] == "MISSING_FONT"]

    # --- visuals
    image_fills, gradients, blends, effects = 0, 0, [], Counter()
    layer_blur_non_image = []
    corner_nodes = 0
    for n in nodes.values():
        fills = n.get("fills") if isinstance(n.get("fills"), list) else []
        has_image = any(p.get("type") == "IMAGE" for p in fills)
        image_fills += sum(1 for p in fills if p.get("type") == "IMAGE")
        gradients += sum(1 for p in fills if p.get("type") in GRADIENTS)
        if n.get("blendMode") not in NORMAL_BLENDS:
            blends.append(n["id"])
        for e in n.get("effects") or []:
            if isinstance(e, dict) and e.get("visible", True):
                effects[e.get("type", "UNKNOWN")] += 1
                if e.get("type") == "LAYER_BLUR" and not has_image:
                    layer_blur_non_image.append(n["id"])
        r = n.get("cornerRadius")
        if r == "MIXED" or (isinstance(r, (int, float)) and r > 0):
            corner_nodes += 1

    # --- variables & components
    bound_nodes, used_variables = [], set()
    for n in nodes.values():
        ids = _alias_ids(n)
        if ids:
            bound_nodes.append(n["id"])
            used_variables |= ids
    instances = [n for n in nodes.values() if n["type"] == "INSTANCE"]
    missing_main = [n["id"] for n in instances if not n.get("mainComponentId")]

    # --- interactions & motion
    with_reactions, triggers, actions, navigations, transitions = (
        [],
        Counter(),
        Counter(),
        Counter(),
        Counter(),
    )
    durations: list[float] = []
    hover_variant = []
    for n in nodes.values():
        reactions = n.get("reactions") or []
        if not reactions:
            continue
        with_reactions.append(n["id"])
        for r in reactions:
            trigger, acts = _reaction_parts(r)
            triggers[trigger or "UNKNOWN"] += 1
            for a in acts:
                actions[a.get("type", "UNKNOWN")] += 1
                if a.get("navigation"):
                    navigations[a["navigation"]] += 1
                transition = a.get("transition") or {}
                if transition.get("type"):
                    transitions[transition["type"]] += 1
                if isinstance(transition.get("duration"), (int, float)):
                    durations.append(transition["duration"])
                if trigger in HOVER_TRIGGERS and a.get("navigation") == "CHANGE_TO":
                    hover_variant.append(n["id"])
    motion_nodes = [n["id"] for n in nodes.values() if n.get("motion")]

    # --- responsive pairing
    sections: dict[str, list[dict]] = {}
    top_level: dict[str, list[dict]] = {}
    for f in frames:
        root = nodes.get(f["rootId"])
        top_level[f["role"]] = _visible_children(nodes, root) if root else []
        sections[f["role"]] = detect_sections(nodes, root) if root else []

    def _keys(role: str) -> list[str]:
        # Match sections by heading text, falling back to the names of their top-level layers.
        out = []
        for s in sections.get(role, []):
            if s["heading"]:
                out.append(s["heading"].strip().lower())
            elif len(s["nodeIds"]) == 1:
                out.append(nodes[s["nodeIds"][0]]["name"].strip().lower())
        return out

    desktop_keys, mobile_keys = _keys("desktop"), set(_keys("mobile"))
    name_match = (
        sum(1 for k in desktop_keys if k in mobile_keys) / len(desktop_keys) if desktop_keys and mobile_keys else 0.0
    )
    default_named = [s["id"] for ss in top_level.values() for s in ss if DEFAULT_NAME_RE.match(s["name"])]
    flat_roles = [
        role
        for role, kids in top_level.items()
        if len(kids) > len(sections.get(role, [])) * 2 and len(sections.get(role, [])) > 1
    ]

    # --- field coverage
    checked, complete, missing_by_field = 0, 0, Counter()
    for n in nodes.values():
        if n.get("insideSvg"):
            continue
        parent = nodes.get(n["parentId"]) if n.get("parentId") else None
        missing = [f for f in _required_fields(n, parent) if f not in n]
        checked += 1
        if not missing:
            complete += 1
        missing_by_field.update(missing)

    # --- assets
    declared = {a["id"] for a in bundle["assets"]}
    uploaded = uploaded_assets if uploaded_assets is not None else set()
    missing_assets = sorted(declared - uploaded) if uploaded_assets is not None else []
    asset_kinds = Counter(a["kind"] for a in bundle["assets"])

    # --- findings (ordered by severity later)
    if "mobile" not in roles:
        finding("error", "MISSING_MOBILE_FRAME", "No mobile frame selected; v1 needs desktop + mobile.")
    if missing_assets:
        finding("error", "MISSING_ASSETS", f"{len(missing_assets)} declared assets were not uploaded.")
    if missing_font_nodes:
        finding("error", "MISSING_FONTS", "Some text uses fonts that are not available.", missing_font_nodes)
    if coverage < 0.8:
        finding(
            "warning",
            "LOW_AUTO_LAYOUT",
            f"Auto Layout covers {coverage:.0%} of containers (target 80%). Layout will be inferred.",
            non_auto,
        )
    if groups_with_children:
        finding(
            "warning",
            "GROUPS_WITH_CHILDREN",
            "Groups carry no layout data; convert them to frames with Auto Layout.",
            groups_with_children,
        )
    if fixed_size:
        finding(
            "warning",
            "FIXED_SIZE_TEXT",
            "Fixed-size text boxes may clip or wrap differently in browsers.",
            fixed_size,
        )
    if blends:
        finding("warning", "NON_NORMAL_BLEND", "Blend modes other than Normal may not match in CSS.", blends)
    if (
        len(sections.get("desktop", []))
        and "mobile" in sections
        and len(sections["desktop"]) != len(sections["mobile"])
    ):
        finding(
            "warning",
            "SECTION_COUNT_MISMATCH",
            f"Desktop has {len(sections['desktop'])} sections, mobile has {len(sections['mobile'])}.",
        )
    for role in flat_roles:
        finding(
            "info",
            "FLAT_LAYOUT",
            f"The {role} frame has {len(top_level[role])} loose top-level layers; "
            f"{len(sections[role])} sections were detected from full-width background shapes. "
            "Wrapping each section in its own frame improves results.",
        )
    if missing_main:
        finding("warning", "MISSING_MAIN_COMPONENT", "Instances without a main component.", missing_main)
    unresolved = sorted(used_variables - {v["id"] for v in bundle.get("variables", [])})
    if unresolved:
        finding(
            "warning",
            "UNRESOLVED_VARIABLES",
            f"{len(unresolved)} bound variables are missing from the bundle; raw values will be used.",
        )
    if layer_blur_non_image:
        finding(
            "info",
            "LAYER_BLUR",
            "Layer blur on non-image layers maps to CSS filter: blur().",
            layer_blur_non_image,
        )
    if default_named:
        finding(
            "info",
            "DEFAULT_SECTION_NAMES",
            "Sections with default names make poor class names.",
            default_named,
        )
    if not with_reactions and not motion_nodes:
        finding("info", "NO_INTERACTIONS", "No prototype interactions or Motion panel data found.")
    if unknown:
        finding("info", "UNKNOWN_NODE_TYPES", f"Node types not handled yet: {', '.join(unknown)}.")
    diag_codes = Counter(
        (d["code"], d.get("severity", "warning")) for d in bundle.get("diagnostics", []) if d["code"] != "MISSING_FONT"
    )
    for (code, severity), count in sorted(diag_codes.items()):
        ids = [d["nodeId"] for d in bundle["diagnostics"] if d["code"] == code and d.get("nodeId")]
        first = next(d["message"] for d in bundle["diagnostics"] if d["code"] == code)
        finding(severity, f"PLUGIN_{code}", f"{count}× from plugin. First: {first}", ids or None)

    order = {"error": 0, "warning": 1, "info": 2}
    findings.sort(key=lambda f: order.get(f["severity"], 3))

    return {
        "bundleId": bundle_id,
        "generatedAt": datetime.now(UTC).isoformat(),
        "source": bundle["source"],
        "summary": {
            "nodeCount": len(nodes),
            "frames": [
                {"role": f["role"], "name": f["name"], "width": f["width"], "height": f["height"]} for f in frames
            ],
            "assetDeclared": len(declared),
            "assetUploaded": len(declared & uploaded) if uploaded_assets is not None else 0,
            "assetKinds": dict(asset_kinds),
            "diagnosticCounts": dict(Counter(d.get("severity", "warning") for d in bundle.get("diagnostics", []))),
        },
        "nodeTypes": dict(types.most_common()),
        "layout": {
            "containers": containers,
            "autoLayoutContainers": auto_layout,
            "autoLayoutCoverage": round(coverage, 4),
            "groupsWithChildren": len(groups_with_children),
            "absoluteChildren": absolute_children,
            "gridLayouts": grid_layouts,
        },
        "text": {
            "textNodes": len(text_nodes),
            "mixedStyleNodes": len(mixed_style),
            "fixedSizeNodes": len(fixed_size),
            "autoResize": dict(auto_resize),
            "fonts": bundle.get("fonts", []),
            "missingFontNodes": len(missing_font_nodes),
        },
        "visuals": {
            "imageFills": image_fills,
            "gradients": gradients,
            "nonNormalBlend": len(blends),
            "effectsByType": dict(effects),
            "roundedNodes": corner_nodes,
        },
        "variables": {
            "variables": len(bundle.get("variables", [])),
            "collections": len(bundle.get("variableCollections", [])),
            "nodesWithBoundVariables": len(bound_nodes),
            "variablesUsed": len(used_variables),
            "unresolvedVariables": len(used_variables - {v["id"] for v in bundle.get("variables", [])}),
        },
        "components": {
            "instances": len(instances),
            "mainComponents": len(bundle.get("components", {})),
            "missingMainComponent": len(missing_main),
        },
        "interactions": {
            "nodesWithReactions": len(with_reactions),
            "triggers": dict(triggers),
            "actions": dict(actions),
            "navigation": dict(navigations),
            "transitions": dict(transitions),
            "hoverVariantSwaps": len(hover_variant),
            "transitionDurationsSec": sorted(set(durations)),
        },
        "motion": {"nodesWithMotionData": len(motion_nodes)},
        "responsive": {
            "sectionsByRole": {role: len(s) for role, s in sections.items()},
            "topLevelLayersByRole": {role: len(k) for role, k in top_level.items()},
            "sectionNameMatchRatio": round(name_match, 4),
            "sections": {
                role: [{k: v for k, v in s.items() if k != "nodeIds"} for s in ss] for role, ss in sections.items()
            },
        },
        "fieldCoverage": {
            "checked": checked,
            "complete": complete,
            "ratio": round(complete / checked, 4) if checked else 0.0,
            "missingByField": dict(missing_by_field.most_common()),
        },
        "findings": findings,
    }


def to_markdown(r: dict) -> str:
    s = r["summary"]
    frames = ", ".join(f"{f['role']} {int(f['width'])}px" for f in s["frames"])
    lines = [
        f"# Extraction report — {r['source']['fileName']}",
        "",
        f"Bundle: `{r['bundleId'] or '(local file)'}` · generated {r['generatedAt']}",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Layers | {s['nodeCount']} |",
        f"| Frames | {frames} |",
        f"| Auto Layout coverage | {r['layout']['autoLayoutCoverage']:.0%} "
        f"({r['layout']['autoLayoutContainers']}/{r['layout']['containers']}) |",
        f"| Field coverage | {r['fieldCoverage']['ratio']:.0%} |",
        f"| Text layers (mixed style) | {r['text']['textNodes']} ({r['text']['mixedStyleNodes']}) |",
        f"| Fonts | {len(r['text']['fonts'])} |",
        f"| Variables / bound layers | {r['variables']['variables']} / {r['variables']['nodesWithBoundVariables']} |",
        f"| Instances (missing main) | {r['components']['instances']} ({r['components']['missingMainComponent']}) |",
        f"| Layers with interactions | {r['interactions']['nodesWithReactions']} |",
        f"| Hover variant swaps | {r['interactions']['hoverVariantSwaps']} |",
        f"| Motion panel layers | {r['motion']['nodesWithMotionData']} |",
        f"| Assets declared | {s['assetDeclared']} {s['assetKinds']} |",
        "",
        "## Sections",
        "",
        "| Frame | # | Top (px) | Height (px) | Top-level layers | Heading |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for role, secs in r["responsive"]["sections"].items():
        for sec in secs:
            heading = (sec["heading"] or "–").replace("\n", " ")
            lines.append(
                f"| {role} | {sec['index'] + 1} | {sec['top']:g} | {sec['height']:g} | "
                f"{sec['topLevelNodes']} | {heading} |"
            )
    lines += ["", "## Findings", ""]
    if not r["findings"]:
        lines.append("None.")
    for f in r["findings"]:
        count = f" ({f['count']})" if "count" in f else ""
        lines.append(f"- **{f['severity'].upper()} {f['code']}**{count}: {f['message']}")
    if r["fieldCoverage"]["missingByField"]:
        lines += ["", "## Missing fields", ""]
        lines += [f"- `{k}`: {v}" for k, v in r["fieldCoverage"]["missingByField"].items()]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("bundle", help="path to a bundle JSON file exported by the plugin")
    parser.add_argument("--md", action="store_true", help="print Markdown instead of JSON")
    args = parser.parse_args(argv)
    with open(args.bundle, encoding="utf-8") as fh:
        bundle = json.load(fh)
    report = build_report(bundle)
    sys.stdout.write(to_markdown(report) if args.md else json.dumps(report, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
