from app.spike_report import build_report, to_markdown


def codes(report):
    return {f["code"] for f in report["findings"]}


def test_sample_metrics(bundle):
    r = build_report(bundle)
    assert r["summary"]["nodeCount"] == 34
    assert r["layout"]["autoLayoutCoverage"] == 1.0
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
    assert codes(r) == {"GROUPS_WITH_CHILDREN"}


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
    assert set(low["nodeIds"]) == {"10:2", "10:10", "10:11"}
    assert r["layout"]["autoLayoutCoverage"] == 0.625


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
