"""v0.5 clean structure: container, fluid sizes, grids, .btn, nav menus with section links, headings,
body typography and the code quality score."""

import re

from app.pipeline import quality
from app.pipeline.generate import generate_site
from app.pipeline.ir import build_pages
from app.pipeline.layout import layout_page

W = 1440
FONT = {"family": "Inter", "style": "Regular"}
BOLD = {"family": "Inter", "style": "Bold"}


def _solid(r, g, b):
    return [{"type": "SOLID", "visible": True, "opacity": 1, "color": {"r": r, "g": g, "b": b}}]


def flat_landing() -> dict:
    """A flat (no Auto Layout) landing page: header with a menu, hero, three cards, contact."""
    nodes: dict = {}

    def add(i, name, t, x, y, w, h, **kw):
        nodes[i] = {"id": i, "name": name, "type": t, "parentId": "R", "children": [], "visible": True,
                    "absoluteBoundingBox": {"x": x, "y": y, "width": w, "height": h}, **kw}  # fmt: skip
        nodes["R"]["children"].append(i)

    def text(i, chars, x, y, w, h, size=16, font=FONT, resize="WIDTH_AND_HEIGHT", align="LEFT"):
        add(i, chars[:20], "TEXT", x, y, w, h, characters=chars, fontName=font, fontSize=size,
            lineHeight={"unit": "PIXELS", "value": h if "\n" not in chars else h / 2},
            textAutoResize=resize, textAlignHorizontal=align, fills=_solid(0.1, 0.1, 0.1),
            fontWeight=700 if font is BOLD else 400,
            textSegments=[{"characters": chars, "fontName": font, "fontSize": size}])  # fmt: skip

    nodes["R"] = {"id": "R", "name": "Landing", "type": "FRAME", "parentId": None, "children": [],
                  "visible": True, "absoluteBoundingBox": {"x": 0, "y": 0, "width": W, "height": 1400},
                  "fills": _solid(1, 1, 1), "layoutMode": "NONE"}  # fmt: skip
    text("logo", "Brand", 120, 24, 80, 32, size=24, font=BOLD)
    text("n1", "Home", 900, 30, 48, 20)
    text("n2", "About us", 980, 30, 70, 20)
    text("n3", "Contact", 1082, 30, 60, 20)
    add("heroBg", "Hero", "RECTANGLE", 0, 80, W, 520, fills=_solid(0.93, 0.95, 1))
    text("h1", "Build better sites", 120, 200, 600, 72, size=64, font=BOLD)
    text("p1", "Design in Figma and ship clean code.\nIt just works.", 120, 300, 520, 56,
         resize="HEIGHT")  # fmt: skip
    add("btn", "Button", "RECTANGLE", 120, 400, 180, 56, fills=_solid(0.3, 0.3, 0.9), cornerRadius=8)
    text("btnL", "Get in touch", 158, 418, 104, 20, font=BOLD)
    text("h2a", "About us", 120, 680, 300, 48, size=40, font=BOLD)
    for k in range(3):
        x = 120 + k * 410
        add(f"card{k}", f"Card {k}", "RECTANGLE", x, 780, 380, 240, fills=_solid(0.96, 0.96, 0.96), cornerRadius=16)
        text(f"ct{k}", f"Feature {k + 1}", x + 32, 812, 200, 28, size=22, font=BOLD)
        text(f"cp{k}", "Short description of this feature.", x + 32, 860, 300, 48, resize="HEIGHT")
    text("h2b", "Contact", 120, 1120, 300, 48, size=40, font=BOLD)
    text("mail", "hello@example.com", 120, 1200, 180, 24)
    return {
        "schemaVersion": "0.1",
        "source": {"fileName": "Flat", "pluginVersion": "t", "exportedAt": "2026-01-01T00:00:00Z"},
        "frames": [
            {
                "role": "desktop",
                "rootId": "R",
                "name": "Home / Desktop",
                "page": "Home",
                "width": W,
                "height": 1400,
                "referenceAssets": [],
            }
        ],  # fmt: skip
        "nodes": nodes,
        "assets": [],
    }


def _build(tmp_path):
    b = flat_landing()
    page = build_pages(b)[0]
    layout_page(page)
    out = tmp_path / "out"
    generate_site([(page, "index.html")], b, tmp_path, out)
    return page, (out / "index.html").read_text(), (out / "css" / "styles.css").read_text(), out


def _rule(css: str, cls: str) -> str:
    m = re.search(r"\." + re.escape(cls) + r" \{([^}]*)\}", css)
    return m.group(1) if m else ""


def test_menu_becomes_nav_list_linking_to_sections(tmp_path):
    page, html, css, _ = _build(tmp_path)
    assert re.search(r'<nav[^>]* aria-label="Main">', html) and 'role="list">' in html
    assert re.search(r'<li><a [^>]*href="#about-us"[^>]*>About us</a></li>', html)
    assert re.search(r'<li><a [^>]*href="#contact"[^>]*>Contact</a></li>', html)
    assert "<header" in html  # logo + menu row at the top
    assert 'id="about-us"' in html and "scroll-behavior: smooth" in css


def test_buttons_are_a_shared_component_and_link_to_contact(tmp_path):
    _, html, css, _ = _build(tmp_path)
    m = re.search(r'<a class="(btn[^"]*)"[^>]*href="([^"]+)"', html)
    assert m and m.group(1).split()[0] == "btn" and m.group(2) == "#contact"
    btn = _rule(css, "btn")
    assert "display: inline-flex" in btn and "padding:" in btn and "width: 100%" not in btn
    assert '<span class="btn__label"' in html


def test_equal_cards_become_a_grid_and_content_uses_the_container(tmp_path):
    page, html, css, _ = _build(tmp_path)
    assert page.container[1] <= 1220 and "--container:" in css
    assert html.count('class="container') == len(page.sections)
    m = re.search(r"grid-template-columns: repeat\(3, minmax\(0, 380px\)\)", css)
    assert m, "three equal cards should be one grid with columns capped at the card width"
    assert "l-grid-3" in html and "@media (max-width: 1023px)" in css


def test_no_fixed_text_boxes_and_body_typography(tmp_path):
    _, html, css, _ = _build(tmp_path)
    assert re.search(r"\nbody \{[^}]*font-family", css)
    for cls in re.findall(r'<p class="([^" ]+)"', html):
        body = _rule(css, cls)
        assert "font-family" not in body  # inherited from body
        assert not re.search(r"(?<![-\w])(width|height): \d", body)
    assert "<br>" in html  # Figma line breaks, no white-space: pre


def test_headings_follow_document_order(tmp_path):
    _, html, _, _ = _build(tmp_path)
    levels = [int(h) for h in re.findall(r"<h([1-6])", html)]
    assert levels.count(1) == 1
    assert all(b <= a + 1 for a, b in zip(levels, levels[1:], strict=False))


def test_quality_score_reports_clean_structure(tmp_path):
    _, _, _, out = _build(tmp_path)
    q = quality.analyze(out, ["index.html"])
    assert q["structureDetail"]["fixedWidth"] == 0 and q["structureDetail"]["headingsOk"]
    assert q["structure"] >= 55 and q["responsive"] is None
