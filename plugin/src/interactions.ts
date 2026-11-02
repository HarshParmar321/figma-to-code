// Prototype interactions: which components react to hover / press / click / time, the variants they
// change to, the overlays that open, and where links go. Read-only: nothing in the file is changed.
import type { ExportedAsset } from './assets.ts';
import { MAX_NODES, serializeTree, type SerializeContext } from './serialize.ts';
import type { BundleInteractions, ComponentState, Diagnostic, InteractiveComponent, OverlayFrame } from './types.ts';
import { assetIdFor, reactionActions, stateSiblings, triggerKind } from './util.ts';

const MAX_COMPONENTS = 60;
const MAX_STATES = 8;
const MAX_OVERLAYS = 20;
const EXTRA_NODE_BUDGET = 4000; // layers kept free for states and overlays

type Reaction = { trigger?: { type?: string; timeout?: number } | null; actions?: unknown[]; action?: unknown };

function plain<T>(v: T): T {
  return v === undefined ? v : JSON.parse(JSON.stringify(v));
}

function reactionsOf(node: BaseNode | null): Reaction[] {
  if (!node || !('reactions' in node)) return [];
  try {
    return plain((node as SceneNode & ReactionMixin).reactions as unknown as Reaction[]) ?? [];
  } catch {
    return [];
  }
}

function variantProps(node: BaseNode | null): Record<string, string> | null {
  if (!node || node.type !== 'COMPONENT') return null;
  try {
    return (node as ComponentNode).variantProperties ?? null;
  } catch {
    return null;
  }
}

async function exportPng(node: SceneNode, label: string, diagnostics: Diagnostic[]): Promise<ExportedAsset | null> {
  try {
    const bytes = await node.exportAsync({ format: 'PNG', constraint: { type: 'SCALE', value: 1 } });
    return {
      meta: {
        id: assetIdFor('ref', `${label}-${node.id}-1x`), kind: 'reference', format: 'PNG', nodeId: node.id, scale: 1,
        width: Math.round(node.width), height: Math.round(node.height), byteLength: bytes.byteLength,
      },
      bytes,
    };
  } catch (e) {
    diagnostics.push({ nodeId: node.id, code: 'STATE_EXPORT_FAILED', severity: 'warning', message: `Could not export "${node.name}": ${String(e)}` });
    return null;
  }
}

/** Serializes an extra root (a variant or an overlay frame) unless it is already in the bundle. */
async function addRoot(node: SceneNode, ctx: SerializeContext): Promise<boolean> {
  if (ctx.nodes[node.id]) return true;
  if (ctx.count > MAX_NODES - EXTRA_NODE_BUDGET) return false;
  await serializeTree(node, ctx);
  return true;
}

function topFrameId(node: BaseNode): string | null {
  let n: BaseNode | null = node;
  while (n && n.parent && n.parent.type !== 'PAGE') n = n.parent;
  return n && n.type !== 'PAGE' && n.type !== 'DOCUMENT' ? n.id : null;
}

export async function collectInteractions(
  ctx: SerializeContext,
  diagnostics: Diagnostic[],
  progress: (msg: string) => void,
): Promise<{ interactions: BundleInteractions; assets: ExportedAsset[] }> {
  const assets: ExportedAsset[] = [];
  const pageNodes = Object.values(ctx.nodes);
  const targets: BundleInteractions['targets'] = {};
  const overlayIds = new Set<string>();
  const instanceReactions = new Map<string, Reaction[]>(); // main component id -> reactions on its instances
  const mainIds: string[] = [];

  for (const n of pageNodes) {
    const reactions = (n.reactions as Reaction[] | undefined) ?? [];
    for (const r of reactions) {
      for (const a of reactionActions(r)) {
        if (a.type !== 'NODE' || typeof a.destinationId !== 'string') continue;
        targets[a.destinationId] = targets[a.destinationId] ?? { name: '', type: '', topFrameId: null };
        if (a.navigation === 'OVERLAY' || a.navigation === 'SWAP') overlayIds.add(a.destinationId);
      }
    }
    const main = n.mainComponentId as string | undefined;
    if (n.type === 'INSTANCE' && main) {
      if (!mainIds.includes(main)) mainIds.push(main);
      if (reactions.length) instanceReactions.set(main, [...(instanceReactions.get(main) ?? []), ...reactions]);
    }
  }

  const components: InteractiveComponent[] = [];
  for (const mainId of mainIds) {
    if (components.length >= MAX_COMPONENTS) break;
    const main = (await figma.getNodeByIdAsync(mainId)) as ComponentNode | null;
    if (!main || main.type !== 'COMPONENT') continue;
    const own = reactionsOf(main);
    const all = [...(instanceReactions.get(mainId) ?? []), ...own];
    const states: ComponentState[] = [];
    const seen = new Set<string>([main.id]);

    const addState = async (dest: SceneNode, partial: Omit<ComponentState, 'variantId' | 'name' | 'variantProperties' | 'referenceAsset'>) => {
      if (seen.has(dest.id) || states.length >= MAX_STATES || dest.type !== 'COMPONENT') return;
      seen.add(dest.id);
      if (!(await addRoot(dest, ctx))) return;
      const ref = await exportPng(dest, 'state', diagnostics);
      if (ref) assets.push(ref);
      states.push({ variantId: dest.id, name: dest.name, variantProperties: variantProps(dest), referenceAsset: ref?.meta.id ?? null, ...partial });
    };

    // Wired states: "While hovering / While pressing / On click / Mouse enter → Change to".
    for (const r of all) {
      const kind = triggerKind(r.trigger);
      for (const a of reactionActions(r)) {
        if (a.type !== 'NODE' || a.navigation !== 'CHANGE_TO' || typeof a.destinationId !== 'string') continue;
        const dest = (await figma.getNodeByIdAsync(a.destinationId)) as SceneNode | null;
        if (!dest) continue;
        if (kind === 'hover' || kind === 'enter') await addState(dest, { trigger: 'hover', wired: true, transition: plain(a.transition) ?? null, from: main.id });
        else if (kind === 'press') await addState(dest, { trigger: 'press', wired: true, transition: plain(a.transition) ?? null, from: main.id });
        else if (kind === 'click') await addState(dest, { trigger: 'click', wired: true, transition: plain(a.transition) ?? null, from: main.id });
      }
    }

    // Auto-advancing chains ("After delay → Change to" from variant to variant): carousels, tickers.
    let current: SceneNode = main;
    for (let order = 1; order <= MAX_STATES; order++) {
      const r = reactionsOf(current).find((x) => triggerKind(x.trigger) === 'timeout');
      const a = r && reactionActions(r).find((x) => x.type === 'NODE' && x.navigation === 'CHANGE_TO');
      if (!r || !a || typeof a.destinationId !== 'string' || a.destinationId === main.id) break;
      const dest = (await figma.getNodeByIdAsync(a.destinationId)) as SceneNode | null;
      if (!dest || seen.has(dest.id)) break;
      await addState(dest, {
        trigger: 'timeout', wired: true, transition: plain(a.transition) ?? null,
        timeout: Number(r.trigger?.timeout ?? 0), order, from: current.id,
      });
      current = dest;
    }

    // Unwired: State=Hover / Pressed / Focus variants next to the default one.
    const set = main.parent && main.parent.type === 'COMPONENT_SET' ? (main.parent as ComponentSetNode) : null;
    const base = variantProps(main);
    if (!states.length && set && base) {
      const siblings = set.children.filter((c): c is ComponentNode => c.type === 'COMPONENT');
      for (const [i, state] of stateSiblings(base, siblings.map((c) => variantProps(c) ?? {}))) {
        await addState(siblings[i], { trigger: state === 'focus' ? 'focus' : state, wired: false, transition: null, from: main.id });
      }
    }

    if (!states.length && !own.length) continue;
    let referenceAsset: string | null = null;
    if (states.length) {
      progress(`Reading states of "${set?.name ?? main.name}"…`);
      await addRoot(main, ctx);
      const ref = await exportPng(main, 'state', diagnostics);
      if (ref) {
        assets.push(ref);
        referenceAsset = ref.meta.id;
      }
    }
    components.push({
      id: main.id, name: main.name, setId: set?.id ?? null, setName: set?.name ?? null, variantProperties: base,
      reactions: own, referenceAsset, states,
    });
  }

  // Overlays (menus, pop-ups, modals) opened by "Open overlay" / "Swap overlay".
  const overlays: OverlayFrame[] = [];
  for (const id of overlayIds) {
    if (overlays.length >= MAX_OVERLAYS) break;
    const node = (await figma.getNodeByIdAsync(id)) as (FrameNode & SceneNode) | null;
    if (!node || !('overlayPositionType' in node)) continue;
    progress(`Reading overlay "${node.name}"…`);
    if (!(await addRoot(node, ctx))) continue;
    const ref = await exportPng(node, 'overlay', diagnostics);
    if (ref) assets.push(ref);
    overlays.push({
      rootId: node.id, name: node.name, positionType: node.overlayPositionType,
      background: plain(node.overlayBackground), closeOnOutside: node.overlayBackgroundInteraction === 'CLOSE_ON_CLICK_OUTSIDE',
      referenceAsset: ref?.meta.id ?? null,
    });
  }

  for (const id of Object.keys(targets)) {
    const node = await figma.getNodeByIdAsync(id);
    if (node) targets[id] = { name: node.name, type: node.type, topFrameId: topFrameId(node) };
  }

  return { interactions: { components, overlays, targets }, assets };
}
