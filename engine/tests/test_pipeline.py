import io
import json
import os

import pytest
from PIL import Image

from app.pipeline import styles
from app.pipeline.ir import El, build_ir
from app.pipeline.layout import detect_offset_decorations, layout_page, nest_contained, split_layers, xy_cut


# ----------------------------------------------------------------------------- helpers
def el(nid, kind, x, y, w, h, z=0, **kw):
    return El(nid, nid, kind, x, y, w, h, z=z, node=kw.pop("node", {"id": nid}), **kw)


def real_assets(bundle):
    """Replaces the fixture's placeholder bytes with real tiny images; returns {asset_id: bytes}."""
    out = {}
    for a in bundle["assets"]:
        if a["format"] == "SVG":
            data = (
                b'<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32"><circle cx="16" cy="16" r="16"/></svg>'
            )
        else:
            buf = io.BytesIO()
            Image.new("RGB", (64, 48), (40, 120, 200)).save(buf, "JPEG" if a["format"] == "JPG" else "PNG")
            data = buf.getvalue()
        a["byteLength"] = len(data)
        out[a["id"]] = data
    return out


# ----------------------------------------------------------------------------- styles
def test_color_and_alpha():
    assert styles.color({"r": 1, "g": 0, "b": 0}) == "#ff0000"
    assert styles.color({"r": 0, "g": 0, "b": 0, "a": 0.5}) == "rgba(0, 0, 0, 0.5)"
    assert styles.color({"r": 1, "g": 1, "b": 1}, opacity=0.25) == "rgba(255, 255, 255, 0.25)"


def test_font_weight_from_style_name():
    assert styles.font_weight("SemiBold") == 600
    assert styles.font_weight("Black Italic") == 900
    assert styles.is_italic("Bold Italic")
    assert styles.font_weight("Unknown", 500) == 500


def test_border_inside_and_widths():
    node = {"strokes": [{"type": "SOLID", "color": {"r": 0, "g": 0, "b": 0}}], "strokeWeight": 2,
            "strokeAlign": "INSIDE"}  # fmt: skip
    assert styles.border(node) == {"border": "2px solid #000000"}
    assert styles.border_widths(node) == (2.0, 2.0, 2.0, 2.0)
    assert styles.border_widths({**node, "strokeAlign": "OUTSIDE"}) == (0, 0, 0, 0)


def test_radius_and_effects():
    assert styles.radius({"type": "ELLIPSE"}) == {"border-radius": "50%"}
    assert styles.radius({"cornerRadius": "MIXED", "topLeftRadius": 30, "topRightRadius": 30}) == {
        "border-radius": "30px 30px 0 0"
    }
    shadow = {"type": "DROP_SHADOW", "visible": True, "radius": 24, "offset": {"x": 0, "y": 8}, "spread": 0,
              "color": {"r": 0, "g": 0, "b": 0, "a": 0.12}}  # fmt: skip
    assert styles.effects({"effects": [shadow]}) == {"box-shadow": "0 8px 24px 0 rgba(0, 0, 0, 0.12)"}


def test_text_style_segment_overrides():
    node = {"fontName": "MIXED", "fontSize": 16, "lineHeight": {"unit": "PIXELS", "value": 24},
            "letterSpacing": {"unit": "PERCENT", "value": 15}, "fills": []}  # fmt: skip
    seg = {"fontName": {"family": "Mitr", "style": "SemiBold"}, "fontSize": 27,
           "fills": [{"type": "SOLID", "color": {"r": 0.2, "g": 0.4, "b": 0.6}}]}  # fmt: skip
    d = styles.text_style(seg, node)
    assert d["font-family"] == "'Mitr'" and d["font-weight"] == "600" and d["font-size"] == "27px"
    assert d["line-height"] == "24px" and d["letter-spacing"] == "0.15em" and d["color"] == "#336699"


# ----------------------------------------------------------------------------- layout
def test_offset_decoration_merges_hard_shadow():
    back = el("back", "shape", 104, 104, 160, 50, z=1)
    front = el("front", "shape", 100, 100, 160, 50, z=2)
    out = detect_offset_decorations([back, front])
    assert out == [front]
    assert front.deco["dx"] == 4 and front.deco["dy"] == 4


def test_containment_makes_cards():
    card = el("card", "shape", 0, 0, 300, 400, z=1)
    image = el("img", "image", 0, 0, 300, 200, z=2)
    title = el("title", "text", 20, 220, 200, 24, z=3)
    out = nest_contained([card, image, title])
    assert out == [card] and card.kind == "box"
    assert card.children == [image, title]


def test_overlapping_graphic_becomes_layer():
    blob = el("blob", "svg", 0, 0, 800, 600, z=1)
    text = el("text", "text", 100, 100, 300, 40, z=2)
    flow, layers = split_layers([blob, text])
    assert layers == [blob] and flow == [text]


def test_xy_cut_rows_and_columns():
    # Two columns, each with a circle above a paragraph (like the About section).
    items = [
        el("c1", "shape", 0, 0, 24, 24), el("t1", "text", 0, 34, 330, 80),
        el("c2", "shape", 380, 0, 24, 24), el("t2", "text", 380, 34, 330, 80),
    ]  # fmt: skip
    mode, children = xy_cut(items)
    assert mode == "row" and len(children) == 2
    assert all(c.kind == "wrapper" and c.layout["mode"] == "column" for c in children)


def test_xy_cut_overlap_falls_back_to_absolute():
    a, b = el("a", "shape", 0, 0, 100, 100), el("b", "shape", 50, 50, 100, 100)
    assert xy_cut([a, b])[0] == "absolute"


def test_sample_bundle_ir_and_layout(bundle):
    pages = build_ir(bundle)
    assert set(pages) == {"desktop", "mobile"}
    page = layout_page(pages["desktop"])
    assert len(page.sections) == 4  # header, hero, features group, footer
    kinds = {e.kind for s in page.sections for e in s.walk()}
    assert {"text", "image", "svg", "container"} <= kinds


# ----------------------------------------------------------------------------- generation
def test_generate_sample(bundle, tmp_path):
    from app.pipeline.generate import generate

    data = real_assets(bundle)
    (tmp_path / "assets").mkdir()
    for a in bundle["assets"]:
        ext = {"PNG": "png", "JPG": "jpg", "SVG": "svg"}[a["format"]]
        (tmp_path / "assets" / f"{a['id']}.{ext}").write_bytes(data[a["id"]])
    page = layout_page(build_ir(bundle)["desktop"])
    out = generate(page, bundle, tmp_path / "assets", tmp_path / "output")
    html = (tmp_path / "output" / "index.html").read_text()
    css = (tmp_path / "output" / "css" / "styles.css").read_text()
    assert "<h1" in html and "Build better experiences" in html
    assert 'data-figma-id="10:12"' in html
    assert html.count("<section") + html.count("<footer") == 4
    assert ":root" in css and "--font-inter" in css
    assert "!important" not in css
    assert (tmp_path / "output" / "images" / "img-a1b2c3d4e5f6a7b8c9d0.webp").exists()
    assert any(f["family"] == "Inter" for f in out.fonts)
    assert "fonts.googleapis.com" in html  # no local font files in this test
    assert any(x["kind"] == "text" for x in out.expectations)


def test_ssim_identical_and_different():
    from app.pipeline.fidelity import ssim

    a = Image.new("RGB", (64, 64), "white")
    b = a.copy()
    b.paste((0, 0, 0), (0, 0, 32, 64))
    assert ssim(a, a) == pytest.approx(1.0)
    assert ssim(a, b) < 0.8


# ----------------------------------------------------------------------------- API
def _upload(client, bundle):
    data = real_assets(bundle)
    bundle_id = client.post("/v1/bundles", content=json.dumps(bundle)).json()["bundleId"]
    for a in bundle["assets"]:
        assert client.put(f"/v1/bundles/{bundle_id}/assets/{a['id']}", content=data[a["id"]]).status_code == 200
    return bundle_id


def test_convert_requires_all_assets(client, bundle):
    bundle_id = client.post("/v1/bundles", content=json.dumps(bundle)).json()["bundleId"]
    res = client.post(f"/v1/bundles/{bundle_id}/convert?verify=false")
    assert res.status_code == 409


def test_convert_and_serve(client, bundle):
    bundle_id = _upload(client, bundle)
    res = client.post(f"/v1/bundles/{bundle_id}/convert?verify=false")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["previewUrl"].endswith(f"/v1/bundles/{bundle_id}/output/index.html")
    assert "index.html" in body["files"] and "css/styles.css" in body["files"]

    page = client.get(f"/v1/bundles/{bundle_id}/output/index.html")
    assert page.status_code == 200 and "<main>" in page.text
    assert client.get(f"/v1/bundles/{bundle_id}/output/css/styles.css").status_code == 200
    z = client.get(f"/v1/bundles/{bundle_id}/code.zip")
    assert z.status_code == 200 and z.headers["content-type"] == "application/zip"
    assert client.get(f"/v1/bundles/{bundle_id}/conversion").json()["files"] == body["files"]


def test_output_path_traversal_blocked(client, bundle):
    bundle_id = _upload(client, bundle)
    client.post(f"/v1/bundles/{bundle_id}/convert?verify=false")
    assert client.get(f"/v1/bundles/{bundle_id}/output/..%2Fbundle.json").status_code == 404
    assert client.get(f"/v1/bundles/{bundle_id}/output/../bundle.json").status_code == 404


def _browser_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            path = os.environ.get("ENGINE_CHROMIUM_PATH")
            b = p.chromium.launch(executable_path=path) if path else p.chromium.launch()
            b.close()
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _browser_available(), reason="Chromium not installed (uv run playwright install chromium)")
def test_full_conversion_with_fidelity(client, bundle, monkeypatch):
    from app import main

    monkeypatch.setattr(main.settings, "chromium_path", os.environ.get("ENGINE_CHROMIUM_PATH"))
    bundle_id = _upload(client, bundle)
    body = client.post(f"/v1/bundles/{bundle_id}/convert").json()
    f = body["fidelity"]
    assert f is not None, body["warnings"]
    assert 0 <= f["score"] <= 100 and f["band"] in ("Verified", "Close", "Needs work", "Failed")
    assert f["counts"]["elements"] > 0
    assert client.get(f"/v1/bundles/{bundle_id}/fidelity/report.html").status_code == 200
