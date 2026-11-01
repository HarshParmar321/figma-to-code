"""Finds font files for a family in the local font folder, whatever their naming.

Accepted layouts (family "TT Hoves" → slug "tt-hoves"):
  <font_dir>/tt-hoves/tt-hoves-latin-700-normal.woff2      (Fontsource naming)
  <font_dir>/tt-hoves/TTHoves-Bold.otf                      (vendor naming)
  <font_dir>/TT Hoves Bold Italic.ttf                       (loose files in the root)
Variable fonts ("...[wght].ttf", "...Variable.woff2") cover every weight.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

EXTENSIONS = {".woff2": "woff2", ".woff": "woff", ".ttf": "truetype", ".otf": "opentype"}
WEIGHT_WORDS = [  # longest first so "extrabold" wins over "bold"
    ("extralight", 200), ("ultralight", 200), ("extrabold", 800), ("ultrabold", 800), ("semibold", 600),
    ("demibold", 600), ("hairline", 100), ("regular", 400), ("medium", 500), ("normal", 400), ("light", 300),
    ("black", 900), ("heavy", 900), ("thin", 100), ("bold", 700), ("book", 400),
]  # fmt: skip


@dataclass(frozen=True)
class FontFile:
    path: Path
    weight: int
    style: str  # normal | italic
    fmt: str
    variable: bool = False


def _squash(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "font"


def parse(path: Path, family: str) -> FontFile | None:
    fmt = EXTENSIONS.get(path.suffix.lower())
    if not fmt:
        return None
    name = _squash(path.stem)
    fam = _squash(family)
    rest = name.replace(fam, "", 1) if fam in name else name
    variable = "variable" in rest or "wght" in path.stem.lower()
    style = "italic" if ("italic" in rest or "oblique" in rest) else "normal"
    m = re.search(r"(?<!\d)([1-9]00)(?!\d)", rest)
    weight = int(m.group(1)) if m else next((w for word, w in WEIGHT_WORDS if word in rest), 400)
    return FontFile(path, weight, style, fmt, variable)


def find(font_dir: Path | None, family: str) -> list[FontFile]:
    if not font_dir or not font_dir.exists():
        return []
    fam = _squash(family)
    candidates: list[Path] = []
    folder = font_dir / slug(family)
    if folder.is_dir():
        candidates += [p for p in folder.rglob("*") if p.is_file()]
    candidates += [p for p in font_dir.iterdir() if p.is_file() and fam in _squash(p.stem)]
    files = [f for f in (parse(p, family) for p in candidates) if f]
    order = {"woff2": 0, "woff": 1, "opentype": 2, "truetype": 3}
    return sorted(files, key=lambda f: (order[f.fmt], f.path.name))


def pick(files: list[FontFile], weight: int, style: str) -> FontFile | None:
    """Best file for a weight/style: variable font, exact match, else nearest weight in the same style."""
    same = [f for f in files if f.style == style] or files
    if not same:
        return None
    var = [f for f in same if f.variable]
    if var:
        return var[0]
    return min(same, key=lambda f: (abs(f.weight - weight), f.weight < weight))
