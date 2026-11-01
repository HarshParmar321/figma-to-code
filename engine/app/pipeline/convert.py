"""One-call conversion: bundle → IR → layout → HTML/CSS → (optional) render and score → zip."""

from __future__ import annotations

import json
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path

from .fidelity import verify, write_report_html
from .generate import generate
from .ir import build_ir
from .layout import layout_page


def convert(
    bundle: dict,
    bundle_dir: Path,
    font_dir: Path | None = None,
    chromium_path: str | None = None,
    run_verify: bool = True,
    role: str = "desktop",
) -> dict:
    started = time.time()
    pages = build_ir(bundle)
    if role not in pages:
        role = next(iter(pages))
    page = layout_page(pages[role])
    out_dir = bundle_dir / "output"
    gen = generate(page, bundle, bundle_dir / "assets", out_dir, font_dir=font_dir)

    zip_base = bundle_dir / "code"
    zip_path = Path(shutil.make_archive(str(zip_base), "zip", root_dir=out_dir))

    result: dict = {
        "generatedAt": datetime.now(UTC).isoformat(),
        "frame": role,
        "files": sorted(str(p.relative_to(out_dir)) for p in out_dir.rglob("*") if p.is_file()),
        "fonts": gen.fonts,
        "assumptions": gen.assumptions,
        "warnings": gen.warnings,
        "zip": zip_path.name,
        "fidelity": None,
    }

    if run_verify:
        ref = bundle_dir / "assets" / f"ref-{role}-1x.png"
        if not ref.exists():
            result["warnings"].append("No 1x reference image; fidelity check skipped.")
        else:
            try:
                fid = verify(out_dir, bundle_dir / "fidelity", page, gen.expectations, ref, chromium_path)
                write_report_html(fid, bundle_dir / "fidelity", bundle["source"].get("fileName", "Design"))
                result["fidelity"] = {
                    k: fid[k] for k in ("score", "band", "subScores", "counts", "sections", "missingFonts")
                }
                result["fidelity"]["topIssues"] = fid["issues"][:10]
            except Exception as e:  # browser missing, crash: generation still succeeds
                msg = str(e).splitlines()[0][:300]
                result["warnings"].append(f"Fidelity check skipped: {msg}")
                if "Executable doesn't exist" in str(e):
                    result["warnings"].append("Install the browser once with: uv run playwright install chromium")
    result["seconds"] = round(time.time() - started, 2)
    (bundle_dir / "conversion.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result
