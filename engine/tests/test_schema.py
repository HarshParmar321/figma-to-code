from app.schema import validate_bundle


def test_sample_bundle_is_valid(bundle):
    assert validate_bundle(bundle) == []


def test_missing_required_field(bundle):
    del bundle["frames"]
    errors = validate_bundle(bundle)
    assert any("frames" in e for e in errors)


def test_bad_role_rejected(bundle):
    bundle["frames"][1]["role"] = "watch"
    assert validate_bundle(bundle)


def test_unsafe_asset_id_rejected(bundle):
    bundle["assets"][0]["id"] = "../../etc/passwd"
    assert any("pattern" in e or "does not match" in e for e in validate_bundle(bundle))


def test_dangling_parent_detected(bundle):
    bundle["nodes"]["10:2"]["parentId"] = "999:999"
    assert any("parentId 999:999" in e for e in validate_bundle(bundle))


def test_root_must_exist(bundle):
    bundle["frames"][0]["rootId"] = "nope"
    assert any("rootId nope" in e for e in validate_bundle(bundle))


def test_phone_only_design_is_valid(bundle):
    for f in bundle["frames"]:
        f["role"] = "mobile"  # a phone-only design: its frames are the pages
    assert validate_bundle(bundle) == []


def test_multiple_desktop_pages_allowed(bundle):
    bundle["frames"][1]["role"] = "desktop"
    bundle["frames"][1]["page"] = "About"
    assert validate_bundle(bundle) == []


def test_same_asset_twice_is_kept_once(bundle):
    n = len(bundle["assets"])
    bundle["assets"].append(dict(bundle["assets"][0]))
    assert validate_bundle(bundle) == [] and len(bundle["assets"]) == n
    clash = dict(bundle["assets"][0], nodeId="other")
    bundle["assets"].append(clash)
    assert any("duplicate asset id" in e for e in validate_bundle(bundle))
