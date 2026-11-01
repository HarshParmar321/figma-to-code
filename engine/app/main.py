"""Engine HTTP API (Spike 1): receive bundles and assets, return the extraction report."""

import json

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import __version__
from .config import settings
from .schema import validate_bundle
from .spike_report import build_report
from .storage import BundleStore, NotFound

app = FastAPI(title="Figma-to-Code Engine", version=__version__)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["GET", "POST", "PUT"],
    allow_headers=["content-type"],
)
store = BundleStore(settings.data_dir)


class BundleCreated(BaseModel):
    bundleId: str
    assetIds: list[str]


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": __version__}


@app.post("/v1/bundles", status_code=201, response_model=BundleCreated)
async def create_bundle(request: Request) -> BundleCreated:
    body = await request.body()
    if len(body) > settings.max_bundle_bytes:
        raise HTTPException(413, f"Bundle JSON exceeds {settings.max_bundle_bytes} bytes")
    try:
        bundle = json.loads(body)
    except json.JSONDecodeError as e:
        raise HTTPException(400, f"Invalid JSON: {e}") from e
    errors = validate_bundle(bundle)
    if errors:
        raise HTTPException(422, {"message": "Bundle does not match schema v0.1", "errors": errors})
    bundle_id = store.create(bundle)
    return BundleCreated(bundleId=bundle_id, assetIds=[a["id"] for a in bundle["assets"]])


@app.put("/v1/bundles/{bundle_id}/assets/{asset_id}")
async def upload_asset(bundle_id: str, asset_id: str, request: Request) -> dict:
    try:
        bundle = store.load(bundle_id)
    except NotFound as e:
        raise HTTPException(404, "Bundle not found") from e
    declared = {a["id"]: a for a in bundle["assets"]}
    if asset_id not in declared:
        raise HTTPException(404, f"Asset {asset_id} is not declared in the bundle")
    data = await request.body()
    if not data:
        raise HTTPException(400, "Empty asset body")
    if len(data) > settings.max_asset_bytes:
        raise HTTPException(413, f"Asset exceeds {settings.max_asset_bytes} bytes")
    expected = declared[asset_id].get("byteLength")
    if expected is not None and expected != len(data):
        raise HTTPException(400, f"Asset size {len(data)} does not match declared byteLength {expected}")
    stored = store.put_asset(bundle_id, asset_id, declared[asset_id]["format"], data)
    return {"assetId": asset_id, "sha256": stored.sha256, "byteLength": stored.byte_length}


@app.get("/v1/bundles/{bundle_id}")
def get_bundle_meta(bundle_id: str) -> dict:
    try:
        bundle = store.load(bundle_id)
        meta = store.load_meta(bundle_id)
    except NotFound as e:
        raise HTTPException(404, "Bundle not found") from e
    declared = [a["id"] for a in bundle["assets"]]
    return {
        **meta,
        "source": bundle["source"],
        "frames": bundle["frames"],
        "missingAssets": [a for a in declared if a not in meta["assets"]],
    }


@app.get("/v1/bundles/{bundle_id}/report")
def get_report(bundle_id: str) -> dict:
    try:
        bundle = store.load(bundle_id)
        meta = store.load_meta(bundle_id)
    except NotFound as e:
        raise HTTPException(404, "Bundle not found") from e
    report = build_report(bundle, uploaded_assets=set(meta["assets"]), bundle_id=bundle_id)
    store.save_report(bundle_id, report)
    return report
