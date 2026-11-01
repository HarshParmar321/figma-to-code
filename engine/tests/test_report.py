from app.spike_report import build_report, to_markdown


def codes(report):
    return {f["code"] for f in report["findings"]}


def test_sample_metrics(bundle):
    r = build_report(bundle)
    assert r["summary"]["nodeCount"] == 34
    # 8 auto-layout frames + 2 groups (groups count as containers without layout)
    assert r["layout"]["containers"] == 10
    assert r["layout"]["autoLayoutCoverage"] == 0.8
    assert r["layout"]["groupsWithChildren"] == 2
    assert r["text"]["textNodes"] == 10
    assert r["text"]["mixedStyleNodes"] == 2
    assert r["interactions"]["hoverVariantSwaps"] == 2
    assert r["interactions"]["transitions"] == {"SMART_ANIMATE": 2}
    assert r["interactions"]["transitionDurationsSec"] == [0.2]
    assert r["motion"]["nodesWithMotionData"] == 2
    assert r["variables"]["variablesUsed"] == 1
    assert r["variables"]["unresolvedVariables"] == 0
    assert r["fieldCoverage"]["ratio"] == 1.0
    assert r["responsive"]["sectionsByRole"] == {"desktop": 4, "mobile": 4}
    assert r["responsive"]["sectionNameMatchRatio"] == 1.0
    assert codes(r) == {"GROUPS_WITH_CHILDREN"}  # coverage 0.8 meets the target


def test_missing_mobile_is_error(bundle):
    bundle["frames"] = [f for f in bundle["frames"] if f["role"] == "desktop"]
    r = build_report(bundle)
    assert r["findings"][0]["code"] == "MISSING_MOBILE_FRAME"
    assert r["findings"][0]["severity"] == "error"


def test_low_auto_layout_flags_containers(bundle):
    # The footer has a single child, so it is not counted as a container either way.
    for nid in ("10:2", "10:10", "10:11", "10:30"):
        bundle["nodes"][nid]["layoutMode"] = "NONE"
    r = build_report(bundle)
    low = next(f for f in r["findings"] if f["code"] == "LOW_AUTO_LAYOUT")
    assert set(low["nodeIds"]) == {"10:2", "10:10", "10:11", "10:20", "20:20"}
    assert r["layout"]["autoLayoutCoverage"] == 0.5


def test_missing_assets_reported(bundle):
    uploaded = {a["id"] for a in bundle["assets"]} - {"ref-mobile-2x"}
    r = build_report(bundle, uploaded_assets=uploaded)
    assert r["summary"]["assetUploaded"] == 6
    assert "MISSING_ASSETS" in codes(r)


def test_missing_fields_counted(bundle):
    del bundle["nodes"]["10:12"]["textSegments"]
    r = build_report(bundle)
    assert r["fieldCoverage"]["missingByField"] == {"textSegments": 1}
    assert r["fieldCoverage"]["ratio"] < 1.0


def test_unresolved_variable(bundle):
    bundle["variables"] = []
    r = build_report(bundle)
    assert "UNRESOLVED_VARIABLES" in codes(r)


def test_plugin_diagnostics_forwarded(bundle):
    bundle["diagnostics"] = [
        {"nodeId": "10:12", "code": "MISSING_FONT", "severity": "error", "message": "font missing"},
        {"nodeId": "10:16", "code": "IMAGE_EXPORT_FAILED", "severity": "error", "message": "boom"},
    ]
    r = build_report(bundle)
    assert {"MISSING_FONTS", "PLUGIN_IMAGE_EXPORT_FAILED"} <= codes(r)


def test_legacy_single_action_reactions(bundle):
    node = bundle["nodes"]["10:14"]
    node["reactions"] = [{"trigger": {"type": "ON_CLICK"}, "action": {"type": "URL", "url": "https://x.y"}}]
    r = build_report(bundle)
    assert r["interactions"]["triggers"]["ON_CLICK"] == 1
    assert r["interactions"]["actions"]["URL"] == 1


def test_markdown_renders(bundle):
    md = to_markdown(build_report(bundle))
    assert "# Extraction report — Sample Landing" in md
    assert "GROUPS_WITH_CHILDREN" in md


def _flat_page():
    """A flat page like many real files: loose layers, sections implied by full-width backgrounds."""
    root_box = {"x": 5000, "y": -200, "width": 1900, "height": 1000}

    def node(nid, ntype, x, y, w, h, **extra):
        box = {"x": root_box["x"] + x, "y": root_box["y"] + y, "width": w, "height": h}
        return {
            "id": nid,
            "name": nid,
            "type": ntype,
            "parentId": "root",
            "children": [],
            "visible": True,
            "absoluteBoundingBox": box,
            **extra,
        }

    kids = [
        node("bg-hero", "RECTANGLE", 0, 0, 1900, 600),  # overlaps the next band
        node("hero-title", "TEXT", 400, 100, 500, 80, characters="Big title", fontSize=64),
        node("hero-btn", "GROUP", 400, 300, 160, 50),
        node("bg-about", "RECTANGLE", 0, 500, 1900, 400),
        node("about-title", "TEXT", 800, 540, 300, 50, characters="About Us", fontSize=40),
        node("about-text", "TEXT", 400, 650, 330, 80, characters="Body", fontSize=16),
        node("footer", "RECTANGLE", 0, 900, 1900, 100),
    ]
    root = {
        "id": "root",
        "name": "Page",
        "type": "FRAME",
        "parentId": None,
        "visible": True,
        "children": [k["id"] for k in kids],
        "absoluteBoundingBox": root_box,
    }
    return {"root": root, **{k["id"]: k for k in kids}}


def test_detect_sections_from_background_bands():
    from app.spike_report import detect_sections

    nodes = _flat_page()
    secs = detect_sections(nodes, nodes["root"])
    assert [(s["top"], s["height"]) for s in secs] == [(0.0, 500.0), (500, 400), (900, 100)]
    assert [s["heading"] for s in secs] == ["Big title", "About Us", None]
    assert set(secs[0]["nodeIds"]) == {"bg-hero", "hero-title", "hero-btn"}
    assert set(secs[1]["nodeIds"]) == {"bg-about", "about-title", "about-text"}


def test_group_children_use_absolute_boxes():
    """x/y of group children are frame-relative; detection must rely on absoluteBoundingBox."""
    from app.spike_report import detect_sections

    nodes = _flat_page()
    nodes["about-text"]["y"] = 99999  # misleading relative coordinate must be ignored
    secs = detect_sections(nodes, nodes["root"])
    assert "about-text" in secs[1]["nodeIds"]
