# Process: what we have built and what remains

The goal is a product people choose over existing Figma-to-code tools. Any Figma design, simple or complex, single page or multi-page, should become code that:

1. **matches the design pixel by pixel** at the design width,
2. **is clean and maintainable**, written the way a front-end developer would write it,
3. **works on every screen**, from phone to tablet to laptop to wide monitor,
4. **reuses** headers, footers and repeated sections instead of duplicating them,
5. can also be downloaded as a **WordPress theme** (PHP templates + ACF or ACF Pro) built from the same HTML.

Every conversion proves itself with two scores: **fidelity** (does it match Figma?) and **code quality** (is the code clean, and does it hold up at other screen sizes?).

---

## Current status: v0.7

| Real design | Layers | Fidelity at design width | Elements in place | Code quality |
| --- | --- | --- | --- | --- |
| Cleaning landing page | 179 | **99.9** (correct on the first render, 0 corrections) | 88 / 88 | **94** Excellent |
| Moving services page | ~500 | 69\* | 76 / 83 | **95** Excellent |
| Peachy Pup Bakery | 10,949 | 69\* (section similarity 98 / 95 / 95 / 100%) | 91 / 94 | **87.5** Good |
| Two-page test site | 2 × 179 | **99.1** | shared header, footer and 2 sections | **91** Excellent |
| Sample (Auto Layout + mobile frame) | 34 | desktop 12 / 15 · **phone 11 / 15** (checked against the mobile frame)† | | **100** Excellent |

\* Capped at 69 because the paid font **TT Hoves** is not installed. Every missed element on these two pages is TT Hoves text. Once the font files are uploaded, the score is expected to be in the high 90s.

† The sample's text boxes are synthetic (widths that no real font produces); every miss on desktop and phone is one of those boxes.

- **Speed:** Peachy Pup (10,949 layers) converts and checks in about 15 seconds. Before v0.4 the check never finished.
- **WordPress theme:** on all four real designs, in both ACF modes, the theme renders the same markup as the checked HTML and its screenshots are pixel-identical.
- **Tests:** 102 engine tests and 9 plugin tests, plus lint and format checks in CI.

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

### v0.6: really responsive, clean download

- **Clean export:** the downloaded zip has no `data-figma-id`, `data-section` or other instruments. Corrections are folded into the real classes (or a small `--tuned-N` modifier when only some elements of a class need one) under one "Desktop fine-tuning" block, and a README.txt is included. The live preview keeps the instruments for the check.
- **Auto Layout as the source of truth:** frames with Auto Layout are written from their own settings: gap, padding, `justify-content` / `align-items` from the alignment, wrap with row and column gaps, children set to fill (`flex: 1 1 0` / `align-self: stretch`), hug or fixed, absolute children, and fixed heights as `min-height`. The Auto Layout sample scores 100 on code quality.
- **Mobile and tablet frames → media queries:** elements are matched between frames (same text, same image bytes, same layer name, in reading order). Only the differences are written, per element, in `@media (max-width: 767px)` (mobile) or `(max-width: 1023px)` (tablet):
  - font size, line height and line length
  - rows turned into columns, gaps, `order`, and grid columns
  - section padding and spacing, box padding, and the phone gutter
  - elements missing from the mobile frame are hidden
- **Phone check:** the page is rendered at the mobile frame's width and every matched element is measured against the mobile frame. Its score is shown next to the desktop score ("Other screen sizes" in the plugin).
- **Fluid type and spacing:** headings from 32px and spacing from 64px use `clamp()`. They are exact at the design width and shrink smoothly to a phone size; one-line headings are sized to fit a phone. Line height follows the same curve, so the desktop pixel check is unchanged.
- **Phones without a mobile frame:**
  - background layers that cover their section follow its height (`inset: 0`), big ones cover the section, and small decorations are hidden
  - sections built from overlapping layers become a vertical flow (no absolute positioning, smaller paddings, decorations hidden)
- **Forms:** two or more equal drawn inputs in a column plus a button become `<form>`, `<input>` / `<textarea>` (type from the placeholder: email, tel, url…) and `<button type="submit">`. They get `font: inherit`, the placeholder style and aria-labels.
- **Fixes:** containers inside composition sections no longer subtract the gutter at full width (this caused uniform -24px corrections), and identical icons in different frames are matched by content hash.

### v0.7: WordPress theme export (ACF and ACF Pro)

- **Two downloads from one conversion:** in the plugin, choose **Clean HTML and CSS**, **WordPress theme · ACF** or **WordPress theme · ACF Pro**. The theme is built from the same clean HTML that passed the design check, so it looks exactly like it.
- **A classic PHP theme, the WordPress way:**
  - `header.php` / `footer.php` hold the shared site header and footer
  - `front-page.php` for the first page and `templates/page-<slug>.php` (with a Template Name) for each other page
  - each section is a template part in `template-parts/sections/<page>/`
  - sections shared by several pages are one file in `template-parts/components/`, so they are edited once
  - `page.php` / `index.php` for any other page or post, plus `style.css`, `functions.php`, `screenshot.png` and `readme.txt`
- **Everything is editable, and nothing breaks without ACF:**
  - every text, image, button / link and repeated card is a field
  - each field shows the design's content until it is filled in, so the site looks like the design right after activation, even before ACF is installed
- **ACF (free):** one field group per page with a tab per section, numbered fields for repeated cards, and shared sections edited once on the front page.
- **ACF Pro:**
  - **Page sections** (Flexible Content): every section of the design is a layout that can be added, removed or reordered on any page
  - repeated cards become **Repeaters**
  - shared sections are edited in a **Site settings** options page
- **Set up on activation:** the design's pages are created with their templates, the first one becomes the front page, the Pro section rows are filled in, and the "Main menu" is built from the design's links (`wp_nav_menu`, with the design's links as the fallback).
- **WordPress plumbing:**
  - field groups as ACF local JSON (`acf-json/`), so they can be synced and versioned
  - escaping everywhere (`esc_html`, `esc_url`, `wp_kses_post`)
  - images through `wp_get_attachment_image`
  - forms post to `admin-post.php` with a nonce and send the message with `wp_mail`
  - fonts and styles are enqueued properly
- **Better landmarks in both outputs:** the shared site header and footer now sit outside `<main>` (`<header>`, `<main>`, `<footer>`). The top band shared by every page is a `<header>` even without a menu.
- **One content column for the whole site:** pages of the same width share the widest page's container, so a shared section has identical markup on every page and can be one template part.
- **Plugin:** a **Select all frames on this page** button (frames inside Figma sections too), so a 10-page site is one click.
- **Checked without a WordPress install:**
  - every generated PHP file passes `php -l`
  - a small stand-in for the WordPress and ACF functions renders each template
  - the result matches the clean HTML tag for tag (with ACF missing, with empty fields and with edited fields)
  - screenshots of both are pixel-identical

Technical details for each version are in [docs/pipeline-v0.2.md](docs/pipeline-v0.2.md).

---

## What remains

### v0.6 follow-ups

- [ ] Test on a **real** file with mobile frames (only the sample has been checked so far), and tune the matching on it.
- [ ] Elements that exist **only** in the mobile frame (for example a hamburger icon) are counted, not generated yet.
- [ ] **Phones for overlapping compositions** (like the Peachy Pup hero): text drawn on top of a panel as a separate layer loses its panel when the layers stack, so a mobile frame is needed for a designed result.
- [ ] Between 768px and the content width, composition sections still scale down like browser zoom instead of reflowing.
- [ ] Corrections are applied on desktop only; corrections measured against the mobile frame are next.

### WordPress follow-ups

- [ ] Install the theme on a **real WordPress** site (with ACF and with ACF Pro) and click through the editor; so far it is checked with `php -l` and a stand-in for the WordPress functions.
- [ ] Gutenberg blocks (ACF blocks or native block patterns) as a third option, and a block theme (`theme.json`) for full-site editing.
- [ ] Footer menu, social links and copyright as Site settings; SEO title / description per page.
- [ ] Blog templates (archive, single post) styled like the design, when the design has them.
- [ ] A contact form plugin option (Contact Form 7 / WPForms markup) next to the built-in handler.

### v0.8: navigation and interactions

- [ ] Figma prototype links: "navigate to" becomes a page link, "scroll to" becomes an anchor, and overlays become pop-ups.
- [ ] A sticky header when the header is set to fixed position in Figma.
- [ ] **Hover, focus and pressed states** from component variants (State = Hover…) and hover interactions, with transitions.
- [ ] A hamburger menu on phones when the design has one or the menu doesn't fit (the first JavaScript in the output, also in the theme).

### v0.9: motion and complex CSS

- [ ] Smart Animate becomes CSS transitions, plus scroll-in animations (tiny JavaScript that respects "reduce motion").
- [ ] Tabs, accordions and carousels from interactive components.
- [ ] Full effects coverage: multiple and inner shadows, blurs, blend modes, gradient text, every stroke type, rotation and image filters. Plus a clear rule for when an effect is better exported as an image.
- [ ] Visual auto-fix: compare colours and crops per element, not only positions.

### v1.5 and later

- [ ] A Tailwind CSS output option, a code preview inside the plugin, and regenerating one section at a time.
- [ ] Hosted Engine (accounts, storage, billing) so users don't run it locally.

### Testing still needed

- [ ] A file built with **Auto Layout and components** (all real files so far were flat).
- [ ] A real file with **mobile frames** (the merge is built and tested on the sample only).
- [ ] A file with **hover variants and prototype links**.
- [ ] A **single-page site with a header menu**.
- [ ] A regression set of 15–20 real community files with expected scores, checked in CI on every change.

---

## Known limits right now

- Without the real font files, text measures slightly differently and the score is capped (this is intentional).
- Phones without a mobile frame get an inferred layout. Normal sections reflow well; sections built from overlapping layers become a plain vertical flow that is readable but not designed.
- Hover states and animations are not generated yet.
- The WordPress theme has not been run on a real WordPress install yet (see WordPress follow-ups).
- The live preview carries `data-figma-id` attributes for the check; the downloaded code does not.
