# Figma to Code

A Figma plugin plus a backend **Engine** that turns a Figma design into clean, responsive HTML/CSS that is **checked against the design pixel by pixel**, or into a ready-to-install **WordPress theme** (PHP templates + ACF or ACF Pro fields) built from the same verified HTML.

Most Figma-to-code tools give you either a picture-perfect page that no developer can maintain, or tidy code that no longer looks like the design. This project does both and proves it. Every conversion is rendered in a real browser, measured against the Figma frame, corrected automatically, and then given two scores:

- **Fidelity** (0–100): does it match Figma? It checks positions and sizes, typography, colours, images and visual similarity per section.
- **Code quality** (0–100): is it code a front-end developer would ship? It looks at structure, headings, alt text and fixed sizes, then renders the page at 390, 768 and 1280px to catch sideways scrolling, overlapping text and text pushed off screen.

> **Status: v0.8.** See [PROCESS.md](PROCESS.md) for what has been built, the results on real designs and what remains.

## What it does today

- **One click in Figma:** select one desktop frame per page (or **All on this page**, or **Pick from all pages…** for frames spread over several Figma pages) and click **Convert to code**. A phone-only or tablet-only design works too. The plugin shows each step as it happens.
- **Multi-page sites:** you get `index.html`, `about.html` and so on, sharing one stylesheet, the images and the fonts. Menu items link to the matching page.
- **Reusable sections:** a header, footer or section that repeats across pages is generated once, in `components/`, with the same markup and classes on every page.
- **Clean structure:**
  - one centred `.container`, fluid widths, and CSS grid for repeated cards
  - a shared `.btn` component and design tokens (CSS variables)
  - semantic HTML: `header`, `nav > ul > li > a`, sections with ids, a proper heading order, and alt text
- **Single-page navigation:** menu links scroll smoothly to the section of the same name, and buttons like "Contact us" or "Get in touch" go to the contact section.
- **Prototype interactions become real behaviour:**
  - "Navigate to" → page links, "Scroll to" → section anchors, "Open link" → URL (new tab when set), "Back" → history back
  - hover, press and focus states from component variants (wired with "While hovering / pressing", or simply named State=Hover / Pressed / Focus), written as CSS with Figma's own timing (durations, easing curves and springs)
  - click toggles (accordions, switches: content that only exists in the Open variant is added and revealed), "After delay" variant chains → auto-playing sliders
  - "Open overlay" → `<dialog>` pop-ups and modals (position, backdrop, close on outside click, Close buttons, Move in / Dissolve animations); a phone menu icon from the mobile frame opens its menu overlay
  - "Fix position when scrolling" → sticky header; Smart Animate page navigation → view transitions
  - scroll-in animations set per layer in the plugin (**Motion**), since Figma has none of its own
  - everything respects "reduce motion"; one small `js/site.js`, only when needed
- **Every screen size:**
  - mobile and tablet frames become media queries, and the phone layout is checked against your mobile frame
  - without them, the layout reflows by itself
  - big type and spacing scale smoothly with `clamp()`
  - Figma Auto Layout settings (gap, padding, alignment, fill / hug / fixed) are used as they are
- **Real forms:** drawn input fields become `<form>`, `<input>`, `<textarea>` and `<button type="submit">`.
- **Two downloads from the same conversion:**
  - **Clean HTML and CSS:** no internal attributes, the corrections folded into the real classes, and a README. (Nothing needs JavaScript yet; the menu scrolling is CSS. JavaScript comes with the interactions in v0.8.)
  - **WordPress theme** for **ACF** (free) or **ACF Pro**: every page, section, text, image, button and repeated card is editable, shared sections are edited once, and the theme renders exactly the HTML that passed the design check (see [WordPress theme](#wordpress-theme)).
- **Self-correcting:** elements that land off their Figma position are fixed in `css/corrections.css` (desktop only) over up to 6 rounds.
- **Hard designs:**
  - masks, cropped images and exact gradients
  - layers stacked in exactly Figma's order
  - illustrations with thousands of vectors exported as one image
  - a 10,000-layer page converts and checks in about 15 seconds
- **Fonts:** Google fonts load automatically. For paid fonts the plugin asks for the font files, and until they are uploaded the score is capped, so it never claims a false match.

## Repository layout

```
schemas/            bundle.schema.json: the contract between plugin and Engine (source of truth)
plugin/             Figma plugin (TypeScript): serializer, asset export, one-click Convert UI
engine/             Engine (Python 3.11+, FastAPI)
  app/main.py       HTTP API, background conversion jobs
  app/storage.py    bundle storage, disk housekeeping
  app/pipeline/     ir → layout → generate → fidelity → corrections, orchestrated by convert.py
                    components.py (shared sections), breakpoints.py (mobile/tablet frames),
                    quality.py (code quality score), export.py (clean download),
                    wordpress.py (WordPress theme: PHP templates + ACF / ACF Pro), fonts.py,
                    interactions.py (prototype states, links, overlays, timing → CSS and js/site.js)
  app/spike_report.py   design check (Auto Layout coverage, sections, tips)
  tests/            111 tests (browser tests need Chromium, theme tests need PHP)
fixtures/           sample bundle + generator script
docs/               pipeline notes (every version) and the first spike
PROCESS.md          progress log: done, results, remaining
```

## How a conversion works

1. **Plugin:** it serializes the selected frames (layers, styles, Auto Layout, text, effects, masks and component data) and exports a reference screenshot, the images and the SVG icons. The bundle and the files are uploaded to the Engine.
2. **Design IR:** a tree of elements with exact boxes. Groups are dissolved, masks are attached, and sections are found from full-width background bands.
3. **Layout inference:**
   - shapes that contain content become containers, but only if that keeps Figma's paint order
   - overlapping backgrounds become positioned layers
   - the rest is split into rows and columns
   - a centred content column (the page container) is found
4. **Generate:**
   - semantic HTML with fluid CSS: container, grids, flex rows and columns, `.btn`, tokens and base typography
   - anchors and nav links
   - images converted to WebP and cropped as in Figma; fonts self-hosted when available
5. **Check:** the page is rendered in Chromium at the design width and every element is measured against Figma. The loop writes corrections, re-renders and measures again.
6. **Score:** the fidelity score, the code quality score, and an HTML report with side-by-side and difference images.
7. **Download:** the clean site, or the WordPress theme built from it (on demand, when you click **Download**).

## WordPress theme

The theme is a classic PHP theme generated from the clean HTML, so it looks exactly like the checked page. With the stub harness in `engine/tests/`, the templates render the same markup as the HTML (with ACF missing, with empty fields and with edited fields), and screenshots of both are pixel-identical on every real design tested.

**Install:** Appearance → Themes → Add New → Upload Theme, choose the zip, Activate. Then install **ACF** or **ACF Pro** (the one you downloaded the theme for). On activation the theme creates the design's pages, sets the first page as the front page and builds the "Main menu". The field groups load from `acf-json/`; if ACF offers a **Sync**, click it.

```
<theme>/
  style.css, functions.php, screenshot.png, readme.txt
  header.php, footer.php        <head>, the shared site header / footer (template parts)
  front-page.php                the first page; templates/page-<slug>.php for the others (Template Name)
  page.php, index.php           any other page or post (title + content, inside the same header/footer)
  template-parts/sections/<page>/<section>.php   each section of a page
  template-parts/components/<name>.php           sections shared by several pages (one file, edited once)
  inc/template-tags.php         field helpers (each falls back to the design's content)
  acf-json/                     field groups (ACF local JSON)
  assets/css/main.css, assets/images/, assets/fonts/
```

| | ACF (free) | ACF Pro |
| --- | --- | --- |
| Page content | One field group per page, one tab per section | **Page sections** (Flexible Content): every section of the design is a layout you can add, remove or reorder on any page |
| Repeated cards, team members, steps | Numbered fields (Card 1, Card 2…) | **Repeaters**: add or remove items |
| Header, footer, shared sections | Edited once, on the front page | Edited once, in **Site settings** (options page) |
| Without ACF | Shows the design's content | Same |

Both: texts are text or textarea fields (`esc_html` / `wp_kses_post`), images are image fields (`wp_get_attachment_image`, falling back to the theme's image), buttons and links are link fields (URL, label, target), and the menu uses `wp_nav_menu` (falling back to the design's links). Forms post to `admin-post.php` with a nonce and send the message to the site's admin email.

## Requirements

- Figma desktop app (needed for loading development plugins)
- Node.js 20+ and npm
- Python 3.11+ and [uv](https://docs.astral.sh/uv/) (`curl -LsSf https://astral.sh/uv/install.sh | sh`)

## Run the Engine

```bash
cd engine
uv sync
uv run playwright install chromium     # once (~150 MB): needed for the design check
uv run uvicorn app.main:app --reload --port 8000
```

Check it at http://localhost:8000/health. Each conversion is stored in `engine/data/bundles/<bundle-id>/`:

| Path | What |
| --- | --- |
| `output/` | The generated site: `index.html` (+ one file per page), `css/styles.css`, `css/corrections.css`, `components/`, `images/`, `fonts/` |
| `fidelity/report.html` | Score, breakdown, side-by-side and difference images, issues (multi-page: a site summary plus one folder per page; `mobile/` and `tablet/` subfolders for breakpoint checks) |
| `conversion.json` | Summary of the last conversion: both scores, pages, components, issues |

Only the 8 most recent conversions are kept. The download zip is built when you click **Download code**.

## Build and load the plugin

```bash
cd plugin
npm install
npm run build      # or: npm run watch
```

In the Figma desktop app, open a design file and press **Cmd + /**. Run **Import plugin from manifest** and choose `plugin/manifest.json`, then run **Figma to Code (dev)**.

1. Select one desktop frame per page (Home, About, Contact…), plus a mobile (and tablet) frame per page if you have them, all on the same Figma page. Name them alike ("Home / Desktop", "Home / Mobile") so they are paired.
2. Click **Convert to code** and watch the steps: Read design → Upload → Generate and check (including correction rounds).
3. Review the fidelity score, the phone/tablet scores (when you gave those frames), the code quality score, the pages and the shared components. Then use **Open preview** or **Report**.
4. Under **Download**, choose **Clean HTML and CSS**, **WordPress theme · ACF** or **WordPress theme · ACF Pro**, then click **Download code**.

During development the plugin can only reach `http://localhost:8000` (see `networkAccess` in the manifest).

## Fonts

Free Google fonts load automatically. For any other font (for example a paid font such as TT Hoves), the result shows **Missing fonts**. Pick the font files in the plugin and click **Upload fonts and convert again**, or copy them into `engine/data/fonts/<family-name>/` yourself (.woff2, .woff, .otf or .ttf; variable fonts work too).

## Settings (environment variables)

| Variable | Default | Purpose |
| --- | --- | --- |
| `ENGINE_DATA_DIR` | `engine/data` | Where bundles and outputs are stored |
| `ENGINE_FONT_DIR` | `engine/data/fonts` | Self-hosted fonts, one folder per family |
| `ENGINE_CHROMIUM_PATH` | Playwright's Chromium | Use a specific Chrome/Chromium binary for the check |
| `ENGINE_PUBLIC_URL` | `http://localhost:8000` | Base URL used in links returned to the plugin |
| `ENGINE_KEEP_BUNDLES` | `8` | How many recent conversions are kept |
| `ENGINE_MIN_FREE_MB` | `400` | Below this much free disk space, old conversions are removed; if that is not enough, the plugin says "Not enough disk space" |

## API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/health` | Liveness check |
| POST | `/v1/bundles` | Upload the bundle JSON (validated against the schema); returns `bundleId` |
| PUT | `/v1/bundles/{id}/assets/{assetId}` | Upload one declared asset (raw bytes) |
| GET | `/v1/bundles/{id}` | Bundle metadata and missing assets |
| GET | `/v1/bundles/{id}/report` | Design check (JSON) |
| POST | `/v1/bundles/{id}/convert` | Start the conversion in the background (202). `?verify=false` skips the browser check; `?wait=true` runs it inline |
| GET | `/v1/bundles/{id}/convert/status` | Job progress: `running` + current step, `done` + result, or `failed` + error |
| GET | `/v1/bundles/{id}/conversion` | Last conversion result |
| GET | `/v1/bundles/{id}/output/{path}` | Live preview of the generated site |
| GET | `/v1/bundles/{id}/fidelity/{path}` | Fidelity report and images |
| GET | `/v1/bundles/{id}/code.zip` | Download the clean code (no internal attributes, corrections folded into classes). `?format=wordpress&acf=free` or `&acf=pro` downloads the WordPress theme instead |
| GET | `/v1/fonts` | Font families available for self-hosting |
| PUT | `/v1/fonts/{family}/{filename}` | Upload a font file (max 15 MB) |

## Tests

```bash
cd engine && uv run pytest && uv run ruff check . && uv run ruff format --check .   # theme tests need `php`
cd plugin && npm run typecheck && npm test && npm run build
```

GitHub Actions runs both suites on every push (`.github/workflows/ci.yml`).

## Changing the bundle contract

Edit `schemas/bundle.schema.json` first. Then update `plugin/src/types.ts` to match, regenerate the fixture (`python fixtures/make_sample_bundle.py`) and run both test suites.
