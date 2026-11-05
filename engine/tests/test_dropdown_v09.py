"""v0.9: a phone header whose menu icon (a layer inside the component) switches it to its Open variant.

Only the icon (and the close icon in the open state) toggles; the clipped header grows to show the
links, instead of the whole header being one big button."""

import re

from app.pipeline import interactions as ix
from app.pipeline.generate import generate
from app.pipeline.ir import build_ir
from app.pipeline.layout import layout_page
from tests.test_structured_v08 import al, node, text

WHITE = [{"type": "SOLID", "color": {"r": 1, "g": 1, "b": 1}}]
DARK = [{"type": "SOLID", "color": {"r": 0.1, "g": 0.1, "b": 0.1}}]


def click_to(dest: str) -> list[dict]:
    return [{"trigger": {"type": "ON_CLICK"}, "actions": [{"type": "NODE", "navigation": "CHANGE_TO",
             "destinationId": dest, "transition": None}]}]  # fmt: skip


def header(prefix: str, parent: str | None, y: float, h: float, nodes: dict, icon_reactions: list, **kw) -> None:
    """The header: a row (logo + menu icon) and a link list below it, inside a clipped column."""
    p = prefix
    node(p, kw.pop("name", "Navigation mobile"), kw.pop("ntype", "FRAME"), parent, 0, y, 390, h, nodes,
         **al("VERTICAL", 24, (16, 16, 16, 16)), clipsContent=True, fills=WHITE, layoutSizingHorizontal="FILL",
         **kw)  # fmt: skip
    node(f"{p}row", "Row", "FRAME", p, 16, y + 16, 358, 46, nodes, **{**al("HORIZONTAL", 0),
         "primaryAxisAlignItems": "SPACE_BETWEEN"}, layoutSizingHorizontal="FILL")  # fmt: skip
    node(f"{p}logo", "Logo", "TEXT", f"{p}row", 16, y + 28, 60, 22, nodes, **text("Area"))
    node(f"{p}icon", "Menu icon", "RECTANGLE", f"{p}row", 334, y + 19, 40, 40, nodes, fills=DARK,
         reactions=icon_reactions)  # fmt: skip
    node(f"{p}list", "Links", "FRAME", p, 16, y + 86, 358, 120, nodes, **al("VERTICAL", 16),
         layoutSizingHorizontal="FILL")  # fmt: skip
    for i, label in enumerate(("Benefits", "Specs", "Contact")):
        node(f"{p}l{i}", label, "TEXT", f"{p}list", 16, y + 86 + i * 40, 80, 22, nodes, **text(label))


def bundle() -> dict:
    nodes: dict = {}
    node("R", "Mobile", "FRAME", None, 0, 0, 390, 600, nodes, **al("VERTICAL"), fills=WHITE)
    # Page instance (closed): sublayer ids are "I<instance>;<main layer>".
    header("NAV", "R", 0, 78, nodes, [], ntype="INSTANCE", mainComponentId="C:", layoutSizingVertical="FIXED")
    for suffix in ("row", "logo", "icon", "list", "l0", "l1", "l2"):
        n = nodes.pop(f"NAV{suffix}")
        n["id"] = f"INAV;C:{suffix}"
        n["parentId"] = n["parentId"] if n["parentId"] in ("NAV", "R") else f"INAV;C:{n['parentId'][3:]}"
        nodes[n["id"]] = n
    for n in nodes.values():
        n["children"] = [c if c in nodes else f"INAV;C:{c[3:]}" for c in n["children"]]
    node("S", "Hero", "FRAME", "R", 0, 78, 390, 522, nodes, **al("VERTICAL", pad=(40, 16, 40, 16)),
         layoutSizingHorizontal="FILL")  # fmt: skip
    node("ST", "Title", "TEXT", "S", 16, 118, 200, 40, nodes, **text("Browse everything.", 28))
    # The component set: Closed (main) and Open, away from the page.
    header("C:", None, 2000, 78, nodes, click_to("O:"), layoutSizingVertical="FIXED", name="State=Closed",
           ntype="COMPONENT")  # fmt: skip
    header("O:", None, 3000, 246, nodes, click_to("C:"), layoutSizingVertical="HUG", name="State=Open",
           ntype="COMPONENT")  # fmt: skip
    nodes["O:icon"]["name"] = "Menu icon"
    frame = {"role": "mobile", "rootId": "R", "name": "Mobile", "width": 390, "height": 600, "referenceAssets": []}
    states = [{"variantId": "O:", "name": "State=Open", "trigger": "click", "wired": True, "transition": None,
               "from": "C:", "triggerLayer": "C:icon"}]  # fmt: skip
    return {
        "schemaVersion": "0.1", "source": {"fileName": "t", "pluginVersion": "0", "exportedAt": "now"},
        "frames": [frame], "nodes": nodes, "assets": [],
        "interactions": {"components": [{"id": "C:", "name": "State=Closed", "setId": "SET", "setName": "Nav",
                         "variantProperties": {"State": "Closed"}, "reactions": [], "referenceAsset": None,
                         "states": states}], "overlays": [], "targets": {}},
    }  # fmt: skip


def test_menu_icon_inside_a_component_opens_its_dropdown(tmp_path):
    b = bundle()
    inter = ix.Interactions(b)
    inter.prepare()
    b["_interactions"] = inter
    assert inter.triggers["NAV"] == ["INAV;C:icon"]  # the icon, not the whole header
    opened = inter.changes["NAV"][0]["changes"]["NAV"]["css"]
    assert opened["height"] == "auto"  # the clipped header grows to its Open (Hug) height

    p = layout_page(build_ir(b)["mobile"])
    generate(p, b, tmp_path / "assets", tmp_path / "out")
    html = (tmp_path / "out" / "index.html").read_text()
    css = (tmp_path / "out" / "css" / "styles.css").read_text()
    root = re.search(r"<\w+ [^>]*data-toggle( |>)[^>]*>", html).group(0)
    assert "data-toggle-by" in root and 'role="button"' not in root
    icon = re.search(r"<\w+ [^>]*data-toggle-trigger[^>]*>", html).group(0)
    assert 'data-figma-id="INAV;C:icon"' in icon and 'role="button"' in icon and 'aria-label="Menu icon"' in icon
    assert re.search(r'\[aria-expanded="true"\] \{[^}]*height: auto', css)
    assert re.search(r"\{[^}]*height: 78px", css)  # closed: clipped to the bar
