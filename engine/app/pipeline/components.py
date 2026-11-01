"""Finds sections that repeat across pages (header, footer, newsletter, CTA…) so they are generated once.

Two sections match when their content is the same: element kinds, positions relative to the section,
sizes, text and the actual image/graphic bytes (by hash). Node ids differ between frames, so they are
never used for matching.
"""

from __future__ import annotations

import hashlib
import re

from .ir import El, Page

POS_TOL = 2  # px rounding so 1px designer drift still matches


def _q(v: float) -> int:
    return int(round(v / POS_TOL))


def section_signature(sec: El, asset_hashes: dict[str, str]) -> str:
    parts: list[str] = [f"h{_q(sec.h)}"]
    for e in sec.walk():
        if e is sec or e.kind in ("wrapper", "container"):
            continue
        token = [e.kind, str(_q(e.x)), str(_q(e.y - sec.y)), str(_q(e.w)), str(_q(e.h))]
        if e.kind == "text" and e.node:
            token.append(e.node.get("characters", ""))
        if e.asset:
            token.append(asset_hashes.get(e.asset, e.asset.split("-", 1)[0]))
        parts.append("|".join(token))
    return hashlib.sha1("\n".join(sorted(parts)).encode("utf-8")).hexdigest()[:16]


HEADER_MAX = 240  # a header strip lives in the top 240px of the first section
STRIP_GAP = 12  # minimum empty band separating the strip from the rest


def _strip_cut(items: list[El], top: float, bottom_limit: float, from_top: bool) -> float | None:
    """y of the first clean horizontal cut (no element crosses it) inside the allowed band."""
    if not items:
        return None
    spans = sorted((e.y, e.bottom) for e in items)
    if from_top:
        reach = None
        for y0, y1 in spans:
            if reach is not None and y0 - reach >= STRIP_GAP and reach - top <= bottom_limit:
                return reach + (y0 - reach) / 2
            reach = y1 if reach is None else max(reach, y1)
        return None
    spans.sort(key=lambda s: -s[1])
    reach = None
    for y0, y1 in spans:
        if reach is not None and reach - y1 >= STRIP_GAP and top - reach <= bottom_limit:
            return y1 + (reach - y1) / 2
        reach = y0 if reach is None else min(reach, y0)
    return None


def _content(items: list[El]) -> list[El]:
    """Items without decorative graphics: shapes/graphics that overlap later-painted items become
    background layers during layout, so they must not block a strip cut."""
    out = []
    for e in items:
        decorative = e.kind in ("svg", "raster", "shape", "image") and any(
            o is not e and o.z > e.z and e.intersects(o) and not (e.kind in ("shape", "image") and e.contains(o))
            for o in items
        )
        if not decorative:
            out.append(e)
    return out


def _strip_signature(items: list[El], origin: float, asset_hashes: dict[str, str]) -> str:
    probe = El("probe", "probe", "section", 0, origin, 0, 0, children=items)
    return section_signature(probe, asset_hashes)


def split_shared_strips(pages: list[Page], asset_hashes: dict[str, str]) -> None:
    """Splits a repeated header row (top of the first section) and footer row (bottom of the last
    section) into their own sections when they are identical on two or more pages.

    Flat designs usually put the navigation inside the hero band, so without this the header would
    never be recognised as shared.
    """
    if len(pages) < 2:
        return
    for where in ("header", "footer"):
        cand: list[tuple[Page, El, float, list[El], str]] = []
        for p in pages:
            if not p.sections:
                continue
            sec = p.sections[0] if where == "header" else p.sections[-1]
            content = _content(sec.children)
            if where == "header":
                cut = _strip_cut(content, sec.y, HEADER_MAX, from_top=True)
                items = [e for e in content if cut is not None and e.bottom <= cut]
            else:
                cut = _strip_cut(content, sec.bottom, HEADER_MAX, from_top=False)
                items = [e for e in content if cut is not None and e.y >= cut]
            if cut is None or not items or len(items) == len(sec.children):
                continue
            origin = sec.y if where == "header" else cut
            cand.append((p, sec, cut, items, _strip_signature(items, origin, asset_hashes)))
        by_sig: dict[str, list] = {}
        for c in cand:
            by_sig.setdefault(c[4], []).append(c)
        for group in by_sig.values():
            if len({id(c[0]) for c in group}) < 2:
                continue
            for p, sec, cut, items, _ in group:
                keep = [e for e in sec.children if e not in items]
                if where == "header":
                    strip = El(f"{sec.id}-header", "Header", "section", 0, sec.y, sec.w, cut - sec.y, children=items)
                    sec.children, sec.h, sec.y = keep, sec.bottom - cut, cut
                    p.sections.insert(0, strip)
                else:
                    strip = El(f"{sec.id}-footer", "Footer", "section", 0, cut, sec.w, sec.bottom - cut, children=items)
                    sec.children, sec.h = keep, cut - sec.y
                    p.sections.append(strip)
                strip.role = strip.component = f"site-{where}"
                strip.tag = "header" if where == "header" else "footer"
                strip.overlay = bool(sec.layers)  # draws above the neighbour's background layers


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:28].strip("-") or "section"


def detect_shared_sections(pages: list[Page], asset_hashes: dict[str, str]) -> dict[int, str]:
    """Returns {id(section El): component name} for sections that appear on two or more pages."""
    if len(pages) < 2:
        return {}
    occurrences: dict[str, list[tuple[int, int, El]]] = {}
    for pi, page in enumerate(pages):
        for si, sec in enumerate(page.sections):
            sig = section_signature(sec, asset_hashes)
            occurrences.setdefault(sig, []).append((pi, si, sec))
    names: dict[int, str] = {}
    used: set[str] = set()
    for occ in occurrences.values():
        if len({pi for pi, _, _ in occ}) < 2:
            continue
        positions = [(si, len(pages[pi].sections)) for pi, si, _ in occ]
        if all(si == 0 for si, _ in positions):
            name = "site-header"
        elif all(si == n - 1 for si, n in positions):
            name = "site-footer"
        else:
            texts = [t for t in occ[0][2].walk() if t.kind == "text" and t.node]
            biggest = max(texts, key=lambda t: t.node.get("fontSize") or 0, default=None)
            name = "shared-" + _slug(biggest.node.get("characters", "section").split("\n")[0] if biggest else "section")
        base, i = name, 2
        while name in used:
            name, i = f"{base}-{i}", i + 1
        used.add(name)
        for _, _, sec in occ:
            names[id(sec)] = name
    return names
