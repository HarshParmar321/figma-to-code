// Serializes a Figma node subtree into the flat bundle node map.
import type { BundleNode, Diagnostic, FontRef } from './types.ts';
import { ILLUSTRATION_PART_TYPES, VECTOR_TYPES, vectorGroupExport } from './util.ts';

export const MAX_NODES = 25000;
const CONTAINER_TYPES = new Set(['FRAME', 'GROUP', 'INSTANCE', 'COMPONENT', 'BOOLEAN_OPERATION']);
const MIXED = 'MIXED';

const COMMON_PROPS = [
  'visible', 'locked', 'opacity', 'blendMode', 'isMask', 'maskType', 'rotation',
  'x', 'y', 'width', 'height', 'absoluteBoundingBox', 'absoluteRenderBounds',
  'constraints', 'layoutPositioning', 'layoutAlign', 'layoutGrow',
  'layoutSizingHorizontal', 'layoutSizingVertical', 'minWidth', 'maxWidth', 'minHeight', 'maxHeight',
  'clipsContent', 'fills', 'strokes', 'strokeWeight', 'strokeTopWeight', 'strokeRightWeight',
  'strokeBottomWeight', 'strokeLeftWeight', 'strokeAlign', 'strokeCap', 'strokeJoin', 'dashPattern',
  'cornerRadius', 'topLeftRadius', 'topRightRadius', 'bottomLeftRadius', 'bottomRightRadius', 'cornerSmoothing',
  'effects', 'boundVariables', 'fillStyleId', 'strokeStyleId', 'effectStyleId', 'exportSettings',
];

const LAYOUT_PROPS = [
  'layoutMode', 'layoutWrap', 'primaryAxisAlignItems', 'counterAxisAlignItems', 'counterAxisAlignContent',
  'primaryAxisSizingMode', 'counterAxisSizingMode', 'itemSpacing', 'counterAxisSpacing',
  'paddingTop', 'paddingRight', 'paddingBottom', 'paddingLeft', 'itemReverseZIndex', 'strokesIncludedInLayout',
  'layoutGrids', 'gridStyleId',
  // Grid auto layout (newer API; read defensively).
  'gridRowCount', 'gridColumnCount', 'gridRowGap', 'gridColumnGap', 'gridRowSizes', 'gridColumnSizes',
  'gridRowAnchorIndex', 'gridColumnAnchorIndex', 'gridRowSpan', 'gridColumnSpan',
  'gridChildHorizontalAlign', 'gridChildVerticalAlign',
];

const TEXT_PROPS = [
  'characters', 'fontName', 'fontSize', 'fontWeight', 'lineHeight', 'letterSpacing', 'textCase',
  'textDecoration', 'textAlignHorizontal', 'textAlignVertical', 'textAutoResize', 'textTruncation',
  'maxLines', 'paragraphSpacing', 'paragraphIndent', 'listSpacing', 'hangingPunctuation', 'hangingList',
  'leadingTrim', 'hyperlink', 'textStyleId', 'hasMissingFont',
];

const SEGMENT_FIELDS = [
  'fontName', 'fontSize', 'fontWeight', 'lineHeight', 'letterSpacing', 'textCase', 'textDecoration',
  'fills', 'hyperlink', 'textStyleId', 'fillStyleId', 'listOptions', 'indentation',
] as const;

const INSTANCE_PROPS = ['componentProperties', 'variantProperties', 'isExposedInstance', 'overrides'];
const COMPONENT_PROPS = ['componentPropertyDefinitions', 'variantProperties', 'description', 'key'];

function toPlain(value: unknown): unknown {
  if (value === undefined) return undefined;
  // Figma returns frozen API objects; a JSON round trip turns them into plain data.
  return JSON.parse(JSON.stringify(value));
}

function readProp(node: SceneNode, prop: string): unknown {
  if (!(prop in node)) return undefined;
  try {
    const v = (node as unknown as Record<string, unknown>)[prop];
    if (v === figma.mixed) return MIXED;
    if (typeof v === 'function' || typeof v === 'symbol') return undefined;
    return toPlain(v);
  } catch {
    return undefined; // Some props throw for certain node kinds (e.g. variant definitions).
  }
}

function hasImageFill(node: SceneNode): boolean {
  if (!('fills' in node)) return false;
  const fills = node.fills;
  return Array.isArray(fills) && fills.some((p) => p.type === 'IMAGE' && p.visible !== false);
}

/** Export decision for a container (see vectorGroupExport). Cheap early exit when real content is found. */
function containerExport(node: SceneNode): 'svg' | 'png' | null {
  if (!CONTAINER_TYPES.has(node.type) || !('findAll' in node)) return null;
  const parent = node as ChildrenMixin & SceneNode;
  const offender = parent.findOne((n) => !ILLUSTRATION_PART_TYPES.has(n.type) || hasImageFill(n));
  if (offender || hasImageFill(node)) return null;
  const types = parent.findAll((n) => n.visible).map((n) => n.type);
  return vectorGroupExport(node.type, types, false);
}

export interface SerializeContext {
  nodes: Record<string, BundleNode>;
  diagnostics: Diagnostic[];
  fonts: Map<string, FontRef>;
  /** imageHash -> node ids using it */
  imageHashes: Map<string, string[]>;
  /** node ids to export as one SVG each */
  svgNodeIds: string[];
  /** very complex illustrations, exported as one 2x PNG each */
  rasterNodeIds: string[];
  components: Record<string, Record<string, unknown>>;
  count: number;
  truncated: boolean;
  onProgress?: (count: number) => void;
}

export function newContext(onProgress?: (count: number) => void): SerializeContext {
  return {
    nodes: {}, diagnostics: [], fonts: new Map(), imageHashes: new Map(), svgNodeIds: [], rasterNodeIds: [],
    components: {}, count: 0, truncated: false, onProgress,
  };
}

function addFont(ctx: SerializeContext, font: unknown) {
  if (!font || typeof font !== 'object') return;
  const f = font as { family?: string; style?: string };
  if (!f.family || !f.style) return;
  const key = `${f.family}::${f.style}`;
  const existing = ctx.fonts.get(key);
  if (existing) existing.usageCount += 1;
  else ctx.fonts.set(key, { family: f.family, style: f.style, usageCount: 1 });
}

async function recordComponent(ctx: SerializeContext, main: ComponentNode) {
  if (ctx.components[main.id]) return;
  const parent = main.parent;
  const inSet = parent && parent.type === 'COMPONENT_SET';
  ctx.components[main.id] = {
    id: main.id,
    name: main.name,
    key: main.key ?? null,
    componentSetId: inSet ? parent.id : null,
    componentSetName: inSet ? parent.name : null,
    variantProperties: readProp(main, 'variantProperties') ?? null,
    remote: main.remote,
    description: main.description || null,
  };
}

async function serializeNode(node: SceneNode, parentId: string | null, ctx: SerializeContext, insideSvg: boolean): Promise<BundleNode> {
  const out: BundleNode = {
    id: node.id,
    name: node.name,
    type: node.type,
    parentId,
    children: 'children' in node ? node.children.map((c) => c.id) : [],
  };

  for (const p of COMMON_PROPS) {
    const v = readProp(node, p);
    if (v !== undefined) out[p] = v;
  }
  if ('layoutMode' in node) {
    for (const p of LAYOUT_PROPS) {
      const v = readProp(node, p);
      if (v !== undefined) out[p] = v;
    }
  }

  if (node.type === 'TEXT') {
    for (const p of TEXT_PROPS) {
      const v = readProp(node, p);
      if (v !== undefined) out[p] = v;
    }
    try {
      const segments = node.getStyledTextSegments([...SEGMENT_FIELDS]);
      out.textSegments = toPlain(segments);
      for (const s of segments) addFont(ctx, s.fontName);
    } catch (e) {
      ctx.diagnostics.push({ nodeId: node.id, code: 'TEXT_SEGMENTS_FAILED', severity: 'warning', message: String(e) });
    }
    if (node.hasMissingFont) {
      ctx.diagnostics.push({ nodeId: node.id, code: 'MISSING_FONT', severity: 'error', message: `Text "${node.name}" uses a font that is not available in this Figma session.` });
    }
  }

  if (node.type === 'INSTANCE') {
    for (const p of INSTANCE_PROPS) {
      const v = readProp(node, p);
      if (v !== undefined) out[p] = v;
    }
    try {
      const main = await node.getMainComponentAsync();
      if (main) {
        out.mainComponentId = main.id;
        await recordComponent(ctx, main);
      } else {
        ctx.diagnostics.push({ nodeId: node.id, code: 'MISSING_MAIN_COMPONENT', severity: 'warning', message: `Instance "${node.name}" has no main component (deleted or unavailable library).` });
      }
    } catch (e) {
      ctx.diagnostics.push({ nodeId: node.id, code: 'MAIN_COMPONENT_FAILED', severity: 'warning', message: String(e) });
    }
  }

  if (node.type === 'COMPONENT' || node.type === 'COMPONENT_SET') {
    for (const p of COMPONENT_PROPS) {
      const v = readProp(node, p);
      if (v !== undefined) out[p] = v;
    }
  }

  const reactions = readProp(node, 'reactions');
  if (Array.isArray(reactions) && reactions.length > 0) out.reactions = reactions;

  const motionRaw = node.getPluginData('motion');
  if (motionRaw) {
    try {
      out.motion = JSON.parse(motionRaw);
    } catch {
      ctx.diagnostics.push({ nodeId: node.id, code: 'MOTION_DATA_INVALID', severity: 'warning', message: 'Motion data on this layer is not valid JSON.' });
    }
  }

  // Asset decisions (only for visible nodes).
  if (node.visible && !insideSvg) {
    if (hasImageFill(node) && 'fills' in node && Array.isArray(node.fills)) {
      for (const paint of node.fills as Paint[]) {
        if (paint.type === 'IMAGE' && paint.imageHash) {
          const list = ctx.imageHashes.get(paint.imageHash) ?? [];
          list.push(node.id);
          ctx.imageHashes.set(paint.imageHash, list);
        }
      }
    }
    const isRoot = parentId === null;
    const groupExport = isRoot ? null : containerExport(node);
    if (groupExport === 'png') {
      out.exportAs = 'png';
      ctx.rasterNodeIds.push(node.id);
    } else if (groupExport === 'svg' || (VECTOR_TYPES.has(node.type) && node.width >= 0.5 && node.height >= 0.5)) {
      out.exportAs = 'svg';
      ctx.svgNodeIds.push(node.id);
    }
  }
  if (insideSvg) out.insideSvg = true;

  return out;
}

/** Depth-first serialization of a root frame. Stops at MAX_NODES with a diagnostic. */
export async function serializeTree(root: SceneNode, ctx: SerializeContext): Promise<void> {
  const stack: Array<{ node: SceneNode; parentId: string | null; insideSvg: boolean }> = [
    { node: root, parentId: null, insideSvg: false },
  ];
  while (stack.length > 0) {
    if (ctx.count >= MAX_NODES) {
      if (!ctx.truncated) {
        ctx.truncated = true;
        ctx.diagnostics.push({ nodeId: root.id, code: 'NODE_LIMIT', severity: 'error', message: `Stopped after ${MAX_NODES} nodes. Select a smaller frame.` });
      }
      return;
    }
    const { node, parentId, insideSvg } = stack.pop()!;
    const out = await serializeNode(node, parentId, ctx, insideSvg);
    ctx.nodes[node.id] = out;
    ctx.count += 1;
    if (ctx.count % 250 === 0) {
      ctx.onProgress?.(ctx.count);
      await new Promise((r) => setTimeout(r, 0)); // yield so the UI stays responsive
    }
    if ('children' in node) {
      const childInsideSvg = insideSvg || out.exportAs === 'svg' || out.exportAs === 'png';
      for (let i = node.children.length - 1; i >= 0; i--) {
        stack.push({ node: node.children[i], parentId: node.id, insideSvg: childInsideSvg });
      }
    }
  }
}
