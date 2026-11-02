"""v0.8: prototype interactions → web behaviour.

Fixture: fixtures/interactive-bundle.json (3 pages + a mobile frame) with wired hover / press states,
nav links whose underline only shows on hover, unwired State=Hover cards, an auto-advancing slider,
click-to-open FAQ items, a fixed header, a modal overlay and a phone menu overlay.
"""

import copy
import io
import json
import re
import subprocess
import zipfile
from pathlib import Path

import pytest

from app.pipeline import interactions as ix
from app.pipeline.export import clean_site
from app.pipeline.generate import slug
from tests.test_pipeline import _browser_available, real_assets
from tests.wp_render import php_available, render, tokens

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "interactive-bundle.json"
needs_browser = pytest.mark.skipif(not _browser_available(), reason="Chromium not installed")
needs_php = pytest.mark.skipif(not php_available(), reason="PHP not installed")


def interactive() -> dict:
    return copy.deepcopy(json.loads(FIXTURE.read_text(encoding="utf-8")))


def assets_for(b: dict) -> dict[str, bytes]:
    data = real_assets(b)
    for a in b["assets"]:
        if a["format"] == "SVG":  # one icon per source layer (instances of a component share it)
            layer = (a.get("nodeId") or a["id"]).split(";")[-1]
            data[a["id"]] = data[a["id"]].replace(b"<circle", f"<!-- {layer} --><circle".encode())
            a["byteLength"] = len(data[a["id"]])
    return data


@pytest.fixture
def site(client):
    from app import main

    b = interactive()
    data = assets_for(b)
    bid = client.post("/v1/bundles", content=json.dumps(b)).json()["bundleId"]
    for a in b["assets"]:
        assert client.put(f"/v1/bundles/{bid}/assets/{a['id']}", content=data[a["id"]]).status_code == 200
    res = client.post(f"/v1/bundles/{bid}/convert?verify=false&wait=true").json()
    out = main.store.bundle_dir(bid) / "output"
    return client, bid, out, res


# ----------------------------------------------------------------------------- timing
def test_figma_easings_and_springs():
    assert ix.easing_css({"type": "EASE_OUT"}) == ("ease-out", None)
    assert ix.easing_css({"type": "EASE_IN_AND_OUT_BACK"})[0] == "cubic-bezier(0.7, -0.4, 0.4, 1.4)"
    custom = {"type": "CUSTOM_CUBIC_BEZIER", "easingFunctionCubicBezier": {"x1": 0.2, "y1": 0, "x2": 0, "y2": 1}}
    assert ix.easing_css(custom)[0] == "cubic-bezier(0.2, 0, 0, 1)"
    curve, duration = ix.easing_css({"type": "BOUNCY"})
    assert curve.startswith("linear(0, ") and curve.endswith(", 1)") and 0.3 < duration < 3
    assert max(float(v) for v in curve[7:-1].split(", ")) > 1.05  # a bouncy spring overshoots
    assert ix.transition_value(["color"], {"type": "DISSOLVE", "duration": 0.3, "easing": {"type": "LINEAR"}}) == (
        "color 0.3s linear"
    )


def test_class_names_never_start_with_a_digit():
    assert slug("01") == "s-01" and slug("2024 report") == "s-2024-report" and slug("Hero") == "hero"


# ----------------------------------------------------------------------------- states
def test_state_changes_follow_smart_animate_rules():
    b = interactive()
    inter = ix.Interactions(b)
    inter.prepare()
    nodes = b["nodes"]

    hover, press = inter.changes["H:start"]  # the hero button: Default → Hover / Pressed
    root = hover["changes"]["H:start"]["css"]
    assert root["background-color"] and root["box-shadow"].startswith("0 8px 24px")
    assert root["gap"] == "12px"  # Auto Layout gap: the layout reflows, not a transform
    assert "transform" not in root or root["transform"] == "none"
    assert press["changes"]["H:start"]["box"][2] == pytest.approx(0.96, abs=0.01)  # pressed = scaled down
    assert press["changes"]["H:start"]["css"]["box-shadow"] == "none"  # pressing replaces the hover look

    nav = inter.changes["H:n1"][0]["changes"]
    assert nodes["IH:n1;C:nav:u"]["_reveal"]  # the hidden underline stays in the page, to be revealed
    assert nav["IH:n1;C:nav:u"]["show"] is True and "color" in nav["IH:n1;C:nav:t"]["css"]

    card = inter.changes["H:card0"][0]
    assert card["state"]["wired"] is False  # an unwired State=Hover variant still becomes :hover
    assert card["changes"]["IH:card0;C:card:go"]["box"][0] == pytest.approx(6)

    faq = inter.changes["H:faq0"][0]["changes"]
    injected = [n for n in nodes if n.startswith("SC:faq-o:")]
    assert injected and nodes[injected[0]]["_reveal"]  # the answer only exists in the Open variant
    assert any(c.get("show") for c in faq.values())
    chev = faq["IH:faq0;C:faq:chev"]["box"]
    assert chev[4] == pytest.approx(180) and chev[0] == 0 and chev[1] == 0  # rotates, does not move

    slides = inter.changes["H:slider"]
    assert [s["state"]["order"] for s in slides] == [1, 2]


def test_generated_site_behaves_like_the_prototype(site):
    _client, _bid, out, res = site
    assert [p["file"] for p in res["pages"]] == ["index.html", "about.html", "contact.html"]
    html = (out / "index.html").read_text()
    css = (out / "css" / "styles.css").read_text()
    js = (out / "js" / "site.js").read_text()
    assert '<script src="js/site.js" defer></script>' in html and "showModal" in js
    # Links: pages, a section, a URL in a new tab, back.
    assert 'href="about.html"' in html and 'href="contact.html"' in html and 'href="#features"' in html
    assert 'target="_blank" rel="noopener" href="https://cal.example.com/northwind"' in html
    assert 'data-action="back"' in (out / "contact.html").read_text()
    # Nav made of link components is a real menu; the fixed header is sticky.
    assert re.search(r'<nav class="[^"]*" aria-label="Main">', html)
    assert re.search(r"\.site-header \{[^}]*position: sticky;[^}]*top: 0;", css)
    # States.
    assert "@media (hover: hover)" in css and re.search(r"\.btn[\w-]*:hover \{[^}]*gap: 12px", css)
    assert re.search(r":active \{[^}]*transform: scale\(0\.96", css)
    assert '[aria-expanded="true"]' in css and 'data-toggle role="button" tabindex="0" aria-expanded="false"' in html
    assert '[data-step="1"]' in css and 'data-autoplay data-steps="2" data-delays="3000,3000"' in html
    assert "prefers-reduced-motion: reduce" in css and "linear(0, " in css  # GENTLE spring on the FAQ
    # Overlays are dialogs; the phone menu exists only below 768px.
    assert '<dialog id="overlay-o-modal"' in html and "data-close-outside" in html
    assert '<dialog id="overlay-o-menu"' in html and 'alt="Menu"' in html
    assert re.search(r"\.overlay-booking-modal__overlay::backdrop \{ background: rgba\(0, 0, 0, 0\.5\); \}", css)
    # Shared header: identical markup on every page (one template part in the theme).
    header = lambda h: re.sub(r' data-figma-id="[^"]*"', "", re.search(r"<header.*?</header>", h, re.S).group(0))  # noqa: E731
    assert header(html) == header((out / "about.html").read_text())


def test_clean_export_keeps_behaviour_attributes(site):
    _client, _bid, out, _res = site
    clean = clean_site(out)
    html = clean["index.html"].decode()
    assert "data-figma-id" not in html
    for attr in ("data-toggle", "data-autoplay", "data-open", "data-close", "data-step"):
        assert attr in html, attr
    assert "js/site.js" in clean


@needs_browser
def test_states_work_in_a_browser(site):
    import os

    from playwright.sync_api import sync_playwright

    _client, _bid, out, _res = site
    with sync_playwright() as p:
        path = os.environ.get("ENGINE_CHROMIUM_PATH")
        browser = p.chromium.launch(executable_path=path) if path else p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto((out / "index.html").as_uri())
        page.wait_for_timeout(200)
        btn = page.locator(".hero a.l-row").first
        bg = lambda: btn.evaluate("e => getComputedStyle(e).backgroundColor")  # noqa: E731
        before = bg()
        btn.hover()
        page.wait_for_timeout(400)
        assert bg() != before
        faq = page.locator("[data-toggle]").first
        faq.click()
        page.wait_for_timeout(200)
        assert faq.get_attribute("aria-expanded") == "true"
        assert faq.locator("p").nth(1).evaluate("e => getComputedStyle(e).display") != "none"
        page.locator("header a[data-open]").click()
        page.wait_for_timeout(300)
        assert page.locator("#overlay-o-modal").evaluate("d => d.open")
        page.locator("#overlay-o-modal [data-close]").click()
        assert not page.locator("#overlay-o-modal").evaluate("d => d.open")
        phone = browser.new_page(viewport={"width": 390, "height": 844})
        phone.goto((out / "index.html").as_uri())
        menu = phone.locator('img[alt="Menu"]')
        assert menu.is_visible() and not phone.locator("nav").is_visible()
        menu.click()
        phone.wait_for_timeout(300)
        box = phone.locator("#overlay-o-menu").bounding_box()
        assert box["width"] == 390 and box["height"] == 844  # a full-screen phone menu
        assert not errors
        browser.close()


@needs_php
def test_wordpress_theme_carries_the_behaviour(site, tmp_path):
    client, bid, out, _res = site
    clean = clean_site(out)
    for acf in ("free", "pro"):
        r = client.get(f"/v1/bundles/{bid}/code.zip?format=wordpress&acf={acf}")
        z = zipfile.ZipFile(io.BytesIO(r.content))
        z.extractall(tmp_path / acf)
        root = tmp_path / acf / z.namelist()[0].split("/")[0]
        assert (root / "assets" / "js" / "site.js").exists()
        assert "wp_enqueue_script( " in (root / "functions.php").read_text()
        for php_file in root.rglob("*.php"):
            assert subprocess.run(["php", "-l", str(php_file)], capture_output=True).returncode == 0, php_file
        for tpl, page in (("front-page.php", "index.html"), ("templates/page-about.php", "about.html"),
                          ("templates/page-contact.php", "contact.html")):  # fmt: skip
            assert tokens(render(root, tpl, fields={})) == tokens(clean[page].decode()), (acf, tpl)
