"""Local filesystem storage for bundles and assets. Swap for S3/R2 later behind the same interface."""

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

ASSET_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
BUNDLE_ID_RE = re.compile(r"^b_[0-9a-f]{32}$")
EXTENSIONS = {"PNG": "png", "JPG": "jpg", "SVG": "svg", "GIF": "gif", "WEBP": "webp"}


class NotFound(Exception):
    pass


@dataclass
class StoredAsset:
    id: str
    path: Path
    sha256: str
    byte_length: int


class BundleStore:
    def __init__(self, root: Path):
        self.root = root

    def _dir(self, bundle_id: str) -> Path:
        if not BUNDLE_ID_RE.match(bundle_id):
            raise NotFound(bundle_id)
        return self.root / "bundles" / bundle_id

    def create(self, bundle: dict) -> str:
        bundle_id = f"b_{uuid.uuid4().hex}"
        d = self._dir(bundle_id)
        (d / "assets").mkdir(parents=True)
        (d / "bundle.json").write_text(json.dumps(bundle), encoding="utf-8")
        meta = {"bundleId": bundle_id, "receivedAt": datetime.now(UTC).isoformat(), "assets": {}}
        (d / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        return bundle_id

    def load(self, bundle_id: str) -> dict:
        path = self._dir(bundle_id) / "bundle.json"
        if not path.exists():
            raise NotFound(bundle_id)
        return json.loads(path.read_text(encoding="utf-8"))

    def load_meta(self, bundle_id: str) -> dict:
        path = self._dir(bundle_id) / "meta.json"
        if not path.exists():
            raise NotFound(bundle_id)
        return json.loads(path.read_text(encoding="utf-8"))

    def put_asset(self, bundle_id: str, asset_id: str, fmt: str, data: bytes) -> StoredAsset:
        if not ASSET_ID_RE.match(asset_id):
            raise ValueError("invalid asset id")
        d = self._dir(bundle_id)
        if not d.exists():
            raise NotFound(bundle_id)
        path = d / "assets" / f"{asset_id}.{EXTENSIONS.get(fmt, 'bin')}"
        path.write_bytes(data)
        stored = StoredAsset(asset_id, path, hashlib.sha256(data).hexdigest(), len(data))
        meta = self.load_meta(bundle_id)
        meta["assets"][asset_id] = {
            "file": path.name,
            "sha256": stored.sha256,
            "byteLength": stored.byte_length,
            "uploadedAt": datetime.now(UTC).isoformat(),
        }
        (d / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        return stored

    def save_report(self, bundle_id: str, report: dict) -> None:
        (self._dir(bundle_id) / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
