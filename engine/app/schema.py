"""Validates plugin bundles against schemas/bundle.schema.json (the shared contract)."""

import json
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator

from .config import settings


@lru_cache(maxsize=4)
def _validator(path: Path) -> Draft202012Validator:
    schema = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def validate_bundle(bundle: object, limit: int = 20) -> list[str]:
    """Returns up to `limit` readable error messages; an empty list means the bundle is valid."""
    validator = _validator(settings.schema_path)
    errors = sorted(validator.iter_errors(bundle), key=lambda e: list(e.absolute_path))
    messages = []
    for err in errors[:limit]:
        where = "/".join(str(p) for p in err.absolute_path) or "(root)"
        messages.append(f"{where}: {err.message}")
    messages.extend(_integrity_errors(bundle) if not errors else [])
    return messages[:limit]


def _integrity_errors(bundle: dict) -> list[str]:
    """Cross-reference checks the JSON Schema cannot express."""
    out: list[str] = []
    nodes = bundle["nodes"]
    for frame in bundle["frames"]:
        if frame["rootId"] not in nodes:
            out.append(f"frames: rootId {frame['rootId']} is not in nodes")
    for node_id, node in nodes.items():
        if node["id"] != node_id:
            out.append(f"nodes/{node_id}: id field is {node['id']}")
        parent = node["parentId"]
        if parent is not None and parent not in nodes:
            out.append(f"nodes/{node_id}: parentId {parent} is not in nodes")
    asset_ids = [a["id"] for a in bundle["assets"]]
    if len(asset_ids) != len(set(asset_ids)):
        out.append("assets: duplicate asset ids")
    # Any frame can be a page: without a desktop frame, the widest frames (tablet, else mobile) are.
    return out
