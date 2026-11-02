# Process: what we have built and what remains

The goal is a product people choose over existing Figma-to-code tools. Any Figma design, simple or complex, single page or multi-page, should become code that:

1. **matches the design pixel by pixel** at the design width,
2. **is clean and maintainable**, written the way a front-end developer would write it,
3. **works on every screen**, from phone to tablet to laptop to wide monitor,
4. **reuses** headers, footers and repeated sections instead of duplicating them,
5. later, **becomes a WordPress theme** (PHP + ACF) from the same HTML.

Every conversion proves itself with two scores: **fidelity** (does it match Figma?) and **code quality** (is the code clean, and does it hold up at other screen sizes?).

---

## Current status: v0.5

| Real design | Layers | Fidelity at design width | Elements in place | Code quality |
| --- | --- | --- | --- | --- |
| Cleaning landing page | 179 | **99.9** (correct on the first render, 0 corrections) | 88 / 88 | **94** Excellent |
| Moving services page | ~500 | 69\* | 76 / 83 | **92** Excellent |
| Peachy Pup Bakery | 10,949 | 69\* (section similarity 98 / 95 / 95 / 100%) | 91 / 94 | **84** Good |
| Two-page test site | 2 × 179 | **99.1** | shared header, footer and 2 sections | **89** Good |

\* Capped at 69 because the paid font **TT Hoves** is not installed. Every missed element on these two pages is TT Hoves text. Once the font files are uploaded, the score is expected to be in the high 90s.

- **Speed:** Peachy Pup (10,949 layers) converts and checks in about 15 seconds. Before v0.4 the check never finished.
- **Tests:** 86 engine tests and 9 plugin tests, plus lint and format checks in CI.

---

## What we did, version by version

### Spike 1: extraction (plugin + Engine intake)

- The plugin serializes the selected frames into a bundle (`schemas/bundle.schema.json`): layers, styles, Auto Layout, text segments, variables, components, fonts and assets.
- The Engine validates the bundle and stores the assets. The design check reports Auto Layout coverage, the sections found and tips.
- Fixed:
  - Figma rejects IP addresses in `devAllowedDomains`, so the plugin uses `localhost`
  - an Engine root route was added

### v0.2: end-to-end pipeline

- **Design IR:** an element tree with exact boxes and sections detected from background bands.
- **Layout inference:** containment nesting, background layers and rows/columns, with absolute positioning as the fallback.
- **Generator:** semantic HTML and CSS, WebP images, and self-hosted or Google fonts.
- **Fidelity checker:** the page is rendered in Chromium, every element is measured, and a score is computed (geometry, typography, colour, assets, per-section similarity, responsive), with an HTML report.
- One-click **Convert to code** in the plugin.

### v0.2.1: blueprint fixes from the second real design (9/78 → 78/78 elements)

- Section boundaries are only cut where no content crosses them, and groups that span two sections are dissolved.
- Text boxes were locked to their Figma size (replaced in v0.5 by clean fluid text).
- SVGs with shadows are drawn at their render bounds.
- **Fonts:** the checker verifies fonts really loaded and caps the score if not; font files can be uploaded from the plugin.

### v0.3: every design, every page

- **Self-correcting loop:** render → measure → CSS corrections (parents first) → repeat, up to 6 rounds. A deliberately broken page went from 67.5 to 99.9.
- **Multi-page sites:** select several frames; page names come from frame names.
- **Shared components:**
  - identical sections across pages are generated once (`site-header`, `site-footer`, `shared-*`)
  - a header row is split out of the hero when it repeats
  - images are de-duplicated by content
- **Large files:**
  - illustrations made of vectors are exported as one SVG, or one PNG above 400 pieces
  - a layout recursion bug was fixed
  - conversion runs as a background job with progress (no more "Failed to fetch")

### v0.4: any screen width, exact layer order, fast checks on huge files

- **The "558 seconds" check:** the cause was lazy-loaded images that never load in a headless browser, not the layer count. Images now load immediately, waits are capped at 20 seconds, and correction rounds stop after 2 minutes.
- **Width:** content was measured from the frame's left edge, so it drifted off-centre on smaller screens. It is now centred, with backgrounds that stretch and scaling below the content width (replaced in v0.5 by a real container).
- **Paint order:** nesting only happens when it keeps Figma's stacking order, and overlapping elements get a z-index matching that order.
- **Rendering fixes:**
  - masks: image masks become CSS `mask-image`, shape masks become `clip-path`, and mask groups are exported as a PNG
  - cropped images use exactly the window chosen in Figma
  - linear and radial gradients map exactly
  - transparency is no longer applied twice to exported layers
- **Smaller exports:** the plugin no longer sends the inner layers of exported illustrations (the Peachy Pup bundle was 14 MB).

### Disk housekeeping

- The server crashed when the Mac's disk was full. Now old conversions are removed automatically, free space is checked first, and a full disk gives a clear "Not enough disk space" message (HTTP 507).
- The download zip is built on demand, and the unused 2× reference image is no longer uploaded.

### v0.5: clean structure (code a developer would write)

- **One centred container,** `.container { width: min(100% - 2 * var(--gutter), var(--container)); margin-inline: auto }`. Its width comes from the real content column.
- **Background layers behave like Figma constraints:** full-width ones stretch, layers touching an edge stay on that edge, and the rest stay put relative to the centre. They sit behind the content with `z-index: -1`.
- **No fixed sizes on text.** Paragraphs keep Figma's line length as a `max-width`, line breaks become `<br>`, and Figma's "Auto" line height is written as px. Images and boxes are fluid and keep their proportions. All positions are whole pixels.
- **Repeated cards become a CSS grid,** `repeat(N, minmax(0, card width))`, with 2 columns on tablet and 1 on phone. Rows use `space-between` or `margin-left: auto` where items are pinned to the edges.
- **Buttons share one `.btn` component** that sizes itself to its label; where a button sits is a separate class.
- **Design tokens:** container, gutter, colours, fonts, spacing, radii, shadows and font sizes are CSS variables. The main text style is set once on `body`.
- **Semantic HTML:**
  - sections get ids
  - a row of menu labels becomes `<nav><ul><li><a>`, and a thin top band holding it becomes `<header>`
  - menu links scroll to the section or heading of the same name, and CTA buttons go to the contact section
  - smooth scrolling, a proper heading order, and alt text taken from the nearest heading
- **Code quality score** shown in the plugin: structure plus responsive health at 390, 768 and 1280px.

Technical details for each version are in [docs/pipeline-v0.2.md](docs/pipeline-v0.2.md).

---

## What remains

### v0.6: really responsive (next)

- [ ] Use Figma's **Auto Layout sizing** (fill / hug / fixed) and **constraints** as the source of truth whenever the file has them.
- [ ] **Merge mobile and tablet frames:** match elements across frames (by name, text, image and order) and write only the differences as media queries.
- [ ] **Better phone layout without a mobile frame:**
  - background layers follow their section's height
  - sections built from overlapping layers reflow instead of being scaled down
  - font sizes scale smoothly (`clamp()`)
- [ ] **Forms:** input fields drawn as rectangles become `<input>` / `<textarea>` / `<button>`.
- [ ] **Clean export:** remove the `data-figma-id` attributes and merge the corrections into the real classes in the downloaded zip.
- [ ] Remove the remaining scaling fallback for overlapping compositions.

### v0.7: navigation and interactions

- [ ] Figma prototype links: "navigate to" becomes a page link, "scroll to" becomes an anchor, and overlays become pop-ups.
- [ ] A sticky header when the header is set to fixed position in Figma.
- [ ] **Hover, focus and pressed states** from component variants (State = Hover…) and hover interactions, with transitions.
- [ ] A hamburger menu on phones when the design has one or the menu doesn't fit.

### v0.8: motion and complex CSS

- [ ] Smart Animate becomes CSS transitions, plus scroll-in animations (tiny JavaScript that respects "reduce motion").
- [ ] Tabs, accordions and carousels from interactive components.
- [ ] Full effects coverage: multiple and inner shadows, blurs, blend modes, gradient text, every stroke type, rotation and image filters. Plus a clear rule for when an effect is better exported as an image.
- [ ] Visual auto-fix: compare colours and crops per element, not only positions.

### v1.5 and later

- [ ] **WordPress theme export** (PHP templates + ACF fields) from the same HTML.
- [ ] A Tailwind CSS output option, a code preview inside the plugin, and regenerating one section at a time.
- [ ] Hosted Engine (accounts, storage, billing) so users don't run it locally.

### Testing still needed

- [ ] A file built with **Auto Layout and components** (all real files so far were flat).
- [ ] A file with **mobile frames**.
- [ ] A file with **hover variants and prototype links**.
- [ ] A **single-page site with a header menu**.
- [ ] A regression set of 15–20 real community files with expected scores, checked in CI on every change.

---

## Known limits right now

- Without the real font files, text measures slightly differently and the score is capped (this is intentional).
- Phones get an inferred stacked layout. Background images keep their desktop height, and sections built from overlapping layers don't rearrange yet.
- Between 768px and the content width, sections built from overlapping layers scale down like browser zoom instead of reflowing.
- Hover states, animations, forms and the WordPress export are not generated yet.
- The generated HTML still carries `data-figma-id` attributes, which the design check and the corrections rely on.
