"""Engine HTTP API: receive bundles and assets, convert them to code, serve preview and reports."""

import io
import json
import re
import threading
import time
import zipfile
from collections import defaultdict
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel

from . import __version__
from .config import settings
from .pipeline import fonts as fontfiles
from .pipeline.convert import convert
from .pipeline.export import clean_site
from .pipeline.wordpress import build_theme
from .schema import validate_bundle
from .spike_report import build_report
from .storage import BundleStore, DiskFull, NotFound, is_disk_full

_convert_locks: dict[str, threading.Lock] = defaultdict(threading.Lock)
_jobs: dict[str, dict] = {}  # bundle id -> conversion job state (in memory; one job per bundle)

app = FastAPI(title="Figma-to-Code Engine", version=__version__)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["GET", "POST", "PUT"],
    allow_headers=["content-type"],
)
store = BundleStore(settings.data_dir, keep=settings.keep_bundles, min_free_mb=settings.min_free_mb)


@app.exception_handler(DiskFull)
async def _disk_full(_request: Request, exc: DiskFull) -> JSONResponse:
    return JSONResponse(status_code=507, content={"detail": str(exc), "code": "DISK_FULL"})


@app.exception_handler(OSError)
async def _os_error(_request: Request, exc: OSError) -> JSONResponse:
    if is_disk_full(exc):
        return JSONResponse(status_code=507, content={"detail": str(DiskFull(store.free_bytes(), store.min_free)),
                                                      "code": "DISK_FULL"})  # fmt: skip
    raise exc


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
    expected = len(body) + sum(int(a.get("byteLength") or 0) for a in bundle["assets"]) * 2  # assets + output
    running = {bid for bid, j in _jobs.items() if j.get("status") == "running"}
    bundle_id = store.create(bundle, expected_bytes=expected, protect=running)
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


FONT_MAX_BYTES = 15 * 1024 * 1024


@app.get("/v1/fonts")
def list_fonts() -> dict:
    """Font families available for self-hosting, with their files."""
    out: dict[str, list[str]] = {}
    root = settings.font_dir
    if root.exists():
        for folder in sorted(p for p in root.iterdir() if p.is_dir()):
            files = sorted(p.name for p in folder.iterdir() if p.suffix.lower() in fontfiles.EXTENSIONS)
            if files:
                out[folder.name] = files
    return {"fontDir": str(root), "families": out}


@app.put("/v1/fonts/{family}/{filename}")
async def upload_font(family: str, filename: str, request: Request) -> dict:
    """Stores a font file so the next conversion can self-host it (needed for non-Google fonts)."""
    ext = Path(filename).suffix.lower()
    if ext not in fontfiles.EXTENSIONS:
        raise HTTPException(400, "Font files must be .woff2, .woff, .otf or .ttf")
    safe_name = re.sub(r"[^A-Za-z0-9 ._-]+", "_", Path(filename).name).strip() or f"font{ext}"
    data = await request.body()
    if not data:
        raise HTTPException(400, "Empty font file")
    if len(data) > FONT_MAX_BYTES:
        raise HTTPException(413, "Font file is larger than 15 MB")
    folder = settings.font_dir / fontfiles.slug(family)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / safe_name).write_bytes(data)
    parsed = fontfiles.parse(folder / safe_name, family)
    return {
        "family": family,
        "file": safe_name,
        "weight": parsed.weight if parsed else None,
        "style": parsed.style if parsed else None,
        "variable": parsed.variable if parsed else False,
    }


def _links(bundle_id: str) -> dict:
    base = f"{settings.public_url.rstrip('/')}/v1/bundles/{bundle_id}"
    return {
        "previewUrl": f"{base}/output/index.html",
        "reportUrl": f"{base}/fidelity/report.html",
        "zipUrl": f"{base}/code.zip",
        "themeUrl": f"{base}/code.zip?format=wordpress&acf=free",
        "themeProUrl": f"{base}/code.zip?format=wordpress&acf=pro",
    }


def _run_conversion(bundle_id: str, verify: bool) -> dict:
    bundle = store.load(bundle_id)
    meta = store.load_meta(bundle_id)
    bdir = store.bundle_dir(bundle_id)
    job = _jobs[bundle_id]

    def progress(stage: str) -> None:
        job["stage"] = stage
        job["updatedAt"] = time.time()

    with _convert_locks[bundle_id]:
        result = convert(
            bundle,
            bdir,
            font_dir=settings.font_dir,
            chromium_path=settings.chromium_path,
            run_verify=verify,
            asset_hashes={aid: a.get("sha256", "") for aid, a in meta["assets"].items()},
            progress=progress,
        )
    return {"bundleId": bundle_id, **result, **_links(bundle_id)}


def _job_thread(bundle_id: str, verify: bool) -> None:
    job = _jobs[bundle_id]
    try:
        job["result"] = _run_conversion(bundle_id, verify)
        job["status"] = "done"
    except Exception as e:  # surfaced to the plugin instead of a dropped connection
        job["status"] = "failed"
        if is_disk_full(e):
            job["error"] = str(DiskFull(store.free_bytes(), store.min_free))
            job["code"] = "DISK_FULL"
        else:
            job["error"] = f"{type(e).__name__}: {str(e).splitlines()[0][:500] if str(e) else ''}"
    job["finishedAt"] = time.time()


@app.post("/v1/bundles/{bundle_id}/convert")
def convert_bundle(bundle_id: str, verify: bool = True, wait: bool = False):
    """Generates HTML/CSS for every page, then measures, self-corrects and scores it.

    By default this starts a background job and returns at once (202); poll
    `GET /v1/bundles/{id}/convert/status`. `?wait=true` runs it inline and returns the result.
    """
    try:
        bundle = store.load(bundle_id)
        meta = store.load_meta(bundle_id)
    except NotFound as e:
        raise HTTPException(404, "Bundle not found") from e
    missing = [a["id"] for a in bundle["assets"] if a["id"] not in meta["assets"]]
    if missing:
        raise HTTPException(409, {"message": "Upload all assets before converting", "missingAssets": missing})
    current = _jobs.get(bundle_id)
    if current and current["status"] == "running":
        return JSONResponse(_public_job(bundle_id), status_code=202)
    _jobs[bundle_id] = {"status": "running", "stage": "Starting", "startedAt": time.time(), "error": None}
    if wait:
        _job_thread(bundle_id, verify)
        job = _jobs[bundle_id]
        if job["status"] == "failed":
            raise HTTPException(500, job["error"])
        return job["result"]
    threading.Thread(target=_job_thread, args=(bundle_id, verify), daemon=True).start()
    return JSONResponse(_public_job(bundle_id), status_code=202)


def _public_job(bundle_id: str) -> dict:
    job = _jobs.get(bundle_id)
    if not job:
        return {"bundleId": bundle_id, "status": "idle"}
    out = {k: v for k, v in job.items() if k != "result"}
    out["bundleId"] = bundle_id
    out["elapsed"] = round((job.get("finishedAt") or time.time()) - job["startedAt"], 1)
    if job["status"] == "done":
        out["result"] = job["result"]
    return out


@app.get("/v1/bundles/{bundle_id}/convert/status")
def convert_status(bundle_id: str) -> dict:
    try:
        store.bundle_dir(bundle_id)
    except NotFound as e:
        raise HTTPException(404, "Bundle not found") from e
    return _public_job(bundle_id)


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
def download_code(bundle_id: str, format: str = "html", acf: str = "free") -> Response:
    """The generated code as a zip, built on request.

    `format=html` (default): clean HTML/CSS (no data-figma-id, corrections folded into the classes).
    `format=wordpress`: a WordPress theme with PHP templates and ACF fields; `acf=free` or `acf=pro`.
    The live preview keeps the instrumented version for the design check."""
    if format not in ("html", "wordpress") or acf not in ("free", "pro"):
        raise HTTPException(400, "format must be html or wordpress; acf must be free or pro")
    try:
        bdir = store.bundle_dir(bundle_id)
    except NotFound as e:
        raise HTTPException(404, "Bundle not found") from e
    out = bdir / "output"
    if not (out / "index.html").exists():
        raise HTTPException(404, "Not converted yet")
    bundle = store.load(bundle_id)
    meta = bundle["source"]
    name = "".join(c if c.isalnum() or c in "-_" else "-" for c in meta.get("fileName", "design")).strip("-")
    if format == "wordpress":
        conv_path = bdir / "conversion.json"
        conversion = json.loads(conv_path.read_text(encoding="utf-8")) if conv_path.exists() else {}
        files = build_theme(out, conversion, acf, _reference_png(bundle, bdir))
        theme = next(iter(files)).split("/")[0] if files else "theme"
        filename = f"{theme}-wordpress-{'acf-pro' if acf == 'pro' else 'acf'}.zip"
    else:
        files = clean_site(out, meta.get("fileName", "Site"))
        filename = f"{name or 'design'}-code.zip"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for rel, data in files.items():
            z.writestr(rel, data)
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return Response(buf.getvalue(), media_type="application/zip", headers=headers)


def _reference_png(bundle: dict, bdir: Path) -> Path | None:
    """The widest frame's 1x reference image (used for the theme screenshot)."""
    for frame in sorted(bundle.get("frames", []), key=lambda f: -float(f.get("width") or 0)):
        for asset_id in frame.get("referenceAssets", []):
            f = bdir / "assets" / f"{asset_id}.png"
            if f.exists() and asset_id.endswith("1x"):
                return f
    return None


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
