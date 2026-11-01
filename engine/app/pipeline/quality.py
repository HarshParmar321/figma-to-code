"""Code quality: is the generated HTML/CSS something a front-end developer would ship?

Two parts, scored 0–100 next to the fidelity score:
- Structure (static, 60 points): fixed sizes, absolute positioning, headings, alt text, links.
- Responsive health (rendered, 40 points): at phone, tablet and laptop widths nothing scrolls
  sideways, no text overlaps other text (unless it already does in the design) and no text is
  pushed off screen.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

HEALTH_WIDTHS = (390, 768, 1280)
ICON_MAX = 64

HEALTH_JS = """
() => {
  const vw = document.documentElement.clientWidth;
  const ownText = el => [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim());
  const texts = [...document.querySelectorAll('h1,h2,h3,h4,h5,h6,p,a,span,li')]
    .filter(el => ownText(el) && el.getClientRects().length);
  const boxes = texts.map(el => {
    const r = el.getBoundingClientRect();
    return { el, x: r.left, y: r.top, w: r.width, h: r.height, t: el.textContent.trim().slice(0, 40) };
  });
  let offscreen = 0;
  const pairs = [];
  for (let i = 0; i < boxes.length; i++) {
    const a = boxes[i];
    if (a.x + a.w < 1 || a.x > vw - 1) offscreen++;
    for (let j = i + 1; j < boxes.length; j++) {
      const b = boxes[j];
      if (a.el.contains(b.el) || b.el.contains(a.el)) continue;
      const ix = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x);
      const iy = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y);
      if (ix > 4 && iy > 4 && ix * iy > 0.25 * Math.min(a.w * a.h, b.w * b.h)) pairs.push([a.t, b.t]);
    }
  }
  return { scrollWidth: document.documentElement.scrollWidth, pairs, offscreen, texts: boxes.length };
}
"""


class _Scan(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.headings: list[int] = []
        self.imgs: list[dict] = []
        self.links: list[str] = []
        self.tags: set[str] = set()

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        self.tags.add(tag)
        if re.fullmatch(r"h[1-6]", tag):
            self.headings.append(int(tag[1]))
        elif tag == "img":
            self.imgs.append(a)
        elif tag == "a":
            self.links.append(a.get("href") or "")


def _rules(css: str) -> list[tuple[str, str]]:
    """(selector, body) for top-level rules (media queries excluded)."""
    top = re.split(r"@media", css)[0]
    return [(m.group(1).strip(), m.group(2)) for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", top)]


def _issue(severity: str, check: str, message: str, **kw) -> dict:
    return {"severity": severity, "check": check, "message": message, **kw}


def static_report(html_files: list[Path], css: str) -> dict:
    rules = [(sel, body) for sel, body in _rules(css) if sel.startswith(".") and "::" not in sel]
    fixed, heights = [], []
    for sel, body in rules:
        positioned = "position: absolute" in body
        widths = [float(m.group(1)) for m in re.finditer(r"(?<![-\w])width:\s*([\d.]+)px", body)]
        if any(w > ICON_MAX for w in widths) and not positioned:
            fixed.append(sel)
        if re.search(r"(?<![-\w])height:\s*\d", body) and not positioned:
            heights.append(sel)
    absolute = [sel for sel, body in rules if "position: absolute" in body]
    tokens = len(re.findall(r"^\s*--[\w-]+:", css, re.M))

    issues: list[dict] = []
    heading_ok = True
    content_imgs = described = 0
    placeholder_links = total_links = 0
    landmarks: set[str] = set()
    for f in html_files:
        scan = _Scan()
        scan.feed(f.read_text(encoding="utf-8"))
        landmarks |= scan.tags & {"main", "header", "nav", "footer", "section"}
        if scan.headings.count(1) != 1:
            heading_ok = False
            msg = f"{scan.headings.count(1)} h1 headings (one per page is expected)."
            issues.append(_issue("major", "headings", msg, page=f.name))
        for prev, cur in zip(scan.headings, scan.headings[1:], strict=False):
            if cur > prev + 1:
                heading_ok = False
                issues.append(_issue("minor", "headings", f"Heading level jumps from h{prev} to h{cur}.", page=f.name))
                break
        for img in scan.imgs:
            if "alt" not in img:
                issues.append(_issue("major", "alt", "Image without alt attribute.", page=f.name))
            if not img.get("src", "").endswith(".svg"):
                content_imgs += 1
                described += bool(img.get("alt"))
        total_links += len(scan.links)
        placeholder_links += sum(1 for h in scan.links if h in ("", "#"))
    if placeholder_links:
        msg = f'{placeholder_links} of {total_links} links point nowhere yet (href="#").'
        issues.append(_issue("info", "links", msg))
    if fixed:
        msg = f"{len(fixed)} classes keep a fixed pixel width (e.g. {', '.join(fixed[:3])})."
        issues.append(_issue("minor", "fixed-width", msg))
    if heights:
        msg = f"{len(heights)} classes set a fixed height (e.g. {', '.join(heights[:3])})."
        issues.append(_issue("info", "fixed-height", msg))
    if absolute:
        issues.append(_issue("info", "absolute", f"{len(absolute)} absolutely positioned decorations or layers."))
    n = max(1, len(rules))
    return {
        "classes": len(rules),
        "fixedWidth": len(fixed),
        "fixedHeight": len(heights),
        "absolute": len(absolute),
        "tokens": tokens,
        "fixedRatio": round(len(fixed) / n, 3),
        "absoluteRatio": round(len(absolute) / n, 3),
        "headingsOk": heading_ok,
        "imagesDescribed": f"{described}/{content_imgs}",
        "placeholderLinks": f"{placeholder_links}/{total_links}",
        "landmarks": sorted(landmarks),
        "issues": issues,
    }


def _measure(browser, path: Path, width: int) -> dict:
    pg = browser.new_page(viewport={"width": width, "height": 900})
    try:
        pg.goto(path.resolve().as_uri(), wait_until="domcontentloaded", timeout=60_000)
        pg.wait_for_timeout(150)
        return pg.evaluate(HEALTH_JS)
    finally:
        pg.close()


def health_report(out_dir: Path, html_files: list[str], renderer, design_widths: dict[str, int]) -> dict:
    """Renders each page at phone/tablet/laptop widths and looks for broken layout.

    Text that overlaps at the design width overlaps on purpose (a heading over an image, a caption
    on a badge), so only new overlaps count.
    """
    results, issues = {}, []
    browser = renderer._browser
    for file in html_files:
        design = _measure(browser, out_dir / file, design_widths.get(file, 1440))
        intended = {tuple(p) for p in design["pairs"]}
        for w in HEALTH_WIDTHS:
            r = _measure(browser, out_dir / file, w)
            new = [p for p in r["pairs"] if tuple(p) not in intended]
            results[f"{file}@{w}"] = {
                "scrollWidth": r["scrollWidth"],
                "overlaps": len(new),
                "offscreen": r["offscreen"],
            }
            phone = w < 768
            if r["scrollWidth"] > w + 1:
                msg = f"Scrolls sideways at {w}px ({r['scrollWidth']}px wide)."
                issues.append(_issue("major", "overflow", msg, page=file, width=w))
            if new:
                msg = f"{len(new)} text overlaps at {w}px (e.g. “{new[0][0]}” / “{new[0][1]}”)."
                issues.append(_issue("minor" if phone else "major", "overlap", msg, page=file, width=w))
            if r["offscreen"]:
                msg = f"{r['offscreen']} texts outside the screen at {w}px."
                issues.append(_issue("minor" if phone else "major", "offscreen", msg, page=file, width=w))
    return {"widths": list(HEALTH_WIDTHS), "results": results, "issues": issues}


def score(static: dict, health: dict | None) -> dict:
    s = 60.0
    s -= min(20.0, static["fixedRatio"] * 60)
    s -= min(10.0, static["absoluteRatio"] * 20)
    s -= 0 if static["headingsOk"] else 6
    described, total = (int(v) for v in static["imagesDescribed"].split("/"))
    s -= 0 if not total else 6 * (1 - described / total)
    h = 40.0
    for i in (health or {}).get("issues", []):
        h -= {"major": 8, "minor": 3}.get(i["severity"], 0)
    total_score = max(0.0, s) + max(0.0, h)
    band = (
        "Excellent" if total_score >= 90 else "Good" if total_score >= 75 else "Fair" if total_score >= 60 else "Poor"
    )
    return {
        "score": round(total_score, 1),
        "band": band,
        "structure": round(max(0.0, s), 1),
        "responsive": round(max(0.0, h), 1) if health else None,
    }


def analyze(out_dir: Path, html_files: list[str], renderer=None, design_widths: dict[str, int] | None = None) -> dict:
    css_path = out_dir / "css" / "styles.css"
    css = css_path.read_text(encoding="utf-8") if css_path.exists() else ""
    static = static_report([out_dir / f for f in html_files], css)
    health = health_report(out_dir, html_files, renderer, design_widths or {}) if renderer is not None else None
    result = score(static, health)
    issues = static["issues"] + (health["issues"] if health else [])
    order = {"major": 0, "minor": 1, "info": 2}
    issues.sort(key=lambda i: order.get(i["severity"], 3))
    detail = {k: v for k, v in static.items() if k != "issues"}
    return {**result, "structureDetail": detail, "health": (health or {}).get("results", {}), "issues": issues}
