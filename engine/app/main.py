"""Engine HTTP API: receive bundles and assets, convert them to code, serve preview and reports."""

import json
import threading
from collections import defaultdict

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import __version__
from .config import settings
from .pipeline.convert import convert
from .schema import validate_bundle
from .spike_report import build_report
from .storage import BundleStore, NotFound

_convert_locks: dict[str, threading.Lock] = defaultdict(threading.Lock)

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


@app.get("/")
def root() -> dict:
    return {
        "service": "Figma-to-Code Engine",
        "version": __version__,
        "health": "/health",
        "docs": "/docs",
    }


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


def _links(bundle_id: str) -> dict:
    base = f"{settings.public_url.rstrip('/')}/v1/bundles/{bundle_id}"
    return {
        "previewUrl": f"{base}/output/index.html",
        "reportUrl": f"{base}/fidelity/report.html",
        "zipUrl": f"{base}/code.zip",
    }


@app.post("/v1/bundles/{bundle_id}/convert")
def convert_bundle(bundle_id: str, verify: bool = True) -> dict:
    """Runs the whole pipeline: Design IR → layout → HTML/CSS → render and score. Takes a few seconds."""
    try:
        bundle = store.load(bundle_id)
        meta = store.load_meta(bundle_id)
        bdir = store.bundle_dir(bundle_id)
    except NotFound as e:
        raise HTTPException(404, "Bundle not found") from e
    missing = [a["id"] for a in bundle["assets"] if a["id"] not in meta["assets"]]
    if missing:
        raise HTTPException(409, {"message": "Upload all assets before converting", "missingAssets": missing})
    with _convert_locks[bundle_id]:
        result = convert(
            bundle,
            bdir,
            font_dir=settings.font_dir,
            chromium_path=settings.chromium_path,
            run_verify=verify,
        )
    return {"bundleId": bundle_id, **result, **_links(bundle_id)}


@app.get("/v1/bundles/{bundle_id}/conversion")
def get_conversion(bundle_id: str) -> dict:
    try:
        path = store.bundle_dir(bundle_id) / "conversion.json"
    except NotFound as e:
        raise HTTPException(404, "Bundle not found") from e
    if not path.exists():
        raise HTTPException(404, "Not converted yet")
    return {"bundleId": bundle_id, **json.loads(path.read_text(encoding="utf-8")), **_links(bundle_id)}


@app.get("/v1/bundles/{bundle_id}/code.zip")
def download_code(bundle_id: str) -> FileResponse:
    try:
        path = store.bundle_dir(bundle_id) / "code.zip"
    except NotFound as e:
        raise HTTPException(404, "Bundle not found") from e
    if not path.exists():
        raise HTTPException(404, "Not converted yet")
    meta = store.load(bundle_id)["source"]
    name = "".join(c if c.isalnum() or c in "-_" else "-" for c in meta.get("fileName", "design")).strip("-")
    return FileResponse(path, media_type="application/zip", filename=f"{name or 'design'}-code.zip")


@app.get("/v1/bundles/{bundle_id}/output/{path:path}")
def serve_output(bundle_id: str, path: str) -> FileResponse:
    return _serve(bundle_id, "output", path)


@app.get("/v1/bundles/{bundle_id}/fidelity/{path:path}")
def serve_fidelity(bundle_id: str, path: str) -> FileResponse:
    return _serve(bundle_id, "fidelity", path)


def _serve(bundle_id: str, subdir: str, path: str) -> FileResponse:
    try:
        target = store.safe_file(bundle_id, subdir, path or "index.html")
    except NotFound as e:
        raise HTTPException(404, "Not found") from e
    return FileResponse(target, headers={"Cache-Control": "no-store"})
