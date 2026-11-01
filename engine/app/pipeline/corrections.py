"""Self-correction: turns measured position/size errors into CSS corrections, parents first.

After each render, every element whose box is outside tolerance gets a correction, unless one of its
measured ancestors is also off (fixing the parent usually moves the children with it). Positions are
corrected with the CSS `translate` property, which moves an element visually without pushing any
sibling, so one fix never creates new errors elsewhere. Sizes of non-text elements are corrected with
explicit width/height. Corrections only apply on desktop widths, where the layout matches the design.
"""

from __future__ import annotations

from pathlib import Path

DESKTOP_MIN = 1024
MIN_SHIFT = 0.5


def _r(v: float) -> float:
    return round(v * 2) / 2  # half-pixel precision


def step(measurements: dict[str, dict], current: dict[str, dict]) -> int:
    """Adds corrections for the outermost failing elements. Returns how many elements changed."""
    failing = {nid for nid, m in measurements.items() if not m["ok"]}
    # A section that is only shifted (its height is right) does not block its children: the shift comes
    # from an earlier section and disappears once that one is fixed.
    blocking = {
        nid
        for nid in failing
        if not (measurements[nid]["kind"] == "section" and abs(measurements[nid]["dh"]) < MIN_SHIFT)
    }
    changed = 0
    for nid in failing:
        m = measurements[nid]
        parent = m.get("parent")
        blocked = False
        while parent:
            if parent in blocking:
                blocked = True
                break
            parent = measurements.get(parent, {}).get("parent")
        if blocked:
            continue
        c = current.setdefault(nid, {"tx": 0.0, "ty": 0.0})
        before = dict(c)
        if m["kind"] == "section":
            # Sections stay in normal flow: fixing a section's height puts every later section back in
            # place, so a section is only translated when it is shifted and the section above it is right
            # (the shift then comes from a lost gap, not from a neighbour's height).
            if abs(m["dh"]) >= MIN_SHIFT:
                c["h"] = _r(m["box"][3])
            elif abs(m["dy"]) >= MIN_SHIFT and _previous_section_ok(nid, measurements):
                c["ty"] = _r(c["ty"] - m["dy"])
            if c != before:
                changed += 1
            continue
        if m["kind"] != "text":
            # Size first: a wrong size also shifts the measured position of the box edges.
            if abs(m["dw"]) >= MIN_SHIFT:
                c["w"] = _r(m["box"][2])
            if abs(m["dh"]) >= MIN_SHIFT:
                c["h"] = _r(m["box"][3])
        if abs(m["dx"]) >= MIN_SHIFT:
            c["tx"] = _r(c["tx"] - m["dx"])
        if abs(m["dy"]) >= MIN_SHIFT:
            c["ty"] = _r(c["ty"] - m["dy"])
        if c != before:
            changed += 1
    return changed


def _previous_section_ok(nid: str, measurements: dict[str, dict]) -> bool:
    y = measurements[nid]["box"][1]
    above = [(v["box"][1], k) for k, v in measurements.items() if v["kind"] == "section" and v["box"][1] < y]
    if not above:
        return True
    return measurements[max(above)[1]]["ok"]


def css(corrections: dict[str, dict]) -> str:
    lines = [
        "/* Fidelity corrections, measured against the Figma design and applied on desktop widths.",
        "   Each rule nudges one element (by its Figma layer id) to its exact designed position/size.",
        "   They are generated automatically; delete this file to see the uncorrected layout. */",
    ]
    rules = []
    for nid, c in sorted(corrections.items()):
        decls = []
        if c.get("tx") or c.get("ty"):
            decls.append(f"translate: {_fmt(c.get('tx', 0))} {_fmt(c.get('ty', 0))};")
        if "w" in c:
            decls.append(f"width: {_fmt(c['w'])}; max-width: none;")
        if "h" in c:
            decls.append(f"height: {_fmt(c['h'])};")
        if decls:
            sel = nid.replace("\\", "\\\\").replace('"', '\\"')
            rules.append(f'  [data-figma-id="{sel}"] {{ {" ".join(decls)} }}')
    if rules:
        lines.append(f"@media (min-width: {DESKTOP_MIN}px) {{")
        lines += rules
        lines.append("}")
    return "\n".join(lines) + "\n"


def write(out_dir: Path, corrections: dict[str, dict]) -> None:
    (out_dir / "css" / "corrections.css").write_text(css(corrections), encoding="utf-8")


def _fmt(v: float) -> str:
    return "0" if v == 0 else f"{v:g}px"
