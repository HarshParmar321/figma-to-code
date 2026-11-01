"""Renders the generated page in Chromium and measures it against the Figma design.

Score per SRS section 16:  F = 0.35 G + 0.25 T + 0.10 C + 0.10 A + 0.10 S + 0.05 M + 0.05 R
"""

from __future__ import annotations

import html
import json
import re
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from PIL import Image

from .ir import Page

WEIGHTS = {"G": 0.35, "T": 0.25, "C": 0.10, "A": 0.10, "S": 0.10, "M": 0.05, "R": 0.05}
LABELS = {
    "G": "Geometry",
    "T": "Typography",
    "C": "Colour",
    "A": "Assets",
    "S": "Visual similarity",
    "M": "Motion",
    "R": "Responsive health",
}
RESPONSIVE_WIDTHS = [1440, 1280, 1024, 768, 390]
COLOR_TOL = 6  # per channel, 0-255
SECTION_SSIM_MINOR = 0.95  # below this a section gets a visual issue
SECTION_SSIM_MAJOR = 0.90

COLLECT_JS = """
(ids) => {
  const out = {};
  for (const id of ids) {
    const el = document.querySelector(`[data-figma-id="${CSS.escape(id)}"]`);
    if (!el) continue;
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    out[id] = {
      x: r.left + window.scrollX, y: r.top + window.scrollY, w: r.width, h: r.height,
      fontFamily: cs.fontFamily, fontSize: cs.fontSize, fontWeight: cs.fontWeight, color: cs.color,
      lineHeight: cs.lineHeight, backgroundColor: cs.backgroundColor,
      loaded: el.tagName === 'IMG' ? (el.complete && el.naturalWidth > 0) : null,
    };
  }
  const sections = [...document.querySelectorAll('[data-section]')].map(s => {
    const r = s.getBoundingClientRect();
    return { name: s.dataset.section, y: r.top + window.scrollY, h: r.height };
  });
  return { elements: out, sections, height: document.documentElement.scrollHeight };
}
"""

READY_JS = """
async () => {
  await document.fonts.ready;
  await Promise.all([...document.images].map(i => i.complete ? null : new Promise(r => { i.onload = i.onerror = r; })));
}
"""


# ----------------------------------------------------------------------------- image metrics
def _gray(im: Image.Image) -> np.ndarray:
    return np.asarray(im.convert("L"), dtype=np.float64)


def _box_filter(a: np.ndarray, k: int) -> np.ndarray:
    """Mean over k×k windows using an integral image (valid region only)."""
    c = np.cumsum(np.cumsum(np.pad(a, ((1, 0), (1, 0))), axis=0), axis=1)
    s = c[k:, k:] - c[:-k, k:] - c[k:, :-k] + c[:-k, :-k]
    return s / (k * k)


def ssim(a: Image.Image, b: Image.Image, k: int = 7) -> float:
    """Structural similarity of two images (b is resized to a), 0..1."""
    if b.size != a.size:
        b = b.resize(a.size, Image.BILINEAR)
    x, y = _gray(a), _gray(b)
    if min(x.shape) <= k:
        return 1.0 if np.allclose(x, y) else 0.0
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    mx, my = _box_filter(x, k), _box_filter(y, k)
    sxx = _box_filter(x * x, k) - mx * mx
    syy = _box_filter(y * y, k) - my * my
    sxy = _box_filter(x * y, k) - mx * my
    m = ((2 * mx * my + c1) * (2 * sxy + c2)) / ((mx * mx + my * my + c1) * (sxx + syy + c2))
    return float(np.clip(m.mean(), 0, 1))


def _parse_rgb(css: str) -> tuple[int, int, int, float] | None:
    m = re.match(r"rgba?\(([^)]+)\)", css or "")
    if m:
        parts = [p.strip() for p in m.group(1).replace("/", ",").split(",")]
        r, g, b = (int(float(p)) for p in parts[:3])
        a = float(parts[3]) if len(parts) > 3 else 1.0
        return r, g, b, a
    m = re.match(r"#([0-9a-f]{6})", css or "", re.I)
    if m:
        h = m.group(1)
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 1.0
    return None


def _same_color(expected: str, actual: str) -> bool:
    e, a = _parse_rgb(expected), _parse_rgb(actual)
    if not e or not a:
        return False
    return all(abs(e[i] - a[i]) <= COLOR_TOL for i in range(3)) and abs(e[3] - a[3]) <= 0.05


def _tol(size: float, ratio: float, floor: float) -> float:
    return max(floor, size * ratio)


# ----------------------------------------------------------------------------- main
def verify(
    out_dir: Path,
    fidelity_dir: Path,
    page: Page,
    expectations: list[dict],
    reference_png: Path,
    chromium_path: str | None = None,
) -> dict:
    from playwright.sync_api import sync_playwright

    fidelity_dir.mkdir(parents=True, exist_ok=True)
    width = round(page.width)
    render_png = fidelity_dir / "render.png"
    ids = [e["id"] for e in expectations]
    overflow = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=chromium_path) if chromium_path else p.chromium.launch()
        try:
            pg = browser.new_page(viewport={"width": width, "height": 900}, device_scale_factor=1)
            pg.goto((out_dir / "index.html").resolve().as_uri())
            pg.add_style_tag(content="*{caret-color:transparent !important}")
            pg.evaluate(READY_JS)
            pg.wait_for_timeout(200)
            pg.screenshot(path=str(render_png), full_page=True)
            data = pg.evaluate(COLLECT_JS, ids)
            for w in RESPONSIVE_WIDTHS:
                pg.set_viewport_size({"width": w, "height": 900})
                pg.wait_for_timeout(50)
                sw = pg.evaluate("document.documentElement.scrollWidth")
                overflow[w] = sw > w + 1
        finally:
            browser.close()

    got = data["elements"]
    issues: list[dict] = []
    geom_ok = geom_n = 0
    typo_ok = typo_n = 0
    color_ok = color_n = 0
    asset_ok = asset_n = 0

    for exp in expectations:
        nid, (ex, ey, ew, eh) = exp["id"], exp["box"]
        a = got.get(nid)
        name = _node_name(page, nid)
        if a is None:
            geom_n += 1
            issues.append({"severity": "critical", "category": "missing", "id": nid, "name": name,
                           "message": "Element not found in the rendered page."})  # fmt: skip
            continue
        dx, dy, dw, dh = a["x"] - ex, a["y"] - ey, a["w"] - ew, a["h"] - eh
        if exp["kind"] == "text":
            ok = abs(dx) <= _tol(ew, 0.01, 3) and abs(dy) <= _tol(eh, 0.02, 4) and abs(dw) <= _tol(ew, 0.04, 4)
        else:
            ok = all(abs(d) <= _tol(s, 0.01, 2) for d, s in ((dx, ew), (dy, eh), (dw, ew), (dh, eh)))
        geom_n += 1
        geom_ok += ok
        if not ok:
            off = max(abs(dx), abs(dy), abs(dw), abs(dh))
            issues.append({
                "severity": "major" if off > 12 else "minor", "category": "geometry", "id": nid, "name": name,
                "message": f"Off by x {dx:+.0f}, y {dy:+.0f}, w {dw:+.0f}, h {dh:+.0f} px",
                "offset": round(off, 1),
            })  # fmt: skip

        if exp["kind"] == "text":
            typo_n += 1
            st = exp.get("style", {})
            fam = st.get("font-family", "").strip("'").lower()
            fam_ok = not fam or fam in a["fontFamily"].lower()
            size_ok = not st.get("font-size") or abs(float(a["fontSize"][:-2]) - float(st["font-size"][:-2])) < 0.6
            weight_ok = not st.get("font-weight") or a["fontWeight"] == st["font-weight"]
            lh = exp.get("lineHeight")
            lines_ok = True
            if lh and exp.get("lines"):
                lines_ok = round(a["h"] / lh) == exp["lines"]
            ok = fam_ok and size_ok and weight_ok and lines_ok
            typo_ok += ok
            if not ok:
                why = [w for w, f in (("font family", fam_ok), ("size", size_ok), ("weight", weight_ok),
                                      ("line count", lines_ok)) if not f]  # fmt: skip
                issues.append({"severity": "major" if not lines_ok else "minor", "category": "typography",
                               "id": nid, "name": name, "message": "Mismatch: " + ", ".join(why)})  # fmt: skip
            if st.get("color"):
                color_n += 1
                c_ok = _same_color(st["color"], a["color"])
                color_ok += c_ok
                if not c_ok:
                    issues.append({"severity": "minor", "category": "colour", "id": nid, "name": name,
                                   "message": f"Text colour {a['color']} vs {st['color']}"})  # fmt: skip
        elif exp.get("background"):
            color_n += 1
            c_ok = _same_color(exp["background"], a["backgroundColor"])
            color_ok += c_ok
            if not c_ok:
                issues.append({"severity": "minor", "category": "colour", "id": nid, "name": name,
                               "message": f"Background {a['backgroundColor']} vs {exp['background']}"})  # fmt: skip
        if exp["kind"] in ("image", "svg"):
            asset_n += 1
            loaded = a["loaded"] is not False
            asset_ok += loaded
            if not loaded:
                issues.append({"severity": "critical", "category": "asset", "id": nid, "name": name,
                               "message": "Image failed to load."})  # fmt: skip

    # Visual similarity per section.
    ref = Image.open(reference_png).convert("RGB")
    ren = Image.open(render_png).convert("RGB")
    section_scores = []
    for sec, rs in zip(page.sections, data["sections"], strict=False):
        top, bottom = int(sec.y), int(min(ref.height, sec.bottom))
        r_top, r_bottom = int(rs["y"]), int(min(ren.height, rs["y"] + rs["h"]))
        if bottom - top < 8 or r_bottom - r_top < 8:
            continue
        a_crop = ref.crop((0, top, ref.width, bottom)).resize((ref.width // 2, max(1, (bottom - top) // 2)))
        b_crop = ren.crop((0, r_top, min(ren.width, ref.width), r_bottom))
        s = ssim(a_crop, b_crop)
        section_scores.append({"section": rs["name"], "ssim": round(s, 4), "top": top, "height": bottom - top})
        if s < SECTION_SSIM_MINOR:
            issues.append({
                "severity": "major" if s < SECTION_SSIM_MAJOR else "minor", "category": "visual", "id": None,
                "name": rs["name"], "message": f"Section looks different from the design ({s * 100:.0f}% similar).",
            })  # fmt: skip

    sub = {
        "G": 100 * geom_ok / geom_n if geom_n else 100.0,
        "T": 100 * typo_ok / typo_n if typo_n else 100.0,
        "C": 100 * color_ok / color_n if color_n else 100.0,
        "A": 100 * asset_ok / asset_n if asset_n else 100.0,
        "S": 100 * float(np.mean([s["ssim"] for s in section_scores])) if section_scores else 0.0,
        "M": 100.0,  # no motion specs in v0.2 inputs
        "R": 100 * sum(not v for v in overflow.values()) / len(overflow),
    }
    score = sum(WEIGHTS[k] * v for k, v in sub.items())
    if any(i["severity"] == "critical" for i in issues):
        score = min(score, 69.0)
    for w, bad in overflow.items():
        if bad:
            issues.append({"severity": "minor", "category": "responsive", "id": None, "name": f"{w}px",
                           "message": f"Horizontal scroll appears at {w}px width."})  # fmt: skip

    order = {"critical": 0, "major": 1, "minor": 2}
    issues.sort(key=lambda i: (order[i["severity"]], -i.get("offset", 0)))
    band = "Verified" if score >= 90 else "Close" if score >= 75 else "Needs work" if score >= 60 else "Failed"
    _write_images(ref, ren, fidelity_dir)
    result = {
        "generatedAt": datetime.now(UTC).isoformat(),
        "viewport": width,
        "score": round(score, 1),
        "band": band,
        "subScores": {k: round(v, 1) for k, v in sub.items()},
        "counts": {
            "elements": geom_n,
            "geometryPass": geom_ok,
            "texts": typo_n,
            "typographyPass": typo_ok,
            "colours": color_n,
            "colourPass": color_ok,
            "assets": asset_n,
            "assetPass": asset_ok,
            "critical": sum(i["severity"] == "critical" for i in issues),
            "major": sum(i["severity"] == "major" for i in issues),
            "minor": sum(i["severity"] == "minor" for i in issues),
        },  # fmt: skip
        "sections": section_scores,
        "renderHeight": data["height"],
        "designHeight": page.height,
        "overflow": {str(k): v for k, v in overflow.items()},
        "issues": issues,
    }
    (fidelity_dir / "fidelity.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def _node_name(page: Page, nid: str) -> str:
    for e in page.root.walk():
        if e.node is not None and e.node.get("id") == nid:
            if e.kind == "text":
                return (e.node.get("characters", "") or e.name)[:40].replace("\n", " ")
            return e.name
    return nid


def _write_images(ref: Image.Image, ren: Image.Image, d: Path) -> None:
    h = max(ref.height, ren.height)
    side = Image.new("RGB", (ref.width * 2 + 24, h), "white")
    side.paste(ref, (0, 0))
    side.paste(ren, (ref.width + 24, 0))
    side.thumbnail((1800, 4000))
    side.save(d / "side-by-side.jpg", quality=85)
    # Difference heatmap over the shared height.
    hh = min(ref.height, ren.height)
    a = np.asarray(ref.crop((0, 0, ref.width, hh)), dtype=np.int16)
    b = np.asarray(ren.crop((0, 0, min(ren.width, ref.width), hh)).resize((ref.width, hh)), dtype=np.int16)
    diff = np.abs(a - b).max(axis=2)
    base = (np.asarray(ref.crop((0, 0, ref.width, hh)).convert("L"), dtype=np.float32) * 0.35 + 150).astype(np.uint8)
    heat = np.stack([base, base, base], axis=2)
    mask = diff > 72  # ignore anti-aliasing noise on text edges
    heat[mask] = [230, 40, 40]
    img = Image.fromarray(heat)
    img.thumbnail((900, 4000))
    img.save(d / "diff.jpg", quality=85)


def write_report_html(result: dict, d: Path, title: str, preview_href: str = "../output/index.html") -> Path:
    rows = "".join(
        f"<tr><td>{LABELS[k]}</td><td>{int(WEIGHTS[k] * 100)}%</td><td class=n>{v:.1f}</td></tr>"
        for k, v in result["subScores"].items()
    )
    secs = "".join(
        f"<tr><td>{html.escape(s['section'])}</td><td class=n>{s['ssim'] * 100:.1f}</td></tr>"
        for s in result["sections"]
    )
    issues = (
        "".join(
            f"<tr class='{i['severity']}'><td>{i['severity']}</td><td>{i['category']}</td>"
            f"<td>{html.escape(str(i.get('name') or ''))}</td><td>{html.escape(i['message'])}</td></tr>"
            for i in result["issues"][:80]
        )
        or "<tr><td colspan=4>No issues.</td></tr>"
    )
    c = result["counts"]
    color = {"Verified": "#16a34a", "Close": "#2563eb", "Needs work": "#d97706", "Failed": "#dc2626"}[result["band"]]
    doc = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Fidelity report · {html.escape(title)}</title>
<style>
:root {{ color-scheme: light dark; --bg:#f7f7f8; --card:#fff; --ink:#18181b; --muted:#6b7280; --line:#e5e7eb; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#111113; --card:#1c1c1f; --ink:#f4f4f5; --muted:#a1a1aa; --line:#2e2e33; }} }}
body {{ margin:0; font:14px/1.5 system-ui, sans-serif; background:var(--bg); color:var(--ink); }}
.wrap {{ max-width:1100px; margin:0 auto; padding:24px 16px 64px; }}
h1 {{ font-size:20px; margin:0 0 4px; }} h2 {{ font-size:15px; margin:28px 0 10px; }}
.muted {{ color:var(--muted); }}
.hero {{ display:flex; gap:24px; align-items:center; flex-wrap:wrap; background:var(--card); border:1px solid var(--line);
  border-radius:12px; padding:20px; margin-top:16px; }}
.score {{ font-size:56px; font-weight:700; line-height:1; color:{color}; }}
.band {{ font-weight:600; color:{color}; }}
.links a {{ display:inline-block; margin-right:12px; color:inherit; font-weight:600; }}
table {{ width:100%; border-collapse:collapse; background:var(--card); border:1px solid var(--line); border-radius:8px; overflow:hidden; }}
td, th {{ padding:8px 10px; border-bottom:1px solid var(--line); text-align:left; vertical-align:top; }}
td.n {{ text-align:right; font-variant-numeric:tabular-nums; font-weight:600; }}
tr.critical td:first-child {{ color:#dc2626; font-weight:700; }} tr.major td:first-child {{ color:#d97706; font-weight:700; }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(280px,1fr)); gap:16px; }}
img.shot {{ width:100%; border:1px solid var(--line); border-radius:8px; background:#fff; }}
</style></head><body><div class="wrap">
<h1>Fidelity report</h1><div class="muted">{html.escape(title)} · viewport {result["viewport"]}px · {result["generatedAt"][:19]} UTC</div>
<div class="hero"><div><div class="score">{result["score"]:.1f}</div><div class="band">{result["band"]}</div></div>
<div><div>{c["geometryPass"]}/{c["elements"]} elements placed within tolerance · {c["typographyPass"]}/{c["texts"]} text styles match</div>
<div>{c["critical"]} critical · {c["major"]} major · {c["minor"]} minor issues</div>
<div class="links" style="margin-top:8px"><a href="{preview_href}" target="_blank">Open preview</a><a href="side-by-side.jpg" target="_blank">Full side by side</a></div></div></div>
<h2>Score breakdown</h2><table><tr><th>Check</th><th>Weight</th><th style="text-align:right">Score</th></tr>{rows}</table>
<h2>Sections (visual similarity)</h2><table><tr><th>Section</th><th style="text-align:right">Similarity</th></tr>{secs}</table>
<h2>Figma vs generated</h2><div class="grid"><div><div class="muted">Design (left) · generated (right)</div><img class="shot" src="side-by-side.jpg" alt="Side by side"></div>
<div><div class="muted">Differences in red</div><img class="shot" src="diff.jpg" alt="Difference map"></div></div>
<h2>Issues</h2><table><tr><th>Severity</th><th>Type</th><th>Element</th><th>Detail</th></tr>{issues}</table>
</div></body></html>"""
    path = d / "report.html"
    path.write_text(doc, encoding="utf-8")
    return path
