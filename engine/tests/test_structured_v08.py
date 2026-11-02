"""Structured (Auto Layout) pages, from the first real Auto Layout file tested ("Minimal Landing Page",
99.6 after these fixes, 54 before): root side padding, a floating menu pill ignoring Auto Layout, FILL
siblings with different paddings, empty image frames with a fixed height, Fit images, blend modes."""

import re

from app.pipeline.generate import generate
from app.pipeline.ir import build_ir
from app.pipeline.layout import layout_page


def node(nid, name, ntype, parent, x, y, w, h, nodes, **props):
    n = {
        "id": nid, "name": name, "type": ntype, "parentId": parent, "children": [], "visible": True, "opacity": 1,
        "x": x, "y": y, "width": w, "height": h, "absoluteBoundingBox": {"x": x, "y": y, "width": w, "height": h},
        "fills": [], "strokes": [], "effects": [],
    }  # fmt: skip
    n.update(props)
    nodes[nid] = n
    if parent:
        nodes[parent]["children"].append(nid)
    return n


def al(mode, gap=0, pad=(0, 0, 0, 0), **kw):
    t, r, b, left = pad
    return {"layoutMode": mode, "itemSpacing": gap, "paddingTop": t, "paddingRight": r, "paddingBottom": b,
            "paddingLeft": left, "primaryAxisAlignItems": "MIN", "counterAxisAlignItems": "MIN", **kw}  # fmt: skip


def text(chars, size=16):
    seg = {"characters": chars, "fontName": {"family": "Inter", "style": "Regular"}, "fontSize": size,
           "fills": [{"type": "SOLID", "color": {"r": 0, "g": 0, "b": 0}}]}  # fmt: skip
    return {"characters": chars, "fontSize": size, "fontName": seg["fontName"], "textSegments": [seg],
            "textAutoResize": "WIDTH_AND_HEIGHT", "lineHeight": {"unit": "PIXELS", "value": size * 1.4},
            "fills": seg["fills"], "layoutSizingHorizontal": "HUG", "layoutSizingVertical": "HUG"}  # fmt: skip


IMG = [{"type": "IMAGE", "visible": True, "scaleMode": "FIT", "imageHash": "h1"}]


def page():
    nodes: dict = {}
    node("R", "Desktop", "FRAME", None, 0, 0, 1280, 900, nodes, **al("VERTICAL", pad=(0, 40, 0, 40)),
         fills=[{"type": "SOLID", "color": {"r": 1, "g": 1, "b": 1}}])  # fmt: skip
    node("S1", "Intro", "FRAME", "R", 40, 0, 1200, 400, nodes, **al("HORIZONTAL", 20),
         layoutSizingHorizontal="FILL", layoutSizingVertical="HUG")  # fmt: skip
    node("IMG", "Image", "FRAME", "S1", 40, 0, 590, 400, nodes, fills=IMG, blendMode="MULTIPLY",
         layoutSizingHorizontal="FILL", layoutSizingVertical="FIXED")  # fmt: skip
    node("Q", "Quote", "FRAME", "S1", 650, 0, 590, 400, nodes, **al("VERTICAL", pad=(0, 0, 0, 50)),
         layoutSizingHorizontal="FILL", layoutSizingVertical="FILL")  # fmt: skip
    node("QT", "Quote text", "TEXT", "Q", 700, 0, 300, 22, nodes, **text("“Area changed how we work.”"))
    node("S2", "01", "FRAME", "R", 40, 400, 1200, 500, nodes, **al("VERTICAL", 10, (40, 0, 40, 0)),
         layoutSizingHorizontal="FILL", layoutSizingVertical="HUG")  # fmt: skip
    node("T2", "01", "TEXT", "S2", 40, 440, 30, 34, nodes, **text("01", 24))
    node("NAV", "Nav Items", "FRAME", "R", 440, 16, 400, 60, nodes, **al("HORIZONTAL", 27, (20, 24, 20, 24)),
         layoutPositioning="ABSOLUTE", cornerRadius=100,
         fills=[{"type": "SOLID", "color": {"r": 1, "g": 1, "b": 1}, "opacity": 0.4}])  # fmt: skip
    for i, label in enumerate(("Benefits", "Specs", "How-to", "Contact")):
        node(f"N{i}", label, "TEXT", "NAV", 464 + i * 90, 36, 60, 20, nodes, **text(label, 14))
    frame = {"role": "desktop", "rootId": "R", "name": "Desktop", "width": 1280, "height": 900, "referenceAssets": []}
    return {"schemaVersion": "0.1", "source": {"fileName": "t", "pluginVersion": "0", "exportedAt": "now"},
            "frames": [frame], "nodes": nodes, "assets": [{"id": "img-h1", "kind": "image-fill", "format": "PNG",
            "nodeId": "IMG", "imageHash": "h1", "width": 10, "height": 10, "byteLength": 0}]}  # fmt: skip


def test_structured_page_details(tmp_path):
    from PIL import Image

    b = page()
    (tmp_path / "assets").mkdir()
    Image.new("RGB", (10, 10)).save(tmp_path / "assets" / "img-h1.png")
    p = layout_page(build_ir(b)["desktop"])
    assert len(p.sections) == 2 and any(lay.node["id"] == "NAV" for lay in p.sections[0].layers)
    generate(p, b, tmp_path / "assets", tmp_path / "out")
    html = (tmp_path / "out" / "index.html").read_text()
    css = (tmp_path / "out" / "css" / "styles.css").read_text()
    assert re.search(r'<nav[^>]*aria-label="Main">', html)  # the floating pill is the menu
    assert not re.search(r'class="\d', html)  # "01" never becomes a class starting with a digit
    assert "width: min(100% - 2 * var(--gutter), 1200px);" in css  # root padding: sections are a column
    assert css.count("flex: 1 1 590px;") == 2  # equal FILL widths even with different paddings
    assert "height: 400px;" in css  # the empty image frame keeps its fixed height
    assert "background-size: contain;" in css and "mix-blend-mode: multiply;" in css
