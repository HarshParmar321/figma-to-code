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
  const loaded = [...document.fonts].filter(f => f.status === 'loaded').map(f => f.family.replace(/["']/g, ''));
  return { elements: out, sections, height: document.documentElement.scrollHeight, fonts: [...new Set(loaded)] };
}
"""

READY_JS = """
async (limitMs) => {
  // Lazy images below the fold never load in a headless page: load everything now.
  document.querySelectorAll('img[loading="lazy"]').forEach(i => { i.loading = 'eager'; });
  const ready = (async () => {
    await document.fonts.ready;
    await Promise.all([...document.images].map(i => i.complete ? null : new Promise(r => { i.onload = i.onerror = r; })));
    await Promise.all([...document.images].map(i => i.decode ? i.decode().catch(() => null) : null));
  })();
  // Never wait forever (a broken font or image link must not stall the check).
  return Promise.race([ready.then(() => true), new Promise(r => setTimeout(() => r(false), limitMs))]);
}
"""
READY_LIMIT_MS = 20_000


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


SYSTEM_FONTS = {
    "arial", "helvetica", "helvetica neue", "times new roman", "times", "georgia", "verdana", "tahoma",
    "trebuchet ms", "courier new", "courier", "system-ui", "sans-serif", "serif", "monospace",
}  # fmt: skip


def _font_available(family: str, loaded: set[str]) -> bool:
    return family.lower() in loaded or family.lower() in SYSTEM_FONTS


def _tol(size: float, ratio: float, floor: float) -> float:
    return max(floor, size * ratio)


# ----------------------------------------------------------------------------- rendering
class Renderer:
    """One Chromium instance reused for every page and correction round of a conversion."""

    def __init__(self, chromium_path: str | None = None):
        self.chromium_path = chromium_path
        self._pw = None
        self._browser = None
        self.ready = True

    def __enter__(self) -> Renderer:
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        launch = {"executable_path": self.chromium_path} if self.chromium_path else {}
        self._browser = self._pw.chromium.launch(**launch)
        return self

    def __exit__(self, *exc) -> None:
        try:
            if self._browser:
                self._browser.close()
        finally:
            if self._pw:
                self._pw.stop()

    def capture(self, html_path: Path, width: int, ids: list[str], screenshot: Path) -> tuple[dict, dict]:
        pg = self._browser.new_page(viewport={"width": width, "height": 900}, device_scale_factor=1)
        try:
            pg.goto(html_path.resolve().as_uri(), wait_until="domcontentloaded", timeout=60_000)
            pg.add_style_tag(content="*{caret-color:transparent !important}")
            self.ready = pg.evaluate(READY_JS, READY_LIMIT_MS)
            pg.wait_for_timeout(150)
            pg.screenshot(path=str(screenshot), full_page=True)
            data = pg.evaluate(COLLECT_JS, ids)
            overflow = {}
            for w in RESPONSIVE_WIDTHS:
                pg.set_viewport_size({"width": w, "height": 900})
                pg.wait_for_timeout(40)
                overflow[w] = pg.evaluate("document.documentElement.scrollWidth") > w + 1
            return data, overflow
        finally:
            pg.close()


# ----------------------------------------------------------------------------- main
def verify(
    out_dir: Path,
    fidelity_dir: Path,
    page: Page,
    expectations: list[dict],
    reference_png: Path,
    chromium_path: str | None = None,
    renderer: Renderer | None = None,
    html_file: str = "index.html",
    write_images: bool = True,
) -> dict:
    fidelity_dir.mkdir(parents=True, exist_ok=True)
    width = round(page.width)
    render_png = fidelity_dir / "render.png"
    ids = [e["id"] for e in expectations]
    if renderer is None:
        with Renderer(chromium_path) as r:
            data, overflow = r.capture(out_dir / html_file, width, ids, render_png)
    else:
        data, overflow = renderer.capture(out_dir / html_file, width, ids, render_png)

    got = data["elements"]
    loaded_fonts = {f.lower() for f in data.get("fonts", [])}
    missing_fonts: dict[str, list[str]] = {}
    issues: list[dict] = []
    geom_ok = geom_n = 0
    typo_ok = typo_n = 0
    color_ok = color_n = 0
    asset_ok = asset_n = 0
    measurements: dict[str, dict] = {}

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
            if not exp.get("hug", True):
                # Fixed-width Figma text box: the HTML element may hug its content, so compare the edge
                # the text is aligned to, and only flag text wider than its box.
                align = exp.get("align", "LEFT")
                if align == "CENTER":
                    dx = (a["x"] + a["w"] / 2) - (ex + ew / 2)
                elif align == "RIGHT":
                    dx = (a["x"] + a["w"]) - (ex + ew)
                dw = max(0.0, dw)
            ok = abs(dx) <= _tol(ew, 0.01, 3) and abs(dy) <= _tol(eh, 0.02, 4) and abs(dw) <= _tol(ew, 0.04, 4)
        else:
            ok = all(abs(d) <= _tol(s, 0.01, 2) for d, s in ((dx, ew), (dy, eh), (dw, ew), (dh, eh)))
        geom_n += 1
        geom_ok += ok
        measurements[nid] = {
            "dx": round(dx, 2), "dy": round(dy, 2), "dw": round(dw, 2), "dh": round(dh, 2), "ok": bool(ok),
            "kind": exp["kind"], "parent": exp.get("parent"), "box": exp["box"],
        }  # fmt: skip
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
            fam_ok = not fam or (fam in a["fontFamily"].lower() and _font_available(fam, loaded_fonts))
            if fam and not _font_available(fam, loaded_fonts):
                missing_fonts.setdefault(st["font-family"].strip("'"), []).append(nid)
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
        if exp["kind"] in ("image", "svg", "raster"):
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
    for family, nodes_using in sorted(missing_fonts.items()):
        issues.append({
            "severity": "critical", "category": "font", "id": nodes_using[0], "name": family,
            "message": f"Font '{family}' is not available, so a fallback font was used ({len(nodes_using)} text layers). "
                       "Add the font files and convert again.",
        })  # fmt: skip
    score = sum(WEIGHTS[k] * v for k, v in sub.items())
    if any(i["severity"] == "critical" for i in issues):
        score = min(score, 69.0)  # SRS §16: a critical issue caps the score
    for w, bad in overflow.items():
        if bad:
            issues.append({"severity": "minor", "category": "responsive", "id": None, "name": f"{w}px",
                           "message": f"Horizontal scroll appears at {w}px width."})  # fmt: skip

    order = {"critical": 0, "major": 1, "minor": 2}
    issues.sort(key=lambda i: (order[i["severity"]], -i.get("offset", 0)))
    band = "Verified" if score >= 90 else "Close" if score >= 75 else "Needs work" if score >= 60 else "Failed"
    if write_images:
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
        "missingFonts": sorted(missing_fonts),
        "measurements": measurements,
        "issues": issues,
    }
    (fidelity_dir / "fidelity.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def _node_name(page: Page, nid: str) -> str:
    if nid.startswith("section:"):
        sid = nid.rsplit(":", 1)[-1]
        sec = next((x for x in page.sections if x.id == sid), None)
        return f"Section · {sec.role or sec.name}" if sec else "Section"
    for e in page.root.walk():
        if e.node is not None and e.node.get("id") == nid:
            if e.kind == "text":
                return (e.node.get("characters", "") or e.name)[:40].replace("\n", " ")
            return e.name
    return nid


def write_compare_images(reference_png: Path, render_png: Path, d: Path) -> None:
    _write_images(Image.open(reference_png).convert("RGB"), Image.open(render_png).convert("RGB"), d)


def write_site_report(result: dict, d: Path, title: str) -> Path:
    """Summary across pages: one row per page with its score, report and preview."""
    rows = []
    for pg in result["pages"]:
        f = pg.get("fidelity")
        rel = pg.get("report", "")
        rel = rel.split("fidelity/", 1)[-1] if rel else ""
        score = f"{f['score']:.1f}" if f else "–"
        band = f["band"] if f else "Not checked"
        fixes = f"{f['corrections']} corrected" if f else ""
        rows.append(
            f"<tr><td><b>{html.escape(pg['name'])}</b></td><td class=n>{score}</td><td>{band}</td><td>{fixes}</td>"
            f"<td><a href='{html.escape(rel)}'>Report</a> · <a href='../output/{html.escape(pg['file'])}'>Preview</a></td></tr>"
        )
    comps = ", ".join(html.escape(c) for c in result.get("components", [])) or "none found"
    f = result["fidelity"]
    doc = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Site report · {html.escape(title)}</title>
<style>
:root {{ color-scheme: light dark; --bg:#f7f7f8; --card:#fff; --ink:#18181b; --muted:#6b7280; --line:#e5e7eb; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#111113; --card:#1c1c1f; --ink:#f4f4f5; --muted:#a1a1aa; --line:#2e2e33; }} }}
body {{ margin:0; font:14px/1.5 system-ui, sans-serif; background:var(--bg); color:var(--ink); }}
.wrap {{ max-width:960px; margin:0 auto; padding:24px 16px 64px; }}
h1 {{ font-size:20px; margin:0 0 4px; }} .muted {{ color:var(--muted); }}
.score {{ font-size:48px; font-weight:700; margin:16px 0 4px; }}
table {{ width:100%; border-collapse:collapse; background:var(--card); border:1px solid var(--line); margin-top:16px; }}
td, th {{ padding:8px 10px; border-bottom:1px solid var(--line); text-align:left; }} td.n {{ text-align:right; font-weight:600; }}
a {{ color:inherit; font-weight:600; }}
</style></head><body><div class="wrap">
<h1>Site report</h1><div class="muted">{html.escape(title)} · {len(result["pages"])} pages</div>
<div class="score">{f["score"]:.1f}</div><div>{f["band"]} · {f["corrections"]} elements auto-corrected</div>
<div class="muted" style="margin-top:8px">Shared components: {comps}</div>
<table><tr><th>Page</th><th style="text-align:right">Score</th><th>Result</th><th>Fixes</th><th></th></tr>{"".join(rows)}</table>
</div></body></html>"""
    d.mkdir(parents=True, exist_ok=True)
    path = d / "report.html"
    path.write_text(doc, encoding="utf-8")
    return path


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
