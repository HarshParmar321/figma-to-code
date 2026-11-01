# Figma to Code

A Figma plugin plus an Engine that turns a Figma design into verified, responsive HTML/CSS (v1) and later a WordPress theme (v1.5). See the SRS for the full specification.

**Current stage: v0.5.** In the plugin, select one desktop frame per page and click **Convert to code**. Every page is converted to HTML/CSS, rendered in a real browser, measured against its Figma frame and **automatically corrected** until it matches. The code is written the way a front-end developer would: one centred container, fluid widths, CSS grid for repeated cards, a shared `.btn` component, design tokens, semantic HTML with a real `<nav>` whose links scroll to the matching sections. Every conversion gets two scores: fidelity (does it match Figma?) and code quality (is it clean and does it hold up on phone, tablet and laptop?). Sections that repeat across pages (header, footer…) are generated once as shared components. You get a live preview, a fidelity report and a downloadable zip.

## Repository layout

```
schemas/          bundle.schema.json: the contract between plugin and Engine (source of truth)
plugin/           Figma plugin (TypeScript): serializer, asset export, one-click Convert UI
engine/           Engine (Python, FastAPI)
  app/pipeline/   ir.py → layout.py → generate.py → fidelity.py, orchestrated by convert.py
  app/spike_report.py   design check (Auto Layout coverage, sections, findings)
fixtures/         sample bundle + generator script
docs/             pipeline notes and spike results
```

## Requirements

- Figma desktop app (needed for loading development plugins)
- Node.js 20+ and npm
- Python 3.11+ and [uv](https://docs.astral.sh/uv/)

## Run the Engine

```bash
cd engine
uv sync
uv run playwright install chromium     # once (~150 MB): needed for the design comparison
uv run uvicorn app.main:app --reload --port 8000
```

Check it: open http://localhost:8000/health. Everything is stored in `engine/data/bundles/<bundle-id>/`:

| Path | What |
| --- | --- |
| `output/` | Generated site: `index.html` (+ one file per page), `css/styles.css`, `css/corrections.css`, `components/`, `images/`, `fonts/` |
| `code.zip` | The same site, zipped for download |
| `fidelity/report.html` | Score, breakdown, side-by-side and difference images, issues (multi-page: site summary + one folder per page) |
| `conversion.json` | Summary of the last conversion |

Without Chromium the code is still generated; the score is skipped and the plugin tells you how to install it.

## Build and load the plugin

```bash
cd plugin
npm install
npm run build      # or: npm run watch
```

In the Figma desktop app, open a design file, press **Cmd + /**, run **Import plugin from manifest** and choose `plugin/manifest.json`. Then run **Figma to Code (dev)**.

1. Select one desktop frame per page (Home, About, Contact…) and their mobile frames if you have them, all on the same Figma page. The plugin shows the page name it will use for each frame ("Home / Desktop" → Home).
2. Click **Convert to code**. Steps: Read design → Upload → Generate and check (live progress, including correction rounds).
3. See the score per page and the shared components found, then **Open preview**, **Report** or **Download code**.

The plugin can only reach `http://localhost:8000` during development (see `networkAccess` in the manifest). **Settings and advanced** in the plugin has export-only, upload-only and JSON download.

## Fonts

Free Google fonts load automatically. For any other font (for example a paid font such as TT Hoves), the result shows **Missing fonts**: pick the font files in the plugin and click **Upload fonts and convert again**. You can also copy the files into `engine/data/fonts/<family-name>/` yourself. Until a font is available the page uses a fallback font and the score is capped, so it never silently claims a match.

## Settings (environment variables)

| Variable | Default | Purpose |
| --- | --- | --- |
| `ENGINE_DATA_DIR` | `engine/data` | Where bundles and outputs are stored |
| `ENGINE_FONT_DIR` | `engine/data/fonts` | Self-hosted fonts, one folder per family (`tt-hoves/TTHoves-Bold.otf`, any naming with a weight word; .woff2/.woff/.otf/.ttf; variable fonts supported). Fonts not found here load from Google Fonts. |
| `ENGINE_CHROMIUM_PATH` | Playwright's Chromium | Use a specific Chrome/Chromium binary for the comparison |
| `ENGINE_PUBLIC_URL` | `http://localhost:8000` | Base URL used in links returned to the plugin |
| `ENGINE_KEEP_BUNDLES` | `8` | Only the most recent conversions are kept; older ones are deleted automatically |
| `ENGINE_MIN_FREE_MB` | `400` | Below this much free disk space, old conversions are removed first; if that is not enough the plugin shows "Not enough disk space" instead of a crash |

## Command-line tools

```bash
cd engine
uv run python -m app.spike_report ../fixtures/sample-bundle.json --md     # design check for a saved bundle
```

## Tests

```bash
cd engine && uv run pytest && uv run ruff check .
cd plugin && npm run typecheck && npm test && npm run build
```

The full browser test runs only when Chromium is installed; otherwise it is skipped.

## API (v0.2)

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/health` | Liveness check |
| POST | `/v1/bundles` | Upload bundle JSON; validated against the schema; returns `bundleId` |
| PUT | `/v1/bundles/{id}/assets/{assetId}` | Upload one declared asset (raw bytes) |
| GET | `/v1/bundles/{id}` | Bundle metadata and missing assets |
| GET | `/v1/bundles/{id}/report` | Design check (JSON) |
| POST | `/v1/bundles/{id}/convert` | Start the conversion in the background (202). `?verify=false` skips the browser check, `?wait=true` runs inline |
| GET | `/v1/bundles/{id}/convert/status` | Job progress: `running` + current step, `done` + result, or `failed` + error |
| GET | `/v1/bundles/{id}/conversion` | Last conversion result |
| GET | `/v1/bundles/{id}/output/{path}` | Live preview of the generated site |
| GET | `/v1/bundles/{id}/fidelity/{path}` | Fidelity report and images |
| GET | `/v1/bundles/{id}/code.zip` | Download the generated code |
| GET | `/v1/fonts` | Font families available for self-hosting |
| PUT | `/v1/fonts/{family}/{filename}` | Upload a font file (.woff2, .woff, .otf, .ttf; max 15 MB) |

## Changing the bundle contract

Edit `schemas/bundle.schema.json` first, then update `plugin/src/types.ts` to match, regenerate the fixture (`python fixtures/make_sample_bundle.py`), and run both test suites.
