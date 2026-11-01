# Spike 1: Plugin extraction completeness

**Goal:** prove that the Figma Plugin API gives us everything v1 needs to generate HTML/CSS, before building the generator. Estimate: 2 developer-days. Exit criterion: five real Figma files exported, reports reviewed, and the gaps listed below are answered.

## How to run

1. Start the Engine and load the plugin (see README).
2. Pick 5 real landing-page files with mixed quality: 2 clean (Auto Layout, components, variables), 2 typical agency files, 1 messy file.
3. For each file: select the desktop and mobile frames → Export → Upload to Engine.
4. Save each report: `curl localhost:8000/v1/bundles/<id>/report > reports/<file-name>.json`.
5. Fill in the results table below.

## What the report measures

| Metric | Why it matters |
| --- | --- |
| Auto Layout coverage | Below 80%, layout must be inferred from coordinates (higher risk) |
| Field coverage | Share of layers where every property the generator needs was captured |
| Missing fields | Which properties the plugin failed to read (fix in `serialize.ts`) |
| Mixed-style text | Rich text runs the generator must turn into spans |
| Bound variables / unresolved | Whether design tokens come through, including library variables |
| Instances / missing main | Whether component data is usable for repeat detection |
| Interactions, hover variant swaps, transitions | What motion can be read automatically (FR-MOT-01 to 03) |
| Motion panel layers | Data from the future Motion panel (`setPluginData('motion')`) |
| Section counts / name match | How easily desktop and mobile sections can be paired |
| Assets | Reference images, image fills, SVG icons exported and uploaded |

## Questions this spike must answer

- [ ] Are Auto Layout properties (including wrap and grid) complete on real files?
- [ ] Do text segments give everything needed for mixed styles and links?
- [ ] Do library (remote) variables resolve, and how often are they missing?
- [ ] Are prototype reactions on interactive components readable (hover → variant, duration, easing)?
- [ ] Do 2x reference exports succeed on tall pages (8,000 px+)? What is the limit?
- [ ] How long does export take for a 2,000+ layer page? Does the UI stay responsive?
- [ ] Does the icon heuristic (small vector-only containers → one SVG) pick the right layers?
- [ ] What is the typical bundle size (JSON and assets)?

## Results

| File | Layers | AL coverage | Field coverage | Export time | Bundle size | Main gaps |
| --- | --- | --- | --- | --- | --- | --- |
| | | | | | | |

## Known limits of this version

- `figma.fileKey` is only available to private plugins; public builds send `null`.
- Dev Mode support (`editorType: dev`) is not enabled yet.
- The UI is plain TypeScript; it moves to Preact when the results view grows (Phase 3).
- CORS allows any origin because Figma plugin iframes have origin `null`; restrict when auth is added.
