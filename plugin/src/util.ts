// Pure helpers with no Figma globals, so they can be unit-tested in Node.
import type { FrameRole } from './types.ts';

export function suggestRole(width: number): FrameRole {
  if (width >= 1024) return 'desktop';
  if (width >= 600) return 'tablet';
  return 'mobile';
}

/** Asset ids must match ^[A-Za-z0-9_.-]+$ (they become file names on the Engine). */
export function assetIdFor(prefix: string, raw: string): string {
  return `${prefix}-${raw.replace(/[^A-Za-z0-9_.-]+/g, '_')}`;
}

export function detectImageFormat(bytes: Uint8Array): 'PNG' | 'JPG' | 'GIF' | 'WEBP' | null {
  if (bytes.length >= 4 && bytes[0] === 0x89 && bytes[1] === 0x50 && bytes[2] === 0x4e && bytes[3] === 0x47) return 'PNG';
  if (bytes.length >= 3 && bytes[0] === 0xff && bytes[1] === 0xd8 && bytes[2] === 0xff) return 'JPG';
  if (bytes.length >= 3 && bytes[0] === 0x47 && bytes[1] === 0x49 && bytes[2] === 0x46) return 'GIF';
  if (
    bytes.length >= 12 &&
    bytes[0] === 0x52 && bytes[1] === 0x49 && bytes[2] === 0x46 && bytes[3] === 0x46 &&
    bytes[8] === 0x57 && bytes[9] === 0x45 && bytes[10] === 0x42 && bytes[11] === 0x50
  ) return 'WEBP';
  return null;
}

/** Walks any JSON value and returns the ids of every VARIABLE_ALIAS it contains. */
export function collectVariableAliasIds(value: unknown, out: Set<string> = new Set()): Set<string> {
  if (Array.isArray(value)) {
    for (const item of value) collectVariableAliasIds(item, out);
  } else if (value && typeof value === 'object') {
    const obj = value as Record<string, unknown>;
    if (obj.type === 'VARIABLE_ALIAS' && typeof obj.id === 'string') out.add(obj.id);
    for (const key of Object.keys(obj)) collectVariableAliasIds(obj[key], out);
  }
  return out;
}

export const VECTOR_TYPES = new Set(['VECTOR', 'BOOLEAN_OPERATION', 'STAR', 'POLYGON']);
const ICON_PART_TYPES = new Set(['VECTOR', 'BOOLEAN_OPERATION', 'STAR', 'POLYGON', 'ELLIPSE', 'RECTANGLE', 'LINE', 'GROUP', 'FRAME']);
export const ICON_MAX_SIZE = 128;

export interface IconProbe {
  type: string;
  width: number;
  height: number;
  hasImageFill: boolean;
  /** Types of all descendants (not including the node itself). */
  descendantTypes: string[];
}

/**
 * A small container whose descendants are all vector-ish shapes (and at least one real vector)
 * is exported as one SVG icon instead of being rebuilt from divs.
 */
export function isIconCandidate(p: IconProbe): boolean {
  if (!['FRAME', 'GROUP', 'INSTANCE', 'COMPONENT'].includes(p.type)) return false;
  if (p.width > ICON_MAX_SIZE || p.height > ICON_MAX_SIZE) return false;
  if (p.hasImageFill || p.descendantTypes.length === 0) return false;
  if (!p.descendantTypes.every((t) => ICON_PART_TYPES.has(t))) return false;
  return p.descendantTypes.some((t) => VECTOR_TYPES.has(t));
}

/** Shapes an illustration may be made of (anything else, e.g. TEXT, means it is real content). */
export const ILLUSTRATION_PART_TYPES = new Set([
  'VECTOR', 'BOOLEAN_OPERATION', 'STAR', 'POLYGON', 'ELLIPSE', 'RECTANGLE', 'LINE', 'GROUP', 'FRAME', 'INSTANCE', 'COMPONENT',
]);
export const ILLUSTRATION_CONTAINERS = new Set(['FRAME', 'GROUP', 'INSTANCE', 'COMPONENT', 'BOOLEAN_OPERATION']);
/** Above this many vector pieces an illustration is exported as a 2x PNG instead of a huge SVG. */
export const RASTER_THRESHOLD = 400;

/**
 * Decides how a container exports: one SVG, one PNG (very complex), or not at all (real content).
 * A container whose descendants are only shapes, with at least one true vector and no image fill,
 * is an illustration or icon of any size and must be exported whole, never piece by piece.
 */
export function vectorGroupExport(type: string, descendantTypes: string[], hasImage: boolean): 'svg' | 'png' | null {
  if (!ILLUSTRATION_CONTAINERS.has(type) || hasImage || descendantTypes.length === 0) return null;
  if (!descendantTypes.every((t) => ILLUSTRATION_PART_TYPES.has(t))) return null;
  const vectors = descendantTypes.filter((t) => VECTOR_TYPES.has(t)).length;
  if (vectors === 0) return null;
  return vectors > RASTER_THRESHOLD ? 'png' : 'svg';
}

/**
 * Mask groups (a photo or texture clipped by a shape or an image) are exported as one 2x PNG so the
 * result is exactly what Figma draws. Groups with text inside stay live HTML; the Engine applies the
 * mask with CSS instead.
 */
export function maskGroupExport(type: string, hasMaskChild: boolean, hasText: boolean): 'png' | null {
  if (!ILLUSTRATION_CONTAINERS.has(type) || type === 'BOOLEAN_OPERATION') return null;
  return hasMaskChild && !hasText ? 'png' : null;
}

/** Validates the role assignment chosen in the UI. Returns an error message or null. */
export function validateRoles(roles: Record<string, FrameRole>): string | null {
  const values = Object.values(roles);
  if (values.length === 0) return 'Select at least one frame.';
  // Without a desktop frame, the widest frames (tablet, else mobile) become the pages.
  return null;
}

const DEVICE_WORDS = /\b((iphone|ipad|pixel|galaxy|android|macbook)(\s*(\d+\w*|pro|max|mini|plus|air|ultra|se))*|desktop|tablet|mobile|phone|web|pc|\d{3,4}(px)?)\b/gi;

/** Site page name from a frame name: "Home / Desktop" and "Home - Mobile" are both "Home". */
export function pageName(frameName: string): string {
  const cleaned = frameName.replace(DEVICE_WORDS, ' ').replace(/[\s/|_\-–—:()[\]]+/g, ' ').trim();
  return cleaned || frameName.trim() || 'Page';
}

// ------------------------------------------------------------------ interactions (pure helpers)
/** Variant values that name an interaction state, and the CSS state they become. */
const STATE_WORDS: Array<[RegExp, 'hover' | 'press' | 'focus']> = [
  [/^(hover|hovered|on hover|mouse ?over)$/i, 'hover'],
  [/^(pressed|press|active|clicked|tap|tapped)$/i, 'press'],
  [/^(focus|focused|focus[- ]visible|keyboard focus)$/i, 'focus'],
];

export function stateOfValue(value: string): 'hover' | 'press' | 'focus' | null {
  const v = value.trim();
  for (const [re, state] of STATE_WORDS) if (re.test(v)) return state;
  return null;
}

/**
 * Sibling variants that are interaction states of `base`: they differ from it in exactly one
 * property, whose value is a state word (State=Hover next to State=Default). Returns
 * [variant index, state] pairs. Used when the designer drew hover variants without wiring them.
 */
export function stateSiblings(
  base: Record<string, string>,
  siblings: Array<Record<string, string>>,
): Array<[number, 'hover' | 'press' | 'focus']> {
  if (Object.values(base).some((v) => stateOfValue(v))) return []; // the base itself is a state
  const out: Array<[number, 'hover' | 'press' | 'focus']> = [];
  siblings.forEach((props, i) => {
    const diff = Object.keys(base).filter((k) => props[k] !== base[k]);
    if (diff.length !== 1 || Object.keys(props).length !== Object.keys(base).length) return;
    const state = stateOfValue(props[diff[0]] ?? '');
    if (state && !out.some(([, s]) => s === state)) out.push([i, state]);
  });
  return out;
}

export type TriggerKind = 'hover' | 'press' | 'click' | 'timeout' | 'enter' | 'leave' | 'other';

/** Figma prototype trigger → how the web page reacts. "While hovering" is ON_HOVER. */
export function triggerKind(trigger: { type?: string } | null | undefined): TriggerKind {
  switch (trigger?.type) {
    case 'ON_HOVER': return 'hover';
    case 'ON_PRESS': case 'MOUSE_DOWN': return 'press';
    case 'ON_CLICK': case 'MOUSE_UP': return 'click';
    case 'AFTER_TIMEOUT': return 'timeout';
    case 'MOUSE_ENTER': return 'enter';
    case 'MOUSE_LEAVE': return 'leave';
    default: return 'other';
  }
}

/** A reaction's actions (newer files use `actions`, older ones a single `action`). */
export function reactionActions(r: { actions?: unknown[]; action?: unknown }): Array<Record<string, unknown>> {
  const list = Array.isArray(r.actions) ? r.actions : r.action ? [r.action] : [];
  return list.filter((a): a is Record<string, unknown> => !!a && typeof a === 'object');
}
