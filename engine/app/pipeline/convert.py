"""Conversion: bundle → pages → shared components → HTML/CSS site → measure, self-correct, score → zip."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from . import corrections as corr
from . import quality
from .components import detect_shared_sections, split_shared_strips
from .fidelity import Renderer, verify, write_compare_images, write_report_html, write_site_report
from .generate import generate_site
from .ir import build_pages, site_pages
from .layout import layout_page, share_container

MAX_ROUNDS = 6
CHECK_BUDGET_S = 120.0  # correction rounds stop after this much checking time (per conversion)
Progress = Callable[[str], None]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "page"


def _reference(bundle_dir: Path, frame: dict, assets: dict[str, dict]) -> Path | None:
    for aid in frame.get("referenceAssets") or []:
        a = assets.get(aid, {})
        if a.get("scale", 1) == 1 and (bundle_dir / "assets" / f"{aid}.png").exists():
            return bundle_dir / "assets" / f"{aid}.png"
    legacy = bundle_dir / "assets" / f"ref-{frame['role']}-1x.png"  # v0.1 bundles
    return legacy if legacy.exists() else None


def convert(
    bundle: dict,
    bundle_dir: Path,
    font_dir: Path | None = None,
    chromium_path: str | None = None,
    run_verify: bool = True,
    role: str = "desktop",  # kept for compatibility; every desktop frame is now a page
    asset_hashes: dict[str, str] | None = None,
    progress: Progress | None = None,
    max_rounds: int = MAX_ROUNDS,
) -> dict:
    say = progress or (lambda _msg: None)
    started = time.time()
    asset_hashes = dict(asset_hashes or {})
    assets = {a["id"]: a for a in bundle["assets"]}
    # Content hashes for any asset the caller did not hash (same bytes = same image in every frame).
    for a in bundle["assets"]:
        if a["id"] not in asset_hashes:
            ext = {"PNG": "png", "JPG": "jpg", "SVG": "svg", "GIF": "gif", "WEBP": "webp"}.get(a["format"], "bin")
            f = bundle_dir / "assets" / f"{a['id']}.{ext}"
            if f.exists():
                asset_hashes[a["id"]] = hashlib.sha256(f.read_bytes()).hexdigest()

    say("Reading layers")
    groups = site_pages(build_pages(bundle))
    if not groups:  # no desktop frame: use whatever was selected
        groups = [{"name": p.name, "desktop": p, "tablet": None, "mobile": None} for p in build_pages(bundle)[:1]]
    pages = [g["desktop"] for g in groups]

    say("Finding shared header, footer and sections")
    split_shared_strips(pages, asset_hashes)
    for sec_id, name in detect_shared_sections(pages, asset_hashes).items():
        for p in pages:
            for sec in p.sections:
                if id(sec) == sec_id:
                    sec.component = name

    say("Building layout")
    for p in pages:
        layout_page(p)
    share_container(pages)
    for g in groups:
        for role_name in ("tablet", "mobile"):
            if g.get(role_name) is not None and g[role_name] is not g["desktop"]:
                layout_page(g[role_name])

    files, used = [], set()
    for i, p in enumerate(pages):
        name = "index.html" if i == 0 else f"{_slug(p.name)}.html"
        while name in used:
            name = name.replace(".html", "-2.html")
        used.add(name)
        files.append(name)

    say("Writing HTML and CSS")
    out_dir = bundle_dir / "output"
    frames = {
        file: {r: g[r] for r in ("tablet", "mobile") if g.get(r) is not None and g[r] is not g["desktop"]}
        for g, file in zip(groups, files, strict=True)
    }
    gen = generate_site(
        list(zip(pages, files, strict=True)),
        bundle,
        bundle_dir / "assets",
        out_dir,
        font_dir=font_dir,
        asset_hashes=asset_hashes,
        frames=frames,
    )

    result: dict = {
        "generatedAt": datetime.now(UTC).isoformat(),
        "pages": [],
        "components": gen.components,
        "fonts": gen.fonts,
        "assumptions": gen.assumptions,
        "warnings": gen.warnings,
        "fidelity": None,
    }
    merged = [f"{g['name']} ({', '.join(r for r in ('tablet', 'mobile') if g.get(r) is not None)})"
              for g in groups if g.get("mobile") is not None or g.get("tablet") is not None]  # fmt: skip
    if merged:
        result["assumptions"].append("Breakpoint frames merged into media queries: " + "; ".join(merged) + ".")

    fid_dir = bundle_dir / "fidelity"
    if fid_dir.exists():
        shutil.rmtree(fid_dir)
    page_results = []
    breakpoint_results: dict[str, dict] = {}
    if run_verify:
        try:
            with Renderer(chromium_path) as renderer:
                fixes: dict[str, dict] = {}
                check_start = time.monotonic()
                for i, (p, file, gp) in enumerate(zip(pages, files, gen.pages, strict=True)):
                    ref = _reference(bundle_dir, p.frame, assets)
                    label = f"{p.name} ({i + 1}/{len(pages)})" if len(pages) > 1 else p.name
                    if ref is None:
                        result["warnings"].append(f"{p.name}: no reference image; design check skipped.")
                        continue
                    pdir = fid_dir / _slug(p.name) if len(pages) > 1 else fid_dir
                    first_score, rounds, fid = None, 0, None
                    for rnd in range(max_rounds + 1):
                        say(f"Checking {label} against the design" + (f" (correction round {rnd})" if rnd else ""))
                        over = time.monotonic() - check_start > CHECK_BUDGET_S
                        final = rnd == max_rounds or over
                        fid = verify(out_dir, pdir, p, gp["expectations"], ref, renderer=renderer,
                                     html_file=file, write_images=False)  # fmt: skip
                        first_score = fid["score"] if first_score is None else first_score
                        if final or not corr.step(fid["measurements"], fixes):
                            break
                        rounds += 1
                        corr.write(out_dir, fixes)
                    if not renderer.ready:
                        result["warnings"].append(f"{p.name}: some images or fonts took over 20 s to load.")
                    if time.monotonic() - check_start > CHECK_BUDGET_S:
                        result["warnings"].append("Correction rounds stopped early: the time budget was reached.")
                    write_compare_images(ref, pdir / "render.png", pdir)
                    back = "../../output/" if len(pages) > 1 else "../output/"
                    write_report_html(fid, pdir, f"{p.name} · {bundle['source'].get('fileName', 'Design')}",
                                      preview_href=back + file)  # fmt: skip
                    applied = sum(1 for nid in fixes if nid in fid["measurements"])
                    page_results.append((p, file, fid, first_score, rounds, applied, pdir))
                    for frame_role, fpage, fexp in getattr(gen, "frame_checks", {}).get(file, []):
                        fref = _reference(bundle_dir, fpage.frame, assets)
                        if fref is None or not fexp:
                            continue
                        say(f"Checking {label} on {frame_role} against the {frame_role} frame")
                        fdir = pdir / frame_role
                        ffid = verify(out_dir, fdir, fpage, fexp, fref, renderer=renderer, html_file=file)
                        write_compare_images(fref, fdir / "render.png", fdir)
                        title = f"{p.name} ({frame_role}) · {bundle['source'].get('fileName', 'Design')}"
                        write_report_html(ffid, fdir, title, preview_href="../" + back + file)
                        breakpoint_results.setdefault(file, {})[frame_role] = {
                            k: ffid[k] for k in ("score", "band", "subScores", "counts", "sections", "missingFonts")
                        } | {"topIssues": ffid["issues"][:10], "width": fpage.width,
                             "report": str(fdir.relative_to(bundle_dir) / "report.html")}  # fmt: skip
                say("Checking code quality and screen sizes")
                widths = {f: round(pg.width) for pg, f in zip(pages, files, strict=True)}
                result["quality"] = quality.analyze(out_dir, files, renderer, widths)
        except Exception as e:  # browser missing or crashed: the code is still generated
            msg = str(e).splitlines()[0][:300]
            result["warnings"].append(f"Design check skipped: {msg}")
            if "Executable doesn't exist" in str(e):
                result["warnings"].append("Install the browser once with: uv run playwright install chromium")

    for p, file in zip(pages, files, strict=True):
        entry: dict = {"name": p.name, "file": file, "frame": p.frame.get("name"), "width": p.width}
        match = next((r for r in page_results if r[0] is p), None)
        if match:
            _, _, fid, first, rounds, applied, pdir = match
            entry["fidelity"] = {
                k: fid[k] for k in ("score", "band", "subScores", "counts", "sections", "missingFonts")
            }
            entry["fidelity"].update({"topIssues": fid["issues"][:10], "firstScore": first,
                                      "correctionRounds": rounds, "corrections": applied})  # fmt: skip
            entry["report"] = str(pdir.relative_to(bundle_dir) / "report.html")
        if file in breakpoint_results:
            entry["breakpoints"] = breakpoint_results[file]
        result["pages"].append(entry)

    scored = [pg["fidelity"] for pg in result["pages"] if pg.get("fidelity")]
    if scored:
        weights = [max(1, f["counts"]["elements"]) for f in scored]
        score = sum(f["score"] * w for f, w in zip(scored, weights, strict=True)) / sum(weights)
        if any(f["counts"]["critical"] for f in scored):
            score = min(score, 69.0)
        worst = min(scored, key=lambda f: f["score"])
        result["fidelity"] = {
            "score": round(score, 1),
            "band": "Verified"
            if score >= 90
            else "Close"
            if score >= 75
            else "Needs work"
            if score >= 60
            else "Failed",
            "subScores": worst["subScores"] if len(scored) > 1 else scored[0]["subScores"],
            "counts": {k: sum(f["counts"][k] for f in scored) for k in scored[0]["counts"]},
            "sections": [s for f in scored for s in f["sections"]],
            "missingFonts": sorted({m for f in scored for m in f["missingFonts"]}),
            "topIssues": [i for f in scored for i in f["topIssues"]][:10],
            "firstScore": round(
                sum(f["firstScore"] * w for f, w in zip(scored, weights, strict=True)) / sum(weights), 1
            ),
            "corrections": sum(f["corrections"] for f in scored),
            "pages": len(scored),
        }
        if len(scored) > 1:
            write_site_report(result, fid_dir, bundle["source"].get("fileName", "Design"))
    for frame_role in ("tablet", "mobile"):
        got = [pg["breakpoints"][frame_role] for pg in result["pages"] if frame_role in pg.get("breakpoints", {})]
        if got:
            w = [max(1, f["counts"]["elements"]) for f in got]
            sc = sum(f["score"] * x for f, x in zip(got, w, strict=True)) / sum(w)
            if any(f["counts"]["critical"] for f in got):
                sc = min(sc, 69.0)
            result[frame_role] = {"score": round(sc, 1), "pages": len(got),
                                  "elementsInPlace": sum(f["counts"]["geometryPass"] for f in got),
                                  "elements": sum(f["counts"]["elements"] for f in got)}  # fmt: skip

    if "quality" not in result:
        result["quality"] = quality.analyze(out_dir, files)  # structure only (no browser check ran)
    (bundle_dir / "code.zip").unlink(missing_ok=True)  # zips are built on download now
    result["files"] = sorted(str(p.relative_to(out_dir)) for p in out_dir.rglob("*") if p.is_file())
    result["zip"] = "code.zip"
    result["frame"] = pages[0].role if pages else role
    result["seconds"] = round(time.time() - started, 2)
    (bundle_dir / "conversion.json").write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    say("Done")
    return result
