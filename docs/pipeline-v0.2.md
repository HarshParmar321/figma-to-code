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

## v0.3: every design, every page

Goal: accuracy that holds on any design, not tuning per file; multi-page sites with reusable sections.

| Capability | How it works |
| --- | --- |
| **Self-correcting loop** | After generating, each page is rendered and every element (and every section) is measured against the design. Elements outside tolerance get a correction, parents before children: sections get their exact height, elements are moved with CSS `translate` (moves visually, never pushes siblings) and non-text elements get their exact size. Re-render, re-measure, up to 6 rounds. Corrections live in `css/corrections.css`, labelled, desktop widths only. Test: a page with 12 elements deliberately pushed 6–40 px off went from 67.5 to 99.9 (88/88 elements, 99.2% visual similarity) in 4 rounds. |
| **Multi-page sites** | Select one desktop frame per page (and mobile frames). Frames are grouped by page name ("Home / Desktop" + "Home - Mobile" = "Home"). Output: `index.html` + `about.html`… sharing one stylesheet, images and fonts. Site report lists every page's score. |
| **Shared components** | Sections that are identical on two or more pages (compared by content, positions and image bytes, not layer ids) are generated once: `site-header`, `site-footer`, `shared-<name>`. A header row inside the hero band (logo + nav) is split out as `site-header` when it repeats. Each component is saved in `components/<name>.html`; its classes are shared, so the CSS exists once. Test: 2-page site → site-header, site-footer, About Us and Testimonials shared, identical markup on both pages. |
| **Image de-duplication** | Identical files (same bytes) become one file used everywhere (20 assets → 7 files in the 2-page test). |
| **Illustrations** | A container made only of vector shapes is exported as one SVG at any size (previously only icons ≤128 px), or as one 2× PNG above 400 vector pieces. The Peachy Pup file had 4,898 vectors exported one by one plus 2,542 failed exports. |
| **Background jobs** | `POST /convert` returns immediately; the plugin polls `/convert/status` and shows live steps. Long conversions no longer end in "Failed to fetch". |
| Bugs fixed | Layout recursion on touching items (Peachy Pup crash); frame sections (Auto Layout files) stacked their children at x=0; sections had two ids; gaps between frame sections were lost. The untouched sample page went from 0/15 to 15/15 elements on the first render. |

## v0.4: any screen width, exact layer order, fast checks on huge files

Found on the Peachy Pup file (10,949 layers) and the services page viewed on a laptop.

| Problem | Cause | Fix |
| --- | --- | --- |
| Check ran 10+ minutes ("Checking … 558 s") | Not the layer count: images below the first 900 px are `loading="lazy"`; the headless check waited forever for them to load. | The checker switches lazy images to eager, waits at most 20 s for fonts/images, and correction rounds stop after a 120 s budget. Peachy Pup now converts **and** checks in **~11 s** (94 HTML elements measured; the 10,836 layers inside illustrations are exported as single images and never measured one by one). |
| Website narrower/wider than the design looked wrong | Every position was measured from the left edge of the 1903 px frame, so on a 1440 px laptop the content sat off-centre and was cut on the right. | Each section spans the screen (backgrounds stretch) and its content sits on a stage of the design width, **centred**. Below the content width (e.g. 1248 px for Peachy Pup) the layout scales down in steps, so nothing is ever cut; below 768 px the mobile layout takes over. Solid panels touching the frame edge keep reaching the screen edge on wide monitors. At exactly the design width nothing changes, so fidelity is unaffected. |
| Layers in the wrong order (bread hidden behind a pattern, glow over the title, badge cut) | Layout inference moved elements into boxes even when another layer painted between them; background layers were always put behind everything. | Elements are only nested when Figma's paint order is preserved; ellipses/vectors never become containers; every element that overlaps another (outside its own box) gets `z-index` = its Figma paint order, so the browser stacks exactly like Figma whatever the DOM structure. |
| Masks ignored (curved "Lorem ipsum" text, clipped photos) | Mask layers were drawn as normal images. | The plugin exports mask groups without text as one 2× PNG. Otherwise the Engine applies the mask: image masks → CSS `mask-image`, rectangles → `clip-path: inset(… round r)`, ellipses → `clip-path: ellipse()`. |
| Cropped photos showed the whole image | `CROP` image fills ignored `imageTransform`. | The exact crop window is cut out when the image is written (`<name>-c<id>.webp`). |
| Radial/linear gradients off | Radial was a corner-to-corner circle; linear ignored handle positions. | Gradients map Figma's handles exactly (ellipse size and centre; angle and stop positions). |
| Patterns too faint | Figma bakes a layer's opacity into its exported PNG/SVG; the CSS applied it a second time. | Opacity is not re-applied to exported layers. |
| 14 MB bundle for one page | The inner layers of exported illustrations were sent too. | The plugin sends only their count (`exportedLayers`); the design check still reports the full layer count. |

Results: Peachy Pup 94/94 elements in place, 98% / 97% / 97% / 100% section similarity (score capped at 69 only because the paid font TT Hoves is not installed); cleaning page 99.8; services page 83/83 (TT Hoves cap).

## v0.5: clean structure (code a developer would write)

Before: every element copied its exact Figma box (fixed width and height on all text, margins measured from the frame edge, a design-width stage scaled with zoom). Pixel-perfect at one width, not maintainable, poor on other screens.

| Area | Now |
| --- | --- |
| **Content column** | One shared `.container { width: min(100% - 2 * var(--gutter), var(--container)); margin-inline: auto }`. Its width is the narrowest centred column that holds every text, button, flow image and icon (e.g. 1200px). Sections only add `padding-block`. |
| **Backgrounds** | Section layers are anchored like Figma constraints: full-width ones stretch (`left: 0; width: 100%`), panels touching an edge stay on it (`width: calc(50% + 262px)`), others stay put relative to the centre. Background layers use `z-index: -1` inside their isolated section, so content needs no z-index. |
| **Fluid sizes** | Text: no width or height; wrapping text keeps Figma's line length as `max-width`; short hugging labels `nowrap`; Figma line breaks are `<br>`; "Auto" line height is written as px. Images: `width: 100%; max-width: Wpx; height: auto; aspect-ratio`. Boxes in rows: `flex: 0 1 Wpx`. Only icons, decorations and overlapping layers keep fixed px. Geometry is snapped to whole pixels. |
| **Rows and grids** | Equal, evenly spaced items become `display: grid; grid-template-columns: repeat(N, minmax(0, Wpx))` with `.l-grid-N` (2 columns below 1024px, 1 below 768px). Two items at both ends use `justify-content: space-between`; an item pinned to the right edge uses `margin-left: auto`; tight groups inside a row (menus, icon + label) stay a group. |
| **Components** | Buttons are a global `.btn` (+ `.btn__label`): inline-flex, centred, padding from the design, `min-width` only when needed. Where a button sits is a separate section class (`.hero__action`). Identical declarations share one class. |
| **Tokens** | `--container`, `--gutter`, colours, font families, spacing (`--space-N`, values used 3+ times), radii, shadows and font sizes. |
| **Typography** | The most common paragraph style is written once on `body`; text classes only declare what differs. Headings are reset (`font-size/weight: inherit`) so the h1–h6 choice is semantic only. |
| **Semantics** | Sections get ids. A row of 2–8 short labels becomes `<nav><ul role="list"><li><a>`; a thin top band holding it becomes `<header>`. Links whose label names a section or heading point to it (`href="#about-us"`), CTA buttons ("Get in touch", "I need help", "Book a call") point to the contact section, menu items matching a page name link to that page. Smooth scrolling and `scroll-margin-top`. Heading levels follow document order (no skipped levels; a logo above the h1 is a paragraph). Image alt text comes from the layer name, or the nearest heading when the name is generic ("Rectangle 250"). |
| **Paint order** | Overlapping elements get the smallest z-index that keeps Figma's order (levels 1, 2, 3…), so identical items share classes. |
| **Compositions** | Sections whose layers overlap across the full width keep the design-width stage; only those sections scale down (`[data-fit] { zoom }`) between 768px and the content width. |
| **Code quality score** | New, next to fidelity. Structure (60): fixed widths, absolute positioning, headings, alt text. Responsive health (40): rendered at 390, 768 and 1280px — sideways scrolling, new text overlaps (overlaps already in the design are ignored), text off screen. Shown in the plugin. |

Results: cleaning page 99.9 fidelity with **0 corrections on the first render**, code quality 94 (Excellent); services page 83 elements (TT Hoves cap), quality 92; Peachy Pup 91/94 (TT Hoves), quality 84; 2-page site 99.1, quality 89.

## v0.6: really responsive, clean download

| Area | How it works |
| --- | --- |
| **Clean export** (`export.py`) | The zip is built on download from the preview: `data-*` instruments removed, `corrections.css` folded into the classes (a `--tuned-N` modifier when only some users of a class are corrected), README.txt added. |
| **Auto Layout** (`auto_flow_css`) | Gap (`itemSpacing`), padding, `primaryAxisAlignItems` → `justify-content`, `counterAxisAlignItems` → `align-items`, wrap → `flex-wrap` + `row-gap`/`column-gap`, child FILL → `flex: 1 1 0` / `align-self: stretch`, HUG → no size, FIXED → max-width / flex-basis, ABSOLUTE children, fixed height → `min-height`. |
| **Breakpoint frames** (`breakpoints.py`) | Match desktop ↔ mobile elements (text, image hash, layer name; i-th occurrence ↔ i-th), boxes by their contents, sections by votes. `derive_css` writes per-element differences into the element's class under `@media (max-width: 767px)` (tablet: 1023px): text size / line height / max-width / align, hidden elements, rows → columns with gaps and `order`, grid columns, paddings (exact Auto Layout padding when the frame has it), section spacing and min-height, phone gutter on `:root`. Mobile expectations re-target desktop element ids at mobile boxes; the conversion runs a second check at the mobile width. |
| **Fluid scale** (`fluid_value`) | `clamp(phone, a + b·vw, design)`: exact at the design width, the phone value at 390px. Font sizes ≥ 32px (one-line headings sized to fit 358px), line heights on the same curve, paddings / gaps / margins ≥ 64px. |
| **Phones without frames** | Section layers covering the section use `inset: 0` (follow its height); big layers cover the section on phones, small decorations and round glows are hidden, small photos join the flow. Inside compositions: no absolute positioning, decorations hidden, large paddings and margins reduced, stage becomes a flex column. |
| **Forms** | `detect_forms`: ≥ 2 drawn inputs with the same left edge and width (≤ 1 short text each) + a button below (or the one differently styled box) → `<form>`, `<input type>` / `<textarea>`, `<button type="submit">`; loose fields in a section column are wrapped in their own `<form>`. |

Results: desktop fidelity unchanged on every real design (cleaning 99.9 with 0 corrections; Peachy Pup and services capped by the TT Hoves font); Auto Layout sample: quality 100, phone layout 11/15 elements against its mobile frame (the misses are the fixture's synthetic text boxes). 97 engine tests.

## Known limits (next steps)

1. **Mobile frames:** read and paired with their page, but not yet merged into media queries (desktop/mobile element matching, FR-EXT-14). This is the next major step.
2. **Inferred mobile layout** is basic (rows stack, grids go to one column below 768 px). Background layers keep their desktop height and composition sections (overlapping layers) do not reflow on phones yet (v0.6).
3. **Interactions and motion:** hover variants, Smart Animate and the Motion panel are not generated yet (FR-MOT).
4. **Visual (non-geometry) fixes:** the loop corrects positions and sizes; wrong colours, effects or fonts are reported but not yet auto-fixed.
5. **Semantics:** form fields drawn as rectangles stay `div`s; heading levels are inferred from size.
6. **Real files tested:** two flat landing pages and a large illustrated page (Peachy Pup). Still needed: a file built with Auto Layout and components, and one with a mobile frame.
7. **Forms:** input fields drawn as rectangles are still `div`s (detection planned).
8. **Between 768 px and the content width** the page scales like browser zoom (stepped every 32 px) rather than reflowing; a supplied tablet frame will replace this once breakpoint merging lands.
