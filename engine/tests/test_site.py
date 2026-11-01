"""Multi-page sites, shared components, self-correction and background jobs."""

import copy
import json
import os
import time

import pytest

from app.pipeline import corrections as corr
from app.pipeline.components import detect_shared_sections
from app.pipeline.ir import build_pages, page_name, site_pages
from tests.test_pipeline import _browser_available, real_assets


def two_page_bundle(bundle: dict) -> dict:
    """Copies the sample desktop frame as an "About" page with a different hero title."""
    b = copy.deepcopy(bundle)
    b["frames"] = [f for f in b["frames"] if f["role"] == "desktop"]
    root_id = b["frames"][0]["rootId"]
    new = {}
    for k, v in b["nodes"].items():
        if not k.startswith("10:"):
            continue
        c = copy.deepcopy(v)
        c["id"], c["parentId"] = "P2_" + k, ("P2_" + v["parentId"]) if v["parentId"] else None
        c["children"] = ["P2_" + x for x in v["children"]]
        c["absoluteBoundingBox"] = {**v["absoluteBoundingBox"], "x": v["absoluteBoundingBox"]["x"] + 3000}
        new[c["id"]] = c
    new["P2_10:12"]["characters"] = "About our team"
    for s in new["P2_10:12"].get("textSegments", []):
        s["characters"] = "About our team"
    for a in list(b["assets"]):
        if a["kind"] == "svg" and a["nodeId"].startswith("10:"):
            b["assets"].append({**a, "id": "svg-P2_" + a["id"][4:], "nodeId": "P2_" + a["nodeId"]})
    b["nodes"].update(new)
    b["frames"][0]["page"] = "Home"
    b["frames"].append({**b["frames"][0], "rootId": "P2_" + root_id, "name": "About / Desktop", "page": "About"})
    return b


def test_page_names_pair_breakpoints():
    assert page_name("Home / Desktop") == page_name("Home - Mobile") == "Home"
    assert page_name("Contact – iPhone 14 Pro") == "Contact"


def test_site_pages_groups_frames(bundle):
    groups = site_pages(build_pages(bundle))
    assert len(groups) == 1 and groups[0]["desktop"] and groups[0]["mobile"]  # mobile paired with the only page


def test_shared_sections_detected(bundle):
    b = two_page_bundle(bundle)
    pages = [g["desktop"] for g in site_pages(build_pages(b))]
    assert [p.name for p in pages] == ["Home", "About"]
    hashes = {a["id"]: a["id"].replace("P2_", "") for a in b["assets"]}  # same bytes → same hash
    names = detect_shared_sections(pages, hashes)
    roles = {names[id(s)] for p in pages for s in p.sections if id(s) in names}
    assert {"site-header", "site-footer"} <= roles
    hero_home, hero_about = pages[0].sections[1], pages[1].sections[1]
    assert id(hero_home) not in names and id(hero_about) not in names  # different title: not shared


def test_multi_page_site_generation(client, bundle):
    b = two_page_bundle(bundle)
    data = real_assets(b)
    res = client.post("/v1/bundles", content=json.dumps(b))
    assert res.status_code == 201, res.text
    bid = res.json()["bundleId"]
    for a in b["assets"]:
        client.put(f"/v1/bundles/{bid}/assets/{a['id']}", content=data[a["id"]])
    body = client.post(f"/v1/bundles/{bid}/convert?verify=false&wait=true").json()
    assert [p["file"] for p in body["pages"]] == ["index.html", "about.html"]
    assert {"site-header", "site-footer"} <= set(body["components"])
    assert "components/site-header.html" in body["files"]
    about = client.get(f"/v1/bundles/{bid}/output/about.html").text
    assert "About our team" in about and 'data-component="site-header"' in about
    assert len([f for f in body["files"] if f.startswith("images/")]) < len(b["assets"]) - 4  # deduplicated


# ----------------------------------------------------------------------------- corrections
def m(dx=0, dy=0, dw=0, dh=0, ok=False, kind="shape", parent=None, box=(0, 0, 100, 50)):
    return {"dx": dx, "dy": dy, "dw": dw, "dh": dh, "ok": ok, "kind": kind, "parent": parent, "box": list(box)}


def test_corrections_parent_first():
    meas = {"card": m(dy=10), "title": m(dy=10, kind="text", parent="card"), "ok": m(ok=True)}
    fixes: dict = {}
    assert corr.step(meas, fixes) == 1
    assert fixes == {"card": {"tx": 0.0, "ty": -10.0}}  # the child waits for its parent


def test_corrections_size_and_sections():
    meas = {
        "s1": m(dh=40, kind="section", box=(0, 0, 1900, 600)),
        "s2": m(dy=40, kind="section", box=(0, 600, 1900, 400)),  # only shifted: no translate
        "img": m(dw=-8, dx=3, parent="s2", box=(10, 610, 120, 80)),
    }
    fixes: dict = {}
    corr.step(meas, fixes)
    assert fixes["s1"] == {"tx": 0.0, "ty": 0.0, "h": 600}
    assert "s2" not in fixes or fixes["s2"] == {"tx": 0.0, "ty": 0.0}
    assert fixes["img"]["w"] == 120 and fixes["img"]["tx"] == -3.0  # shifted-only section does not block
    css = corr.css(fixes)
    assert '[data-figma-id="s1"] { height: 600px; }' in css and "@media (min-width: 1024px)" in css


def test_corrections_noop_when_all_ok():
    assert corr.step({"a": m(ok=True)}, {}) == 0


@pytest.mark.skipif(not _browser_available(), reason="Chromium not installed")
def test_self_correction_repairs_a_broken_page(bundle, tmp_path):
    """Pushes elements off their place on purpose; the loop must bring every one back."""
    from app.pipeline.fidelity import Renderer, verify
    from app.pipeline.generate import generate_site
    from app.pipeline.layout import layout_page

    data = real_assets(bundle)
    (tmp_path / "assets").mkdir()
    for a in bundle["assets"]:
        ext = {"PNG": "png", "JPG": "jpg", "SVG": "svg"}[a["format"]]
        (tmp_path / "assets" / f"{a['id']}.{ext}").write_bytes(data[a["id"]])
    page = build_pages(bundle)[0]
    layout_page(page)
    gen = generate_site([(page, "index.html")], bundle, tmp_path / "assets", tmp_path / "out")
    exp = gen.pages[0]["expectations"]
    css = tmp_path / "out" / "css" / "styles.css"
    css.write_text(css.read_text() + '\n[data-figma-id="10:12"] { margin-top: 30px; margin-left: 17px; }\n')
    fixes: dict = {}
    with Renderer(os.environ.get("ENGINE_CHROMIUM_PATH")) as r:
        first = verify(
            tmp_path / "out", tmp_path / "f", page, exp, tmp_path / "assets" / "ref-desktop-1x.png", renderer=r
        )
        assert first["counts"]["geometryPass"] < first["counts"]["elements"]
        for _ in range(6):
            res = verify(tmp_path / "out", tmp_path / "f", page, exp, tmp_path / "assets" / "ref-desktop-1x.png",
                         renderer=r, write_images=False)  # fmt: skip
            if not corr.step(res["measurements"], fixes):
                break
            corr.write(tmp_path / "out", fixes)
        final = verify(
            tmp_path / "out", tmp_path / "f", page, exp, tmp_path / "assets" / "ref-desktop-1x.png", renderer=r
        )
    assert final["counts"]["geometryPass"] == final["counts"]["elements"]


# ----------------------------------------------------------------------------- background jobs
def test_background_job_with_progress(client, bundle):
    data = real_assets(bundle)
    bid = client.post("/v1/bundles", content=json.dumps(bundle)).json()["bundleId"]
    for a in bundle["assets"]:
        client.put(f"/v1/bundles/{bid}/assets/{a['id']}", content=data[a["id"]])
    assert client.get(f"/v1/bundles/{bid}/convert/status").json()["status"] == "idle"
    start = client.post(f"/v1/bundles/{bid}/convert?verify=false")
    assert start.status_code == 202 and start.json()["status"] == "running"
    for _ in range(100):
        job = client.get(f"/v1/bundles/{bid}/convert/status").json()
        if job["status"] != "running":
            break
        time.sleep(0.1)
    assert job["status"] == "done", job
    assert job["result"]["pages"][0]["file"] == "index.html"
    assert job["stage"] == "Done"
