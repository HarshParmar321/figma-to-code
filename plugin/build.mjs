// Builds the plugin: dist/code.js (main thread) and dist/ui.html (UI with inlined script).
import { build, context } from 'esbuild';
import { mkdir, readFile, writeFile } from 'node:fs/promises';

const watch = process.argv.includes('--watch');
await mkdir('dist', { recursive: true });

const common = { bundle: true, target: 'es2017', logLevel: 'info' };

async function buildUi() {
  const result = await build({ ...common, entryPoints: ['src/ui.ts'], write: false, format: 'iife' });
  const js = result.outputFiles[0].text;
  const html = await readFile('src/ui.html', 'utf8');
  // Figma loads the UI as a single HTML document, so the script is inlined.
  await writeFile('dist/ui.html', html.replace('<!-- SCRIPT -->', `<script>${js}</script>`));
}

if (watch) {
  const ctx = await context({ ...common, entryPoints: ['src/code.ts'], outfile: 'dist/code.js' });
  await ctx.watch();
  const uiCtx = await context({
    ...common,
    entryPoints: ['src/ui.ts'],
    write: false,
    plugins: [{ name: 'inline-ui', setup(b) { b.onEnd(() => buildUi()); } }],
  });
  await uiCtx.watch();
} else {
  await build({ ...common, entryPoints: ['src/code.ts'], outfile: 'dist/code.js' });
  await buildUi();
}
