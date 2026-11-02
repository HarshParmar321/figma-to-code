"""Render the generated WordPress theme with tests/php/wp_stub.php and compare it with the HTML."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from app.pipeline.wordpress import Node, find, parse

STUB = Path(__file__).parent / "php" / "wp_stub.php"


def php_available() -> bool:
    return shutil.which("php") is not None


def render(theme: Path, template: str, fields=None, option=None, rows=None, front_id: int = 1) -> str:
    stub = {"theme": str(theme), "template": template, "options": {"page_on_front": front_id}}
    if fields is not None:
        stub["fields"] = fields
        stub["option"] = option or {}
        if rows is not None:
            stub["rows"] = rows
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(stub, f)
    out = subprocess.run(["php", str(STUB)], env={"F2C_STUB": f.name, "PATH": "/usr/bin:/bin"},
                         capture_output=True, text=True, timeout=60)  # fmt: skip
    Path(f.name).unlink()
    if out.returncode != 0 or "Warning" in out.stdout or "Fatal" in out.stdout:
        raise AssertionError(out.stdout[-2000:] + out.stderr[-2000:])
    return out.stdout


def _url(v: str) -> str:
    v = re.sub(r"^(theme/assets/|http://site\.test)", "", v)
    if v.startswith("/") and not v.startswith("/#"):
        page = v.strip("/") or "index"
        return f"{page}.html"
    return v.lstrip("/") if v.startswith("/#") else v


def tokens(html: str) -> list:
    """Markup of <body> as comparable tokens (internal attributes and WordPress plumbing ignored)."""
    main = find(parse(html), "body")
    out: list = []

    def visit(n: Node) -> None:
        for c in n.children:
            if isinstance(c, str):
                t = " ".join(c.split())
                if t:
                    out.append(("text", t))
                continue
            if c.tag == "input" and c.get("type") == "hidden":
                continue
            attrs = []
            for k, v in c.attrs:
                if k.startswith("data-") or (c.tag == "form" and k in ("method", "action")):
                    continue
                if k in ("src", "href") and v:
                    v = _url(v)
                attrs.append((k, v))
            out.append(("open", c.tag, tuple(sorted(attrs))))
            visit(c)
            out.append(("close", c.tag))

    visit(main)
    return out
