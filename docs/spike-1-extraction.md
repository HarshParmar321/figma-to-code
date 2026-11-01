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
| 12 Free Landing Pages · "Cleaning" (desktop only, 1903×3383) | 179 | 0% (0/28) | 100% | 7.5 s | 296 KB JSON + 21 MB assets | No mobile frame; fully flat layout (39 loose layers, 27 groups); one 11 MB JPG image fill |

### Learnings so far

- **Extraction is complete.** Every required property was read; all 13 assets uploaded; fonts (Roboto, Mitr, Poppins) are all Google Fonts.
- **Real files are often flat.** No section frames and no Auto Layout: sections exist only as full-width background rectangles. The report now detects sections from those bands (5 found here: Hero, About Us, Services, Testimonials, Footer). The generator needs the same logic (EXT-03).
- **Use absolute boxes for geometry.** Children of groups report x/y relative to the nearest frame, not the group, and the root frame sat at x=10333 on the canvas. The extractor must work from `absoluteBoundingBox` relative to the root frame.
- **Repeated patterns are visible but unmarked.** 3 service cards, 4 testimonial cards, 6 numbered steps and 4 identical buttons are separate groups, not components. Repeat detection must work from structure, not component data.
- **Fake shadows.** Buttons are two stacked rectangles offset by 4 px (a "hard shadow"); the generator should map this pattern to `box-shadow` rather than two elements.
- **Image size.** One image fill is 11 MB; asset optimization (FR-EXT-21) is needed before preview hosting.
- **Odd desktop width.** 1903 px (1920 minus a scrollbar); content sits in a ~1200 px centred column (x 350–1552), which the generator should infer as the container width.

## Known limits of this version

- `figma.fileKey` is only available to private plugins; public builds send `null`.
- Dev Mode support (`editorType: dev`) is not enabled yet.
- The UI is plain TypeScript; it moves to Preact when the results view grows (Phase 3).
- CORS allows any origin because Figma plugin iframes have origin `null`; restrict when auth is added.
