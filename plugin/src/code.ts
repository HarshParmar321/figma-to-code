// Plugin main thread: watches the selection, serializes frames, exports assets, sends all to the UI.
import { exportImageFills, exportRasters, exportReference, exportSvgs, type ExportedAsset } from './assets.ts';
import { collectInteractions } from './interactions.ts';
import { newContext, serializeTree } from './serialize.ts';
import type { Bundle, BundleFrame, FigmaPageFrames, FrameRole, MainToUi, SelectedFrame, UiToMain } from './types.ts';
import { pageName, suggestRole, validateRoles } from './util.ts';
import { collectVariables } from './variables.ts';

const PLUGIN_VERSION = '0.1.0';
const FRAME_TYPES = new Set(['FRAME', 'COMPONENT', 'INSTANCE']);

figma.showUI(__html__, { width: 420, height: 640, themeColors: true });

function post(msg: MainToUi) {
  figma.ui.postMessage(msg);
}

function sendSelection() {
  const frames: SelectedFrame[] = [];
  const ignored: string[] = [];
  for (const node of figma.currentPage.selection) {
    if (FRAME_TYPES.has(node.type)) {
      frames.push(frameInfo(node));
    } else {
      ignored.push(`${node.name} (${node.type})`);
    }
  }
  frames.sort((a, b) => (a.suggestedRole === b.suggestedRole ? a.name.localeCompare(b.name) : b.width - a.width));
  post({ type: 'selection', frames, ignored });
}

function frameInfo(node: SceneNode, figmaPage?: string): SelectedFrame {
  return { id: node.id, name: node.name, width: node.width, height: node.height, suggestedRole: suggestRole(node.width), figmaPage };
}

/** Every top-level frame of every Figma page (and frames inside Figma sections), for the page picker. */
async function listPages(): Promise<FigmaPageFrames[]> {
  const out: FigmaPageFrames[] = [];
  for (const page of figma.root.children) {
    post({ type: 'progress', message: `Reading page "${page.name}"…` });
    await page.loadAsync();
    const frames: SelectedFrame[] = [];
    for (const node of page.children) {
      if (!node.visible) continue;
      if (FRAME_TYPES.has(node.type)) frames.push(frameInfo(node, page.name));
      else if (node.type === 'SECTION') {
        for (const c of node.children) if (c.visible && FRAME_TYPES.has(c.type)) frames.push(frameInfo(c, page.name));
      }
    }
    if (frames.length) out.push({ id: page.id, name: page.name, frames });
  }
  post({ type: 'progress', message: '' });
  return out;
}

function pageOf(node: BaseNode): PageNode | null {
  let n: BaseNode | null = node;
  while (n && n.type !== 'PAGE') n = n.parent;
  return n as PageNode | null;
}

async function runExport(roles: Record<string, FrameRole>) {
  const started = Date.now();
  const error = validateRoles(roles);
  if (error) {
    post({ type: 'error', message: error });
    return;
  }

  const ctx = newContext((count) => post({ type: 'progress', message: `Serialized ${count} layers…` }));
  const frames: BundleFrame[] = [];
  const assets: ExportedAsset[] = [];

  for (const [id, role] of Object.entries(roles)) {
    const node = await figma.getNodeByIdAsync(id);
    if (!node || !FRAME_TYPES.has(node.type)) {
      post({ type: 'error', message: `Frame ${id} is no longer available.` });
      return;
    }
    const frame = node as FrameNode;
    await pageOf(frame)?.loadAsync(); // frames may come from other Figma pages
    post({ type: 'progress', message: `Reading ${role} frame "${frame.name}"…` });
    await serializeTree(frame, ctx);

    post({ type: 'progress', message: `Exporting ${role} reference images…` });
    const refs = await exportReference(frame, role, ctx.diagnostics);
    assets.push(...refs);
    frames.push({
      role, rootId: frame.id, name: frame.name, page: pageName(frame.name), width: frame.width, height: frame.height,
      referenceAssets: refs.map((r) => r.meta.id),
    });
  }

  post({ type: 'progress', message: 'Reading prototype interactions…' });
  const { interactions, assets: stateAssets } = await collectInteractions(ctx, ctx.diagnostics, (message) => post({ type: 'progress', message }));
  assets.push(...stateAssets);

  post({ type: 'progress', message: `Exporting ${ctx.imageHashes.size} images…` });
  assets.push(...(await exportImageFills(ctx.imageHashes, ctx.diagnostics)));

  post({ type: 'progress', message: `Exporting ${ctx.svgNodeIds.length} vector graphics…` });
  assets.push(...(await exportSvgs(ctx.svgNodeIds, ctx.diagnostics)));
  if (ctx.rasterNodeIds.length) {
    post({ type: 'progress', message: `Exporting ${ctx.rasterNodeIds.length} detailed illustrations…` });
    assets.push(...(await exportRasters(ctx.rasterNodeIds, ctx.diagnostics)));
  }

  // The same image can be reached twice (a state variant that is also an instance on the page, an icon
  // shared by two variants): each asset id is sent once.
  const seenAssets = new Set<string>();
  const unique = assets.filter((a) => (seenAssets.has(a.meta.id) ? false : (seenAssets.add(a.meta.id), true)));
  assets.length = 0;
  assets.push(...unique);

  post({ type: 'progress', message: 'Collecting variables…' });
  const { variables, variableCollections } = await collectVariables(ctx.nodes, ctx.diagnostics);

  const bundle: Bundle = {
    schemaVersion: '0.1',
    source: {
      fileKey: figma.fileKey ?? null,
      fileName: figma.root.name,
      pageId: figma.currentPage.id,
      pageName: figma.currentPage.name,
      pluginVersion: PLUGIN_VERSION,
      editorType: figma.editorType,
      exportedAt: new Date().toISOString(),
    },
    frames,
    nodes: ctx.nodes,
    variables,
    variableCollections,
    components: ctx.components,
    fonts: [...ctx.fonts.values()].sort((a, b) => b.usageCount - a.usageCount),
    assets: assets.map((a) => a.meta),
    diagnostics: ctx.diagnostics,
    interactions,
  };

  post({ type: 'bundle', bundle });
  for (const a of assets) post({ type: 'asset', id: a.meta.id, bytes: a.bytes });
  post({ type: 'done', nodeCount: ctx.count, assetCount: assets.length, ms: Date.now() - started });
}

figma.on('selectionchange', sendSelection);
sendSelection();

figma.ui.onmessage = async (msg: UiToMain) => {
  if (msg.type === 'export') {
    try {
      await runExport(msg.roles);
    } catch (e) {
      post({ type: 'error', message: `Export failed: ${String(e)}` });
    }
  } else if (msg.type === 'open-url') {
    if (/^https?:\/\//.test(msg.url)) figma.openExternal(msg.url);
  } else if (msg.type === 'select-all-frames') {
    // Every page of the site in one click: top-level frames, plus frames grouped in Figma sections.
    const picked: SceneNode[] = [];
    for (const node of figma.currentPage.children) {
      if (!node.visible) continue;
      if (FRAME_TYPES.has(node.type)) picked.push(node);
      else if (node.type === 'SECTION') picked.push(...node.children.filter((c) => c.visible && FRAME_TYPES.has(c.type)));
    }
    figma.currentPage.selection = picked;
    if (picked.length) figma.viewport.scrollAndZoomIntoView(picked);
    sendSelection();
  } else if (msg.type === 'set-motion') {
    // Scroll-in animation for the selected layers (Figma has no scroll animations of its own).
    const value = msg.appear === 'none' ? '' : JSON.stringify({ appear: msg.appear, duration: msg.duration, delay: msg.delay });
    for (const node of figma.currentPage.selection) node.setPluginData('motion', value);
    post({ type: 'motion-set', count: figma.currentPage.selection.length, appear: msg.appear });
  } else if (msg.type === 'list-pages') {
    post({ type: 'pages', pages: await listPages() });
  } else if (msg.type === 'select-node') {
    const node = await figma.getNodeByIdAsync(msg.nodeId);
    const page = node && pageOf(node);
    if (page && page !== figma.currentPage) await figma.setCurrentPageAsync(page);
    if (node && 'visible' in node) {
      figma.currentPage.selection = [node as SceneNode];
      figma.viewport.scrollAndZoomIntoView([node as SceneNode]);
    }
  }
};
