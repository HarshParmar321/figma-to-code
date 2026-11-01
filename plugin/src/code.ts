// Plugin main thread: watches the selection, serializes frames, exports assets, sends all to the UI.
import { exportImageFills, exportRasters, exportReference, exportSvgs, type ExportedAsset } from './assets.ts';
import { newContext, serializeTree } from './serialize.ts';
import type { Bundle, BundleFrame, FrameRole, MainToUi, SelectedFrame, UiToMain } from './types.ts';
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
      frames.push({ id: node.id, name: node.name, width: node.width, height: node.height, suggestedRole: suggestRole(node.width) });
    } else {
      ignored.push(`${node.name} (${node.type})`);
    }
  }
  frames.sort((a, b) => (a.suggestedRole === b.suggestedRole ? a.name.localeCompare(b.name) : b.width - a.width));
  post({ type: 'selection', frames, ignored });
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

  post({ type: 'progress', message: `Exporting ${ctx.imageHashes.size} images…` });
  assets.push(...(await exportImageFills(ctx.imageHashes, ctx.diagnostics)));

  post({ type: 'progress', message: `Exporting ${ctx.svgNodeIds.length} vector graphics…` });
  assets.push(...(await exportSvgs(ctx.svgNodeIds, ctx.diagnostics)));
  if (ctx.rasterNodeIds.length) {
    post({ type: 'progress', message: `Exporting ${ctx.rasterNodeIds.length} detailed illustrations…` });
    assets.push(...(await exportRasters(ctx.rasterNodeIds, ctx.diagnostics)));
  }

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
  } else if (msg.type === 'select-node') {
    const node = await figma.getNodeByIdAsync(msg.nodeId);
    if (node && 'visible' in node) {
      figma.currentPage.selection = [node as SceneNode];
      figma.viewport.scrollAndZoomIntoView([node as SceneNode]);
    }
  }
};
