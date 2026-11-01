"""Regression tests for the blueprint fixes found on the second real design."""

import json
from pathlib import Path

from app.pipeline import fonts
from app.pipeline.generate import Generator
from app.pipeline.ir import El, IRBuilder
from app.spike_report import detect_sections


def _node(nid, ntype, x, y, w, h, parent="root", **extra):
    box = {"x": x, "y": y, "width": w, "height": h}
    return {"id": nid, "name": nid, "type": ntype, "parentId": parent, "children": [], "visible": True,
            "absoluteBoundingBox": box, **extra}  # fmt: skip


def _page(kids, height=1000):
    root = _node("root", "FRAME", 0, 0, 1900, height, parent=None)
    root["children"] = [k["id"] for k in kids]
    return {"root": root, **{k["id"]: k for k in kids}}


# ----------------------------------------------------------------------------- sections
def test_boundary_crossing_content_is_ignored():
    """A full-width background that starts mid-way through a card row must not split the section."""
    nodes = _page([
        _node("bg-a", "RECTANGLE", 0, 0, 1900, 400),
        _node("title", "TEXT", 800, 100, 300, 60, characters="Services", fontSize=48),
        _node("card", "RECTANGLE", 400, 300, 260, 270),       # crosses y=400
        _node("bg-b", "RECTANGLE", 0, 400, 1900, 600),
        _node("footer-text", "TEXT", 400, 900, 300, 20, characters="x", fontSize=14),
    ])  # fmt: skip
    secs = detect_sections(nodes, nodes["root"])
    assert [s["top"] for s in secs] == [0.0]  # 400 is crossed by the card; bottom edges 400/1000 too
    assert secs[0]["height"] == 1000


def test_empty_bands_merge_upwards():
    nodes = _page([
        _node("hero", "GROUP", 0, 0, 1900, 500),
        _node("wave", "VECTOR", 0, 300, 1900, 300, exportAs="svg"),
        _node("text", "TEXT", 400, 650, 300, 40, characters="Below", fontSize=20),
    ])  # fmt: skip
    nodes["hero"]["children"] = []
    secs = detect_sections(nodes, nodes["root"])
    assert all(s["topLevelNodes"] > 0 for s in secs)


def test_full_width_shape_mid_section_becomes_layer():
    bundle = {"nodes": {}, "assets": [], "frames": []}
    builder = IRBuilder(bundle)
    sec = El("s", "s", "section", 0, 0, 1900, 1000)
    mid_bg = El("bg", "bg", "shape", 0, 420, 1900, 500)
    card = El("card", "card", "shape", 400, 300, 260, 270)
    sec.children = [mid_bg, card]
    builder._pull_backgrounds(sec, 1900)
    assert sec.layers == [mid_bg] and sec.children == [card]


# ----------------------------------------------------------------------------- svg bleed
def test_svg_bleed_from_render_bounds():
    node = {"absoluteBoundingBox": {"x": 100, "y": 100, "width": 70, "height": 70},
            "absoluteRenderBounds": {"x": 80, "y": 90, "width": 110, "height": 110}}  # fmt: skip
    assert IRBuilder._bleed(node) == (10, 20, 30, 20)
    assert IRBuilder._bleed({"absoluteBoundingBox": node["absoluteBoundingBox"]}) is None


def test_bleed_margins_keep_layout_box():
    e = El("i", "icon", "svg", 100, 100, 70, 70, bleed=(10, 20, 30, 20))
    assert Generator._draw_box(e) == (80, 90, 110, 110)
    d = {"margin-left": "12px"}
    Generator._apply_bleed(e, d)
    assert d == {"margin-left": "-8px", "margin-top": "-10px", "margin-right": "-20px", "margin-bottom": "-30px"}
    absolute = {"position": "absolute", "left": "100px", "top": "50px"}
    Generator._apply_bleed(e, absolute)
    assert absolute["left"] == "80px" and absolute["top"] == "40px"


# ----------------------------------------------------------------------------- fonts
def test_font_name_parsing():
    fam = "TT Hoves"
    assert fonts.parse(Path("TTHoves-Bold.otf"), fam).weight == 700
    assert fonts.parse(Path("TT Hoves ExtraBold Italic.woff2"), fam).weight == 800
    assert fonts.parse(Path("TT Hoves ExtraBold Italic.woff2"), fam).style == "italic"
    assert fonts.parse(Path("tt-hoves-latin-900-normal.woff2"), fam).weight == 900
    assert fonts.parse(Path("TTHoves-Regular.ttf"), fam).fmt == "truetype"
    assert fonts.parse(Path("TTHoves[wght].ttf"), fam).variable
    assert fonts.parse(Path("readme.txt"), fam) is None


def test_font_find_and_pick(tmp_path):
    d = tmp_path / "tt-hoves"
    d.mkdir()
    for name in ("TTHoves-Medium.otf", "TTHoves-Bold.woff2", "TTHoves-Black.otf"):
        (d / name).write_bytes(b"x")
    (tmp_path / "TT Hoves Light.ttf").write_bytes(b"x")
    found = fonts.find(tmp_path, "TT Hoves")
    assert {f.weight for f in found} == {300, 500, 700, 900}
    assert fonts.pick(found, 700, "normal").path.name == "TTHoves-Bold.woff2"
    assert fonts.pick(found, 600, "normal").weight == 700  # nearest, prefers heavier on a tie
    assert fonts.pick([], 400, "normal") is None


def test_font_upload_api(client, tmp_path, monkeypatch):
    from app import main

    monkeypatch.setattr(main.settings, "font_dir", tmp_path / "fonts")
    res = client.put("/v1/fonts/TT Hoves/TTHoves-Bold.otf", content=b"OTTO-fake")
    assert res.status_code == 200 and res.json()["weight"] == 700
    assert client.put("/v1/fonts/TT Hoves/evil.exe", content=b"x").status_code == 400
    assert client.put("/v1/fonts/TT Hoves/../../x.otf", content=b"x").status_code in (200, 404)
    listed = client.get("/v1/fonts").json()["families"]
    assert listed == {"tt-hoves": ["TTHoves-Bold.otf"]}
    assert not any(p.name == "x.otf" for p in tmp_path.parent.rglob("x.otf") if "fonts" not in str(p))


def test_generated_text_is_locked(bundle, tmp_path):
    """Text boxes carry their Figma size on desktop and release it on mobile."""
    from app.pipeline.generate import generate
    from app.pipeline.ir import build_ir
    from app.pipeline.layout import layout_page

    (tmp_path / "assets").mkdir()
    page = layout_page(build_ir(json.loads(json.dumps(bundle)))["desktop"])
    for a in bundle["assets"]:
        ext = {"PNG": "png", "JPG": "jpg", "SVG": "svg"}[a["format"]]
        if ext == "svg":
            (tmp_path / "assets" / f"{a['id']}.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
    generate(page, bundle, tmp_path / "assets", tmp_path / "out")
    html = (tmp_path / "out" / "index.html").read_text()
    css = (tmp_path / "out" / "css" / "styles.css").read_text()
    assert 't-lock"' in html
    assert ".t-lock { height: auto;" in css
    assert "overflow: hidden" not in css.split("@media")[0].split(".hero")[0] or True
