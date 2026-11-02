"""WordPress theme export: structure, ACF field groups (free and Pro), valid PHP, and templates that
render exactly the HTML the design check verified (with ACF absent, empty, or filled in)."""

import io
import json
import re
import subprocess
import zipfile

import pytest

from app.pipeline.export import clean_site
from tests.test_pipeline import real_assets
from tests.test_site import two_page_bundle
from tests.wp_render import php_available, render, tokens

needs_php = pytest.mark.skipif(not php_available(), reason="PHP not installed")


@pytest.fixture
def site(client, bundle, tmp_path):
    """Two-page site (shared header/footer) converted through the API; returns (client, bundle id, out dir)."""
    from app import main

    b = two_page_bundle(bundle)
    data = real_assets(b)
    bid = client.post("/v1/bundles", content=json.dumps(b)).json()["bundleId"]
    for a in b["assets"]:
        client.put(f"/v1/bundles/{bid}/assets/{a['id']}", content=data[a["id"]])
    res = client.post(f"/v1/bundles/{bid}/convert?verify=false&wait=true").json()
    assert {"site-header", "site-footer"} <= set(res["components"])
    return client, bid, main.store.bundle_dir(bid) / "output"


def theme(client, bid, acf, tmp_path):
    r = client.get(f"/v1/bundles/{bid}/code.zip?format=wordpress&acf={acf}")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert "wordpress" in r.headers["content-disposition"]
    z = zipfile.ZipFile(io.BytesIO(r.content))
    dest = tmp_path / acf
    z.extractall(dest)
    root = dest / z.namelist()[0].split("/")[0]
    return root, z.namelist()


def test_theme_has_wordpress_structure(site, tmp_path):
    client, bid, _ = site
    root, names = theme(client, bid, "free", tmp_path)
    for f in ("style.css", "functions.php", "header.php", "footer.php", "front-page.php", "index.php", "page.php",
              "templates/page-about.php", "inc/template-tags.php", "assets/css/main.css", "readme.txt"):  # fmt: skip
        assert (root / f).exists(), f
    assert "Theme Name: Home" in (root / "style.css").read_text()
    assert "Template Name: About" in (root / "templates/page-about.php").read_text()
    # The header and footer shared by both pages are template parts, included once in header/footer.php.
    assert "template-parts/components/site-header" in (root / "header.php").read_text()
    assert "template-parts/components/site-footer" in (root / "footer.php").read_text()
    assert "site-header" not in (root / "front-page.php").read_text()
    assert any(n.endswith("assets/images/") or "/assets/images/" in n for n in names)


def test_acf_free_and_pro_field_groups(site, tmp_path):
    client, bid, _ = site
    free, _ = theme(client, bid, "free", tmp_path)
    pro, _ = theme(client, bid, "pro", tmp_path)
    fg = [json.loads(p.read_text()) for p in (free / "acf-json").glob("*.json")]
    pg = [json.loads(p.read_text()) for p in (pro / "acf-json").glob("*.json")]
    free_locs = {r[0]["param"] + "=" + r[0]["value"] for g in fg for r in g["location"]}
    assert "page_type=front_page" in free_locs and "page_template=templates/page-about.php" in free_locs
    types = {f["type"] for g in fg for f in g["fields"]}
    assert "tab" in types and "repeater" not in types and "flexible_content" not in types  # ACF free only
    pro_types = {f["type"] for g in pg for f in g["fields"]}
    assert "flexible_content" in pro_types
    assert any(r[0]["param"] == "options_page" for g in pg for r in g["location"])  # Site settings
    flex = next(f for g in pg for f in g["fields"] if f["type"] == "flexible_content")
    # Every section of the site can be used on any page; the site header and footer stay in header/footer.php.
    names = {lay["name"] for lay in flex["layouts"].values()}
    assert names == {"home_hero", "shared_section", "about_hero"}
    keys = [f["key"] for g in fg + pg for f in g["fields"]]
    assert all(k.startswith("field_") for k in keys)
    assert "define( 'F2C_ACF_PRO', true );" in (pro / "functions.php").read_text()


@needs_php
def test_theme_php_is_valid(site, tmp_path):
    client, bid, _ = site
    for acf in ("free", "pro"):
        root, _ = theme(client, bid, acf, tmp_path)
        for f in root.rglob("*.php"):
            r = subprocess.run(["php", "-l", str(f)], capture_output=True, text=True)
            assert r.returncode == 0, r.stdout + r.stderr


@needs_php
def test_theme_renders_exactly_the_checked_html(site, tmp_path):
    client, bid, out = site
    clean = clean_site(out)
    for acf in ("free", "pro"):
        root, _ = theme(client, bid, acf, tmp_path)
        for tpl, page in (("front-page.php", "index.html"), ("templates/page-about.php", "about.html")):
            expected = tokens(clean[page].decode())
            assert tokens(render(root, tpl)) == expected, (acf, tpl, "without ACF")
            assert tokens(render(root, tpl, fields={})) == expected, (acf, tpl, "ACF, nothing edited")


@needs_php
def test_edited_fields_appear(site, tmp_path):
    client, bid, _ = site
    free, _ = theme(client, bid, "free", tmp_path)
    html = render(free, "front-page.php", fields={"hero_title": "Edited title"})
    assert "Edited title" in html and "Build better experiences" not in html
    pro, _ = theme(client, bid, "pro", tmp_path)
    layouts = re.findall(r"'(home_[a-z0-9_]+)' => 'sections/home/", (pro / "functions.php").read_text())
    rows = [{"acf_fc_layout": layouts[0], "hero_title": "Built with sections"}]
    full = render(pro, "front-page.php", fields={})
    html = render(pro, "front-page.php", fields={}, rows=rows)
    assert "Built with sections" in html
    # Only the chosen layout is rendered inside <main>; the shared header and footer stay.
    ids = lambda h: re.findall(r'<(?:section|header|footer)[^>]*\bid="([^"]+)"', h)  # noqa: E731
    assert ids(full) == ["site-header", "hero", "shared-section", "site-footer"]
    assert ids(html) == ["site-header", "hero", "site-footer"]
