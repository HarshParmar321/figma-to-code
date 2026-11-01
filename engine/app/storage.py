"""Local filesystem storage for bundles and assets. Swap for S3/R2 later behind the same interface."""

import errno
import hashlib
import json
import re
import shutil
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

ASSET_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
BUNDLE_ID_RE = re.compile(r"^b_[0-9a-f]{32}$")
EXTENSIONS = {"PNG": "png", "JPG": "jpg", "SVG": "svg", "GIF": "gif", "WEBP": "webp"}


class NotFound(Exception):
    pass


class DiskFull(Exception):
    """Not enough free disk space for a new conversion (even after removing old ones)."""

    def __init__(self, free_bytes: int, needed_bytes: int):
        self.free_bytes, self.needed_bytes = free_bytes, needed_bytes
        super().__init__(
            f"The disk is full: {free_bytes // 2**20} MB free, about {needed_bytes // 2**20} MB needed. "
            "Old conversions were already removed. Free some space on this computer and try again."
        )


def is_disk_full(err: BaseException) -> bool:
    return isinstance(err, OSError) and err.errno in (errno.ENOSPC, errno.EDQUOT)


@dataclass
class StoredAsset:
    id: str
    path: Path
    sha256: str
    byte_length: int


class BundleStore:
    def __init__(self, root: Path, keep: int = 8, min_free_mb: int = 400):
        self.root = root
        self.keep = keep
        self.min_free = min_free_mb * 2**20

    # ------------------------------------------------------------------ housekeeping
    def free_bytes(self) -> int:
        path = self.root
        while not path.exists() and path != path.parent:
            path = path.parent
        return shutil.disk_usage(path).free

    def bundles_oldest_first(self) -> list[Path]:
        base = self.root / "bundles"
        if not base.exists():
            return []
        dirs = [d for d in base.iterdir() if d.is_dir() and BUNDLE_ID_RE.match(d.name)]
        return sorted(dirs, key=lambda d: d.stat().st_mtime)

    def cleanup(self, keep: int | None = None, protect: set[str] | frozenset = frozenset()) -> list[str]:
        """Deletes the oldest conversions beyond `keep`. Returns the removed bundle ids."""
        keep = self.keep if keep is None else keep
        dirs = [d for d in self.bundles_oldest_first() if d.name not in protect]
        removed = []
        for d in dirs[: max(0, len(dirs) - keep)]:
            shutil.rmtree(d, ignore_errors=True)
            removed.append(d.name)
        return removed

    def ensure_space(self, needed: int = 0, protect: set[str] | frozenset = frozenset()) -> None:
        """Frees space by removing old conversions (oldest first); raises DiskFull if still too little."""
        want = self.min_free + needed
        if self.free_bytes() >= want:
            return
        for d in self.bundles_oldest_first():
            if d.name in protect:
                continue
            shutil.rmtree(d, ignore_errors=True)
            if self.free_bytes() >= want:
                return
        free = self.free_bytes()
        if free < want:
            raise DiskFull(free, want)

    def _dir(self, bundle_id: str) -> Path:
        if not BUNDLE_ID_RE.match(bundle_id):
            raise NotFound(bundle_id)
        return self.root / "bundles" / bundle_id

    def create(self, bundle: dict, expected_bytes: int = 0, protect: set[str] | frozenset = frozenset()) -> str:
        self.cleanup(keep=max(0, self.keep - 1), protect=protect)  # room for this one
        self.ensure_space(expected_bytes, protect=protect)
        bundle_id = f"b_{uuid.uuid4().hex}"
        d = self._dir(bundle_id)
        try:
            (d / "assets").mkdir(parents=True)
            (d / "bundle.json").write_text(json.dumps(bundle), encoding="utf-8")
            meta = {"bundleId": bundle_id, "receivedAt": datetime.now(UTC).isoformat(), "assets": {}}
            (d / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        except OSError as e:
            shutil.rmtree(d, ignore_errors=True)
            if is_disk_full(e):
                raise DiskFull(self.free_bytes(), self.min_free + expected_bytes) from e
            raise
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

    def bundle_dir(self, bundle_id: str) -> Path:
        d = self._dir(bundle_id)
        if not d.exists():
            raise NotFound(bundle_id)
        return d

    def safe_file(self, bundle_id: str, subdir: str, rel: str) -> Path:
        """A file inside <bundle>/<subdir>, refusing any path that escapes it."""
        base = (self.bundle_dir(bundle_id) / subdir).resolve()
        target = (base / rel).resolve()
        if base != target and base not in target.parents:
            raise NotFound(rel)
        if target.is_dir():
            target = target / "index.html"
        if not target.is_file():
            raise NotFound(rel)
        return target

    def save_report(self, bundle_id: str, report: dict) -> None:
        (self._dir(bundle_id) / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
