# Conversion pipeline (v0.2)

**Result on the first real file** (12 Free Landing Pages · "Cleaning", flat design, desktop only, 1903×3383): **99.8 / 100, Verified**. 83/83 elements within tolerance, 40/40 text styles match, all colours and images correct, sections 94–100% visually similar, no horizontal scroll at 1440–390 px. Conversion takes about 4 seconds.

## Stages

| Stage | File | What it does |
| --- | --- | --- |
| Design IR | `app/pipeline/ir.py` | Builds an element tree from absolute boxes (relative to the root frame). Groups are dissolved; Auto Layout frames keep their structure; frames that draw nothing are dissolved. Sections come from top-level frames, or from full-width background bands on flat designs. Full-width backgrounds become section background layers. |
| Layout inference | `app/pipeline/layout.py` | Per container: (1) two equal shapes offset by ≤10 px become one shape plus an offset outline ("hard shadow"); (2) a shape/image that fully contains later-painted elements becomes their box (cards, buttons); (3) graphics that overlap later elements become absolute background layers; (4) XY-cut splits the rest into rows and columns at the largest gaps, recursively. True overlaps fall back to absolute positioning. |
| Generator | `app/pipeline/generate.py` | Semantic HTML (`section`, `footer`, `h1`–`h3`, `p`, `a` for buttons, `img`), `data-figma-id` on every element, BEM-style classes deduplicated by style, flex layout with `gap` when spacing is uniform, colour and font tokens as CSS variables, self-hosted fonts or Google Fonts, images converted to WebP at 2× display size, small SVG masks inlined, frosted shapes as masked `backdrop-filter`. No `!important`. |
| Fidelity | `app/pipeline/fidelity.py` | Renders in Chromium at the frame width, measures every element against the design, checks font family/size/weight/line count, colours and image loading, compares each section with SSIM, checks horizontal scroll at 1440/1280/1024/768/390 px. Score per SRS §16; sections under 95% similarity raise an issue. |
| Orchestration | `app/pipeline/convert.py` | Runs all stages, zips the output, writes `conversion.json`. The fidelity step is optional and never blocks code generation. |

## Lessons from the first file

- Flat designs work: sections, cards, buttons and grids were recovered without any Auto Layout.
- Group children report x/y relative to the nearest frame; only absolute boxes are reliable.
- CSS borders add to the box while Figma strokes sit inside it: padding must absorb the border width.
- Mixed text styles in one layer must reset properties a span does not have (letter-spacing leaked from one line to another).
- Browsers refuse CSS masks loaded from `file://`; inline small SVGs as data URIs.
- A high score can hide one missing element: section-level visual similarity is what caught the missing frosted shape.

## v0.2.1: blueprint fixes from the second real file

Second file (services landing page, 1903×3754, 24 SVG graphics, TT Hoves + Poppins) scored 68.1 on the user's Mac with only 9/78 elements in place. Causes and fixes:

| Problem | Fix |
| --- | --- |
| A full-width background started half-way down a card row; the section was split there, cards were clipped and everything below moved down about 190 px | Section edges are top/bottom edges of full-width shapes, used only when **no content crosses them**; bottom edges hidden under another background are ignored; groups that span two sections are dissolved so each child lands where it really is; empty bands merge upwards |
| Full-width shapes starting mid-section stayed in the flow | Every full-width shape, image or graphic in a section is a background layer, at its exact position |
| Sections clipped their content (`overflow: hidden`) | No clipping: layers may extend past a section, later sections paint over them, as in Figma |
| One taller line of text pushes the whole page | **Blueprint lock**: every text box keeps its Figma width and height on desktop (`.t-lock`), released below 768 px |
| SVGs with shadows were squeezed into the layer box (Figma exports at render bounds) | SVGs are drawn at their render bounds with negative margins, so they occupy exactly the layer box |
| TT Hoves (not a Google font) silently fell back to another font while Typography said 100 | The checker now confirms each font actually **loaded**; a missing font is a critical issue (score capped at 69). The plugin shows a "Missing fonts" panel to upload the files and convert again. Font files are found whatever their naming (`TTHoves-Bold.otf`, Fontsource names, variable fonts) |

Result after fixes: **78/78 elements in place, 97.3% visual similarity**; the remaining gap is the missing TT Hoves font. The first file still scores 99.8.

## Known limits (next steps)

1. **Mobile frames:** the generator uses the desktop frame; a mobile frame is read but not yet merged into media queries (desktop/mobile element matching, FR-EXT-14).
2. **Inferred mobile layout** is basic (rows stack below 768 px). Sections that fall back to absolute positioning do not adapt.
3. **Interactions and motion:** hover variants, Smart Animate and the Motion panel are not generated yet (FR-MOT).
4. **Auto-fix loop:** issues are reported but not yet patched automatically (FR-FID-17 to 24).
5. **Semantics:** form fields drawn as rectangles stay `div`s; heading levels are inferred from size.
6. **Tested on two flat files so far.** Next test files: one with Auto Layout and components, one with a mobile frame.
