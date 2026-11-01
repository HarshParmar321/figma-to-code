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

## Known limits (next steps)

1. **Mobile frames:** the generator uses the desktop frame; a mobile frame is read but not yet merged into media queries (desktop/mobile element matching, FR-EXT-14).
2. **Inferred mobile layout** is basic (rows stack below 768 px). Sections that fall back to absolute positioning do not adapt.
3. **Interactions and motion:** hover variants, Smart Animate and the Motion panel are not generated yet (FR-MOT).
4. **Auto-fix loop:** issues are reported but not yet patched automatically (FR-FID-17 to 24).
5. **Semantics:** form fields drawn as rectangles stay `div`s; heading levels are inferred from size.
6. **Tested on one flat file so far.** Next test files: one with Auto Layout and components, one with a mobile frame.
