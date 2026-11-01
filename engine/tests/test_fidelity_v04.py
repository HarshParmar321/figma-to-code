"""v0.4: paint order, masks, crops, gradients, exported opacity, any-width layout and a checker that never hangs."""

import os
import time

import pytest

from app.pipeline.generate import Generator, generate_site
from app.pipeline.ir import El, IRBuilder, build_pages
from app.pipeline.layout import layout_page, nest_contained
from app.pipeline.styles import _gradient
from tests.test_pipeline import _browser_available, real_assets

ROT90 = [[6.123e-17, 1, 0], [-1, 6.123e-17, 1]]  # Figma's default top → bottom gradient handles
STOPS = [
    {"color": {"r": 1, "g": 0, "b": 0, "a": 1}, "position": 0},
    {"color": {"r": 0, "g": 0, "b": 1, "a": 1}, "position": 1},
]


# ----------------------------------------------------------------------------- gradients
def test_linear_gradient_follows_handles():
    left_right = _gradient(
        {"type": "GRADIENT_LINEAR", "gradientStops": STOPS, "gradientTransform": [[1, 0, 0], [0, 1, 0]]}, (400, 200)
    )
    assert left_right.startswith("linear-gradient(90deg") and "0%" in left_right and "100%" in left_right
    top_bottom = _gradient({"type": "GRADIENT_LINEAR", "gradientStops": STOPS, "gradientTransform": ROT90}, (400, 200))
    assert top_bottom.startswith("linear-gradient(180deg")


def test_radial_gradient_fits_the_layer():
    g = _gradient({"type": "GRADIENT_RADIAL", "gradientStops": STOPS, "gradientTransform": ROT90}, (1064, 840))
    assert g.startswith("radial-gradient(ellipse 50% 50% at 50% 50%")  # not a corner-to-corner circle


# ----------------------------------------------------------------------------- paint order
def _el(i, kind, x, y, w, h, z, t="RECTANGLE"):
    return El(i, i, kind, x, y, w, h, z=z, node={"id": i, "type": t})


def test_nesting_keeps_figma_paint_order():
    panel = _el("panel", "shape", 0, 0, 600, 400, 1)
    other = _el("other", "shape", 500, 0, 400, 400, 2)  # painted between panel and photo, overlaps the photo
    photo = _el("photo", "image", 300, 50, 280, 200, 3)
    text = _el("text", "text", 20, 20, 100, 30, 4)
    top = nest_contained([panel, other, photo, text])
    assert photo in top  # moving it into the panel would put `other` on top of it
    assert text in panel.children  # nothing in between overlaps it: safe to nest


def test_ellipses_never_become_containers():
    glow = _el("glow", "shape", 0, 0, 800, 600, 1, t="ELLIPSE")
    text = _el("t", "text", 300, 250, 100, 30, 2)
    assert set(nest_contained([glow, text])) == {glow, text}


def test_overlapping_elements_get_figma_z_index():
    sec = El("s", "s", "section", 0, 0, 1000, 500)
    a, b = _el("a", "shape", 0, 0, 300, 300, 5), _el("b", "image", 200, 100, 300, 300, 9)
    child = _el("c", "text", 10, 10, 50, 20, 6)
    a.children = [child]
    sec.children = [a, b]
    need = Generator.stacking(sec)
    assert id(a) in need and id(b) in need
    assert id(child) not in need  # only overlaps its own parent


# ----------------------------------------------------------------------------- masks and crops
def _mask_bundle(bundle):
    root = bundle["frames"][0]["rootId"]
    rb = bundle["nodes"][root]["absoluteBoundingBox"]

    def node(i, t, x, y, w, h, **kw):
        return {"id": i, "name": i, "type": t, "parentId": "M:1", "children": [], "visible": True,
                "absoluteBoundingBox": {"x": rb["x"] + x, "y": rb["y"] + y, "width": w, "height": h}, **kw}  # fmt: skip

    group = node("M:1", "GROUP", 100, 600, 300, 200, children=["M:2", "M:3"])
    group["parentId"] = root
    bundle["nodes"][root]["children"].append("M:1")
    bundle["nodes"]["M:1"] = group
    bundle["nodes"]["M:2"] = node("M:2", "ELLIPSE", 100, 600, 200, 200, isMask=True, fills=[])
    bundle["nodes"]["M:3"] = node("M:3", "RECTANGLE", 100, 600, 300, 200, fills=[
        {"type": "SOLID", "color": {"r": 1, "g": 0, "b": 0}, "visible": True}])  # fmt: skip
    return bundle


def test_mask_layer_clips_siblings_above_it(bundle):
    b = _mask_bundle(bundle)
    ir = IRBuilder(b)
    els = ir.flatten(b["nodes"]["M:1"], b["nodes"][b["frames"][0]["rootId"]])
    assert [e.id for e in els] == ["M:3"]  # the mask itself is not drawn
    assert els[0].mask["kind"] == "ellipse"
    page = build_pages(b)[0]
    gen = Generator(page, b, None)
    css = gen.mask_css(els[0])
    assert css["clip-path"].startswith("ellipse(100px 100px at 100px 100px")


def test_crop_window_from_image_transform():
    fill = {"type": "IMAGE", "scaleMode": "CROP", "visible": True, "imageTransform": [[1, 0, 0], [0, 0.77, 0.223]]}
    e = El("i", "i", "image", 0, 0, 642, 740, node={"fills": [fill]})
    x0, y0, x1, y1 = Generator.image_crop(e)
    assert (x0, round(y0, 3), x1, round(y1, 3)) == (0, 0.223, 1, 0.993)
    e.node["fills"][0]["scaleMode"] = "FILL"
    assert Generator.image_crop(e) is None


def test_exported_layers_keep_their_baked_opacity(bundle):
    page = build_pages(bundle)[0]
    gen = Generator(page, bundle, None)
    e = El("r", "r", "raster", 0, 0, 10, 10, node={"opacity": 0.4})
    assert "opacity" not in gen.visual(e)  # Figma already applied it when exporting the PNG
    e.kind = "shape"
    assert gen.visual(e)["opacity"] == "0.4"


# ----------------------------------------------------------------------------- any screen width
def _site(bundle, tmp_path):
    data = real_assets(bundle)
    (tmp_path / "assets").mkdir()
    for a in bundle["assets"]:
        ext = {"PNG": "png", "JPG": "jpg", "SVG": "svg"}[a["format"]]
        (tmp_path / "assets" / f"{a['id']}.{ext}").write_bytes(data[a["id"]])
    page = build_pages(bundle)[0]
    layout_page(page)
    gen = generate_site([(page, "index.html")], bundle, tmp_path / "assets", tmp_path / "out")
    return page, gen, tmp_path / "out"


def test_content_column_is_a_container_not_a_stage(bundle, tmp_path):
    """v0.5: normal sections use one shared .container; only overlapping compositions keep a stage."""
    page, gen, out = _site(bundle, tmp_path)
    html = (out / "index.html").read_text()
    css = (out / "css" / "styles.css").read_text()
    assert page.container == (80.0, 1280)
    assert "--container: 1280px;" in css
    assert ".container { width: min(100% - 2 * var(--gutter), var(--container)); margin-inline: auto; }" in css
    assert "l-stage" not in html and "zoom" not in css  # nothing here overlaps: no scaling fallback
    assert ":root { --gutter: 16px; }" in css  # phones get their own side padding


def test_compositions_keep_a_stage_with_fit_zoom(bundle, tmp_path):
    page = build_pages(bundle)[0]
    layout_page(page)
    sec = page.sections[1]
    sec.layout["stage"] = True  # as if its layers overlapped across the full width
    gen = Generator(page, bundle, tmp_path)
    gen.add_page(page, "index.html")
    assert gen._fit_sections
    css = gen.fit_css(page.width)
    assert "[data-fit] { zoom:" in css and "main { zoom" not in css


@pytest.mark.skipif(not _browser_available(), reason="Chromium not installed")
def test_layout_centres_and_never_cuts_content(bundle, tmp_path):
    from playwright.sync_api import sync_playwright

    page, gen, out = _site(bundle, tmp_path)
    ids = [e["id"] for e in gen.pages[0]["expectations"] if e["kind"] == "text"]
    path = os.environ.get("ENGINE_CHROMIUM_PATH")
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=path) if path else p.chromium.launch()
        for width in (1000, 1280, 2200):
            pg = b.new_page(viewport={"width": width, "height": 900})
            pg.goto((out / "index.html").as_uri())
            boxes = pg.evaluate(
                """ids => ids.map(id => {
                     const r = document.querySelector(`[data-figma-id="${id}"]`).getBoundingClientRect();
                     return [r.left, r.right]; })""",
                ids,
            )
            assert pg.evaluate("document.documentElement.scrollWidth") <= width
            assert all(left >= -0.5 and right <= width + 0.5 for left, right in boxes), (width, boxes)
            lo, hi = min(x for x, _ in boxes), max(x for _, x in boxes)
            design_lo = min(e["box"][0] for e in gen.pages[0]["expectations"] if e["kind"] == "text")
            design_hi = max(e["box"][0] + e["box"][2] for e in gen.pages[0]["expectations"] if e["kind"] == "text")
            # Same centre as the design, relative to the screen.
            assert abs((lo + hi) / 2 / width - (design_lo + design_hi) / 2 / page.width) < 0.2
        b.close()


# ----------------------------------------------------------------------------- checker never hangs
@pytest.mark.skipif(not _browser_available(), reason="Chromium not installed")
def test_checker_loads_lazy_images_below_the_fold(tmp_path):
    from PIL import Image

    from app.pipeline.fidelity import Renderer

    Image.new("RGB", (40, 40), (200, 50, 50)).save(tmp_path / "a.png")
    (tmp_path / "index.html").write_text(
        '<body style="margin:0"><div style="height:3000px"></div>'
        '<img data-figma-id="x" loading="lazy" src="a.png" width="40" height="40"></body>'
    )
    start = time.monotonic()
    with Renderer(os.environ.get("ENGINE_CHROMIUM_PATH")) as r:
        data, _ = r.capture(tmp_path / "index.html", 800, ["x"], tmp_path / "shot.png")
        assert r.ready
    assert time.monotonic() - start < 20
    assert data["elements"]["x"]["w"] == 40
