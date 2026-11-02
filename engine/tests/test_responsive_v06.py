"""v0.6: clean export, Auto Layout as the source of truth, mobile/tablet frames merged into media
queries (and checked), fluid type and spacing, real form fields."""

import json
import os
import re

import pytest

from app.pipeline import breakpoints as bp
from app.pipeline.export import clean_site, fold_corrections
from app.pipeline.generate import MOBILE_Q, Generator, fluid_value, generate_site
from app.pipeline.ir import build_pages, site_pages
from app.pipeline.layout import layout_page
from tests.test_pipeline import _browser_available, real_assets
from tests.test_structure_v05 import _solid, flat_landing


def _pages(bundle):
    g = site_pages(build_pages(bundle))[0]
    layout_page(g["desktop"])
    layout_page(g["mobile"])
    return g["desktop"], g["mobile"]


def _find(page, node_id):
    return next(e for s in page.sections for e in s.walk() if e.node and e.node.get("id") == node_id)


def _site(bundle, tmp_path, frames=True):
    desk, mob = _pages(bundle)
    data = real_assets(bundle)
    (tmp_path / "assets").mkdir(exist_ok=True)
    for a in bundle["assets"]:
        ext = {"PNG": "png", "JPG": "jpg", "SVG": "svg"}[a["format"]]
        (tmp_path / "assets" / f"{a['id']}.{ext}").write_bytes(data[a["id"]])
    out = tmp_path / "out"
    gen = generate_site([(desk, "index.html")], bundle, tmp_path / "assets", out,
                        frames={"index.html": {"mobile": mob}} if frames else None)  # fmt: skip
    return desk, mob, gen, out


# ----------------------------------------------------------------------------- clean export
def test_clean_export_strips_instruments_and_folds_corrections(bundle, tmp_path):
    _, _, _, out = _site(bundle, tmp_path, frames=False)
    (out / "css" / "corrections.css").write_text(
        "/* corrections */\n@media (min-width: 1024px) {\n"
        '  [data-figma-id="10:12"] { translate: -3px 0; }\n'
        '  [data-figma-id="10:21"] { translate: 0 2px; }\n}\n'
    )
    files = clean_site(out, "Sample")
    html = files["index.html"].decode()
    css = files["css/styles.css"].decode()
    assert "data-figma-id" not in html and "data-section" not in html and "corrections.css" not in html
    assert "css/corrections.css" not in files and "README.txt" in files
    assert "/* Desktop fine-tuning" in css and "translate: -3px 0" in css
    # 10:21 and 10:22 share a class but only one is corrected: it gets its own modifier class.
    assert "--tuned-1" in html and re.search(r"\.[\w-]+--tuned-1 \{ translate: 0 2px; \}", css)


def test_fold_corrections_uses_the_class_when_all_its_elements_agree():
    htmls = {"index.html": '<p class="a__t" data-figma-id="1">x</p><p class="a__t" data-figma-id="2">y</p>'}
    corr = (
        "@media (min-width: 1024px) {\n"
        '  [data-figma-id="1"] { translate: 1px 0; }\n  [data-figma-id="2"] { translate: 1px 0; }\n}'
    )
    new, block = fold_corrections(htmls, corr)
    assert ".a__t { translate: 1px 0; }" in block and "--tuned" not in new["index.html"]


# ----------------------------------------------------------------------------- Auto Layout
def test_auto_layout_frames_use_their_figma_settings(bundle, tmp_path):
    desk, _ = _pages(bundle)
    gen = Generator(desk, bundle, tmp_path)
    hero = _find(desk, "10:10")
    d, per_child = gen.auto_flow_css(hero, hero.children)
    assert d["gap"] == "48px" and d["align-items"] == "flex-start" and "flex-direction" not in d
    button = _find(desk, "10:14")
    d, _ = gen.auto_flow_css(button, button.children)
    assert d["justify-content"] == "center" and d["align-items"] == "center"
    header = _find(desk, "10:2")
    d, _ = gen.auto_flow_css(header, header.children)
    assert d["justify-content"] == "space-between" and "gap" not in d


# ----------------------------------------------------------------------------- breakpoints
def test_mobile_frame_elements_are_matched(bundle):
    desk, mob = _pages(bundle)
    m = bp.match(desk, mob, {a["id"]: "same" for a in bundle["assets"] if a["kind"] == "svg"})
    names = {e.name for s in desk.sections for e in s.walk() if id(e) in m.pairs}
    assert {"Title", "Body", "Hero image", "Card 1", "Card 2", "Copyright", "Logo"} <= names
    assert m.pairs[id(_find(desk, "10:16"))].w == 358  # the image is full width on the phone


def test_mobile_frame_becomes_media_queries(bundle, tmp_path):
    _, _, _, out = _site(bundle, tmp_path)
    css = (out / "css" / "styles.css").read_text()
    mobile = css[css.index("@media (max-width: 767px) {\n  .") :]
    assert re.search(r"\.hero \{[^}]*flex-direction: column", mobile)  # row on desktop, stacked on the phone
    assert re.search(r"\.hero__title \{[^}]*font-size: 36px", mobile)  # the frame's exact size
    assert re.search(r"grid-template-columns: 1fr", mobile)  # cards: one column
    assert ":root { --gutter: 16px; }" in css


def test_mobile_expectations_use_mobile_boxes(bundle, tmp_path):
    desk, mob, gen, _ = _site(bundle, tmp_path)
    role, fpage, exps = gen.frame_checks["index.html"][0]
    assert role == "mobile" and fpage is mob
    img = next(x for x in exps if x["id"] == "10:16")
    assert img["box"][2] == 358 and img["box"][0] == 16
    title = next(x for x in exps if x["id"] == "10:12")
    assert title["style"]["font-size"] == "36px"


@pytest.mark.skipif(not _browser_available(), reason="Chromium not installed")
def test_conversion_checks_the_phone_layout(client, bundle):
    data = real_assets(bundle)
    bid = client.post("/v1/bundles", content=json.dumps(bundle)).json()["bundleId"]
    for a in bundle["assets"]:
        client.put(f"/v1/bundles/{bid}/assets/{a['id']}", content=data[a["id"]])
    body = client.post(f"/v1/bundles/{bid}/convert?wait=true").json()
    assert body["mobile"]["elements"] >= 10
    page = body["pages"][0]
    assert page["breakpoints"]["mobile"]["width"] == 390
    zipped = client.get(f"/v1/bundles/{bid}/code.zip")
    assert zipped.status_code == 200 and zipped.headers["content-type"] == "application/zip"


# ----------------------------------------------------------------------------- fluid
def test_fluid_values_are_exact_at_the_design_width():
    v = fluid_value(64, 38, 1440)
    m = re.match(r"clamp\((\d+)px, ([\d.-]+)px \+ ([\d.]+)vw, 64px\)", v)
    assert m and m.group(1) == "38"
    base, vw = float(m.group(2)), float(m.group(3))
    assert base + vw * 14.40 >= 64  # at 1440px the preferred value reaches the design size
    assert abs(base + vw * 3.90 - 38) < 0.1  # and the phone size at 390px


def test_large_type_is_fluid_and_frame_sizes_win(bundle, tmp_path):
    _, _, _, out = _site(bundle, tmp_path, frames=False)
    css = (out / "css" / "styles.css").read_text()
    assert re.search(r"font-size: (var\(--font-size-\d+\)|clamp\()", css)
    assert "clamp(" in css


# ----------------------------------------------------------------------------- forms
def test_drawn_inputs_become_a_real_form(tmp_path):
    b = flat_landing()
    nodes = b["nodes"]

    def rect(i, x, y, w, h, fills=None, strokes=None):
        nodes[i] = {"id": i, "name": i, "type": "RECTANGLE", "parentId": "R", "children": [], "visible": True,
                    "absoluteBoundingBox": {"x": x, "y": y, "width": w, "height": h},
                    "fills": fills or [], "strokes": strokes or [], "cornerRadius": 8}  # fmt: skip
        nodes["R"]["children"].append(i)

    for k in range(3):
        rect(f"f{k}", 120, 1260 + k * 72, 360, 56, fills=_solid(0.95, 0.95, 0.95))
    rect("send", 120, 1476, 160, 48, strokes=_solid(0.3, 0.3, 0.9))
    nodes["R"]["absoluteBoundingBox"]["height"] = 1600
    b["frames"][0]["height"] = 1600
    page = build_pages(b)[0]
    layout_page(page)
    generate_site([(page, "index.html")], b, tmp_path, tmp_path / "out")
    html = (tmp_path / "out" / "index.html").read_text()
    assert html.count("<input ") == 3 and '<button class="' in html and 'type="submit"' in html
    assert re.search(r"<form[ >]", html) and 'aria-label="Field 1"' in html
    css = (tmp_path / "out" / "css" / "styles.css").read_text()
    assert "font: inherit" in css


def test_mobile_query_constant():
    assert MOBILE_Q == "@media (max-width: 767px)"
    assert os.path.basename(__file__).startswith("test_")
