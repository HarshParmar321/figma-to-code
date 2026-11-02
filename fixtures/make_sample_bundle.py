"""Generates fixtures/sample-bundle.json: a small landing page (desktop + mobile) shaped like real plugin output.

Run:  python fixtures/make_sample_bundle.py
"""

import json
from pathlib import Path

INTER = {"family": "Inter", "style": "Regular"}
INTER_BOLD = {"family": "Inter", "style": "Bold"}
WHITE = [{"type": "SOLID", "visible": True, "opacity": 1, "blendMode": "NORMAL",
          "color": {"r": 1, "g": 1, "b": 1}}]
BRAND = [{"type": "SOLID", "visible": True, "opacity": 1, "blendMode": "NORMAL",
          "color": {"r": 0.31, "g": 0.27, "b": 0.9},
          "boundVariables": {"color": {"type": "VARIABLE_ALIAS", "id": "VariableID:1:1"}}}]
CARD = [{"type": "SOLID", "visible": True, "opacity": 1, "blendMode": "NORMAL",
         "color": {"r": 0.95, "g": 0.95, "b": 0.97}}]
IMAGE_HASH = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0"

nodes: dict[str, dict] = {}


def add(node_id, name, ntype, parent, x, y, w, h, abs_x, abs_y, **props):
    node = {
        "id": node_id, "name": name, "type": ntype, "parentId": parent, "children": [],
        "visible": True, "opacity": 1, "blendMode": "PASS_THROUGH", "rotation": 0,
        "x": x, "y": y, "width": w, "height": h,
        "absoluteBoundingBox": {"x": abs_x, "y": abs_y, "width": w, "height": h},
    }
    if ntype in ("FRAME", "COMPONENT", "INSTANCE"):
        node.update({"layoutMode": "NONE", "fills": [], "effects": [], "clipsContent": False})
    if ntype in ("RECTANGLE", "ELLIPSE"):
        node.update({"fills": [], "strokes": []})
    node.update(props)
    nodes[node_id] = node
    if parent:
        nodes[parent]["children"].append(node_id)
        if nodes[parent].get("layoutMode") in ("HORIZONTAL", "VERTICAL"):
            node.setdefault("layoutSizingHorizontal", "HUG")
            node.setdefault("layoutSizingVertical", "HUG")
            node.setdefault("layoutPositioning", "AUTO")
    return node


def auto(direction, gap, pad, primary="MIN", counter="MIN"):
    return {
        "layoutMode": direction, "itemSpacing": gap,
        "paddingTop": pad[0], "paddingRight": pad[1], "paddingBottom": pad[2], "paddingLeft": pad[3],
        "primaryAxisAlignItems": primary, "counterAxisAlignItems": counter,
        "primaryAxisSizingMode": "FIXED", "counterAxisSizingMode": "AUTO",
    }


def text(chars, font, size, line, segments=None, resize="WIDTH_AND_HEIGHT"):
    return {
        "characters": chars, "fontName": font if not segments else "MIXED", "fontSize": size,
        "lineHeight": {"unit": "PIXELS", "value": line}, "letterSpacing": {"unit": "PERCENT", "value": 0},
        "textAlignHorizontal": "LEFT", "textAutoResize": resize,
        "textSegments": segments or [{"characters": chars, "start": 0, "end": len(chars), "fontName": font,
                                      "fontSize": size}],
    }


def page(prefix, role, width, height, ox):
    p = prefix
    hero_dir = "HORIZONTAL" if role == "desktop" else "VERTICAL"
    pad = 80 if role == "desktop" else 16
    title_size, title_line = (64, 72) if role == "desktop" else (36, 44)

    add(f"{p}:1", f"Home / {role.title()}", "FRAME", None, ox, 0, width, height, ox, 0,
        **auto("VERTICAL", 0, (0, 0, 0, 0)), fills=WHITE)
    add(f"{p}:2", "Header", "FRAME", f"{p}:1", 0, 0, width, 72, ox, 0,
        **auto("HORIZONTAL", 24, (16, pad, 16, pad), primary="SPACE_BETWEEN", counter="CENTER"))
    add(f"{p}:3", "Logo", "FRAME", f"{p}:2", pad, 20, 32, 32, ox + pad, 20, exportAs="svg")
    add(f"{p}:4", "Vector", "VECTOR", f"{p}:3", 0, 0, 32, 32, ox + pad, 20, fills=BRAND, insideSvg=True)
    add(f"{p}:5", "Nav", "TEXT", f"{p}:2", width - pad - 200, 26, 200, 20, ox + width - pad - 200, 26,
        **text("Features  Pricing  Contact", INTER, 14, 20))

    add(f"{p}:10", "Hero", "FRAME", f"{p}:1", 0, 72, width, 560, ox, 72,
        **auto(hero_dir, 48, (100 if role == "desktop" else 48, pad, 100 if role == "desktop" else 48, pad)))
    # Desktop: text column and image side by side, cards in a row. Mobile: everything stacked in a
    # 358px column (16px side padding), the way a real mobile frame is drawn.
    mob = role != "desktop"
    cw = width - 2 * pad if mob else 560
    title_h = title_line * 2
    body_h = 84 if mob else 56
    body_y = title_h + 24 if mob else 168
    btn_y = body_y + body_h + 24 if mob else 248
    content_h = btn_y + 48 if mob else 320
    top = 72 + (48 if mob else 100)
    add(f"{p}:11", "Content", "FRAME", f"{p}:10", pad, top - 72, cw, content_h, ox + pad, top,
        **auto("VERTICAL", 24, (0, 0, 0, 0)))
    add(f"{p}:12", "Title", "TEXT", f"{p}:11", 0, 0, cw, title_h, ox + pad, top,
        **text("Build better experiences", INTER_BOLD, title_size, title_line, resize="HEIGHT"))
    body = "Design in Figma, ship verified code."
    add(f"{p}:13", "Body", "TEXT", f"{p}:11", 0, body_y, cw, body_h, ox + pad, top + body_y,
        **text(body, INTER, 18, 28, resize="HEIGHT", segments=[
            {"characters": "Design in Figma, ", "start": 0, "end": 17, "fontName": INTER, "fontSize": 18},
            {"characters": "ship verified code.", "start": 17, "end": len(body), "fontName": INTER_BOLD,
             "fontSize": 18},
        ]))
    add(f"{p}:14", "Button", "INSTANCE", f"{p}:11", 0, btn_y, 160, 48, ox + pad, top + btn_y,
        **auto("HORIZONTAL", 8, (12, 24, 12, 24), primary="CENTER", counter="CENTER"),
        fills=BRAND, cornerRadius=8, mainComponentId="50:1",
        variantProperties={"State": "Default"},
        reactions=[{"trigger": {"type": "ON_HOVER"}, "actions": [{
            "type": "NODE", "destinationId": "50:2", "navigation": "CHANGE_TO",
            "transition": {"type": "SMART_ANIMATE", "easing": {"type": "EASE_OUT"}, "duration": 0.2}}]}],
        motion={"preset": "fade-up", "trigger": {"type": "in-view", "threshold": 0.2, "once": True},
                "duration": 600, "delay": 200})
    add(f"{p}:15", "Label", "TEXT", f"{p}:14", 24, 12, 112, 24, ox + pad + 24, top + btn_y + 12,
        **text("Get started", INTER_BOLD, 16, 24), fills=WHITE)
    img_w, img_h = (cw, round(cw * 360 / 672)) if mob else (672, 360)
    img_x, img_y = (pad, top + content_h + 48) if mob else (688, 172)
    add(f"{p}:16", "Hero image", "RECTANGLE", f"{p}:10", img_x, img_y - 72, img_w, img_h, ox + img_x, img_y,
        cornerRadius=16, fills=[{"type": "IMAGE", "visible": True, "scaleMode": "FILL",
                                 "imageHash": IMAGE_HASH}],
        effects=[{"type": "DROP_SHADOW", "visible": True, "radius": 24, "offset": {"x": 0, "y": 8},
                  "color": {"r": 0, "g": 0, "b": 0, "a": 0.12}, "spread": 0}])
    hero_h = (img_y + img_h + 48 - 72) if mob else 560
    nodes[f"{p}:10"]["height"] = hero_h
    nodes[f"{p}:10"]["absoluteBoundingBox"]["height"] = hero_h

    # A group (no layout data) to exercise the GROUPS_WITH_CHILDREN finding.
    fy = 72 + hero_h
    card_w = cw if mob else 300
    second = (pad, fy + 16 + 332) if mob else (pad + 332, fy)
    first = (pad, fy + 16) if mob else (pad, fy)
    gh = 664 + 16 if mob else 300
    add(f"{p}:20", "Features", "GROUP", f"{p}:1", 0, fy, width, gh, ox, fy)
    add(f"{p}:21", "Card 1", "RECTANGLE", f"{p}:20", first[0], first[1] - fy, card_w, 300, ox + first[0], first[1],
        cornerRadius=12, fills=CARD)
    add(f"{p}:22", "Card 2", "RECTANGLE", f"{p}:20", second[0], second[1] - fy, card_w, 300, ox + second[0],
        second[1], cornerRadius=12, fills=CARD)

    add(f"{p}:30", "Footer", "FRAME", f"{p}:1", 0, height - 120, width, 120, ox, height - 120,
        **auto("HORIZONTAL", 16, (40, pad, 40, pad)))
    add(f"{p}:31", "Copyright", "TEXT", f"{p}:30", pad, 50, 300, 20, ox + pad, height - 70,
        **text("© 2026 Example Inc.", INTER, 14, 20))


page("10", "desktop", 1440, 1100, 0)
page("20", "mobile", 390, 1500, 1600)
_m_bottom = nodes["20:20"]["absoluteBoundingBox"]["y"] + nodes["20:20"]["absoluteBoundingBox"]["height"]
assert _m_bottom <= 1380, _m_bottom  # mobile footer starts at 1380

bundle = {
    "schemaVersion": "0.1",
    "source": {
        "fileKey": None, "fileName": "Sample Landing", "pageId": "0:1", "pageName": "Page 1",
        "pluginVersion": "0.1.0", "editorType": "figma", "exportedAt": "2026-11-01T09:00:00.000Z",
    },
    "frames": [
        {"role": "desktop", "rootId": "10:1", "name": "Home / Desktop", "width": 1440, "height": 1100,
         "referenceAssets": ["ref-desktop-1x", "ref-desktop-2x"]},
        {"role": "mobile", "rootId": "20:1", "name": "Home / Mobile", "width": 390, "height": 1500,
         "referenceAssets": ["ref-mobile-1x", "ref-mobile-2x"]},
    ],
    "nodes": nodes,
    "variables": [{
        "id": "VariableID:1:1", "name": "color/brand/500", "resolvedType": "COLOR",
        "collectionId": "VariableCollectionId:1:0",
        "valuesByMode": {"1:0": {"r": 0.31, "g": 0.27, "b": 0.9, "a": 1}}, "scopes": ["ALL_FILLS"],
        "codeSyntax": {}, "remote": False,
    }],
    "variableCollections": [{
        "id": "VariableCollectionId:1:0", "name": "Tokens", "defaultModeId": "1:0",
        "modes": [{"modeId": "1:0", "name": "Light"}],
    }],
    "components": {"50:1": {"id": "50:1", "name": "State=Default", "key": "abc123", "componentSetId": "50:0",
                            "componentSetName": "Button", "variantProperties": {"State": "Default"},
                            "remote": False}},
    "fonts": [{"family": "Inter", "style": "Regular", "usageCount": 6},
              {"family": "Inter", "style": "Bold", "usageCount": 6}],
    "assets": [
        {"id": "ref-desktop-1x", "kind": "reference", "format": "PNG", "nodeId": "10:1", "scale": 1,
         "width": 1440, "height": 1100, "byteLength": 8},
        {"id": "ref-desktop-2x", "kind": "reference", "format": "PNG", "nodeId": "10:1", "scale": 2,
         "width": 2880, "height": 2200, "byteLength": 8},
        {"id": "ref-mobile-1x", "kind": "reference", "format": "PNG", "nodeId": "20:1", "scale": 1,
         "width": 390, "height": 1500, "byteLength": 8},
        {"id": "ref-mobile-2x", "kind": "reference", "format": "PNG", "nodeId": "20:1", "scale": 2,
         "width": 780, "height": 3000, "byteLength": 8},
        {"id": "img-a1b2c3d4e5f6a7b8c9d0", "kind": "image-fill", "format": "JPG", "nodeId": "10:16",
         "imageHash": IMAGE_HASH, "width": 1344, "height": 720, "byteLength": 8},
        {"id": "svg-10_3", "kind": "svg", "format": "SVG", "nodeId": "10:3", "width": 32, "height": 32,
         "byteLength": 8},
        {"id": "svg-20_3", "kind": "svg", "format": "SVG", "nodeId": "20:3", "width": 32, "height": 32,
         "byteLength": 8},
    ],
    "diagnostics": [],
}

out = Path(__file__).with_name("sample-bundle.json")
out.write_text(json.dumps(bundle, indent=2) + "\n", encoding="utf-8")
print(f"Wrote {out} ({len(nodes)} nodes)")
