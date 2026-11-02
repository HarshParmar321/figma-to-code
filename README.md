# Figma to Code

A Figma plugin plus a backend **Engine** that turns a Figma design into clean, responsive HTML/CSS that is **checked against the design pixel by pixel**. A WordPress theme (PHP + ACF) built from the same HTML comes next.

Most Figma-to-code tools give you either a picture-perfect page that no developer can maintain, or tidy code that no longer looks like the design. This project does both and proves it. Every conversion is rendered in a real browser, measured against the Figma frame, corrected automatically, and then given two scores:

- **Fidelity** (0–100): does it match Figma? It checks positions and sizes, typography, colours, images and visual similarity per section.
- **Code quality** (0–100): is it code a front-end developer would ship? It looks at structure, headings, alt text and fixed sizes, then renders the page at 390, 768 and 1280px to catch sideways scrolling, overlapping text and text pushed off screen.

> **Status: v0.6.** See [PROCESS.md](PROCESS.md) for what has been built, the results on real designs and what remains.

## What it does today

- **One click in Figma:** select one desktop frame per page and click **Convert to code**. The plugin shows each step as it happens.
- **Multi-page sites:** you get `index.html`, `about.html` and so on, sharing one stylesheet, the images and the fonts. Menu items link to the matching page.
- **Reusable sections:** a header, footer or section that repeats across pages is generated once, in `components/`, with the same markup and classes on every page.
- **Clean structure:**
  - one centred `.container`, fluid widths, and CSS grid for repeated cards
  - a shared `.btn` component and design tokens (CSS variables)
  - semantic HTML: `header`, `nav > ul > li > a`, sections with ids, a proper heading order, and alt text
- **Single-page navigation:** menu links scroll smoothly to the section of the same name, and buttons like "Contact us" or "Get in touch" go to the contact section.
- **Every screen size:**
  - mobile and tablet frames become media queries, and the phone layout is checked against your mobile frame
  - without them, the layout reflows by itself
  - big type and spacing scale smoothly with `clamp()`
  - Figma Auto Layout settings (gap, padding, alignment, fill / hug / fixed) are used as they are
- **Real forms:** drawn input fields become `<form>`, `<input>`, `<textarea>` and `<button type="submit">`.
- **Clean download:** the zip has no internal attributes, the corrections are folded into the real classes, and a README is included.
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
                    quality.py (code quality score), export.py (clean download), fonts.py
  app/spike_report.py   design check (Auto Layout coverage, sections, tips)
  tests/            97 tests (browser tests run when Chromium is installed)
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
3. Review the fidelity score, the phone/tablet scores (when you gave those frames), the code quality score, the pages and the shared components. Then use **Open preview**, **Report** or **Download code**.

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
| GET | `/v1/bundles/{id}/code.zip` | Download the clean code (no internal attributes, corrections folded into classes) |
| GET | `/v1/fonts` | Font families available for self-hosting |
| PUT | `/v1/fonts/{family}/{filename}` | Upload a font file (max 15 MB) |

## Tests

```bash
cd engine && uv run pytest && uv run ruff check . && uv run ruff format --check .
cd plugin && npm run typecheck && npm test && npm run build
```

GitHub Actions runs both suites on every push (`.github/workflows/ci.yml`).

## Changing the bundle contract

Edit `schemas/bundle.schema.json` first. Then update `plugin/src/types.ts` to match, regenerate the fixture (`python fixtures/make_sample_bundle.py`) and run both test suites.
