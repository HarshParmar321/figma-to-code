import json


def upload_all(client, bundle_id, bundle):
    for asset in bundle["assets"]:
        res = client.put(f"/v1/bundles/{bundle_id}/assets/{asset['id']}", content=b"x" * asset["byteLength"])
        assert res.status_code == 200, res.text


def test_health(client):
    assert client.get("/health").json()["status"] == "ok"


def test_full_round_trip(client, bundle):
    res = client.post("/v1/bundles", content=json.dumps(bundle))
    assert res.status_code == 201, res.text
    bundle_id = res.json()["bundleId"]
    assert len(res.json()["assetIds"]) == 7

    meta = client.get(f"/v1/bundles/{bundle_id}").json()
    assert len(meta["missingAssets"]) == 7

    upload_all(client, bundle_id, bundle)
    meta = client.get(f"/v1/bundles/{bundle_id}").json()
    assert meta["missingAssets"] == []
    assert len(meta["assets"]["ref-desktop-1x"]["sha256"]) == 64

    report = client.get(f"/v1/bundles/{bundle_id}/report").json()
    assert report["bundleId"] == bundle_id
    assert report["summary"]["assetUploaded"] == 7
    assert "MISSING_ASSETS" not in {f["code"] for f in report["findings"]}


def test_report_flags_missing_assets(client, bundle):
    bundle_id = client.post("/v1/bundles", content=json.dumps(bundle)).json()["bundleId"]
    report = client.get(f"/v1/bundles/{bundle_id}/report").json()
    assert report["findings"][0]["code"] == "MISSING_ASSETS"


def test_invalid_bundle_rejected(client, bundle):
    bundle["schemaVersion"] = "9.9"
    res = client.post("/v1/bundles", content=json.dumps(bundle))
    assert res.status_code == 422
    assert res.json()["detail"]["errors"]


def test_invalid_json_rejected(client):
    assert client.post("/v1/bundles", content=b"{not json").status_code == 400


def test_undeclared_asset_rejected(client, bundle):
    bundle_id = client.post("/v1/bundles", content=json.dumps(bundle)).json()["bundleId"]
    assert client.put(f"/v1/bundles/{bundle_id}/assets/not-declared", content=b"x").status_code == 404


def test_asset_size_mismatch_rejected(client, bundle):
    bundle_id = client.post("/v1/bundles", content=json.dumps(bundle)).json()["bundleId"]
    res = client.put(f"/v1/bundles/{bundle_id}/assets/ref-desktop-1x", content=b"too long for declared size")
    assert res.status_code == 400


def test_unknown_or_malformed_bundle_id(client):
    assert client.get("/v1/bundles/b_" + "0" * 32).status_code == 404
    assert client.get("/v1/bundles/../../etc/report").status_code == 404


def test_cors_allows_figma_null_origin(client):
    res = client.options(
        "/v1/bundles",
        headers={
            "Origin": "null",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert res.status_code == 200
    assert res.headers["access-control-allow-origin"] in ("*", "null")
