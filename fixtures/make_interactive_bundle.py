"""Generates fixtures/interactive-bundle.json: a 3-page site (Home, About, Contact + a Home mobile frame)
with the prototype features real files use, shaped like the plugin's output:

* a Button component (Default / Hover / Pressed) wired with "While hovering" and "While pressing";
* nav links (Default / Hover) where an underline only appears on hover, linking to the other pages;
* cards with an unwired State=Hover variant (shadow, border, arrow nudge);
* a testimonial slider whose variants change after a delay (fade carousel);
* FAQ items that open on click (the answer only exists in the Open variant, the chevron rotates);
* a fixed header, a "Book a call" button opening a modal overlay with a close button;
* a mobile frame whose menu icon opens a full-screen menu overlay.

Run:  python fixtures/make_interactive_bundle.py
"""

import json
from pathlib import Path

INTER = {"family": "Inter", "style": "Regular"}
INTER_SEMI = {"family": "Inter", "style": "Semi Bold"}
INTER_BOLD = {"family": "Inter", "style": "Bold"}


def solid(r, g, b, a=1.0):
    return [{"type": "SOLID", "visible": True, "opacity": a, "blendMode": "NORMAL", "color": {"r": r, "g": g, "b": b}}]


def hexfill(h, a=1.0):
    h = h.lstrip("#")
    return solid(int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255, a)


WHITE, INK, MUTED, BRAND = hexfill("#ffffff"), hexfill("#111827"), hexfill("#4b5563"), hexfill("#4f46e5")
BRAND_DARK, BRAND_DEEP, SOFT, LINE = hexfill("#4338ca"), hexfill("#3730a3"), hexfill("#f5f5ff"), hexfill("#e5e7eb")


def shadow(y, blur, a, color=(0.07, 0.09, 0.15)):
    r, g, b = color
    return [{"type": "DROP_SHADOW", "visible": True, "blendMode": "NORMAL", "color": {"r": r, "g": g, "b": b, "a": a},
             "offset": {"x": 0, "y": y}, "radius": blur, "spread": 0, "showShadowBehindNode": False}]


nodes: dict[str, dict] = {}
assets: list[dict] = []


def add(node_id, name, ntype, parent, x, y, w, h, **props):
    """x, y are absolute canvas coordinates (absoluteBoundingBox); relative x/y are derived."""
    px_, py_ = 0.0, 0.0
    if parent:
        pb = nodes[parent]["absoluteBoundingBox"]
        px_, py_ = pb["x"], pb["y"]
    node = {
        "id": node_id, "name": name, "type": ntype, "parentId": parent, "children": [],
        "visible": True, "opacity": 1, "blendMode": "PASS_THROUGH", "rotation": 0,
        "x": x - px_, "y": y - py_, "width": w, "height": h,
        "absoluteBoundingBox": {"x": x, "y": y, "width": w, "height": h},
    }
    if ntype in ("FRAME", "COMPONENT", "INSTANCE"):
        node.update({"layoutMode": "NONE", "fills": [], "strokes": [], "effects": [], "clipsContent": False})
    if ntype in ("RECTANGLE", "ELLIPSE", "VECTOR"):
        node.update({"fills": [], "strokes": []})
    node.update(props)
    nodes[node_id] = node
    if parent:
        nodes[parent]["children"].append(node_id)
        if nodes[parent].get("layoutMode") in ("HORIZONTAL", "VERTICAL"):
            node.setdefault("layoutSizingHorizontal", "HUG")
            node.setdefault("layoutSizingVertical", "HUG")
            node.setdefault("layoutPositioning", "AUTO")
    return node


def auto(direction, gap, pad, primary="MIN", counter="MIN"):
    return {
        "layoutMode": direction, "itemSpacing": gap,
        "paddingTop": pad[0], "paddingRight": pad[1], "paddingBottom": pad[2], "paddingLeft": pad[3],
        "primaryAxisAlignItems": primary, "counterAxisAlignItems": counter,
        "primaryAxisSizingMode": "AUTO", "counterAxisSizingMode": "AUTO",
    }


def text(chars, font, size, line, fills=INK, resize="WIDTH_AND_HEIGHT", align="LEFT"):
    return {
        "characters": chars, "fontName": font, "fontSize": size,
        "lineHeight": {"unit": "PIXELS", "value": line}, "letterSpacing": {"unit": "PERCENT", "value": 0},
        "textAlignHorizontal": align, "textAutoResize": resize, "fills": fills,
        "textSegments": [{"characters": chars, "start": 0, "end": len(chars), "fontName": font, "fontSize": size,
                          "fills": fills, "lineHeight": {"unit": "PIXELS", "value": line},
                          "letterSpacing": {"unit": "PERCENT", "value": 0}, "textDecoration": "NONE",
                          "textCase": "ORIGINAL"}],
    }


def icon(node_id, name, parent, x, y, size=16, rotation=0, reactions=None):
    """A vector icon, exported as one SVG by the plugin."""
    n = add(node_id, name, "VECTOR", parent, x, y, size, size, fills=INK, exportAs="svg", rotation=rotation)
    if reactions:
        n["reactions"] = reactions
    assets.append({"id": f"svg-{node_id.replace(':', '_').replace(';', '_')}", "kind": "svg", "format": "SVG",
                   "nodeId": node_id, "width": size, "height": size, "byteLength": 0})
    return n


def ref(label, node_id, w, h):
    aid = f"ref-{label}-{node_id.replace(':', '_').replace(';', '_')}-1x"
    assets.append({"id": aid, "kind": "reference", "format": "PNG", "nodeId": node_id, "scale": 1,
                   "width": w, "height": h, "byteLength": 0})
    return aid


def smart(duration, easing="EASE_OUT"):
    return {"type": "SMART_ANIMATE", "duration": duration, "easing": {"type": easing}}


def react(trigger, navigation=None, dest=None, transition=None, **action):
    if navigation:
        act = {"type": "NODE", "destinationId": dest, "navigation": navigation, "transition": transition, **action}
    else:
        act = action
    return {"trigger": trigger, "actions": [act]}


# ----------------------------------------------------------------------------- components (extra roots)
CX, CY = 0, 6000  # where the components live on the canvas


def button_variant(vid, state, x, fill, gap, effects=(), scale=1.0):
    w, h = round((24 + 60 + gap + 16 + 24) * scale), round(48 * scale)
    add(vid, f"State={state}", "COMPONENT", None, x, CY, w, h, **auto("HORIZONTAL", gap, (14, 24, 14, 24), "CENTER", "CENTER"),
        fills=fill, effects=list(effects), cornerRadius=8 * scale, variantProperties={"State": state})
    nodes[vid]["parentId"] = None
    add(f"{vid}:label", "Label", "TEXT", vid, x + 24 * scale, CY + 14 * scale, 60 * scale, 20 * scale,
        **text("Button", INTER_SEMI, 16 * scale, 20 * scale, WHITE))
    add(f"{vid}:arrow", "Arrow", "VECTOR", vid, x + (24 + 60 + gap) * scale, CY + 16 * scale, 16 * scale, 16 * scale,
        fills=WHITE, exportAs="svg")
    assets.append({"id": f"svg-{vid.replace(':', '_')}_arrow", "kind": "svg", "format": "SVG", "nodeId": f"{vid}:arrow",
                   "width": 16, "height": 16, "byteLength": 0})


button_variant("C:btn", "Default", CX, BRAND, 8)
button_variant("C:btn-h", "Hover", CX + 300, BRAND_DARK, 12, shadow(8, 24, 0.35, (0.31, 0.27, 0.9)))
button_variant("C:btn-p", "Pressed", CX + 600, BRAND_DEEP, 8, (), scale=0.96)
nodes["C:btn"]["reactions"] = [
    react({"type": "ON_HOVER"}, "CHANGE_TO", "C:btn-h", smart(0.2)),
    react({"type": "ON_PRESS"}, "CHANGE_TO", "C:btn-p", smart(0.1)),
]


def navlink_variant(vid, state, x, color, underline_visible):
    add(vid, f"State={state}", "COMPONENT", None, x, CY + 200, 64, 28, variantProperties={"State": state})
    nodes[vid]["parentId"] = None
    add(f"{vid}:t", "Label", "TEXT", vid, x, CY + 200, 64, 20, **text("Link", INTER_SEMI, 15, 20, color))
    add(f"{vid}:u", "Underline", "RECTANGLE", vid, x, CY + 226, 64, 2, fills=BRAND, visible=underline_visible)


navlink_variant("C:nav", "Default", CX, MUTED, False)
navlink_variant("C:nav-h", "Hover", CX + 200, BRAND, True)
nodes["C:nav"]["reactions"] = [
    react({"type": "MOUSE_ENTER", "delay": 0}, "CHANGE_TO", "C:nav-h", smart(0.15)),
]
nodes["C:nav-h"]["reactions"] = [react({"type": "MOUSE_LEAVE", "delay": 0}, "CHANGE_TO", "C:nav", smart(0.15))]


def card_variant(vid, state, x, effects, stroke, arrow_dx):
    add(vid, f"State={state}", "COMPONENT", None, x, CY + 400, 384, 240, fills=WHITE, effects=effects,
        strokes=stroke, strokeWeight=1, strokeAlign="INSIDE", cornerRadius=16, variantProperties={"State": state})
    nodes[vid]["parentId"] = None
    add(f"{vid}:title", "Title", "TEXT", vid, x + 32, CY + 432, 280, 32, **text("Card title", INTER_BOLD, 24, 32))
    add(f"{vid}:body", "Body", "TEXT", vid, x + 32, CY + 480, 320, 72,
        **text("Card text that explains\nthe feature in a few\nshort lines.", INTER, 16, 24, MUTED, "HEIGHT"))
    add(f"{vid}:go", "Go", "VECTOR", vid, x + 32 + arrow_dx, CY + 584, 24, 24, fills=BRAND, exportAs="svg")
    assets.append({"id": f"svg-{vid.replace(':', '_')}_go", "kind": "svg", "format": "SVG", "nodeId": f"{vid}:go",
                   "width": 24, "height": 24, "byteLength": 0})


card_variant("C:card", "Default", CX, shadow(2, 8, 0.06), LINE, 0)
card_variant("C:card-h", "Hover", CX + 500, shadow(16, 40, 0.12), hexfill("#c7d2fe"), 6)

QUOTES = ["“The best tool we bought this year.”", "“Our team ships twice as fast now.”", "“Setup took ten minutes. Really.”"]


def slide_variant(vid, n, x):
    add(vid, f"Slide={n}", "COMPONENT", None, x, CY + 800, 1200, 160, fills=SOFT, cornerRadius=24,
        clipsContent=True, variantProperties={"Slide": str(n)})
    nodes[vid]["parentId"] = None
    for i, q in enumerate(QUOTES):
        add(f"{vid}:q{i}", f"Quote {i + 1}", "TEXT", vid, x + 80, CY + 856, 1040, 48,
            **text(q, INTER_SEMI, 32, 48, INK, "HEIGHT", "CENTER"), opacity=1 if i == n - 1 else 0)


for n in (1, 2, 3):
    slide_variant(f"C:slide{n}", n, CX + (n - 1) * 1400)
for n, nxt in ((1, 2), (2, 3), (3, 1)):
    nodes[f"C:slide{n}"]["reactions"] = [
        react({"type": "AFTER_TIMEOUT", "timeout": 3}, "CHANGE_TO", f"C:slide{nxt}", smart(0.6, "EASE_IN_AND_OUT"))
    ]


def faq_variant(vid, state, x, is_open):
    h = 168 if is_open else 72
    add(vid, f"State={state}", "COMPONENT", None, x, CY + 1100, 800, h, **auto("VERTICAL", 12, (24, 24, 24, 24)),
        fills=WHITE, strokes=LINE, strokeWeight=1, strokeAlign="INSIDE", cornerRadius=12,
        variantProperties={"State": state})
    nodes[vid]["parentId"] = None
    nodes[vid]["layoutSizingHorizontal"] = "FIXED"
    add(f"{vid}:row", "Question row", "FRAME", vid, x + 24, CY + 1124, 752, 24,
        **auto("HORIZONTAL", 16, (0, 0, 0, 0), "SPACE_BETWEEN", "CENTER"), layoutSizingHorizontal="FILL")
    add(f"{vid}:q", "Question", "TEXT", f"{vid}:row", x + 24, CY + 1124, 400, 24,
        **text("Question", INTER_SEMI, 18, 24))
    add(f"{vid}:chev", "Chevron", "VECTOR", f"{vid}:row", x + 752, CY + 1128, 16, 16, fills=INK, exportAs="svg",
        rotation=180 if is_open else 0)
    assets.append({"id": f"svg-{vid.replace(':', '_')}_chev", "kind": "svg", "format": "SVG", "nodeId": f"{vid}:chev",
                   "width": 16, "height": 16, "byteLength": 0})
    if is_open:
        add(f"{vid}:a", "Answer", "TEXT", vid, x + 24, CY + 1160, 752, 72,
            **text("Yes. Every plan includes it, and you can turn it off at any time from the settings.\n"
                   "Changes apply to the whole team right away.\nNo extra cost.", INTER, 16, 24, MUTED, "HEIGHT"),
            layoutSizingHorizontal="FILL")


faq_variant("C:faq", "Closed", CX, False)
faq_variant("C:faq-o", "Open", CX + 1000, True)
nodes["C:faq"]["reactions"] = [react({"type": "ON_CLICK"}, "CHANGE_TO", "C:faq-o", smart(0.3, "GENTLE"))]
nodes["C:faq-o"]["reactions"] = [react({"type": "ON_CLICK"}, "CHANGE_TO", "C:faq", smart(0.3, "GENTLE"))]


# ----------------------------------------------------------------------------- instances
def instance(iid, main, parent, x, y, overrides_text=None, reactions=None, override_ids=None):
    """An instance of `main`: copies its layers with Figma-style ids (I<instance>;<main layer>)."""
    m = nodes[main]
    w, h = m["width"], m["height"]
    props = {k: v for k, v in m.items() if k not in ("id", "name", "type", "parentId", "children", "x", "y",
                                                       "absoluteBoundingBox", "reactions", "variantProperties")}
    add(iid, m["name"].split("=")[-1] if main.startswith("C:") else m["name"], "INSTANCE", parent, x, y, w, h,
        **props, mainComponentId=main)
    nodes[iid]["name"] = {"C:btn": "Button", "C:nav": "Nav link", "C:card": "Card", "C:slide1": "Testimonials",
                          "C:faq": "FAQ item"}.get(main, nodes[iid]["name"])
    if reactions:
        nodes[iid]["reactions"] = reactions
    mb = m["absoluteBoundingBox"]

    def copy(src_id, dst_parent):
        s = nodes[src_id]
        b = s["absoluteBoundingBox"]
        nid = f"I{iid};{src_id}"
        props = {k: v for k, v in s.items() if k not in ("id", "name", "type", "parentId", "children", "x", "y",
                                                           "absoluteBoundingBox")}
        if overrides_text and s["type"] == "TEXT" and s["name"] in overrides_text:
            chars = overrides_text[s["name"]]
            props.update(text(chars, s["fontName"], s["fontSize"], s["lineHeight"]["value"], s["fills"],
                              s["textAutoResize"], s["textAlignHorizontal"]))
        add(nid, s["name"], s["type"], dst_parent, x + b["x"] - mb["x"], y + b["y"] - mb["y"], b["width"], b["height"],
            **props)
        if s.get("exportAs") == "svg":
            assets.append({"id": f"svg-{nid.replace(':', '_').replace(';', '_')}", "kind": "svg", "format": "SVG",
                           "nodeId": nid, "width": b["width"], "height": b["height"], "byteLength": 0})
        for c in s["children"]:
            copy(c, nid)

    for c in m["children"]:
        copy(c, iid)
    if override_ids:
        nodes[iid]["overrides"] = [{"id": f"I{iid};{k}", "overriddenFields": v} for k, v in override_ids.items()]
    return nodes[iid]


def page_frame(fid, name, ox, w, h, fixed=0):
    add(fid, name, "FRAME", None, ox, 0, w, h, fills=WHITE, clipsContent=True, numberOfFixedChildren=fixed)
    nodes[fid]["parentId"] = None


PAGES = {"home": "P:home", "about": "P:about", "contact": "P:contact"}


def header(prefix, fid, ox, w):
    """Desktop header: logo, three nav links (pages), a Book a call button (modal). Fixed when scrolling."""
    hid = f"{prefix}:header"
    add(hid, "Header", "FRAME", fid, ox, 0, w, 80, fills=WHITE, effects=shadow(1, 0, 0.08))
    add(f"{prefix}:logo", "Logo", "TEXT", hid, ox + 120, 26, 120, 28, **text("Northwind", INTER_BOLD, 22, 28),
        reactions=[react({"type": "ON_CLICK"}, "NAVIGATE", PAGES["home"], smart(0.4))])
    nav = f"{prefix}:nav"
    add(nav, "Menu", "FRAME", hid, ox + 560, 26, 320, 28, **auto("HORIZONTAL", 40, (0, 0, 0, 0)))
    for i, (label, page) in enumerate((("Home", "home"), ("About", "about"), ("Contact", "contact"))):
        instance(f"{prefix}:n{i}", "C:nav", nav, ox + 560 + i * 104, 26, {"Label": label},
                 reactions=[react({"type": "ON_CLICK"}, "NAVIGATE", PAGES[page], smart(0.4))])
    instance(f"{prefix}:cta", "C:btn", hid, ox + w - 120 - 132, 16, {"Label": "Book a call"},
             reactions=[react({"type": "ON_CLICK"}, "OVERLAY", "O:modal", {"type": "DISSOLVE", "duration": 0.25,
                                                                         "easing": {"type": "EASE_OUT"}})])
    return hid


# Home / Desktop -------------------------------------------------------------
W = 1440
page_frame("P:home", "Home / Desktop", 0, W, 2040, fixed=1)
add("H:hero-bg", "Hero background", "RECTANGLE", "P:home", 0, 80, W, 560, fills=SOFT)
add("H:h1", "Title", "TEXT", "P:home", 120, 200, 760, 144,
    **text("Ship your next idea\nin a weekend", INTER_BOLD, 64, 72, INK, "HEIGHT"))
add("H:lead", "Lead", "TEXT", "P:home", 120, 368, 560, 56,
    **text("Northwind gives your team the pages, forms and analytics\nto launch without waiting on anyone.",
           INTER, 18, 28, MUTED, "HEIGHT"))
instance("H:start", "C:btn", "P:home", 120, 472, {"Label": "Get started"},
         reactions=[react({"type": "ON_CLICK"}, "SCROLL_TO", "H:features-title", {"type": "SCROLL_ANIMATE",
                                                                              "duration": 0.6, "easing": {"type": "EASE_IN_AND_OUT"}})])
add("H:features-title", "Features", "TEXT", "P:home", 120, 720, 500, 48,
    **text("Everything in one place", INTER_BOLD, 40, 48))
for i, (title, page) in enumerate((("Pages", "about"), ("Forms", "contact"), ("Analytics", "about"))):
    instance(f"H:card{i}", "C:card", "P:home", 120 + i * 408, 808, {"Title": title},
             reactions=[react({"type": "ON_CLICK"}, "NAVIGATE", PAGES[page], smart(0.4))])
instance("H:slider", "C:slide1", "P:home", 120, 1128)
add("H:faq-title", "FAQ", "TEXT", "P:home", 120, 1368, 600, 48, **text("Questions", INTER_BOLD, 40, 48))
instance("H:faq0", "C:faq", "P:home", 120, 1448, {"Question": "Is there a free plan?"})
instance("H:faq1", "C:faq", "P:home", 120, 1540, {"Question": "Can I cancel any time?"})
add("H:footer", "Footer", "RECTANGLE", "P:home", 0, 1880, W, 160, fills=INK)
add("H:copy", "Copyright", "TEXT", "P:home", 120, 1948, 400, 24, **text("© 2026 Northwind", INTER, 16, 24, WHITE))
header("H", "P:home", 0, W)  # last child: on top, fixed (numberOfFixedChildren = 1)

# About / Desktop -------------------------------------------------------------
AX = 1600
page_frame("P:about", "About / Desktop", AX, W, 1200, fixed=1)
add("A:hero-bg", "About background", "RECTANGLE", "P:about", AX, 80, W, 400, fills=SOFT)
add("A:h1", "Title", "TEXT", "P:about", AX + 120, 200, 760, 72, **text("About Northwind", INTER_BOLD, 64, 72))
add("A:lead", "Lead", "TEXT", "P:about", AX + 120, 296, 640, 56,
    **text("We are a small team building tools for people who launch things.\nFounded in 2021, remote since day one.",
           INTER, 18, 28, MUTED, "HEIGHT"))
for i, title in enumerate(("Mission", "Team", "Values")):
    instance(f"A:card{i}", "C:card", "P:about", AX + 120 + i * 408, 560, {"Title": title})
add("A:footer", "Footer", "RECTANGLE", "P:about", AX, 1040, W, 160, fills=INK)
add("A:copy", "Copyright", "TEXT", "P:about", AX + 120, 1108, 400, 24, **text("© 2026 Northwind", INTER, 16, 24, WHITE))
header("A", "P:about", AX, W)

# Contact / Desktop -----------------------------------------------------------
KX = 3200
page_frame("P:contact", "Contact / Desktop", KX, W, 900, fixed=1)
add("K:h1", "Title", "TEXT", "P:contact", KX + 120, 200, 760, 72, **text("Talk to us", INTER_BOLD, 64, 72))
add("K:lead", "Lead", "TEXT", "P:contact", KX + 120, 296, 560, 28,
    **text("hello@northwind.example · +1 555 0100", INTER, 18, 28, MUTED))
instance("K:back", "C:btn", "P:contact", KX + 120, 372, {"Label": "Go back"},
         reactions=[react({"type": "ON_CLICK"}, None, None, None, type="BACK")])
add("K:footer", "Footer", "RECTANGLE", "P:contact", KX, 740, W, 160, fills=INK)
add("K:copy", "Copyright", "TEXT", "P:contact", KX + 120, 808, 400, 24, **text("© 2026 Northwind", INTER, 16, 24, WHITE))
header("K", "P:contact", KX, W)

# Home / Mobile ---------------------------------------------------------------
MX, MW = 4800, 390
page_frame("P:mobile", "Home / Mobile", MX, MW, 1600, fixed=1)
add("M:hero-bg", "Hero background", "RECTANGLE", "P:mobile", MX, 64, MW, 520, fills=SOFT)
add("M:h1", "Title", "TEXT", "P:mobile", MX + 16, 120, 358, 132,
    **text("Ship your next idea\nin a weekend", INTER_BOLD, 40, 44, INK, "HEIGHT"))
add("M:lead", "Lead", "TEXT", "P:mobile", MX + 16, 276, 358, 84,
    **text("Northwind gives your team the pages, forms and analytics\nto launch without waiting on anyone.",
           INTER, 18, 28, MUTED, "HEIGHT"))
instance("M:start", "C:btn", "P:mobile", MX + 16, 392, {"Label": "Get started"})
add("M:features-title", "Features", "TEXT", "P:mobile", MX + 16, 640, 358, 48,
    **text("Everything in one place", INTER_BOLD, 28, 36, INK, "HEIGHT"))
add("M:footer", "Footer", "RECTANGLE", "P:mobile", MX, 1440, MW, 160, fills=INK)
add("M:copy", "Copyright", "TEXT", "P:mobile", MX + 16, 1508, 300, 24, **text("© 2026 Northwind", INTER, 16, 24, WHITE))
add("M:header", "Header", "FRAME", "P:mobile", MX, 0, MW, 64, fills=WHITE, effects=shadow(1, 0, 0.08))
add("M:logo", "Logo", "TEXT", "M:header", MX + 16, 18, 120, 28, **text("Northwind", INTER_BOLD, 22, 28))
icon("M:menu", "Menu", "M:header", MX + MW - 16 - 24, 20, 24,
     reactions=[react({"type": "ON_CLICK"}, "OVERLAY", "O:menu", {"type": "MOVE_IN", "direction": "LEFT",
                                                                 "matchLayers": False, "duration": 0.3,
                                                                 "easing": {"type": "EASE_OUT"}})])

# Overlays --------------------------------------------------------------------
add("O:modal", "Booking modal", "FRAME", None, 0, 5000, 480, 280, fills=WHITE, cornerRadius=16,
    effects=shadow(24, 64, 0.25), overlayPositionType="CENTER",
    overlayBackground={"type": "SOLID_COLOR", "color": {"r": 0, "g": 0, "b": 0, "a": 0.5}},
    overlayBackgroundInteraction="CLOSE_ON_CLICK_OUTSIDE")
nodes["O:modal"]["parentId"] = None
add("O:m-title", "Title", "TEXT", "O:modal", 40, 5040, 360, 36, **text("Book a call", INTER_BOLD, 28, 36))
add("O:m-text", "Text", "TEXT", "O:modal", 40, 5092, 400, 48,
    **text("Pick a time that suits you and we will\nsend a calendar invite.", INTER, 16, 24, MUTED, "HEIGHT"))
instance("O:m-ok", "C:btn", "O:modal", 40, 5184, {"Label": "Choose a time"},
         reactions=[react({"type": "ON_CLICK"}, None, None, None, type="URL", url="https://cal.example.com/northwind",
                          openInNewTab=True)])
icon("O:m-close", "Close", "O:modal", 424, 5032, 24, reactions=[react({"type": "ON_CLICK"}, None, None, None, type="CLOSE")])

add("O:menu", "Mobile menu", "FRAME", None, 600, 5000, 390, 844, fills=WHITE, overlayPositionType="TOP_LEFT",
    overlayBackground={"type": "NONE"}, overlayBackgroundInteraction="NONE")
nodes["O:menu"]["parentId"] = None
for i, (label, page) in enumerate((("Home", "home"), ("About", "about"), ("Contact", "contact"))):
    add(f"O:mi{i}", label, "TEXT", "O:menu", 616, 5096 + i * 64, 200, 40, **text(label, INTER_BOLD, 32, 40),
        reactions=[react({"type": "ON_CLICK"}, "NAVIGATE", PAGES[page], None)])
icon("O:mclose", "Close", "O:menu", 950, 5020, 24, reactions=[react({"type": "ON_CLICK"}, None, None, None, type="CLOSE")])

# ----------------------------------------------------------------------------- bundle
frames = []
for fid, role in (("P:home", "desktop"), ("P:about", "desktop"), ("P:contact", "desktop"), ("P:mobile", "mobile")):
    f = nodes[fid]
    frames.append({"role": role, "rootId": fid, "name": f["name"], "page": f["name"].split(" / ")[0],
                   "width": f["width"], "height": f["height"],
                   "referenceAssets": [ref(role, fid, f["width"], f["height"])]})


def comp_entry(cid, set_name, states, reactions=()):
    return {"id": cid, "name": nodes[cid]["name"], "setId": f"SET:{set_name}", "setName": set_name,
            "variantProperties": nodes[cid].get("variantProperties"), "reactions": list(reactions),
            "referenceAsset": ref("state", cid, nodes[cid]["width"], nodes[cid]["height"]), "states": states}


def state(vid, trigger, wired=True, transition=None, **kw):
    return {"variantId": vid, "name": nodes[vid]["name"], "variantProperties": nodes[vid].get("variantProperties"),
            "trigger": trigger, "wired": wired, "transition": transition,
            "referenceAsset": ref("state", vid, nodes[vid]["width"], nodes[vid]["height"]), **kw}


interactions = {
    "components": [
        comp_entry("C:btn", "Button", [state("C:btn-h", "hover", True, smart(0.2), **{"from": "C:btn"}),
                                       state("C:btn-p", "press", True, smart(0.1), **{"from": "C:btn"})],
                   nodes["C:btn"]["reactions"]),
        comp_entry("C:nav", "Nav link", [state("C:nav-h", "hover", True, smart(0.15), **{"from": "C:nav"})],
                   nodes["C:nav"]["reactions"]),
        comp_entry("C:card", "Card", [state("C:card-h", "hover", False, None, **{"from": "C:card"})]),
        comp_entry("C:slide1", "Testimonial slider",
                   [state("C:slide2", "timeout", True, smart(0.6, "EASE_IN_AND_OUT"), timeout=3, order=1, **{"from": "C:slide1"}),
                    state("C:slide3", "timeout", True, smart(0.6, "EASE_IN_AND_OUT"), timeout=3, order=2, **{"from": "C:slide2"})],
                   nodes["C:slide1"]["reactions"]),
        comp_entry("C:faq", "FAQ item", [state("C:faq-o", "click", True, smart(0.3, "GENTLE"), **{"from": "C:faq"})],
                   nodes["C:faq"]["reactions"]),
    ],
    "overlays": [
        {"rootId": "O:modal", "name": "Booking modal", "positionType": "CENTER",
         "background": nodes["O:modal"]["overlayBackground"], "closeOnOutside": True,
         "referenceAsset": ref("overlay", "O:modal", 480, 280)},
        {"rootId": "O:menu", "name": "Mobile menu", "positionType": "TOP_LEFT", "background": {"type": "NONE"},
         "closeOnOutside": False, "referenceAsset": ref("overlay", "O:menu", 390, 844)},
    ],
    "targets": {
        "P:home": {"name": "Home / Desktop", "type": "FRAME", "topFrameId": "P:home"},
        "P:about": {"name": "About / Desktop", "type": "FRAME", "topFrameId": "P:about"},
        "P:contact": {"name": "Contact / Desktop", "type": "FRAME", "topFrameId": "P:contact"},
        "H:features-title": {"name": "Features", "type": "TEXT", "topFrameId": "P:home"},
        "O:modal": {"name": "Booking modal", "type": "FRAME", "topFrameId": "O:modal"},
        "O:menu": {"name": "Mobile menu", "type": "FRAME", "topFrameId": "O:menu"},
    },
}

bundle = {
    "schemaVersion": "0.1",
    "source": {"fileKey": None, "fileName": "Northwind (interactive fixture)", "pageId": "0:1", "pageName": "Site",
               "pluginVersion": "0.1.0", "editorType": "figma", "exportedAt": "2026-11-03T10:00:00Z"},
    "frames": frames,
    "nodes": nodes,
    "variables": [],
    "variableCollections": [],
    "components": {c["id"]: {"id": c["id"], "name": nodes[c["id"]]["name"], "key": None, "componentSetId": c["setId"],
                             "componentSetName": c["setName"], "variantProperties": c["variantProperties"],
                             "remote": False, "description": None} for c in interactions["components"]},
    "fonts": [{"family": "Inter", "style": s, "usageCount": 10} for s in ("Regular", "Semi Bold", "Bold")],
    "assets": assets,
    "diagnostics": [],
    "interactions": interactions,
}

out = Path(__file__).with_name("interactive-bundle.json")
out.write_text(json.dumps(bundle, indent=1) + "\n", encoding="utf-8")
print(f"wrote {out} ({len(nodes)} nodes, {len(assets)} assets)")
