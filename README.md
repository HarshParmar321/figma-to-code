# Figma to Code

A Figma plugin plus a cloud Engine that turns a Figma design into verified, responsive HTML/CSS (v1) and later a WordPress theme (v1.5). See the SRS for the full specification.

**Current stage: Spike 1, plugin extraction.** The plugin reads selected frames into a bundle; the Engine stores it and produces an extraction report showing how complete and convertible the design is.

## Repository layout

```
schemas/        bundle.schema.json: the contract between plugin and Engine (source of truth)
plugin/         Figma plugin (TypeScript): serializer, asset export, UI
engine/         Engine (Python, FastAPI): bundle intake, storage, extraction report
fixtures/       sample bundle + generator script
docs/           spike guides and notes
```

## Requirements

- Figma desktop app (needed for loading development plugins)
- Node.js 20+ and npm
- Python 3.11+ and [uv](https://docs.astral.sh/uv/)

## Run the Engine

```bash
cd engine
uv sync
uv run uvicorn app.main:app --reload --port 8000
```

Check it: `curl localhost:8000/health`. Bundles are stored in `engine/data/` (override with `ENGINE_DATA_DIR`).

## Build and load the plugin

```bash
cd plugin
npm install
npm run build      # or: npm run watch
```

In the Figma desktop app: **Plugins → Development → Import plugin from manifest…** and choose `plugin/manifest.json`. Then run **Plugins → Development → Figma to Code (dev)**.

1. Select a desktop frame and its mobile frame on the canvas.
2. Check the role next to each frame (suggested from width).
3. Click **Export**, then **Upload to Engine**. The extraction report appears in the plugin.
4. **Download JSON** saves the bundle for offline analysis.

The plugin can only reach `http://localhost:8000` during development (see `networkAccess` in the manifest).

## Report from a saved bundle

```bash
cd engine
uv run python -m app.spike_report ../fixtures/sample-bundle.json --md
```

## Tests

```bash
cd engine && uv run pytest && uv run ruff check .
cd plugin && npm run typecheck && npm test && npm run build
```

## API (v0.1)

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/health` | Liveness check |
| POST | `/v1/bundles` | Upload bundle JSON; validated against the schema; returns `bundleId` |
| PUT | `/v1/bundles/{id}/assets/{assetId}` | Upload one declared asset (raw bytes) |
| GET | `/v1/bundles/{id}` | Bundle metadata and missing assets |
| GET | `/v1/bundles/{id}/report` | Extraction report (JSON) |

## Changing the bundle contract

Edit `schemas/bundle.schema.json` first, then update `plugin/src/types.ts` to match, regenerate the fixture (`python fixtures/make_sample_bundle.py`), and run both test suites.
